"""Owner-scoped product ledger: receipt items grouped by name and currency."""

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import (
    ColumnElement,
    Select,
    String,
    UnaryExpression,
    Uuid,
    and_,
    case,
    delete,
    func,
    literal,
    null,
    or_,
    select,
    tuple_,
)
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.sql.selectable import CTE

from cartalytix.accounting import accounting, item_included, product_excluded
from cartalytix.dependencies import Db, Owner
from cartalytix.exclusion_schemas import (
    ExclusionOutput,
    ExclusionReason,
    ExclusionScope,
    ExclusionSort,
    ExclusionsOutput,
)
from cartalytix.models import ProductExclusion, ProviderConnection, Receipt, ReceiptItem
from cartalytix.schemas import (
    ExclusionFilter,
    ItemDetailOutput,
    ItemPurchaseOutput,
    ItemSort,
    ItemsOutput,
    ItemSummaryOutput,
    ItemTotalOutput,
    ItemTotalsInput,
    ItemTotalsOutput,
    ProductExclusionInput,
)
from cartalytix.search import name_matches

PURCHASE_LIMIT = 200
CENTS = Decimal("0.01")

PRICE = func.coalesce(
    ReceiptItem.unit_price, ReceiptItem.total_amount / func.nullif(ReceiptItem.quantity, 0)
)
RECEIPT_COUNT = func.count(func.distinct(ReceiptItem.receipt_id))
TOTAL_AMOUNT = func.sum(ReceiptItem.total_amount)
AVERAGE_PRICE = func.avg(PRICE)
LAST_PURCHASED = func.max(Receipt.purchased_at)
ITEM_ORDER: dict[ItemSort, UnaryExpression[Any]] = {
    ItemSort.NAME_ASC: ReceiptItem.name.asc(),
    ItemSort.NAME_DESC: ReceiptItem.name.desc(),
    ItemSort.RECEIPT_COUNT_DESC: RECEIPT_COUNT.desc(),
    ItemSort.RECEIPT_COUNT_ASC: RECEIPT_COUNT.asc(),
    ItemSort.TOTAL_AMOUNT_DESC: TOTAL_AMOUNT.desc(),
    ItemSort.TOTAL_AMOUNT_ASC: TOTAL_AMOUNT.asc(),
    ItemSort.AVERAGE_PRICE_DESC: AVERAGE_PRICE.desc(),
    ItemSort.AVERAGE_PRICE_ASC: AVERAGE_PRICE.asc(),
    ItemSort.LAST_PURCHASED_DESC: LAST_PURCHASED.desc(),
    ItemSort.LAST_PURCHASED_ASC: LAST_PURCHASED.asc(),
}

router = APIRouter(prefix="/api/v1/items", tags=["items"])


def _money(value: Decimal | float) -> str:
    return format(Decimal(str(value)).quantize(CENTS, rounding=ROUND_HALF_UP), ".2f")


def _quantity(value: Decimal | float) -> str:
    return format(Decimal(str(value)).normalize(), "f")


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _summaries(
    filters: list[ColumnElement[bool]],
    owner_id: UUID,
    amounts: CTE,
    included_metrics: bool = False,
) -> Select[
    str, str, str | None, int, Decimal, Decimal, Decimal, Decimal, Decimal, datetime, bool, Decimal
]:
    receipt_count = RECEIPT_COUNT
    quantity = func.sum(ReceiptItem.quantity)
    average = AVERAGE_PRICE
    minimum = func.min(PRICE)
    maximum = func.max(PRICE)
    if included_metrics:
        receipt_count = func.count(
            func.distinct(case((amounts.c.is_included, ReceiptItem.receipt_id), else_=None))
        )
        quantity = func.sum(case((amounts.c.is_included, ReceiptItem.quantity), else_=0))
        included_price = case((amounts.c.is_included, PRICE), else_=None)
        average = func.coalesce(func.avg(included_price), 0)
        minimum = func.coalesce(func.min(included_price), 0)
        maximum = func.coalesce(func.max(included_price), 0)
    statement = (
        select(
            ReceiptItem.name,
            Receipt.currency,
            func.max(ReceiptItem.unit),
            receipt_count,
            quantity,
            TOTAL_AMOUNT,
            average,
            minimum,
            maximum,
            LAST_PURCHASED,
            product_excluded(owner_id),
            func.sum(amounts.c.included_amount),
        )
        .select_from(ReceiptItem)
        .join(Receipt, ReceiptItem.receipt_id == Receipt.id)
        .join(ProviderConnection, Receipt.connection_id == ProviderConnection.id)
        .join(amounts, amounts.c.id == ReceiptItem.id)
        .where(*filters)
        .group_by(ReceiptItem.name, Receipt.currency)
    )
    return cast(
        Select[
            str,
            str,
            str | None,
            int,
            Decimal,
            Decimal,
            Decimal,
            Decimal,
            Decimal,
            datetime,
            bool,
            Decimal,
        ],
        statement,
    )


