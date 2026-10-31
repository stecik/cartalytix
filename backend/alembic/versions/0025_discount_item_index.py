"""Index line discounts, which line-level allocations and refreshes look up by line."""

from alembic import op

revision: str = "0025_discount_item_index"
down_revision: str | None = "0024_change_history"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_index("ix_discounts_item_id", "discounts", ["item_id"])


def downgrade() -> None:
    op.drop_index("ix_discounts_item_id", table_name="discounts")
