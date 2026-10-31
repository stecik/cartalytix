"""Partially counted receipt lines; NULL keeps counting the whole line."""

import sqlalchemy as sa

from alembic import op

revision: str = "0022_included_quantity"
down_revision: str | None = "0021_foodora_vendor_merchant"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("receipt_items") as batch:
        batch.add_column(sa.Column("included_quantity", sa.Numeric(18, 6), nullable=True))
        batch.create_check_constraint(
            "ck_receipt_items_included_quantity",
            "included_quantity IS NULL"
            " OR (included_quantity >= 0 AND included_quantity <= quantity)",
        )


def downgrade() -> None:
    with op.batch_alter_table("receipt_items") as batch:
        batch.drop_constraint("ck_receipt_items_included_quantity", type_="check")
        batch.drop_column("included_quantity")
