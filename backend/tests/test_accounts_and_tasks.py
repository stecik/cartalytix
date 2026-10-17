from pathlib import Path
from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from alembic import command
from cartalytix.cli import provision_user
from cartalytix.models import Base, ProviderConnection, SyncRun, User
from cartalytix.security import verify_password
from cartalytix.settings import Settings
from cartalytix.sync import SyncError, enqueue_sync


def migration_config(url: str) -> Config:
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def test_migration_and_closed_registration(tmp_path: Path) -> None:
    settings = Settings(environment="development", database_url=f"sqlite:///{tmp_path / 'app.db'}")
    config = migration_config(settings.database_url)
    command.upgrade(config, "head")
    provision_user(settings, "Owner@EXAMPLE.COM", "correct horse battery")
    engine = create_engine(settings.database_url)
    try:
        with Session(engine) as db:
            owner = db.scalar(select(User))
            assert owner is not None
            assert owner.email == "owner@example.com"
            assert verify_password(owner.password_hash, "correct horse battery")
        with pytest.raises(ValueError, match="User already exists"):
            provision_user(settings, "owner@example.com", "correct horse battery")
        with pytest.raises(ValueError, match="12 to 1024"):
            provision_user(settings, "new@example.com", "short")
        with engine.connect() as connection:
            assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    finally:
        engine.dispose()


def test_migration_downgrade_and_reupgrade(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'migration.db'}"
    config = migration_config(url)
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    finally:
        engine.dispose()


def test_sync_job_checks_owner_and_requires_link(tmp_path: Path) -> None:
    settings = Settings(environment="test", database_url=f"sqlite:///{tmp_path / 'app.db'}")
    engine = create_engine(settings.database_url)
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            owner = User(email="owner@example.com", password_hash="unused")
            db.add(owner)
            db.flush()
            connection = ProviderConnection(owner_id=owner.id, provider="lidl", name="Private")
            db.add(connection)
            db.commit()
            owner_id, connection_id = owner.id, connection.id
        with pytest.raises(SyncError, match="connection_not_found"):
            enqueue_sync(engine, connection_id, uuid4())
        with Session(engine) as db:
            assert db.scalar(select(func.count()).select_from(SyncRun)) == 0
        with pytest.raises(SyncError, match="auth_required"):
            enqueue_sync(engine, connection_id, owner_id)
        with Session(engine) as db:
            assert db.scalar(select(func.count()).select_from(SyncRun)) == 0
    finally:
        engine.dispose()


def test_production_requires_secure_cookie_and_https_origin() -> None:
    with pytest.raises(ValueError, match="secure cookies"):
        Settings(environment="production", cookie_secure=False)
    with pytest.raises(ValueError, match="HTTPS origins"):
        Settings(environment="production", allowed_origins=["http://app.example.com"])
    with pytest.raises(ValueError, match="Forbidden database"):
        Settings(database_url="postgresql://localhost/cjf_prod")
