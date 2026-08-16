"""Interpret Alegra's timezone-naive invoice datetimes as Colombia local time.

Revision ID: 20260816_17
Revises: 20260815_16
Create Date: 2026-08-16
"""

from alembic import op


revision = "20260816_17"
down_revision = "20260815_16"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Alegra's invoice ``datetime`` payloads for this account are timezone-naive
    # local Colombia timestamps. The previous projection treated them as UTC.
    # Use the newest non-empty payload per invoice so historical rows are fixed
    # without consulting the external API or changing the accounting date.
    op.execute(
        """
        WITH latest_invoice_payload AS (
            SELECT DISTINCT ON (tenant_id, external_id)
                   tenant_id,
                   external_id,
                   (payload->>'datetime')::timestamp AT TIME ZONE 'America/Bogota' AS issued_at
            FROM raw_alegra_documents
            WHERE entity_type = 'invoice'
              AND NULLIF(payload->>'datetime', '') IS NOT NULL
            ORDER BY tenant_id, external_id, received_at DESC, id DESC
        )
        UPDATE sales_invoices AS invoice
        SET issued_at = payload.issued_at
        FROM latest_invoice_payload AS payload
        WHERE invoice.tenant_id = payload.tenant_id
          AND invoice.alegra_id = payload.external_id
        """
    )


def downgrade() -> None:
    # The prior values were semantically incorrect UTC interpretations and are
    # not safely reconstructable after this correction.
    pass
