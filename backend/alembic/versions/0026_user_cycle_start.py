"""Per-user start day of the overview cycle period."""

import sqlalchemy as sa

from alembic import op

revision: str = "0026_user_cycle_start"
down_revision: str | None = "0025_discount_item_index"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("cycle_start_day", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "users",
        sa.Column(
            "cycle_weekend_shift", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "cycle_weekend_shift")
    op.drop_column("users", "cycle_start_day")