def _summary(
    name: str,
    currency: str,
    unit: str | None,
    count: int,
    quantity: Decimal,
    total: Decimal,
    average: Decimal,
    minimum: Decimal,
    maximum: Decimal,
    last: datetime,
    excluded: bool,
    included_amount: Decimal,
) -> ItemSummaryOutput:
    return ItemSummaryOutput(
        name=name,
        currency=currency,
        unit=unit,
        receipt_count=count,
        quantity=_quantity(quantity),
        total_amount=_money(total),
        average_price=_money(average),
        min_price=_money(minimum),
        max_price=_money(maximum),
        last_purchased_at=_aware(last),
        is_excluded=excluded,
        included_amount=_money(included_amount),
    )


@router.get("", response_model=ItemsOutput)
def items(
    user: Owner,
    db: Db,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
    connection_id: UUID | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    purchased_from: datetime | None = None,
    purchased_to: datetime | None = None,
    price_min: Annotated[Decimal | None, Query(ge=0, max_digits=18, decimal_places=2)] = None,
    price_max: Annotated[Decimal | None, Query(ge=0, max_digits=18, decimal_places=2)] = None,
    sort: ItemSort = ItemSort.NAME_ASC,
    exclusion: ExclusionFilter = ExclusionFilter.INCLUDED,
) -> ItemsOutput:
    filters: list[ColumnElement[bool]] = [ProviderConnection.owner_id == user.id]
    included = item_included(user.id)
    if exclusion != ExclusionFilter.ALL:
        filters.append(included if exclusion == ExclusionFilter.INCLUDED else ~included)
    if connection_id is not None:
        filters.append(Receipt.connection_id == connection_id)
    if q and q.strip():
        filters.append(name_matches(ReceiptItem.name, q.strip()))
    if purchased_from is not None:
        filters.append(Receipt.purchased_at >= purchased_from)
    if purchased_to is not None:
        filters.append(Receipt.purchased_at < purchased_to)
    having: list[ColumnElement[bool]] = []
    if price_min is not None:
        having.append(price_min <= AVERAGE_PRICE)
    if price_max is not None:
        having.append(price_max >= AVERAGE_PRICE)
    groups = (
        select(ReceiptItem.name, Receipt.currency)
        .select_from(ReceiptItem)
        .join(Receipt, ReceiptItem.receipt_id == Receipt.id)
        .join(ProviderConnection, Receipt.connection_id == ProviderConnection.id)
        .where(*filters)
        .group_by(ReceiptItem.name, Receipt.currency)
        .having(*having)
    )
    total = db.scalar(select(func.count()).select_from(groups.subquery())) or 0
    order = (
        ITEM_ORDER.get(sort, ITEM_ORDER[ItemSort.NAME_ASC]),
        ReceiptItem.name.asc(),
        Receipt.currency.asc(),
    )
    page_products = groups.order_by(*order).limit(limit).offset(offset).cte("item_page")
    matching_receipts = (
        select(Receipt.id)
        .join(ProviderConnection)
        .join(ReceiptItem, ReceiptItem.receipt_id == Receipt.id)
        .join(
            page_products,
            and_(
                page_products.c.name == ReceiptItem.name,
                page_products.c.currency == Receipt.currency,
            ),
        )
        .where(*filters)
    )
    amounts, _ = accounting(user.id, matching_receipts)
    rows = db.execute(
        _summaries(filters, user.id, amounts)
        .join(
            page_products,
            and_(
                page_products.c.name == ReceiptItem.name,
                page_products.c.currency == Receipt.currency,
            ),
        )
        .having(*having)
        .order_by(*order)
    )
    return ItemsOutput(
        items=[_summary(*row) for row in rows], total=total, limit=limit, offset=offset
    )


