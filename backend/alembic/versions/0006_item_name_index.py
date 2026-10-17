"""Exact item-name lookups for the product ledger."""

from alembic import op

revision: str = "0006_item_name_index"
down_revision: str | None = "0005_search_unaccent"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_index("ix_receipt_items_name_receipt", "receipt_items", ["name", "receipt_id"])


def downgrade() -> None:
    op.drop_index("ix_receipt_items_name_receipt", table_name="receipt_items")
