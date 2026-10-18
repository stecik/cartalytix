"""OAuth clients, consents and tokens for the MCP connector."""

import sqlalchemy as sa

from alembic import op

revision: str = "0010_mcp_oauth"
down_revision: str | None = "0009_user_theme"
branch_labels: str | None = None
depends_on: str | None = None

INDEXES = {
    "oauth_authorization_requests": ["client_id", "expires_at"],
    "oauth_grants": ["owner_id", "client_id"],
    "oauth_codes": ["client_id", "owner_id", "expires_at", "grant_id"],
    "oauth_tokens": ["grant_id", "expires_at"],
}


def upgrade() -> None:
    op.create_table(
        "oauth_clients",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("registration", sa.JSON(), nullable=False),
        sa.Column("encrypted_secret", sa.LargeBinary(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "oauth_authorization_requests",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["oauth_clients.id"]),
        sa.PrimaryKeyConstraint("token_hash"),
    )
    op.create_table(
        "oauth_grants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["client_id"], ["oauth_clients.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "client_id"),
    )
    op.create_table(
        "oauth_codes",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("grant_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["client_id"], ["oauth_clients.id"]),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["grant_id"], ["oauth_grants.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("token_hash"),
    )
    op.create_table(
        "oauth_tokens",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("grant_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("resource", sa.String(length=512), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["grant_id"], ["oauth_grants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("token_hash"),
    )
    for table, columns in INDEXES.items():
        for column in columns:
            op.create_index(op.f(f"ix_{table}_{column}"), table, [column], unique=False)


def downgrade() -> None:
    for table in reversed(INDEXES):
        op.drop_table(table)
    op.drop_table("oauth_clients")
