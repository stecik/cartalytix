"""Fixed original-basket allocations used by exclusion-aware aggregates."""

from decimal import Decimal
from uuid import UUID

from sqlalchemy import ColumnElement, Numeric, Select, and_, case, cast, exists, func, select
from sqlalchemy.sql.selectable import CTE

from cartalytix.models import Discount, ProductExclusion, ProviderConnection, Receipt, ReceiptItem

MONEY = Numeric(18, 2)


def product_excluded(owner_id: UUID) -> ColumnElement[bool]:
    return (
        exists()
        .where(
            ProductExclusion.owner_id == owner_id,
            ProductExclusion.name == ReceiptItem.name,
            ProductExclusion.currency == Receipt.currency,
        )
        .correlate(ReceiptItem, Receipt)
    )


def accounting(
    owner_id: UUID, receipt_ids: list[UUID] | Select[UUID] | None = None
) -> tuple[CTE, CTE]:
    """Return item and receipt amounts; exclusions never change original allocation weights."""
    # Receipt-level sums are index lookups carried down the item chain: joining the item rows back
    # to receipt CTEs made the planner, misled by small row estimates, rescan them per item.
    discount_total = (
        select(func.sum(Discount.amount))
        .where(Discount.receipt_id == Receipt.id)
        .correlate(Receipt)
        .scalar_subquery()
    )
    unlinked_discount = (
        select(func.coalesce(func.sum(Discount.amount), 0))
        .where(Discount.receipt_id == Receipt.id, Discount.item_id.is_(None))
        .correlate(Receipt)
        .scalar_subquery()
    )
    receipt_query = (
        select(
            Receipt.id,
            Receipt.total_amount,
            Receipt.is_excluded,
            discount_total.label("discount_amount"),
            unlinked_discount.label("unlinked_discount"),
        )
        .join(ProviderConnection, Receipt.connection_id == ProviderConnection.id)
        .where(ProviderConnection.owner_id == owner_id)
    )
    if receipt_ids is not None:
        receipt_query = receipt_query.where(Receipt.id.in_(receipt_ids))
    owned_receipts = receipt_query.cte("accounting_receipts")
    item_discounts = (
        select(Discount.item_id, func.sum(Discount.amount).label("amount"))
        .join(owned_receipts, Discount.receipt_id == owned_receipts.c.id)
        .where(Discount.item_id.is_not(None))
        .group_by(Discount.item_id)
        .cte("accounting_item_discounts")
    )
    linked_discount = func.coalesce(item_discounts.c.amount, 0)
    base_amount = ReceiptItem.total_amount - linked_discount
    source_items = (
        select(
            ReceiptItem.id,
            ReceiptItem.receipt_id,
            ReceiptItem.position,
            base_amount.label("base_amount"),
            func.coalesce(ReceiptItem.deposit_amount, 0).label("deposit_amount"),
            linked_discount.label("linked_discount"),
            case((base_amount > 0, base_amount), else_=0).label("positive_weight"),
            and_(
                Receipt.is_excluded.is_(False),
                ReceiptItem.is_excluded.is_(False),
                ~product_excluded(owner_id),
            ).label("is_included"),
            owned_receipts.c.total_amount.label("receipt_total"),
            owned_receipts.c.unlinked_discount,
        )
        .join(Receipt, ReceiptItem.receipt_id == Receipt.id)
        .join(owned_receipts, Receipt.id == owned_receipts.c.id)
        .outerjoin(item_discounts, ReceiptItem.id == item_discounts.c.item_id)
        .cte("accounting_source_items")
    )
    partition = source_items.c.receipt_id
    source_weights = select(
        source_items,
        func.sum(source_items.c.positive_weight)
        .over(partition_by=partition)
        .label("positive_total"),
        func.sum(func.abs(source_items.c.base_amount))
        .over(partition_by=partition)
        .label("absolute_total"),
    ).cte("accounting_source_weights")
    weights = select(
        source_weights,
        case(
            (source_weights.c.positive_total > 0, source_weights.c.positive_weight),
            (source_weights.c.absolute_total > 0, func.abs(source_weights.c.base_amount)),
            else_=1,
        ).label("weight"),
    ).cte("accounting_weights")
    weighted_items = select(
        weights,
        func.sum(weights.c.weight).over(partition_by=weights.c.receipt_id).label("weight_total"),
        func.sum(weights.c.weight)
        .over(
            partition_by=weights.c.receipt_id,
            order_by=weights.c.position,
            rows=(None, 0),
        )
        .label("cumulative_weight"),
        func.sum(weights.c.base_amount + weights.c.deposit_amount)
        .over(partition_by=weights.c.receipt_id)
        .label("base_total"),
    ).cte("accounting_weighted_items")
    cumulative_share = weighted_items.c.cumulative_weight / weighted_items.c.weight_total
    previous_share = (
        weighted_items.c.cumulative_weight - weighted_items.c.weight
    ) / weighted_items.c.weight_total
    receipt_discount = weighted_items.c.unlinked_discount
    discount_share = cast(
        func.round(receipt_discount * cumulative_share, 2)
        - func.round(receipt_discount * previous_share, 2),
        MONEY,
    )
    # Reconciliation differences stay separate from discounts and follow fixed original weights.
    residual = weighted_items.c.receipt_total - (weighted_items.c.base_total - receipt_discount)
    residual_share = cast(
        func.round(residual * cumulative_share, 2) - func.round(residual * previous_share, 2), MONEY
    )
    net_amount = cast(
        weighted_items.c.base_amount + weighted_items.c.deposit_amount - discount_share, MONEY
    )
    item_amounts = select(
        weighted_items.c.id,
        weighted_items.c.receipt_id,
        weighted_items.c.position,
        net_amount.label("net_amount"),
        case((weighted_items.c.is_included, net_amount + residual_share), else_=Decimal(0)).label(
            "included_amount"
        ),
        case(
            (weighted_items.c.is_included, weighted_items.c.linked_discount + discount_share),
            else_=Decimal(0),
        ).label("included_discount_amount"),
        case((weighted_items.c.is_included, net_amount + residual_share), else_=Decimal(0)).label(
            "receipt_component_amount"
        ),
        weighted_items.c.is_included,
    ).cte("accounting_item_amounts")
    item_totals = (
        select(
            item_amounts.c.receipt_id,
            func.count().label("item_count"),
            func.sum(case((item_amounts.c.is_included, 1), else_=0)).label("included_count"),
            func.sum(item_amounts.c.receipt_component_amount).label("amount"),
            func.sum(item_amounts.c.included_discount_amount).label("discount_amount"),
        )
        .group_by(item_amounts.c.receipt_id)
        .cte("accounting_item_totals")
    )
    receipt_amounts = (
        select(
            owned_receipts.c.id.label("receipt_id"),
            case(
                (owned_receipts.c.is_excluded, Decimal(0)),
                else_=func.coalesce(item_totals.c.amount, owned_receipts.c.total_amount),
            ).label("included_amount"),
            case(
                (owned_receipts.c.is_excluded, Decimal(0)),
                else_=func.coalesce(
                    item_totals.c.discount_amount, owned_receipts.c.discount_amount, Decimal(0)
                ),
            ).label("included_discount_amount"),
            and_(
                owned_receipts.c.is_excluded.is_(False),
                func.coalesce(item_totals.c.included_count, 1) > 0,
            ).label("is_included"),
        )
        .outerjoin(item_totals, owned_receipts.c.id == item_totals.c.receipt_id)
        .cte("accounting_receipt_amounts")
    )
    return item_amounts, receipt_amounts
