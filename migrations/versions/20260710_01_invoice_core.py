"""create invoice ingestion core

Revision ID: 20260710_01
Revises:
Create Date: 2026-07-10
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260710_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_table(
        "sync_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource", sa.String(length=50), nullable=False),
        sa.Column("mode", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("records_read", sa.Integer(), nullable=False),
        sa.Column("records_written", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sync_runs_tenant_id", "sync_runs", ["tenant_id"])
    op.create_table(
        "raw_alegra_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sync_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("external_id", sa.String(length=100), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["sync_run_id"], ["sync_runs.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "entity_type",
            "external_id",
            "payload_hash",
            name="uq_raw_alegra_document_version",
        ),
    )
    op.create_index("ix_raw_alegra_documents_tenant_id", "raw_alegra_documents", ["tenant_id"])
    op.create_index("ix_raw_alegra_documents_sync_run_id", "raw_alegra_documents", ["sync_run_id"])
    op.create_table(
        "sales_invoices",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("alegra_id", sa.String(length=100), nullable=False),
        sa.Column("issue_date", sa.Date(), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("client_alegra_id", sa.String(length=100), nullable=True),
        sa.Column("client_name", sa.String(length=300), nullable=True),
        sa.Column("seller_alegra_id", sa.String(length=100), nullable=True),
        sa.Column("seller_name", sa.String(length=300), nullable=True),
        sa.Column("currency_code", sa.String(length=10), nullable=True),
        sa.Column("total", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("total_paid", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("balance", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "alegra_id", name="uq_sales_invoice_tenant_alegra"),
    )
    op.create_index("ix_sales_invoices_tenant_id", "sales_invoices", ["tenant_id"])
    op.create_table(
        "sales_invoice_lines",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("item_alegra_id", sa.String(length=100), nullable=True),
        sa.Column("item_name", sa.String(length=500), nullable=False),
        sa.Column("item_reference", sa.String(length=200), nullable=True),
        sa.Column("quantity", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("unit_price", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("line_total", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.ForeignKeyConstraint(["invoice_id"], ["sales_invoices.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("invoice_id", "line_number", name="uq_sales_invoice_line_number"),
    )
    op.create_index("ix_sales_invoice_lines_invoice_id", "sales_invoice_lines", ["invoice_id"])


def downgrade() -> None:
    op.drop_index("ix_sales_invoice_lines_invoice_id", table_name="sales_invoice_lines")
    op.drop_table("sales_invoice_lines")
    op.drop_index("ix_sales_invoices_tenant_id", table_name="sales_invoices")
    op.drop_table("sales_invoices")
    op.drop_index("ix_raw_alegra_documents_sync_run_id", table_name="raw_alegra_documents")
    op.drop_index("ix_raw_alegra_documents_tenant_id", table_name="raw_alegra_documents")
    op.drop_table("raw_alegra_documents")
    op.drop_index("ix_sync_runs_tenant_id", table_name="sync_runs")
    op.drop_table("sync_runs")
    op.drop_table("tenants")
