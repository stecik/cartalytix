"""Per-user receipt display preference."""

import sqlalchemy as sa

from alembic import op

revision: str = "0004_user_receipt_view"
down_revision: str | None = "0003_receipt_search"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("receipt_view", sa.String(16), nullable=False, server_default="rendered"),
    )


def downgrade() -> None:
    op.drop_column("users", "receipt_view")
