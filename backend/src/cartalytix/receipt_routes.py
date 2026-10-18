"""Owner-scoped receipts and the overview summary."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import ColumnElement, exists, func, or_, select

from cartalytix.dependencies import Db, Owner
from cartalytix.models import Discount, ProviderConnection, Receipt, ReceiptItem
from cartalytix.schemas import (
    CurrencyTotal,
    DiscountOutput,
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

router = APIRouter(prefix="/api/v1", tags=["receipts"])


def receipt_output(receipt: Receipt, provider: str) -> ReceiptOutput:
    return ReceiptOutput(
        id=receipt.id,
        connection_id=receipt.connection_id,
        provider=provider,
        external_id=receipt.external_id,
        purchased_at=receipt.purchased_at,
        merchant=receipt.merchant,
        total_amount=format(receipt.total_amount, ".2f"),
        currency=receipt.currency,
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
) -> ReceiptsOutput:
    filters: list[ColumnElement[bool]] = [ProviderConnection.owner_id == user.id]
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
    rows = db.execute(
        select(Receipt, ProviderConnection.provider)
        .join(ProviderConnection)
        .where(
            *filters,
        )
        .order_by(*RECEIPT_ORDER.get(sort, RECEIPT_ORDER[ReceiptSort.PURCHASED_AT_DESC]))
        .limit(limit)
        .offset(offset)
    )
    return ReceiptsOutput(
        items=[receipt_output(receipt, provider) for receipt, provider in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/receipts/{receipt_id}", response_model=ReceiptDetailOutput)
def receipt_detail(receipt_id: UUID, user: Owner, db: Db) -> ReceiptDetailOutput:
    row = db.execute(
        select(Receipt, ProviderConnection.provider)
        .join(ProviderConnection)
        .where(
            Receipt.id == receipt_id,
            ProviderConnection.owner_id == user.id,
        )
    ).first()
    if row is None:
        raise HTTPException(404, "receipt_not_found")
    receipt, provider = row
    items = db.scalars(
        select(ReceiptItem)
        .where(ReceiptItem.receipt_id == receipt.id)
        .order_by(ReceiptItem.position)
    )
    discounts = db.scalars(
        select(Discount).where(Discount.receipt_id == receipt.id).order_by(Discount.id)
    )
    return ReceiptDetailOutput(
        **receipt_output(receipt, provider).model_dump(),
        rounding_amount=receipt.rounding_amount,
        quality_issues=receipt.quality_issues,
        source_format=receipt.source_format,
        items=[ReceiptItemOutput.model_validate(item) for item in items],
        discounts=[DiscountOutput.model_validate(discount) for discount in discounts],
    )


@router.get("/overview", response_model=OverviewOutput)
def overview(user: Owner, db: Db) -> OverviewOutput:
    owner_filter = ProviderConnection.owner_id == user.id
    receipt_count = (
        db.scalar(
            select(func.count())
            .select_from(Receipt)
            .join(
                ProviderConnection,
            )
            .where(owner_filter)
        )
        or 0
    )
    connection_count = (
        db.scalar(
            select(func.count())
            .select_from(ProviderConnection)
            .where(
                owner_filter,
            )
        )
        or 0
    )
    totals = db.execute(
        select(Receipt.currency, func.sum(Receipt.total_amount))
        .join(
            ProviderConnection,
        )
        .where(owner_filter)
        .group_by(Receipt.currency)
        .order_by(Receipt.currency)
    )
    latest = db.execute(
        select(Receipt, ProviderConnection.provider)
        .join(ProviderConnection)
        .where(
            owner_filter,
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
        latest_receipts=[receipt_output(receipt, provider) for receipt, provider in latest],
    )
