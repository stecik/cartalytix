"""Reversible receipt, occurrence and owner-scoped product exclusions."""

import sqlalchemy as sa

from alembic import op

revision: str = "0014_exclusions"
down_revision: str | None = "0013_user_dashboard_period"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    for table in ("receipts", "receipt_items"):
        op.add_column(
            table, sa.Column("is_excluded", sa.Boolean(), nullable=False, server_default=sa.false())
        )
    op.create_table(
        "product_exclusions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "owner_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.UniqueConstraint("owner_id", "name", "currency"),
    )
    op.create_index("ix_product_exclusions_owner_id", "product_exclusions", ["owner_id"])


def downgrade() -> None:
    op.drop_table("product_exclusions")
    for table in ("receipt_items", "receipts"):
        op.drop_column(table, "is_excluded")
