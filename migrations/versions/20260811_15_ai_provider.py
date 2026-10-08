"""add configurable AI provider state to copilot conversations

Revision ID: 20260811_15
Revises: 20260810_14
Create Date: 2026-08-11
"""

import sqlalchemy as sa
from alembic import op

revision = "20260811_15"
down_revision = "20260810_14"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_conversations",
        sa.Column("provider", sa.String(length=20), nullable=False, server_default="openai"),
    )
    op.add_column(
        "ai_conversations",
        sa.Column("provider_conversation_id", sa.String(length=255), nullable=True),
    )
    op.create_index(
        "ix_ai_conversations_tenant_provider_updated",
        "ai_conversations",
        ["tenant_id", "provider", "updated_at"],
    )
    op.alter_column("ai_conversations", "provider", server_default=None)


def downgrade() -> None:
    op.drop_index(
        "ix_ai_conversations_tenant_provider_updated",
        table_name="ai_conversations",
    )
    op.drop_column("ai_conversations", "provider_conversation_id")
    op.drop_column("ai_conversations", "provider")
