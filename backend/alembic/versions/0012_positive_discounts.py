"""Store Lidl printed-receipt discounts as positive savings, like every other source."""

from alembic import op

revision: str = "0012_positive_discounts"
down_revision: str | None = "0011_user_roles"
branch_labels: str | None = None
depends_on: str | None = None

HTML_RECEIPTS = (
    "SELECT receipts.id FROM receipts WHERE receipts.source_format = 'htmlPrintedReceipt'"
)


def upgrade() -> None:
    op.execute(
        "UPDATE discounts SET amount = -discounts.amount "
        f"WHERE discounts.amount < 0 AND discounts.receipt_id IN ({HTML_RECEIPTS})"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE discounts SET amount = -discounts.amount "
        f"WHERE discounts.amount > 0 AND discounts.receipt_id IN ({HTML_RECEIPTS})"
    )
