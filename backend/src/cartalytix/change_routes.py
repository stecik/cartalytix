"""Atomic batches of spending changes to receipts, their lines and products."""

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import ColumnElement, and_, delete, func, literal, or_, select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from cartalytix.accounting import accounting
from cartalytix.change_schemas import (
    Change,
    ChangeBatchDetailOutput,
    ChangeBatchInput,
    ChangeBatchOutput,
    ChangeEntryOutput,
    ChangeHistoryBatchOutput,
    ChangeHistoryOutput,
    ChangeKind,
    ChangeOutput,
    ChangeSource,
    ChangeValue,
    CurrencyImpactOutput,
    ItemExclusionChange,
    ItemImpactOutput,
    ItemIncludedQuantityChange,
    ProductExclusionChange,
    ReceiptExclusionChange,
    ReceiptImpactOutput,
    UndoInput,
)
from cartalytix.dependencies import Db, Owner, lock_owner
from cartalytix.models import (
    ChangeBatch,
    ChangeEntry,
    OAuthClient,
    ProductExclusion,
    ProviderConnection,
    Receipt,
    ReceiptItem,
    User,
)

router = APIRouter(prefix="/api/v1", tags=["changes"])


def included_quantity(item: ReceiptItem, value: Decimal | None) -> Decimal | None:
    """Validated counted quantity of a line; the whole quantity is stored as NULL."""
    if value is None or value == item.quantity:
        return None
    if item.quantity <= 0 or not 0 <= value <= item.quantity:
        raise HTTPException(422, "included_quantity_out_of_range")
    # Pieces split only into whole pieces; weighed or fractional lines take any part.
    if (
        not item.is_weight
        and item.quantity == item.quantity.to_integral_value()
        and value != value.to_integral_value()
    ):
        raise HTTPException(422, "included_quantity_not_whole")
    return value


def _error(status: int, code: str, index: int) -> HTTPException:
    return HTTPException(status, {"code": code, "operation": index})


def _quantity(value: Decimal | None) -> str | None:
    return None if value is None else format(value.normalize(), "f")


def _money(value: Decimal) -> str:
    return format(value, ".2f")


@dataclass
class _Target:
    index: int
    change: Change
    name: str
    currency: str
    before: ChangeValue
    after: ChangeValue
    receipt: Receipt | None = None
    item: ReceiptItem | None = None


