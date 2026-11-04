"""Remembered sign-in sessions with sliding expiry."""

import sqlalchemy as sa

from alembic import op

revision: str = "0027_remembered_sessions"
down_revision: str | None = "0026_user_cycle_start"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "user_sessions",
        sa.Column("remember", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("user_sessions", "remember")
