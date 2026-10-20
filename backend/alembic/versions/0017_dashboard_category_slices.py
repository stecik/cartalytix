"""Per-user number of segments in the dashboard category charts."""

import sqlalchemy as sa

from alembic import op

revision: str = "0017_dashboard_category_slices"
down_revision: str | None = "0016_grant_scopes"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("dashboard_category_slices", sa.Integer(), nullable=False, server_default="9"),
    )


def downgrade() -> None:
    op.drop_column("users", "dashboard_category_slices")
