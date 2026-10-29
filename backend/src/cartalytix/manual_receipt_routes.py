"""Manually added receipts, kept under one hidden connection per owner."""

from decimal import Decimal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from sqlalchemy import delete, func, select

from cartalytix.accounting import accounting
from cartalytix.dependencies import Db, Owner
from cartalytix.models import Discount, ProviderConnection, Receipt, ReceiptItem, User
from cartalytix.receipt_routes import MANUAL_PROVIDER, MANUAL_SOURCE, receipt_output
from cartalytix.schemas import (
    ManualReceiptInput,
    ManualReceiptsInput,
    ManualReceiptsOutput,
    ShopOutput,
    ShopsOutput,
)

SHOP_LIMIT = 200
UNIT_PRICE = Decimal("0.000001")

router = APIRouter(prefix="/api/v1", tags=["receipts"])


def _manual_connection(db: Db, user: User) -> ProviderConnection:
    connection = db.scalar(
        select(ProviderConnection)
        .where(
            ProviderConnection.owner_id == user.id,
            ProviderConnection.provider == MANUAL_PROVIDER,
        )
        .limit(1)
    )
    if connection is None:
        connection = ProviderConnection(
            owner_id=user.id, provider=MANUAL_PROVIDER, name="Manual", status="manual"
        )
        db.add(connection)
        db.flush()
    return connection


def _fill(receipt: Receipt, entry: ManualReceiptInput) -> None:
    purchased_at = entry.purchased_at
    if purchased_at.tzinfo is None:
        purchased_at = purchased_at.replace(tzinfo=ZoneInfo("Europe/Prague"))
    total = entry.total_amount
    if total is None:
        total = sum((item.total_amount for item in entry.items), Decimal(0))
    receipt.purchased_at = purchased_at
    receipt.merchant = entry.merchant
    receipt.shop_provider = entry.provider
    receipt.currency = entry.currency
    receipt.total_amount = total


def _items(
    receipt_id: UUID, entry: ManualReceiptInput, excluded: set[str] | None = None
) -> list[ReceiptItem]:
    return [
        ReceiptItem(
            receipt_id=receipt_id,
            position=position,
            name=item.name,
            quantity=item.quantity,
            unit=item.unit,
            unit_price=(item.total_amount / item.quantity).quantize(UNIT_PRICE),
            total_amount=item.total_amount,
            original_amount=item.total_amount,
            is_excluded=item.name in (excluded or set()),
        )
        for position, item in enumerate(entry.items)
    ]


def _owned_manual_receipt(db: Db, user: User, receipt_id: UUID) -> Receipt:
    receipt = db.scalar(
        select(Receipt)
        .join(ProviderConnection)
        .where(Receipt.id == receipt_id, ProviderConnection.owner_id == user.id)
        .with_for_update(of=Receipt)
    )
    if receipt is None:
        raise HTTPException(404, "receipt_not_found")
    if receipt.source_format != MANUAL_SOURCE:
        raise HTTPException(409, "receipt_not_manual")
    return receipt


@router.post("/receipts/manual", response_model=ManualReceiptsOutput, status_code=201)
def create_manual_receipts(user: Owner, db: Db, body: ManualReceiptsInput) -> ManualReceiptsOutput:
    connection = _manual_connection(db, user)
    ids: list[UUID] = []
    for entry in body.receipts:
        receipt = Receipt(
            connection_id=connection.id, external_id=str(uuid4()), source_format=MANUAL_SOURCE
        )
        _fill(receipt, entry)
        db.add(receipt)
        db.flush()
        db.add_all(_items(receipt.id, entry))
        ids.append(receipt.id)
    db.commit()
    _, receipt_amounts = accounting(user.id, ids)
    rows = db.execute(
        select(Receipt, ProviderConnection.provider, receipt_amounts.c.included_amount)
        .join(ProviderConnection)
        .join(receipt_amounts, receipt_amounts.c.receipt_id == Receipt.id)
        .order_by(Receipt.purchased_at.desc(), Receipt.id.desc())
    )
    return ManualReceiptsOutput(
        items=[receipt_output(receipt, provider, amount) for receipt, provider, amount in rows]
    )


@router.put("/receipts/{receipt_id}", status_code=204)
def update_manual_receipt(receipt_id: UUID, user: Owner, db: Db, body: ManualReceiptInput) -> None:
    receipt = _owned_manual_receipt(db, user, receipt_id)
    # Item rows are replaced; exclusions follow the product name, categories already do.
    excluded = set(
        db.scalars(
            select(ReceiptItem.name).where(
                ReceiptItem.receipt_id == receipt_id, ReceiptItem.is_excluded
            )
        )
    )
    db.execute(delete(Discount).where(Discount.receipt_id == receipt_id))
    db.execute(delete(ReceiptItem).where(ReceiptItem.receipt_id == receipt_id))
    _fill(receipt, body)
    db.add_all(_items(receipt_id, body, excluded))
    db.commit()


@router.delete("/receipts/{receipt_id}", status_code=204)
def delete_manual_receipt(receipt_id: UUID, user: Owner, db: Db) -> None:
    _owned_manual_receipt(db, user, receipt_id)
    db.execute(delete(Discount).where(Discount.receipt_id == receipt_id))
    db.execute(delete(ReceiptItem).where(ReceiptItem.receipt_id == receipt_id))
    db.execute(delete(Receipt).where(Receipt.id == receipt_id))
    db.commit()


@router.get("/shops", response_model=ShopsOutput)
def shops(user: Owner, db: Db) -> ShopsOutput:
    provider = func.coalesce(Receipt.shop_provider, ProviderConnection.provider)
    count = func.count()
    rows = db.execute(
        select(Receipt.merchant, provider, count)
        .join(ProviderConnection)
        .where(ProviderConnection.owner_id == user.id)
        .group_by(Receipt.merchant, provider)
        .order_by(count.desc(), Receipt.merchant)
        .limit(SHOP_LIMIT)
    )
    return ShopsOutput(
        items=[
            ShopOutput(
                merchant=merchant,
                provider=None if code == MANUAL_PROVIDER else code,
                receipt_count=receipt_count,
            )
            for merchant, code, receipt_count in rows
        ]
    )
