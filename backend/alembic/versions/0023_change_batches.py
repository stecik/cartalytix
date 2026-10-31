"""Batches of spending changes, replayable by their idempotency key."""

import sqlalchemy as sa

from alembic import op

revision: str = "0023_change_batches"
down_revision: str | None = "0022_included_quantity"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "change_batches",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "owner_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.UniqueConstraint("owner_id", "idempotency_key"),
    )
    op.create_index(
        "ix_change_batches_owner_created", "change_batches", ["owner_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_table("change_batches")
