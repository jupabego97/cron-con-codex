"""create durable inbound event queue

Revision ID: 20260710_02
Revises: 20260710_01
Create Date: 2026-07-10
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260710_02"
down_revision = "20260710_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inbound_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subject", sa.String(length=80), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("external_id", sa.String(length=100), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "subject", "payload_hash", name="uq_inbound_event_deduplication"
        ),
    )
    op.create_index("ix_inbound_events_tenant_id", "inbound_events", ["tenant_id"])
    op.create_index("ix_inbound_events_external_id", "inbound_events", ["external_id"])
    op.create_index("ix_inbound_events_status", "inbound_events", ["status"])
    op.create_index("ix_inbound_events_available_at", "inbound_events", ["available_at"])


def downgrade() -> None:
    op.drop_index("ix_inbound_events_available_at", table_name="inbound_events")
    op.drop_index("ix_inbound_events_status", table_name="inbound_events")
    op.drop_index("ix_inbound_events_external_id", table_name="inbound_events")
    op.drop_index("ix_inbound_events_tenant_id", table_name="inbound_events")
    op.drop_table("inbound_events")