def _resolve(db: Session, owner_id: UUID, changes: list[Change]) -> list[_Target]:
    """Lock and validate every target before anything is written."""
    seen: set[tuple[object, ...]] = set()
    for index, change in enumerate(changes):
        match change:
            case ReceiptExclusionChange():
                key: tuple[object, ...] = (change.kind, change.receipt_id)
            case ItemExclusionChange() | ItemIncludedQuantityChange():
                key = (change.kind, change.item_id)
            case ProductExclusionChange():
                key = (change.kind, change.name, change.currency)
        if key in seen:
            raise _error(422, "duplicate_change", index)
        seen.add(key)
    receipt_ids = {
        change.receipt_id for change in changes if not isinstance(change, ProductExclusionChange)
    }
    item_ids = {
        change.item_id
        for change in changes
        if isinstance(change, ItemExclusionChange | ItemIncludedQuantityChange)
    }
    products = {
        (change.name, change.currency)
        for change in changes
        if isinstance(change, ProductExclusionChange)
    }
    # Rows are locked in ID order so concurrent batches cannot deadlock.
    receipts = {
        receipt.id: receipt
        for receipt in db.scalars(
            select(Receipt)
            .join(ProviderConnection)
            .where(ProviderConnection.owner_id == owner_id, Receipt.id.in_(receipt_ids))
            .order_by(Receipt.id)
            .with_for_update(of=Receipt)
        )
    }
    items = {
        item.id: item
        for item in db.scalars(
            select(ReceiptItem)
            .where(ReceiptItem.id.in_(item_ids), ReceiptItem.receipt_id.in_(list(receipts)))
            .order_by(ReceiptItem.id)
            .with_for_update(of=ReceiptItem)
        )
    }
    purchased: set[tuple[str, str]] = set()
    excluded_products: set[tuple[str, str]] = set()
    if products:
        purchased = {
            (name, currency)
            for name, currency in db.execute(
                select(ReceiptItem.name, Receipt.currency)
                .join(Receipt, ReceiptItem.receipt_id == Receipt.id)
                .join(ProviderConnection, Receipt.connection_id == ProviderConnection.id)
                .where(
                    ProviderConnection.owner_id == owner_id,
                    tuple_(ReceiptItem.name, Receipt.currency).in_(products),
                )
                .distinct()
            )
        }
        excluded_products = {
            (name, currency)
            for name, currency in db.execute(
                select(ProductExclusion.name, ProductExclusion.currency).where(
                    ProductExclusion.owner_id == owner_id,
                    tuple_(ProductExclusion.name, ProductExclusion.currency).in_(products),
                )
            )
        }
    targets: list[_Target] = []
    for index, change in enumerate(changes):
        if isinstance(change, ProductExclusionChange):
            if (change.name, change.currency) not in purchased:
                raise _error(404, "item_not_found", index)
            targets.append(
                _Target(
                    index,
                    change,
                    change.name,
                    change.currency,
                    (change.name, change.currency) in excluded_products,
                    change.excluded,
                )
            )
            continue
        receipt = receipts.get(change.receipt_id)
        if receipt is None:
            raise _error(404, "receipt_not_found", index)
        if isinstance(change, ReceiptExclusionChange):
            targets.append(
                _Target(
                    index,
                    change,
                    receipt.merchant,
                    receipt.currency,
                    receipt.is_excluded,
                    change.excluded,
                    receipt,
                )
            )
            continue
        item = items.get(change.item_id)
        if item is None or item.receipt_id != receipt.id:
            raise _error(404, "receipt_item_not_found", index)
        if isinstance(change, ItemExclusionChange):
            before: ChangeValue = item.is_excluded
            after: ChangeValue = change.excluded
        else:
            try:
                counted = included_quantity(item, change.included_quantity)
            except HTTPException as exc:
                raise _error(exc.status_code, str(exc.detail), index) from exc
            before, after = _quantity(item.included_quantity), _quantity(counted)
        targets.append(
            _Target(index, change, item.name, receipt.currency, before, after, receipt, item)
        )
    return targets


def _apply(db: Session, owner_id: UUID, target: _Target) -> None:
    change = target.change
    match change:
        case ReceiptExclusionChange() if target.receipt is not None:
            target.receipt.is_excluded = change.excluded
        case ItemExclusionChange() if target.item is not None:
            target.item.is_excluded = change.excluded
        case ItemIncludedQuantityChange() if target.item is not None:
            target.item.included_quantity = (
                None if target.after is None else Decimal(str(target.after))
            )
        case ProductExclusionChange() if change.excluded:
            db.add(ProductExclusion(owner_id=owner_id, name=change.name, currency=change.currency))
        case ProductExclusionChange():
            db.execute(
                delete(ProductExclusion).where(
                    ProductExclusion.owner_id == owner_id,
                    ProductExclusion.name == change.name,
                    ProductExclusion.currency == change.currency,
                )
            )


Amounts = tuple[dict[UUID, tuple[Receipt, Decimal]], dict[UUID, tuple[ReceiptItem, str, Decimal]]]


