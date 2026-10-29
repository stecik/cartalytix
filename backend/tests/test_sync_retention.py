from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session

from cartalytix.models import Base, ProviderConnection, SyncRun, User
from cartalytix.settings import Settings
from cartalytix.sync import prune_sync_runs


@pytest.fixture
def engine() -> Iterator[Engine]:
    database = create_engine("sqlite://")
    Base.metadata.create_all(database)
    yield database
    database.dispose()


def test_prune_keeps_imports_recent_active_and_latest_runs(engine: Engine) -> None:
    now = datetime.now(UTC)
    with Session(engine) as db:
        owner = User(email="owner@example.com", password_hash="unused")
        db.add(owner)
        db.flush()
        busy = ProviderConnection(owner_id=owner.id, provider="lidl", name="Busy")
        idle = ProviderConnection(owner_id=owner.id, provider="lidl", name="Idle")
        db.add_all([busy, idle])
        db.flush()
        runs = {
            "old_empty": SyncRun(
                connection_id=busy.id, status="completed", created_at=now - timedelta(days=20)
            ),
            "old_import": SyncRun(
                connection_id=busy.id,
                status="completed",
                receipts_imported=3,
                created_at=now - timedelta(days=400),
            ),
            "recent_empty": SyncRun(
                connection_id=busy.id, status="completed", created_at=now - timedelta(days=3)
            ),
            "old_failed": SyncRun(
                connection_id=busy.id, status="failed", created_at=now - timedelta(days=100)
            ),
            "failed_with_import": SyncRun(
                connection_id=busy.id,
                status="failed",
                receipts_imported=1,
                created_at=now - timedelta(days=100),
            ),
            "recent_failed": SyncRun(
                connection_id=busy.id, status="failed", created_at=now - timedelta(days=30)
            ),
            "stuck_pending": SyncRun(
                connection_id=busy.id, status="pending", created_at=now - timedelta(days=50)
            ),
            "idle_older": SyncRun(
                connection_id=idle.id, status="completed", created_at=now - timedelta(days=60)
            ),
            "idle_latest": SyncRun(
                connection_id=idle.id, status="completed", created_at=now - timedelta(days=40)
            ),
        }
        db.add_all(runs.values())
        db.commit()
        identifiers = {name: run.id for name, run in runs.items()}

    config = Settings(environment="test", database_url="sqlite://")
    assert prune_sync_runs(engine, config, batch_size=1) == 3

    with Session(engine) as db:
        kept = set(db.scalars(select(SyncRun.id)))
    assert {name for name, identifier in identifiers.items() if identifier not in kept} == {
        "old_empty",
        "old_failed",
        "idle_older",
    }
