"""create auditable historical modal supplier projection

Revision ID: 20260802_11
Revises: 20260802_10
Create Date: 2026-08-02
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "20260802_11"
down_revision = "20260802_10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "product_supplier_modes",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_key", sa.BigInteger(), nullable=False),
        sa.Column("supplier_key", sa.BigInteger(), nullable=False),
        sa.Column("mode_method", sa.String(length=40), nullable=False),
        sa.Column("purchase_line_count", sa.Integer(), nullable=False),
        sa.Column("purchased_units", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("purchased_amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("total_purchase_lines", sa.Integer(), nullable=False),
        sa.Column("supplier_count", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=9, scale=6), nullable=False),
        sa.Column("last_purchase_date", sa.Date()),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["product_key"], ["dim_product.key"]),
        sa.ForeignKeyConstraint(["supplier_key"], ["dim_contact.key"]),
        sa.PrimaryKeyConstraint("tenant_id", "product_key", name="pk_product_supplier_modes"),
    )
    op.create_index(
        "ix_product_supplier_modes_tenant_supplier",
        "product_supplier_modes",
        ["tenant_id", "supplier_key"],
    )
    op.create_index(
        "ix_product_supplier_modes_tenant_confidence",
        "product_supplier_modes",
        ["tenant_id", "confidence"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_product_supplier_modes_tenant_confidence",
        table_name="product_supplier_modes",
    )
    op.drop_index(
        "ix_product_supplier_modes_tenant_supplier",
        table_name="product_supplier_modes",
    )
    op.drop_table("product_supplier_modes")