def _amounts(db: Session, owner_id: UUID, targets: list[_Target]) -> Amounts:
    """Counted amounts of every receipt and line the targets can change."""
    receipt_ids = {target.receipt.id for target in targets if target.receipt is not None}
    products = [
        (target.name, target.currency)
        for target in targets
        if isinstance(target.change, ProductExclusionChange)
    ]
    conditions: list[ColumnElement[bool]] = [Receipt.id.in_(receipt_ids)]
    if products:
        conditions.append(tuple_(ReceiptItem.name, Receipt.currency).in_(products))
    affected = (
        select(Receipt.id)
        .join(ProviderConnection, Receipt.connection_id == ProviderConnection.id)
        .outerjoin(ReceiptItem, ReceiptItem.receipt_id == Receipt.id)
        .where(ProviderConnection.owner_id == owner_id, or_(literal(False), *conditions))
    )
    item_amounts, receipt_amounts = accounting(owner_id, affected)
    receipts = {
        receipt.id: (receipt, amount)
        for receipt, amount in db.execute(
            select(Receipt, receipt_amounts.c.included_amount).join(
                receipt_amounts, receipt_amounts.c.receipt_id == Receipt.id
            )
        )
    }
    items = {
        item.id: (item, currency, amount)
        for item, currency, amount in db.execute(
            select(ReceiptItem, Receipt.currency, item_amounts.c.included_amount)
            .join(Receipt, ReceiptItem.receipt_id == Receipt.id)
            .join(item_amounts, item_amounts.c.id == ReceiptItem.id)
        )
    }
    return receipts, items


def _state(targets: list[_Target]) -> str:
    current = [[target.index, target.before] for target in targets]
    return hashlib.sha256(json.dumps(current).encode()).hexdigest()


def _request_hash(body: ChangeBatchInput) -> str:
    operations = [change.model_dump(mode="json") for change in body.operations]
    return hashlib.sha256(json.dumps(operations, sort_keys=True).encode()).hexdigest()


def _output(
    targets: list[_Target], before: Amounts, after: Amounts, dry_run: bool
) -> ChangeBatchOutput:
    receipts: list[ReceiptImpactOutput] = []
    totals: dict[str, tuple[Decimal, Decimal]] = {}
    for receipt_id, (receipt, old) in before[0].items():
        new = after[0].get(receipt_id, (receipt, old))[1]
        currency_before, currency_after = totals.get(receipt.currency, (Decimal(0), Decimal(0)))
        totals[receipt.currency] = (currency_before + old, currency_after + new)
        if old != new:
            receipts.append(
                ReceiptImpactOutput(
                    receipt_id=receipt_id,
                    merchant=receipt.merchant,
                    purchased_at=receipt.purchased_at,
                    currency=receipt.currency,
                    before_amount=_money(old),
                    after_amount=_money(new),
                    difference=_money(new - old),
                )
            )
    items = [
        ItemImpactOutput(
            receipt_id=item.receipt_id,
            item_id=item_id,
            name=item.name,
            currency=currency,
            before_amount=_money(old),
            after_amount=_money(new),
            difference=_money(new - old),
        )
        for item_id, (item, currency, old) in before[1].items()
        if (new := after[1].get(item_id, (item, currency, old))[2]) != old
    ]
    receipts.sort(key=lambda row: (row.purchased_at, str(row.receipt_id)))
    items.sort(key=lambda row: (str(row.receipt_id), row.name))
    return ChangeBatchOutput(
        batch_id=None,
        dry_run=dry_run,
        state=_state(targets),
        changes=[
            ChangeOutput(
                index=target.index,
                kind=ChangeKind(target.change.kind),
                receipt_id=target.receipt.id if target.receipt is not None else None,
                item_id=target.item.id if target.item is not None else None,
                name=target.name,
                currency=target.currency,
                before=target.before,
                after=target.after,
                changed=target.before != target.after,
            )
            for target in targets
        ],
        receipts=receipts,
        items=items,
        totals=[
            CurrencyImpactOutput(
                currency=currency,
                before_amount=_money(old),
                after_amount=_money(new),
                difference=_money(new - old),
            )
            for currency, (old, new) in sorted(totals.items())
            if old != new
        ],
    )


