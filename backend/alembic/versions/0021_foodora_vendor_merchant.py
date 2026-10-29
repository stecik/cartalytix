"""Name stored foodora receipts after their restaurant instead of the platform."""

import json

import sqlalchemy as sa

from alembic import op

revision: str = "0021_foodora_vendor_merchant"
down_revision: str | None = "0020_default_sync_interval"
branch_labels: str | None = None
depends_on: str | None = None

receipts = sa.table(
    "receipts",
    sa.column("id", sa.Uuid()),
    sa.column("connection_id", sa.Uuid()),
    sa.column("raw_document_id", sa.Uuid()),
    sa.column("merchant", sa.String()),
)
raw_documents = sa.table("raw_documents", sa.column("id", sa.Uuid()), sa.column("payload"))
provider_connections = sa.table(
    "provider_connections", sa.column("id", sa.Uuid()), sa.column("provider", sa.String())
)


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.select(receipts.c.id, raw_documents.c.payload)
        .join(raw_documents, raw_documents.c.id == receipts.c.raw_document_id)
        .join(provider_connections, provider_connections.c.id == receipts.c.connection_id)
        .where(provider_connections.c.provider == "foodora", receipts.c.merchant == "Foodora")
    )
    updates: list[dict[str, object]] = []
    for identifier, payload in rows:
        try:
            detail = json.loads(payload)
        except ValueError:
            continue
        vendor = detail.get("vendor") if isinstance(detail, dict) else None
        name = vendor.get("name") if isinstance(vendor, dict) else None
        if isinstance(name, str) and name.strip():
            updates.append({"receipt_id": identifier, "name": name.strip()[:256]})
    if updates:
        connection.execute(
            receipts.update()
            .where(receipts.c.id == sa.bindparam("receipt_id"))
            .values(merchant=sa.bindparam("name")),
            updates,
        )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        receipts.update()
        .where(
            receipts.c.connection_id.in_(
                sa.select(provider_connections.c.id).where(
                    provider_connections.c.provider == "foodora"
                )
            )
        )
        .values(merchant="Foodora")
    )