@router.post("/totals", response_model=ItemTotalsOutput)
def item_totals(user: Owner, db: Db, body: ItemTotalsInput) -> ItemTotalsOutput:
    if not body.items:
        return ItemTotalsOutput(items=[], totals=[])
    filters: list[ColumnElement[bool]] = [ProviderConnection.owner_id == user.id]
    if body.connection_id is not None:
        filters.append(Receipt.connection_id == body.connection_id)
    if body.purchased_from is not None:
        filters.append(Receipt.purchased_at >= body.purchased_from)
    if body.purchased_to is not None:
        filters.append(Receipt.purchased_at < body.purchased_to)
    matching_receipts = (
        select(Receipt.id)
        .join(ProviderConnection)
        .join(ReceiptItem, ReceiptItem.receipt_id == Receipt.id)
        .where(
            *filters,
            tuple_(ReceiptItem.name, Receipt.currency).in_(
                [(item.name, item.currency) for item in body.items]
            ),
        )
    )
    amounts, _ = accounting(user.id, matching_receipts)
    filters.extend(
        [
            amounts.c.is_included,
            tuple_(ReceiptItem.name, Receipt.currency).in_(
                [(item.name, item.currency) for item in body.items]
            ),
        ]
    )
    rows = db.execute(
        _summaries(filters, user.id, amounts).order_by(TOTAL_AMOUNT.desc(), ReceiptItem.name.asc())
    ).all()
    totals: dict[str, Decimal] = {}
    for row in rows:
        totals[row[1]] = totals.get(row[1], Decimal(0)) + Decimal(str(row[11]))
    return ItemTotalsOutput(
        items=[_summary(*row) for row in rows],
        totals=[
            ItemTotalOutput(currency=currency, total_amount=_money(amount))
            for currency, amount in sorted(totals.items())
        ],
    )


def _exclusion_records(
    filters: list[ColumnElement[bool]],
    owner_id: UUID,
    scope: ExclusionScope,
    price_min: Decimal | None,
    price_max: Decimal | None,
) -> CTE:
    product_prices: list[ColumnElement[bool]] = []
    item_prices: list[ColumnElement[bool]] = []
    if price_min is not None:
        product_prices.append(price_min <= AVERAGE_PRICE)
        item_prices.append(price_min <= PRICE)
    if price_max is not None:
        product_prices.append(price_max >= AVERAGE_PRICE)
        item_prices.append(price_max >= PRICE)
    product_blockers = (
        select(
            ReceiptItem.name,
            Receipt.currency,
            func.max(case((Receipt.is_excluded, 1), else_=0)).label("receipt_blocked"),
            func.max(case((ReceiptItem.is_excluded, 1), else_=0)).label("item_blocked"),
        )
        .select_from(ReceiptItem)
        .join(Receipt, ReceiptItem.receipt_id == Receipt.id)
        .join(ProviderConnection, Receipt.connection_id == ProviderConnection.id)
        .where(
            ProviderConnection.owner_id == owner_id,
            product_excluded(owner_id),
            or_(Receipt.is_excluded.is_(True), ReceiptItem.is_excluded.is_(True)),
        )
        .group_by(ReceiptItem.name, Receipt.currency)
        .cte("product_exclusion_blockers")
    )
    products = (
        select(
            literal("product").label("kind"),
            ReceiptItem.name,
            Receipt.currency,
            sql_cast(null(), Uuid).label("receipt_id"),
            sql_cast(null(), Uuid).label("item_id"),
            LAST_PURCHASED.label("purchased_at"),
            sql_cast(null(), String).label("merchant"),
            TOTAL_AMOUNT.label("total_amount"),
            func.coalesce(func.max(product_blockers.c.receipt_blocked), 0).label("receipt_blocked"),
            func.coalesce(func.max(product_blockers.c.item_blocked), 0).label("item_blocked"),
            literal(False).label("product_blocked"),
        )
        .select_from(ReceiptItem)
        .join(Receipt, ReceiptItem.receipt_id == Receipt.id)
        .join(ProviderConnection, Receipt.connection_id == ProviderConnection.id)
        .outerjoin(
            product_blockers,
            and_(
                product_blockers.c.name == ReceiptItem.name,
                product_blockers.c.currency == Receipt.currency,
            ),
        )
        .where(*filters, product_excluded(owner_id), literal(scope != ExclusionScope.ITEM))
        .group_by(ReceiptItem.name, Receipt.currency)
        .having(*product_prices)
    )
    occurrences = (
        select(
            literal("item").label("kind"),
            ReceiptItem.name,
            Receipt.currency,
            Receipt.id.label("receipt_id"),
            ReceiptItem.id.label("item_id"),
            Receipt.purchased_at,
            Receipt.merchant,
            ReceiptItem.total_amount,
            case((Receipt.is_excluded, 1), else_=0).label("receipt_blocked"),
            literal(0).label("item_blocked"),
            product_excluded(owner_id).label("product_blocked"),
        )
        .select_from(ReceiptItem)
        .join(Receipt, ReceiptItem.receipt_id == Receipt.id)
        .join(ProviderConnection, Receipt.connection_id == ProviderConnection.id)
        .where(
            *filters,
            *item_prices,
            ReceiptItem.is_excluded.is_(True),
            literal(scope != ExclusionScope.PRODUCT),
        )
    )
    return products.union_all(occurrences).cte("item_exclusion_records")