def _replay(
    db: Session, owner_id: UUID, body: ChangeBatchInput, digest: str
) -> ChangeBatchOutput | None:
    stored = db.scalar(
        select(ChangeBatch).where(
            ChangeBatch.owner_id == owner_id, ChangeBatch.idempotency_key == body.idempotency_key
        )
    )
    if stored is None:
        return None
    if stored.request_hash != digest:
        raise HTTPException(409, "idempotency_key_reused")
    return ChangeBatchOutput.model_validate({**stored.result, "replayed": True})


@dataclass
class Actor:
    """Who a batch is recorded for: the owner in the app or one of their MCP clients."""

    user: User
    source: ChangeSource = ChangeSource.USER
    client_id: str | None = None


def _undo_conflicts(targets: list[_Target], undoing: list[ChangeEntry]) -> None:
    conflicts = [
        {
            "entry_id": str(entry.id),
            "name": entry.name,
            "expected": entry.after,
            "current": target.before,
        }
        for entry, target in zip(undoing, targets, strict=True)
        if target.before != entry.after
    ]
    if conflicts:
        # A later change touched these values; undo never overwrites it silently.
        raise HTTPException(409, {"code": "undo_conflict", "conflicts": conflicts})


def apply_changes(
    db: Session,
    actor: Actor,
    body: ChangeBatchInput,
    *,
    undo_of: ChangeBatch | None = None,
    undoing: list[ChangeEntry] | None = None,
) -> ChangeBatchOutput:
    """Validate, lock and apply a whole batch in one transaction, or nothing at all."""
    user = actor.user
    digest = _request_hash(body)
    keyed = body.idempotency_key is not None and not body.dry_run
    if keyed and (replayed := _replay(db, user.id, body, digest)) is not None:
        return replayed
    try:
        lock_owner(db, user.id)
        if keyed and (replayed := _replay(db, user.id, body, digest)) is not None:
            db.rollback()
            return replayed
        targets = _resolve(db, user.id, body.operations)
        if body.expected_state is not None and body.expected_state != _state(targets):
            raise HTTPException(409, "changes_conflict")
        if undoing is not None:
            _undo_conflicts(targets, undoing)
        before = _amounts(db, user.id, targets)
        for target in targets:
            if target.before != target.after:
                _apply(db, user.id, target)
        db.flush()
        output = _output(targets, before, _amounts(db, user.id, targets), body.dry_run)
        if body.dry_run or not any(change.changed for change in output.changes):
            db.rollback()
            return output
        batch = ChangeBatch(
            owner_id=user.id,
            idempotency_key=body.idempotency_key,
            request_hash=digest,
            result={},
            source=actor.source,
            client_id=actor.client_id,
            undo_of_id=undo_of.id if undo_of is not None else None,
        )
        db.add(batch)
        db.flush()
        db.add_all(
            ChangeEntry(
                batch_id=batch.id,
                position=target.index,
                kind=target.change.kind,
                receipt_id=target.receipt.id if target.receipt is not None else None,
                item_id=target.item.id if target.item is not None else None,
                name=target.name,
                currency=target.currency,
                before=target.before,
                after=target.after,
            )
            for target in targets
            if target.before != target.after
        )
        for entry in undoing or []:
            entry.undone_by_id = batch.id
        output.batch_id = batch.id
        batch.result = output.model_dump(mode="json")
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        # A concurrent request with the same key committed first.
        if keyed and (replayed := _replay(db, user.id, body, digest)) is not None:
            return replayed
        raise HTTPException(409, "changes_conflict") from exc
    except BaseException:
        db.rollback()
        raise
    return output


def apply_change(db: Session, actor: Actor, change: Change) -> None:
    """One change through the batch path, reporting errors with their plain code."""
    try:
        apply_changes(db, actor, ChangeBatchInput(operations=[change]))
    except HTTPException as exc:
        detail = exc.detail
        code = detail.get("code") if isinstance(detail, dict) else detail
        raise HTTPException(exc.status_code, code) from exc


