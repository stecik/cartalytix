"""Store connector-generated adjustment rows as translatable slugs."""

from alembic import op

revision: str = "0008_system_name_slugs"
down_revision: str | None = "0007_connection_account_label"
branch_labels: str | None = None
depends_on: str | None = None

RENAMES = (
    ("receipt_items", "Shipping and fees", "shipping_and_fees"),
    ("discounts", "Discounts and credits", "discounts_and_credits"),
)
MIXIT_RECEIPTS = (
    "SELECT receipts.id FROM receipts JOIN provider_connections"
    " ON provider_connections.id = receipts.connection_id"
    " WHERE provider_connections.provider = 'mixit'"
)


def _rename(table: str, old: str, new: str) -> None:
    op.execute(
        f"UPDATE {table} SET name = '{new}' WHERE name = '{old}'"
        f" AND receipt_id IN ({MIXIT_RECEIPTS})"
    )


def upgrade() -> None:
    for table, label, slug in RENAMES:
        _rename(table, label, slug)


def downgrade() -> None:
    for table, label, slug in RENAMES:
        _rename(table, slug, label)
