"""Owner-scoped product ledger: receipt items grouped by name and currency."""

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import ColumnElement, Select, UnaryExpression, func, select

from cartalytix.dependencies import Db, Owner
from cartalytix.models import ProviderConnection, Receipt, ReceiptItem
from cartalytix.schemas import (
    ItemDetailOutput,
    ItemPurchaseOutput,
    ItemSort,
    ItemsOutput,
    ItemSummaryOutput,
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
) -> Select[str, str, str | None, int, Decimal, Decimal, Decimal, Decimal, Decimal, datetime]:
    return (
        select(
            ReceiptItem.name,
            Receipt.currency,
            func.max(ReceiptItem.unit),
            RECEIPT_COUNT,
            func.sum(ReceiptItem.quantity),
            TOTAL_AMOUNT,
            AVERAGE_PRICE,
            func.min(PRICE),
            func.max(PRICE),
            LAST_PURCHASED,
        )
        .select_from(ReceiptItem)
        .join(Receipt, ReceiptItem.receipt_id == Receipt.id)
        .join(ProviderConnection, Receipt.connection_id == ProviderConnection.id)
        .where(*filters)
        .group_by(ReceiptItem.name, Receipt.currency)
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
) -> ItemsOutput:
    filters: list[ColumnElement[bool]] = [ProviderConnection.owner_id == user.id]
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
    rows = db.execute(
        _summaries(filters)
        .having(*having)
        .order_by(
            ITEM_ORDER.get(sort, ITEM_ORDER[ItemSort.NAME_ASC]),
            ReceiptItem.name.asc(),
            Receipt.currency.asc(),
        )
        .limit(limit)
        .offset(offset)
    )
    return ItemsOutput(
        items=[_summary(*row) for row in rows], total=total, limit=limit, offset=offset
    )


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
    row = db.execute(_summaries(filters)).first()
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
        )
        .select_from(ReceiptItem)
        .join(Receipt, ReceiptItem.receipt_id == Receipt.id)
        .join(ProviderConnection, Receipt.connection_id == ProviderConnection.id)
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
            )
            for receipt_id, purchased_at, merchant, quantity, unit, price, amount in purchases
        ],
    )
