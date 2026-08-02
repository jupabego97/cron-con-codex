"""project product supplier and support sales supplier attribution

Revision ID: 20260802_10
Revises: 20260802_09
Create Date: 2026-08-02
"""

import sqlalchemy as sa
from alembic import op


revision = "20260802_10"
down_revision = "20260802_09"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "catalog_items",
        sa.Column("preferred_supplier_name", sa.String(length=300)),
    )
    op.add_column(
        "dim_product",
        sa.Column("preferred_supplier_name", sa.String(length=300)),
    )
    op.create_index(
        "ix_dim_product_tenant_preferred_supplier",
        "dim_product",
        ["tenant_id", "preferred_supplier_name"],
    )
    op.execute(
        """
        UPDATE catalog_items c
        SET preferred_supplier_name = NULLIF(trim(cf.field->>'value'), '')
        FROM alegra_entities e
        CROSS JOIN LATERAL jsonb_array_elements(
          CASE WHEN jsonb_typeof(e.payload->'customFields') = 'array'
               THEN e.payload->'customFields' ELSE '[]'::jsonb END
        ) AS cf(field)
        WHERE c.tenant_id = e.tenant_id
          AND c.alegra_id = e.external_id
          AND e.resource = 'item'
          AND (
            upper(coalesce(cf.field->>'name', '')) = 'PROVEEDOR'
            OR upper(coalesce(cf.field->>'label', '')) = 'PROVEEDOR'
          )
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_dim_product_tenant_preferred_supplier",
        table_name="dim_product",
    )
    op.drop_column("dim_product", "preferred_supplier_name")
    op.drop_column("catalog_items", "preferred_supplier_name")
