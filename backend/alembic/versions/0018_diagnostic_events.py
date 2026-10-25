"""Owner-scoped, credential-free diagnostic events."""

import sqlalchemy as sa

from alembic import op

revision: str = "0018_diagnostic_events"
down_revision: str | None = "0017_dashboard_category_slices"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "diagnostic_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("component", sa.String(16), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_diagnostic_events_created_at", "diagnostic_events", ["created_at"])
    op.create_index(
        "ix_diagnostic_events_owner_created", "diagnostic_events", ["owner_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_diagnostic_events_owner_created", table_name="diagnostic_events")
    op.drop_index("ix_diagnostic_events_created_at", table_name="diagnostic_events")
    op.drop_table("diagnostic_events")
