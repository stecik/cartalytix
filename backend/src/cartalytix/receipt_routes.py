"""Owner-scoped receipts and the overview summary."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import ColumnElement, exists, func, or_, select

from cartalytix.accounting import accounting, product_excluded
from cartalytix.dependencies import Db, Owner
from cartalytix.models import Discount, ProviderConnection, Receipt, ReceiptItem
from cartalytix.schemas import (
    CurrencyTotal,
    DiscountOutput,
    ExclusionFilter,
    ExclusionInput,
    OverviewOutput,
    ReceiptDetailOutput,
    ReceiptItemOutput,
    ReceiptOutput,
    ReceiptSort,
    ReceiptsOutput,
)
from cartalytix.search import matches, name_matches

RECEIPT_ORDER = {
    ReceiptSort.PURCHASED_AT_DESC: (Receipt.purchased_at.desc(), Receipt.id.desc()),
    ReceiptSort.PURCHASED_AT_ASC: (Receipt.purchased_at.asc(), Receipt.id.asc()),
    ReceiptSort.TOTAL_AMOUNT_DESC: (Receipt.total_amount.desc(), Receipt.id.desc()),
    ReceiptSort.TOTAL_AMOUNT_ASC: (Receipt.total_amount.asc(), Receipt.id.asc()),
}

# Manually added receipts share one hidden connection per owner and show their shop's provider.
MANUAL_PROVIDER = "manual"
MANUAL_SOURCE = "manual"

router = APIRouter(prefix="/api/v1", tags=["receipts"])


def receipt_output(receipt: Receipt, provider: str, included_amount: Decimal) -> ReceiptOutput:
    return ReceiptOutput(
        id=receipt.id,
        connection_id=receipt.connection_id,
        provider=receipt.shop_provider or provider,
        external_id=receipt.external_id,
        purchased_at=receipt.purchased_at,
        merchant=receipt.merchant,
        total_amount=format(receipt.total_amount, ".2f"),
        currency=receipt.currency,
        is_excluded=receipt.is_excluded,
        included_amount=format(included_amount, ".2f"),
        is_manual=receipt.source_format == MANUAL_SOURCE,
    )


@router.get("/receipts", response_model=ReceiptsOutput)
def receipts(
    user: Owner,
    db: Db,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
    connection_id: UUID | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    purchased_from: datetime | None = None,
    purchased_to: datetime | None = None,
    amount_min: Annotated[Decimal | None, Query(ge=0, max_digits=18, decimal_places=2)] = None,
    amount_max: Annotated[Decimal | None, Query(ge=0, max_digits=18, decimal_places=2)] = None,
    sort: ReceiptSort = ReceiptSort.PURCHASED_AT_DESC,
    exclusion: ExclusionFilter = ExclusionFilter.INCLUDED,
) -> ReceiptsOutput:
    filters: list[ColumnElement[bool]] = [ProviderConnection.owner_id == user.id]
    if exclusion != ExclusionFilter.ALL:
        filters.append(Receipt.is_excluded.is_(exclusion == ExclusionFilter.EXCLUDED))
    if connection_id is not None:
        filters.append(Receipt.connection_id == connection_id)
    if q and q.strip():
        term = q.strip()
        filters.append(
            or_(
                matches(Receipt.merchant, term),
                exists().where(
                    ReceiptItem.receipt_id == Receipt.id,
                    name_matches(ReceiptItem.name, term),
                ),
            )
        )
    if purchased_from is not None:
        filters.append(Receipt.purchased_at >= purchased_from)
    if purchased_to is not None:
        filters.append(Receipt.purchased_at < purchased_to)
    if amount_min is not None:
        filters.append(Receipt.total_amount >= amount_min)
    if amount_max is not None:
        filters.append(Receipt.total_amount <= amount_max)
    total = (
        db.scalar(
            select(func.count())
            .select_from(Receipt)
            .join(ProviderConnection)
            .where(
                *filters,
            )
        )
        or 0
    )
    order = RECEIPT_ORDER.get(sort, RECEIPT_ORDER[ReceiptSort.PURCHASED_AT_DESC])
    page_receipts = (
        select(Receipt.id)
        .join(ProviderConnection)
        .where(*filters)
        .order_by(*order)
        .limit(limit)
        .offset(offset)
    )
    _, receipt_amounts = accounting(user.id, page_receipts)
    rows = db.execute(
        select(Receipt, ProviderConnection.provider, receipt_amounts.c.included_amount)
        .join(ProviderConnection)
        .join(receipt_amounts, receipt_amounts.c.receipt_id == Receipt.id)
        .where(
            *filters,
        )
        .order_by(*order)
    )
    return ReceiptsOutput(
        items=[receipt_output(receipt, provider, amount) for receipt, provider, amount in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/receipts/{receipt_id}", response_model=ReceiptDetailOutput)
def receipt_detail(receipt_id: UUID, user: Owner, db: Db) -> ReceiptDetailOutput:
    item_amounts, receipt_amounts = accounting(user.id, [receipt_id])
    row = db.execute(
        select(Receipt, ProviderConnection.provider, receipt_amounts.c.included_amount)
        .join(ProviderConnection)
        .join(receipt_amounts, receipt_amounts.c.receipt_id == Receipt.id)
        .where(
            Receipt.id == receipt_id,
            ProviderConnection.owner_id == user.id,
        )
    ).first()
    if row is None:
        raise HTTPException(404, "receipt_not_found")
    receipt, provider, amount = row
    items = db.execute(
        select(ReceiptItem, item_amounts.c.included_amount, product_excluded(user.id))
        .join(Receipt, Receipt.id == ReceiptItem.receipt_id)
        .join(item_amounts, item_amounts.c.id == ReceiptItem.id)
        .where(ReceiptItem.receipt_id == receipt.id)
        .order_by(ReceiptItem.position)
    )
    discounts = db.scalars(
        select(Discount).where(Discount.receipt_id == receipt.id).order_by(Discount.id)
    )
    return ReceiptDetailOutput(
        **receipt_output(receipt, provider, amount).model_dump(),
        rounding_amount=receipt.rounding_amount,
        quality_issues=receipt.quality_issues,
        source_format=receipt.source_format,
        items=[
            ReceiptItemOutput.model_validate(item).model_copy(
                update={
                    "included_amount": format(included, ".2f"),
                    "exclusion_reason": "receipt"
                    if receipt.is_excluded
                    else "item"
                    if item.is_excluded
                    else "product"
                    if global_excluded
                    else None,
                }
            )
            for item, included, global_excluded in items
        ],
        discounts=[DiscountOutput.model_validate(discount) for discount in discounts],
    )


@router.patch("/receipts/{receipt_id}/exclusion", response_model=ReceiptDetailOutput)
def set_receipt_exclusion(
    receipt_id: UUID, body: ExclusionInput, user: Owner, db: Db
) -> ReceiptDetailOutput:
    receipt = db.scalar(
        select(Receipt)
        .join(ProviderConnection)
        .where(Receipt.id == receipt_id, ProviderConnection.owner_id == user.id)
        .with_for_update(of=Receipt)
    )
    if receipt is None:
        raise HTTPException(404, "receipt_not_found")
    receipt.is_excluded = body.excluded
    db.commit()
    return receipt_detail(receipt_id, user, db)


@router.patch(
    "/receipts/{receipt_id}/items/{item_id}/exclusion", response_model=ReceiptDetailOutput
)
def set_receipt_item_exclusion(
    receipt_id: UUID, item_id: UUID, body: ExclusionInput, user: Owner, db: Db
) -> ReceiptDetailOutput:
    receipt = db.scalar(
        select(Receipt)
        .join(ProviderConnection)
        .where(
            Receipt.id == receipt_id,
            ProviderConnection.owner_id == user.id,
        )
        .with_for_update(of=Receipt)
    )
    if receipt is None:
        raise HTTPException(404, "receipt_not_found")
    item = db.scalar(
        select(ReceiptItem)
        .where(
            ReceiptItem.receipt_id == receipt_id,
            ReceiptItem.id == item_id,
        )
        .with_for_update(of=ReceiptItem)
    )
    if item is None:
        raise HTTPException(404, "receipt_item_not_found")
    item.is_excluded = body.excluded
    db.commit()
    return receipt_detail(receipt_id, user, db)


@router.get("/overview", response_model=OverviewOutput)
def overview(user: Owner, db: Db) -> OverviewOutput:
    owner_filter = ProviderConnection.owner_id == user.id
    _, receipt_amounts = accounting(user.id)
    receipt_count = (
        db.scalar(
            select(func.count())
            .select_from(Receipt)
            .join(
                ProviderConnection,
            )
            .join(receipt_amounts, receipt_amounts.c.receipt_id == Receipt.id)
            .where(owner_filter, receipt_amounts.c.is_included)
        )
        or 0
    )
    connection_count = (
        db.scalar(
            select(func.count())
            .select_from(ProviderConnection)
            .where(
                owner_filter,
                ProviderConnection.provider != MANUAL_PROVIDER,
            )
        )
        or 0
    )
    totals = db.execute(
        select(Receipt.currency, func.sum(receipt_amounts.c.included_amount))
        .join(
            ProviderConnection,
        )
        .join(receipt_amounts, receipt_amounts.c.receipt_id == Receipt.id)
        .where(owner_filter, receipt_amounts.c.is_included)
        .group_by(Receipt.currency)
        .order_by(Receipt.currency)
    )
    latest = db.execute(
        select(Receipt, ProviderConnection.provider, receipt_amounts.c.included_amount)
        .join(ProviderConnection)
        .join(receipt_amounts, receipt_amounts.c.receipt_id == Receipt.id)
        .where(
            owner_filter,
            receipt_amounts.c.is_included,
        )
        .order_by(Receipt.purchased_at.desc(), Receipt.id.desc())
        .limit(5)
    )
    return OverviewOutput(
        receipt_count=receipt_count,
        connection_count=connection_count,
        totals=[
            CurrencyTotal(currency=currency, amount=format(amount, ".2f"))
            for currency, amount in totals
        ],
        latest_receipts=[
            receipt_output(receipt, provider, amount) for receipt, provider, amount in latest
        ],
    )
