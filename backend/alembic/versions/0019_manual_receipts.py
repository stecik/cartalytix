"""Shop of a manually added receipt, shown instead of its hidden manual connection."""

import sqlalchemy as sa

from alembic import op

revision: str = "0019_manual_receipts"
down_revision: str | None = "0018_diagnostic_events"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("receipts", sa.Column("shop_provider", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("receipts", "shop_provider")