def _reverse(entry: ChangeEntry) -> Change:
    """The change that restores an entry's previous value."""
    match entry.kind:
        case ChangeKind.RECEIPT_EXCLUSION if entry.receipt_id is not None:
            return ReceiptExclusionChange(
                kind=ChangeKind.RECEIPT_EXCLUSION,
                receipt_id=entry.receipt_id,
                excluded=bool(entry.before),
            )
        case ChangeKind.ITEM_EXCLUSION if entry.receipt_id and entry.item_id:
            return ItemExclusionChange(
                kind=ChangeKind.ITEM_EXCLUSION,
                receipt_id=entry.receipt_id,
                item_id=entry.item_id,
                excluded=bool(entry.before),
            )
        case ChangeKind.ITEM_INCLUDED_QUANTITY if entry.receipt_id and entry.item_id:
            return ItemIncludedQuantityChange(
                kind=ChangeKind.ITEM_INCLUDED_QUANTITY,
                receipt_id=entry.receipt_id,
                item_id=entry.item_id,
                included_quantity=None if entry.before is None else Decimal(str(entry.before)),
            )
        case ChangeKind.PRODUCT_EXCLUSION:
            return ProductExclusionChange(
                kind=ChangeKind.PRODUCT_EXCLUSION,
                name=entry.name,
                currency=entry.currency,
                excluded=bool(entry.before),
            )
    # The receipt was deleted since; there is nothing left to restore.
    raise HTTPException(409, {"code": "undo_target_missing", "entry_id": str(entry.id)})


def undo_changes(db: Session, actor: Actor, batch_id: UUID, body: UndoInput) -> ChangeBatchOutput:
    """Restore previous values of a batch, or of some of its entries, as a new batch."""
    try:
        lock_owner(db, actor.user.id)
        batch = db.scalar(
            select(ChangeBatch).where(
                ChangeBatch.id == batch_id, ChangeBatch.owner_id == actor.user.id
            )
        )
        if batch is None:
            raise HTTPException(404, "change_batch_not_found")
        query = select(ChangeEntry).where(ChangeEntry.batch_id == batch.id)
        if body.entry_ids is not None:
            query = query.where(ChangeEntry.id.in_(body.entry_ids))
        entries = list(db.scalars(query.order_by(ChangeEntry.position)))
        if body.entry_ids is not None and len(entries) != len(set(body.entry_ids)):
            raise HTTPException(404, "change_entry_not_found")
        if not entries:
            raise HTTPException(409, "already_undone")
        restore = ChangeBatchInput(
            operations=[_reverse(entry) for entry in entries],
            dry_run=body.dry_run,
            idempotency_key=body.idempotency_key,
        )
        # A retried undo whose response got lost replays its result instead of being refused.
        if body.idempotency_key is not None and not body.dry_run:
            replayed = _replay(db, actor.user.id, restore, _request_hash(restore))
            if replayed is not None:
                db.rollback()
                return replayed
        if any(entry.undone_by_id is not None for entry in entries):
            raise HTTPException(409, "already_undone")
    except BaseException:
        db.rollback()
        raise
    return apply_changes(db, actor, restore, undo_of=batch, undoing=entries)


def _history_entries(db: Session, batch_ids: list[UUID]) -> dict[UUID, list[ChangeEntry]]:
    entries: dict[UUID, list[ChangeEntry]] = {batch_id: [] for batch_id in batch_ids}
    for entry in db.scalars(
        select(ChangeEntry)
        .where(ChangeEntry.batch_id.in_(list(entries)))
        .order_by(ChangeEntry.batch_id, ChangeEntry.position)
    ):
        entries[entry.batch_id].append(entry)
    return entries


