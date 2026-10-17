"""Accent-insensitive trigram indexes for receipt search."""

from alembic import op

revision: str = "0005_search_unaccent"
down_revision: str | None = "0004_user_receipt_view"
branch_labels: str | None = None
depends_on: str | None = None

SEARCH_FUNCTION_DDL = """
CREATE OR REPLACE FUNCTION cartalytix_search_text(value text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
AS $$ SELECT lower(public.unaccent('public.unaccent'::regdictionary, value)) $$
"""


def upgrade() -> None:
    op.drop_index("ix_receipt_items_name_trgm", table_name="receipt_items")
    op.drop_index("ix_receipts_merchant_trgm", table_name="receipts")
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    op.execute(SEARCH_FUNCTION_DDL)
    op.execute(
        "CREATE INDEX ix_receipts_merchant_search ON receipts "
        "USING gin (cartalytix_search_text(merchant) gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_receipt_items_name_search ON receipt_items "
        "USING gin (cartalytix_search_text(name) gin_trgm_ops)"
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.drop_index("ix_receipt_items_name_search", table_name="receipt_items")
        op.drop_index("ix_receipts_merchant_search", table_name="receipts")
        op.execute("DROP FUNCTION IF EXISTS cartalytix_search_text(text)")
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
