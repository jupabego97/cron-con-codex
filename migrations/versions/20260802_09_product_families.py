"""project product families and normalize purchase currency

Revision ID: 20260802_09
Revises: 20260801_08
Create Date: 2026-08-02
"""

import sqlalchemy as sa
from alembic import op


revision = "20260802_09"
down_revision = "20260801_08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("catalog_items", sa.Column("family_name", sa.String(length=120)))
    op.add_column("dim_product", sa.Column("family_name", sa.String(length=120)))
    op.create_index("ix_dim_product_tenant_family", "dim_product", ["tenant_id", "family_name"])
    op.execute(
        """
        UPDATE catalog_items c
        SET family_name = NULLIF(trim(cf.field->>'value'), '')
        FROM alegra_entities e
        CROSS JOIN LATERAL jsonb_array_elements(
          CASE WHEN jsonb_typeof(e.payload->'customFields') = 'array'
               THEN e.payload->'customFields' ELSE '[]'::jsonb END
        ) AS cf(field)
        WHERE c.tenant_id = e.tenant_id
          AND c.alegra_id = e.external_id
          AND e.resource = 'item'
          AND (
            upper(coalesce(cf.field->>'name', '')) = 'FAMILIA'
            OR upper(coalesce(cf.field->>'label', '')) = 'FAMILIA'
          )
        """
    )


def downgrade() -> None:
    op.drop_index("ix_dim_product_tenant_family", table_name="dim_product")
    op.drop_column("dim_product", "family_name")
    op.drop_column("catalog_items", "family_name")
