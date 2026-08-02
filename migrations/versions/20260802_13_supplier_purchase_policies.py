"""create supplier replenishment policies

Revision ID: 20260802_13
Revises: 20260802_12
Create Date: 2026-08-02
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "20260802_13"
down_revision = "20260802_12"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "supplier_replenishment_policies",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("supplier_key", sa.BigInteger(), nullable=False),
        sa.Column("currency_code", sa.String(length=10), nullable=False, server_default="COP"),
        sa.Column("minimum_order_amount", sa.Numeric(18, 2)),
        sa.Column("shipping_cost", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("free_shipping_threshold", sa.Numeric(18, 2)),
        sa.Column("default_lead_time_days", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("max_wait_days", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notes", sa.Text()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["supplier_key"], ["dim_contact.key"]),
        sa.PrimaryKeyConstraint(
            "tenant_id", "supplier_key", "currency_code",
            name="pk_supplier_replenishment_policies",
        ),
        sa.CheckConstraint("minimum_order_amount IS NULL OR minimum_order_amount >= 0", name="ck_supplier_policy_min_amount"),
        sa.CheckConstraint("shipping_cost >= 0", name="ck_supplier_policy_shipping"),
        sa.CheckConstraint("free_shipping_threshold IS NULL OR free_shipping_threshold >= 0", name="ck_supplier_policy_free_shipping"),
        sa.CheckConstraint("default_lead_time_days >= 0", name="ck_supplier_policy_lead_time"),
        sa.CheckConstraint("max_wait_days >= 0", name="ck_supplier_policy_max_wait"),
        sa.CheckConstraint("priority >= 0", name="ck_supplier_policy_priority"),
    )
    op.create_index(
        "ix_supplier_replenishment_policies_tenant_active",
        "supplier_replenishment_policies",
        ["tenant_id", "active"],
    )

    op.create_table(
        "supplier_product_policies",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("supplier_key", sa.BigInteger(), nullable=False),
        sa.Column("product_key", sa.BigInteger(), nullable=False),
        sa.Column("currency_code", sa.String(length=10), nullable=False, server_default="COP"),
        sa.Column("minimum_order_quantity", sa.Numeric(18, 4)),
        sa.Column("pack_size", sa.Numeric(18, 4), nullable=False, server_default="1"),
        sa.Column("lead_time_days", sa.Integer()),
        sa.Column("max_wait_days", sa.Integer()),
        sa.Column("is_preferred", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notes", sa.Text()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["supplier_key"], ["dim_contact.key"]),
        sa.ForeignKeyConstraint(["product_key"], ["dim_product.key"]),
        sa.PrimaryKeyConstraint(
            "tenant_id", "supplier_key", "product_key", "currency_code",
            name="pk_supplier_product_policies",
        ),
        sa.CheckConstraint("minimum_order_quantity IS NULL OR minimum_order_quantity >= 0", name="ck_supplier_product_policy_min_qty"),
        sa.CheckConstraint("pack_size > 0", name="ck_supplier_product_policy_pack"),
        sa.CheckConstraint("lead_time_days IS NULL OR lead_time_days >= 0", name="ck_supplier_product_policy_lead"),
        sa.CheckConstraint("max_wait_days IS NULL OR max_wait_days >= 0", name="ck_supplier_product_policy_wait"),
    )
    op.create_index(
        "ix_supplier_product_policies_tenant_product",
        "supplier_product_policies",
        ["tenant_id", "product_key"],
    )
    op.create_index(
        "ix_supplier_product_policies_tenant_supplier",
        "supplier_product_policies",
        ["tenant_id", "supplier_key"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_supplier_product_policies_tenant_supplier",
        table_name="supplier_product_policies",
    )
    op.drop_index(
        "ix_supplier_product_policies_tenant_product",
        table_name="supplier_product_policies",
    )
    op.drop_table("supplier_product_policies")
    op.drop_index(
        "ix_supplier_replenishment_policies_tenant_active",
        table_name="supplier_replenishment_policies",
    )
    op.drop_table("supplier_replenishment_policies")
