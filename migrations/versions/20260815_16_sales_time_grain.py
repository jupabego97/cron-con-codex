"""preserve invoice emission time in the sales fact

Revision ID: 20260815_16
Revises: 20260811_15
Create Date: 2026-08-15
"""

import sqlalchemy as sa
from alembic import op

revision = "20260815_16"
down_revision = "20260811_15"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "fact_sales_line",
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "fact_sales_line",
        sa.Column("sale_hour_local", sa.SmallInteger(), nullable=True),
    )
    op.create_index(
        "ix_fact_sales_line_tenant_issued_at",
        "fact_sales_line",
        ["tenant_id", "issued_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_fact_sales_line_tenant_issued_at", table_name="fact_sales_line")
    op.drop_column("fact_sales_line", "sale_hour_local")
    op.drop_column("fact_sales_line", "issued_at")
