"""record procurement minimum overrides and freight approval audit

Revision ID: 20261006_19
Revises: 20260828_18
Create Date: 2026-10-06
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261006_19"
down_revision = "20260828_18"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "purchase_plan_runs",
        sa.Column(
            "approval_audit",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("purchase_plan_runs", "approval_audit")
