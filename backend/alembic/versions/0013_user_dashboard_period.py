"""Per-user default period of the overview dashboard."""

import sqlalchemy as sa

from alembic import op

revision: str = "0013_user_dashboard_period"
down_revision: str | None = "0012_positive_discounts"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("dashboard_period", sa.String(16), nullable=False, server_default="12m"),
    )


def downgrade() -> None:
    op.drop_column("users", "dashboard_period")
