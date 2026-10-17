"""Sign-in e-mail displayed for unnamed provider accounts."""

import sqlalchemy as sa

from alembic import op

revision: str = "0007_connection_account_label"
down_revision: str | None = "0006_item_name_index"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("provider_connections", sa.Column("account_label", sa.String(254)))


def downgrade() -> None:
    op.drop_column("provider_connections", "account_label")
