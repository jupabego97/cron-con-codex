"""Share future identical raw responses; never rewrite historical snapshots."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261115_23"
down_revision = "20261114_22"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Fail promptly on a busy table instead of queueing an unbounded DDL lock.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.create_table(
        "inventory_snapshot_payloads",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("content_hash", sa.String(64), primary_key=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
    )
    op.add_column("inventory_snapshots", sa.Column("payload_hash", sa.String(64)))
    op.alter_column("inventory_snapshots", "payload", nullable=True)
    # Enforce new writes without scanning gigabytes of unchanged legacy rows.
    op.execute("""ALTER TABLE inventory_snapshots
      ADD CONSTRAINT fk_inventory_snapshot_payload FOREIGN KEY (tenant_id,payload_hash)
      REFERENCES inventory_snapshot_payloads(tenant_id,content_hash) NOT VALID""")
    op.execute("""ALTER TABLE inventory_snapshots
      ADD CONSTRAINT ck_inventory_snapshot_payload_source CHECK (
        (payload IS NOT NULL AND payload_hash IS NULL) OR
        (payload IS NULL AND payload_hash IS NOT NULL)) NOT VALID""")


def downgrade() -> None:
    # A code rollback may keep this additive schema. Never drop referenced data.
    if op.get_bind().execute(sa.text(
        "SELECT EXISTS(SELECT 1 FROM inventory_snapshots WHERE payload_hash IS NOT NULL)"
    )).scalar():
        raise RuntimeError("Referenced raw responses exist; keep schema 23 on code rollback")
    op.drop_constraint("ck_inventory_snapshot_payload_source", "inventory_snapshots")
    op.drop_constraint("fk_inventory_snapshot_payload", "inventory_snapshots")
    op.alter_column("inventory_snapshots", "payload", nullable=False)
    op.drop_column("inventory_snapshots", "payload_hash")
    op.drop_table("inventory_snapshot_payloads")
