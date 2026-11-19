"""Invalidate Albert SMS flows on replacement, completion and disconnect."""

import sqlalchemy as sa

from alembic import op

revision: str = "0028_login_revision"
down_revision: str | None = "0027_remembered_sessions"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "provider_connections",
        sa.Column("login_revision", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("provider_connections", "login_revision")