@router.get("/exclusions", response_model=ExclusionsOutput)
def item_exclusions(
    user: Owner,
    db: Db,
    scope: ExclusionScope = ExclusionScope.ALL,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
    connection_id: UUID | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    purchased_from: datetime | None = None,
    purchased_to: datetime | None = None,
    price_min: Annotated[Decimal | None, Query(ge=0, max_digits=18, decimal_places=2)] = None,
    price_max: Annotated[Decimal | None, Query(ge=0, max_digits=18, decimal_places=2)] = None,
    sort: ExclusionSort = ExclusionSort.LAST_PURCHASED_DESC,
) -> ExclusionsOutput:
    filters: list[ColumnElement[bool]] = [ProviderConnection.owner_id == user.id]
    if connection_id is not None:
        filters.append(Receipt.connection_id == connection_id)
    if q and q.strip():
        filters.append(name_matches(ReceiptItem.name, q.strip()))
    if purchased_from is not None:
        filters.append(Receipt.purchased_at >= purchased_from)
    if purchased_to is not None:
        filters.append(Receipt.purchased_at < purchased_to)
    records = _exclusion_records(filters, user.id, scope, price_min, price_max)
    order: dict[ExclusionSort, UnaryExpression[Any]] = {
        ExclusionSort.NAME_ASC: records.c.name.asc(),
        ExclusionSort.NAME_DESC: records.c.name.desc(),
        ExclusionSort.TOTAL_AMOUNT_ASC: records.c.total_amount.asc(),
        ExclusionSort.TOTAL_AMOUNT_DESC: records.c.total_amount.desc(),
        ExclusionSort.LAST_PURCHASED_ASC: records.c.purchased_at.asc(),
        ExclusionSort.LAST_PURCHASED_DESC: records.c.purchased_at.desc(),
    }
    total = db.scalar(select(func.count()).select_from(records)) or 0
    rows = db.execute(
        select(records)
        .order_by(
            order.get(sort, records.c.purchased_at.desc()),
            records.c.name.asc(),
            records.c.currency.asc(),
            records.c.kind.asc(),
            records.c.receipt_id.asc(),
            records.c.item_id.asc(),
        )
        .limit(limit)
        .offset(offset)
    ).mappings()
    results: list[ExclusionOutput] = []
    for row in rows:
        blocked_by: list[ExclusionReason] = []
        if row.get("receipt_blocked"):
            blocked_by.append("receipt")
        if row.get("item_blocked"):
            blocked_by.append("item")
        if row.get("product_blocked"):
            blocked_by.append("product")
        results.append(
            ExclusionOutput(
                kind=row.get("kind"),
                name=row.get("name"),
                currency=row.get("currency"),
                receipt_id=row.get("receipt_id"),
                item_id=row.get("item_id"),
                purchased_at=_aware(row.get("purchased_at")),
                merchant=row.get("merchant"),
                total_amount=_money(row.get("total_amount")),
                blocked_by=blocked_by,
            )
        )
    return ExclusionsOutput(items=results, total=total, limit=limit, offset=offset)


