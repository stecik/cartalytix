"""Lidl credentials, synchronization progress and source financial values."""

import sqlalchemy as sa

from alembic import op

revision: str = "0002_lidl_connector"
down_revision: str | None = "0001_initial"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("provider_connections", sa.Column("encrypted_refresh_token", sa.LargeBinary()))
    op.add_column("provider_connections", sa.Column("account_subject", sa.String(256)))
    op.add_column("provider_connections", sa.Column("sync_interval_minutes", sa.Integer()))
    op.add_column("provider_connections", sa.Column("last_synced_at", sa.DateTime(timezone=True)))
    for name in ["started_at", "heartbeat_at", "last_dispatched_at"]:
        op.add_column("sync_runs", sa.Column(name, sa.DateTime(timezone=True)))
    for name in ["receipts_seen", "receipts_imported", "receipts_skipped", "pages_count"]:
        op.add_column(
            "sync_runs", sa.Column(name, sa.Integer(), nullable=False, server_default="0")
        )
    op.add_column(
        "sync_runs",
        sa.Column(
            "refresh_existing", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )
    op.create_index(
        "ix_sync_runs_active_connection",
        "sync_runs",
        ["connection_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'running')"),
        sqlite_where=sa.text("status IN ('pending', 'running')"),
    )
    op.create_index("ix_sync_runs_connection_created", "sync_runs", ["connection_id", "created_at"])
    op.add_column(
        "receipts",
        sa.Column("rounding_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
    )
    op.add_column(
        "receipts", sa.Column("quality_issues", sa.JSON(), nullable=False, server_default="[]")
    )
    op.add_column(
        "receipts",
        sa.Column("source_format", sa.String(40), nullable=False, server_default="unknown"),
    )
    op.add_column("receipt_items", sa.Column("source_code", sa.String(256)))
    op.add_column(
        "receipt_items",
        sa.Column("is_weight", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    for name in ["original_amount", "deposit_amount", "printed_net_amount"]:
        op.add_column("receipt_items", sa.Column(name, sa.Numeric(18, 2)))
    op.add_column("receipt_items", sa.Column("warranty", sa.Text()))


def downgrade() -> None:
    for name in [
        "warranty",
        "printed_net_amount",
        "deposit_amount",
        "original_amount",
        "is_weight",
        "source_code",
    ]:
        op.drop_column("receipt_items", name)
    for name in ["source_format", "quality_issues", "rounding_amount"]:
        op.drop_column("receipts", name)
    op.drop_index("ix_sync_runs_active_connection", table_name="sync_runs")
    op.drop_index("ix_sync_runs_connection_created", table_name="sync_runs")
    for name in [
        "refresh_existing",
        "pages_count",
        "receipts_skipped",
        "receipts_imported",
        "receipts_seen",
        "last_dispatched_at",
        "heartbeat_at",
        "started_at",
    ]:
        op.drop_column("sync_runs", name)
    for name in [
        "last_synced_at",
        "sync_interval_minutes",
        "account_subject",
        "encrypted_refresh_token",
    ]:
        op.drop_column("provider_connections", name)
