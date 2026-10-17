"""Trigram indexes for receipt search."""

from alembic import op

revision: str = "0003_receipt_search"
down_revision: str | None = "0002_lidl_connector"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_index(
        "ix_receipts_merchant_trgm",
        "receipts",
        ["merchant"],
        postgresql_using="gin",
        postgresql_ops={"merchant": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_receipt_items_name_trgm",
        "receipt_items",
        ["name"],
        postgresql_using="gin",
        postgresql_ops={"name": "gin_trgm_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_receipt_items_name_trgm", table_name="receipt_items")
    op.drop_index("ix_receipts_merchant_trgm", table_name="receipts")
