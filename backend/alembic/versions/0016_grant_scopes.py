"""Owner-editable MCP permissions stored on the grant instead of on each token."""

import json

import sqlalchemy as sa

from alembic import op

revision: str = "0016_grant_scopes"
down_revision: str | None = "0015_categories"
branch_labels: str | None = None
depends_on: str | None = None

READ_SCOPE = "cartalytix:read"
oauth_grants = sa.table("oauth_grants", sa.column("id", sa.Uuid()), sa.column("scopes", sa.JSON()))
oauth_tokens = sa.table(
    "oauth_tokens", sa.column("grant_id", sa.Uuid()), sa.column("scopes", sa.JSON())
)


def upgrade() -> None:
    op.add_column(
        "oauth_grants",
        sa.Column("scopes", sa.JSON(), nullable=False, server_default=json.dumps([READ_SCOPE])),
    )
    bind = op.get_bind()
    # Existing connections keep exactly what their tokens were issued with.
    granted: dict[object, set[str]] = {}
    for grant_id, scopes in bind.execute(sa.select(oauth_tokens.c.grant_id, oauth_tokens.c.scopes)):
        granted.setdefault(grant_id, {READ_SCOPE}).update(scopes or [])
    for grant_id, scopes in granted.items():
        bind.execute(
            sa.update(oauth_grants)
            .where(oauth_grants.c.id == grant_id)
            .values(scopes=sorted(scopes))
        )


def downgrade() -> None:
    op.drop_column("oauth_grants", "scopes")
