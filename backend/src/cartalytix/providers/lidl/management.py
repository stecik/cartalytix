"""Scoped lifecycle mutations, called under the connection advisory and row locks."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from cartalytix.models import Discount, RawDocument, Receipt, ReceiptItem, SyncRun


def stop_pending_runs(db: Session, connection_id: UUID, reason: str) -> None:
    now = datetime.now(UTC)
    db.execute(
        update(SyncRun)
        .where(SyncRun.connection_id == connection_id, SyncRun.status.in_(("pending", "running")))
        .values(status="failed", error_code=reason, finished_at=now, heartbeat_at=now)
    )


def clear_purchases(db: Session, connection_id: UUID) -> None:
    receipts = select(Receipt.id).where(Receipt.connection_id == connection_id)
    db.execute(delete(Discount).where(Discount.receipt_id.in_(receipts)))
    db.execute(delete(ReceiptItem).where(ReceiptItem.receipt_id.in_(receipts)))
    db.execute(delete(Receipt).where(Receipt.connection_id == connection_id))
    db.execute(delete(RawDocument).where(RawDocument.connection_id == connection_id))
