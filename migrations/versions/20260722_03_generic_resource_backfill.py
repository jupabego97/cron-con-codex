"""create generic resource backfill storage

Revision ID: 20260722_03
Revises: 20260710_02
Create Date: 2026-07-22
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260722_03"
down_revision = "20260710_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "alegra_entities",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sync_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("resource", sa.String(length=50), nullable=False),
        sa.Column("external_id", sa.String(length=100), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["sync_run_id"], ["sync_runs.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "resource", "external_id", name="uq_alegra_entity_tenant_resource_id"
        ),
    )
    op.create_index("ix_alegra_entities_tenant_id", "alegra_entities", ["tenant_id"])
    op.create_index("ix_alegra_entities_sync_run_id", "alegra_entities", ["sync_run_id"])
    op.create_index("ix_alegra_entities_resource", "alegra_entities", ["resource"])
    op.create_index("ix_alegra_entities_external_id", "alegra_entities", ["external_id"])

    op.create_table(
        "resource_sync_states",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource", sa.String(length=50), nullable=False),
        sa.Column("last_full_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "resource", name="uq_resource_sync_state_tenant_resource"),
    )
    op.create_index("ix_resource_sync_states_tenant_id", "resource_sync_states", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_resource_sync_states_tenant_id", table_name="resource_sync_states")
    op.drop_table("resource_sync_states")
    op.drop_index("ix_alegra_entities_external_id", table_name="alegra_entities")
    op.drop_index("ix_alegra_entities_resource", table_name="alegra_entities")
    op.drop_index("ix_alegra_entities_sync_run_id", table_name="alegra_entities")
    op.drop_index("ix_alegra_entities_tenant_id", table_name="alegra_entities")
    op.drop_table("alegra_entities")
