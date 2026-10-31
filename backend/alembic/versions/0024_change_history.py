"""Change history: who made each batch, its changed values and what undid them."""

import sqlalchemy as sa

from alembic import op

revision: str = "0024_change_history"
down_revision: str | None = "0023_change_batches"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("change_batches") as batch:
        batch.add_column(sa.Column("source", sa.String(8), nullable=False, server_default="user"))
        batch.add_column(sa.Column("client_id", sa.String(64), nullable=True))
        batch.add_column(sa.Column("undo_of_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_change_batches_client_id_oauth_clients",
            "oauth_clients",
            ["client_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_change_batches_undo_of_id_change_batches",
            "change_batches",
            ["undo_of_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_change_batches_undo_of_id", ["undo_of_id"])
    op.create_table(
        "change_entries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "batch_id",
            sa.Uuid(),
            sa.ForeignKey("change_batches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column(
            "receipt_id",
            sa.Uuid(),
            sa.ForeignKey("receipts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("item_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("before", sa.JSON(), nullable=True),
        sa.Column("after", sa.JSON(), nullable=True),
        sa.Column(
            "undone_by_id",
            sa.Uuid(),
            sa.ForeignKey("change_batches.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    for column in ("batch_id", "receipt_id", "item_id"):
        op.create_index(f"ix_change_entries_{column}", "change_entries", [column])


def downgrade() -> None:
    op.drop_table("change_entries")
    with op.batch_alter_table("change_batches") as batch:
        batch.drop_index("ix_change_batches_undo_of_id")
        batch.drop_constraint("fk_change_batches_undo_of_id_change_batches", type_="foreignkey")
        batch.drop_constraint("fk_change_batches_client_id_oauth_clients", type_="foreignkey")
        batch.drop_column("undo_of_id")
        batch.drop_column("client_id")
        batch.drop_column("source")
