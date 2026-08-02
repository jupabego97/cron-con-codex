"""create supplier purchase statistics and replenishment review workflow

Revision ID: 20260802_12
Revises: 20260802_11
Create Date: 2026-08-02
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "20260802_12"
down_revision = "20260802_11"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "supplier_product_stats",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_key", sa.BigInteger(), nullable=False),
        sa.Column("supplier_key", sa.BigInteger(), nullable=False),
        sa.Column("currency_code", sa.String(length=10), nullable=False),
        sa.Column("purchase_line_count", sa.Integer(), nullable=False),
        sa.Column("purchased_units", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("purchased_amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("average_unit_cost", sa.Numeric(precision=18, scale=4)),
        sa.Column("median_unit_cost", sa.Numeric(precision=18, scale=4)),
        sa.Column("minimum_unit_cost", sa.Numeric(precision=18, scale=4)),
        sa.Column("maximum_unit_cost", sa.Numeric(precision=18, scale=4)),
        sa.Column("last_unit_cost", sa.Numeric(precision=18, scale=4)),
        sa.Column("last_purchase_date", sa.Date()),
        sa.Column("total_purchase_lines", sa.Integer(), nullable=False),
        sa.Column("total_purchased_units", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("line_share_pct", sa.Numeric(precision=9, scale=4), nullable=False),
        sa.Column("unit_share_pct", sa.Numeric(precision=9, scale=4), nullable=False),
        sa.Column("frequency_rank", sa.Integer(), nullable=False),
        sa.Column("cost_rank", sa.Integer(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["product_key"], ["dim_product.key"]),
        sa.ForeignKeyConstraint(["supplier_key"], ["dim_contact.key"]),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "product_key",
            "supplier_key",
            "currency_code",
            name="pk_supplier_product_stats",
        ),
    )
    op.create_index(
        "ix_supplier_product_stats_tenant_product",
        "supplier_product_stats",
        ["tenant_id", "product_key"],
    )
    op.create_index(
        "ix_supplier_product_stats_tenant_supplier",
        "supplier_product_stats",
        ["tenant_id", "supplier_key"],
    )

    op.create_table(
        "replenishment_item_actions",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_key", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("note", sa.Text()),
        sa.Column("snoozed_until", sa.Date()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["product_key"], ["dim_product.key"]),
        sa.PrimaryKeyConstraint("tenant_id", "product_key", name="pk_replenishment_item_actions"),
        sa.CheckConstraint(
            "status IN ('pending', 'reviewed', 'snoozed', 'purchased', 'discarded')",
            name="ck_replenishment_item_actions_status",
        ),
    )
    op.create_index(
        "ix_replenishment_item_actions_tenant_status",
        "replenishment_item_actions",
        ["tenant_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_replenishment_item_actions_tenant_status",
        table_name="replenishment_item_actions",
    )
    op.drop_table("replenishment_item_actions")
    op.drop_index(
        "ix_supplier_product_stats_tenant_supplier",
        table_name="supplier_product_stats",
    )
    op.drop_index(
        "ix_supplier_product_stats_tenant_product",
        table_name="supplier_product_stats",
    )
    op.drop_table("supplier_product_stats")
