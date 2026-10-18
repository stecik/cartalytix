"""Per-user color theme preference."""

import sqlalchemy as sa

from alembic import op

revision: str = "0009_user_theme"
down_revision: str | None = "0008_system_name_slugs"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("theme", sa.String(16), nullable=False, server_default="system"),
    )


def downgrade() -> None:
    op.drop_column("users", "theme")
