"""Per-user sync interval preset for new connections."""

import sqlalchemy as sa

from alembic import op

revision: str = "0020_default_sync_interval"
down_revision: str | None = "0019_manual_receipts"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "default_sync_interval_minutes", sa.Integer(), nullable=True, server_default="360"
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "default_sync_interval_minutes")
