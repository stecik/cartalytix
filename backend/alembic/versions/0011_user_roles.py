"""Administrator role, disabled accounts and forced password changes."""

import sqlalchemy as sa

from alembic import op

revision: str = "0011_user_roles"
down_revision: str | None = "0010_mcp_oauth"
branch_labels: str | None = None
depends_on: str | None = None

COLUMNS = ("is_admin", "is_disabled", "must_change_password")


def upgrade() -> None:
    for name in COLUMNS:
        op.add_column(
            "users",
            sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.text("false")),
        )


def downgrade() -> None:
    for name in reversed(COLUMNS):
        op.drop_column("users", name)