@router.get("/detail", response_model=ItemDetailOutput)
def item_detail(
    user: Owner,
    db: Db,
    name: Annotated[str, Query(min_length=1, max_length=1000)],
    currency: Annotated[str, Query(pattern=r"^[A-Z]{3}$")],
) -> ItemDetailOutput:
    filters: list[ColumnElement[bool]] = [
        ProviderConnection.owner_id == user.id,
        ReceiptItem.name == name,
        Receipt.currency == currency,
    ]
    matching_receipts = (
        select(Receipt.id)
        .join(ProviderConnection)
        .join(ReceiptItem, ReceiptItem.receipt_id == Receipt.id)
        .where(*filters)
    )
    amounts, _ = accounting(user.id, matching_receipts)
    row = db.execute(_summaries(filters, user.id, amounts, included_metrics=True)).first()
    if row is None:
        raise HTTPException(404, "item_not_found")
    purchases = db.execute(
        select(
            Receipt.id,
            Receipt.purchased_at,
            Receipt.merchant,
            ReceiptItem.quantity,
            ReceiptItem.unit,
            PRICE,
            ReceiptItem.total_amount,
            ReceiptItem.id,
            ReceiptItem.is_excluded,
            Receipt.is_excluded,
            product_excluded(user.id),
            amounts.c.included_amount,
        )
        .select_from(ReceiptItem)
        .join(Receipt, ReceiptItem.receipt_id == Receipt.id)
        .join(ProviderConnection, Receipt.connection_id == ProviderConnection.id)
        .join(amounts, amounts.c.id == ReceiptItem.id)
        .where(*filters)
        .order_by(Receipt.purchased_at.desc(), Receipt.id.desc(), ReceiptItem.position)
        .limit(PURCHASE_LIMIT)
    )
    return ItemDetailOutput(
        **_summary(*row).model_dump(),
        purchases=[
            ItemPurchaseOutput(
                receipt_id=receipt_id,
                purchased_at=_aware(purchased_at),
                merchant=merchant,
                quantity=_quantity(quantity),
                unit=unit,
                unit_price=_money(price),
                total_amount=_money(amount),
                item_id=item_id,
                is_excluded=item_excluded,
                exclusion_reason="receipt"
                if receipt_excluded
                else "item"
                if item_excluded
                else "product"
                if global_excluded
                else None,
                included_amount=_money(included),
            )
            for (
                receipt_id,
                purchased_at,
                merchant,
                quantity,
                unit,
                price,
                amount,
                item_id,
                item_excluded,
                receipt_excluded,
                global_excluded,
                included,
            ) in purchases
        ],
    )


@router.patch("/exclusion", response_model=ItemDetailOutput)
def set_product_exclusion(user: Owner, db: Db, body: ProductExclusionInput) -> ItemDetailOutput:
    found = db.scalar(
        select(ReceiptItem.id)
        .join(Receipt)
        .join(ProviderConnection)
        .where(
            ProviderConnection.owner_id == user.id,
            ReceiptItem.name == body.name,
            Receipt.currency == body.currency,
        )
        .limit(1)
    )
    if found is None:
        raise HTTPException(404, "item_not_found")
    key = [
        ProductExclusion.owner_id == user.id,
        ProductExclusion.name == body.name,
        ProductExclusion.currency == body.currency,
    ]
    if body.excluded:
        insert = postgres_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
        db.execute(
            insert(ProductExclusion)
            .values(owner_id=user.id, name=body.name, currency=body.currency)
            .on_conflict_do_nothing(index_elements=["owner_id", "name", "currency"])
        )
    else:
        db.execute(delete(ProductExclusion).where(*key))
    db.commit()
    return item_detail(user, db, name=body.name, currency=body.currency)