def _history_output(
    batch: ChangeBatch, client_name: str | None, entries: list[ChangeEntry]
) -> ChangeHistoryBatchOutput:
    return ChangeHistoryBatchOutput(
        id=batch.id,
        created_at=batch.created_at,
        source=ChangeSource(batch.source),
        client_name=client_name,
        undo_of_id=batch.undo_of_id,
        entries=[
            ChangeEntryOutput(
                id=entry.id,
                kind=ChangeKind(entry.kind),
                receipt_id=entry.receipt_id,
                item_id=entry.item_id,
                name=entry.name,
                currency=entry.currency,
                before=entry.before,
                after=entry.after,
                undone_by_id=entry.undone_by_id,
            )
            for entry in entries
        ],
        totals=batch.result.get("totals", []),
    )


def change_history(
    db: Session, user: User, receipt_id: UUID | None, limit: int, offset: int
) -> ChangeHistoryOutput:
    filters: list[ColumnElement[bool]] = [ChangeBatch.owner_id == user.id]
    if receipt_id is not None:
        # Product exclusions count on the receipt when it contains that product.
        products = (
            select(ReceiptItem.name, Receipt.currency)
            .join(Receipt, ReceiptItem.receipt_id == Receipt.id)
            .where(Receipt.id == receipt_id)
        )
        filters.append(
            ChangeBatch.id.in_(
                select(ChangeEntry.batch_id).where(
                    or_(
                        ChangeEntry.receipt_id == receipt_id,
                        and_(
                            ChangeEntry.kind == ChangeKind.PRODUCT_EXCLUSION,
                            tuple_(ChangeEntry.name, ChangeEntry.currency).in_(products),
                        ),
                    )
                )
            )
        )
    total = db.scalar(select(func.count()).select_from(ChangeBatch).where(*filters)) or 0
    batches = [
        (batch, client_name)
        for batch, client_name in db.execute(
            select(ChangeBatch, OAuthClient.name)
            .outerjoin(OAuthClient, OAuthClient.id == ChangeBatch.client_id)
            .where(*filters)
            .order_by(ChangeBatch.created_at.desc(), ChangeBatch.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ]
    entries = _history_entries(db, [batch.id for batch, _ in batches])
    return ChangeHistoryOutput(
        items=[
            _history_output(batch, client_name, entries.get(batch.id, []))
            for batch, client_name in batches
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


def change_detail(db: Session, user: User, batch_id: UUID) -> ChangeBatchDetailOutput:
    row = db.execute(
        select(ChangeBatch, OAuthClient.name)
        .outerjoin(OAuthClient, OAuthClient.id == ChangeBatch.client_id)
        .where(ChangeBatch.id == batch_id, ChangeBatch.owner_id == user.id)
    ).first()
    if row is None:
        raise HTTPException(404, "change_batch_not_found")
    batch, client_name = row
    entries = _history_entries(db, [batch.id]).get(batch.id, [])
    return ChangeBatchDetailOutput(
        **_history_output(batch, client_name, entries).model_dump(),
        receipts=batch.result.get("receipts", []),
        items=batch.result.get("items", []),
    )


@router.post("/changes", response_model=ChangeBatchOutput)
def change_batch(user: Owner, db: Db, body: ChangeBatchInput) -> ChangeBatchOutput:
    return apply_changes(db, Actor(user), body)


@router.get("/changes", response_model=ChangeHistoryOutput)
def changes(
    user: Owner,
    db: Db,
    receipt_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
) -> ChangeHistoryOutput:
    return change_history(db, user, receipt_id, limit, offset)


@router.get("/changes/{batch_id}", response_model=ChangeBatchDetailOutput)
def change(batch_id: UUID, user: Owner, db: Db) -> ChangeBatchDetailOutput:
    return change_detail(db, user, batch_id)


@router.post("/changes/{batch_id}/undo", response_model=ChangeBatchOutput)
def undo(batch_id: UUID, user: Owner, db: Db, body: UndoInput) -> ChangeBatchOutput:
    return undo_changes(db, Actor(user), batch_id, body)
