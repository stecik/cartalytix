"""User categories and durable product assignments with many-to-many membership."""

import sqlalchemy as sa

from alembic import op

revision: str = "0015_categories"
down_revision: str | None = "0014_exclusions"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "categories",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("name_key", sa.String(300), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "name_key"),
    )
    op.create_index("ix_categories_owner_id", "categories", ["owner_id"])
    op.create_table(
        "category_products",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("name_hash", sa.String(64), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("assignment_source", sa.String(6), nullable=False),
        sa.ForeignKeyConstraint(["connection_id"], ["provider_connections.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("connection_id", "name_hash", "currency"),
    )
    op.create_index("ix_category_products_connection_id", "category_products", ["connection_id"])
    op.create_table(
        "category_memberships",
        sa.Column("category_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["category_products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("category_id", "product_id"),
    )
    op.create_index("ix_category_memberships_product_id", "category_memberships", ["product_id"])


def downgrade() -> None:
    op.drop_table("category_memberships")
    op.drop_table("category_products")
    op.drop_table("categories")
