"""add auditable sales cost allocation and margin fields

Revision ID: 20260801_08
Revises: 20260801_07
Create Date: 2026-08-01
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "20260801_08"
down_revision = "20260801_07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "fact_sales_line",
        sa.Column("cogs_amount", sa.Numeric(precision=18, scale=2)),
    )
    op.add_column(
        "fact_sales_line",
        sa.Column("cost_status", sa.String(length=30)),
    )
    op.add_column(
        "fact_sales_line",
        sa.Column("cost_confidence", sa.String(length=30)),
    )
    op.add_column(
        "fact_sales_line",
        sa.Column("cost_method", sa.String(length=30)),
    )

    op.create_table(
        "sales_cost_allocation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cutoff_date", sa.Date(), nullable=False),
        sa.Column("method", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="running"),
        sa.Column("lines_read", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lines_costed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lines_partial", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lines_unavailable", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sales_units", sa.Numeric(precision=18, scale=4), nullable=False, server_default="0"),
        sa.Column("costed_units", sa.Numeric(precision=18, scale=4), nullable=False, server_default="0"),
        sa.Column("cogs_amount", sa.Numeric(precision=18, scale=2), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("error_message", sa.Text()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
    )
    op.create_index(
        "ix_sales_cost_allocation_runs_tenant_started",
        "sales_cost_allocation_runs",
        ["tenant_id", "started_at"],
    )

    op.create_table(
        "sales_cost_allocations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sale_date", sa.Date(), nullable=False),
        sa.Column("document_type", sa.String(length=30), nullable=False),
        sa.Column("document_alegra_id", sa.String(length=100), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("allocation_sequence", sa.Integer(), nullable=False),
        sa.Column("product_key", sa.BigInteger()),
        sa.Column("warehouse_key", sa.BigInteger()),
        sa.Column("source_movement_id", postgresql.UUID(as_uuid=True)),
        sa.Column("source_layer_id", postgresql.UUID(as_uuid=True)),
        sa.Column("quantity_allocated", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("unit_cost", sa.Numeric(precision=18, scale=4)),
        sa.Column("cost_amount", sa.Numeric(precision=18, scale=2)),
        sa.Column("allocation_type", sa.String(length=40), nullable=False),
        sa.Column("confidence", sa.String(length=30), nullable=False),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["run_id"], ["sales_cost_allocation_runs.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["product_key"], ["dim_product.key"]),
        sa.ForeignKeyConstraint(["warehouse_key"], ["dim_warehouse.key"]),
        sa.UniqueConstraint(
            "run_id",
            "document_type",
            "document_alegra_id",
            "line_number",
            "allocation_sequence",
            name="uq_sales_cost_allocation_line",
        ),
    )
    op.create_index(
        "ix_sales_cost_allocations_run_line",
        "sales_cost_allocations",
        ["run_id", "document_type", "document_alegra_id", "line_number"],
    )
    op.create_index(
        "ix_sales_cost_allocations_tenant_product",
        "sales_cost_allocations",
        ["tenant_id", "product_key", "sale_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_sales_cost_allocations_tenant_product", table_name="sales_cost_allocations")
    op.drop_index("ix_sales_cost_allocations_run_line", table_name="sales_cost_allocations")
    op.drop_table("sales_cost_allocations")
    op.drop_index(
        "ix_sales_cost_allocation_runs_tenant_started",
        table_name="sales_cost_allocation_runs",
    )
    op.drop_table("sales_cost_allocation_runs")
    op.drop_column("fact_sales_line", "cost_method")
    op.drop_column("fact_sales_line", "cost_confidence")
    op.drop_column("fact_sales_line", "cost_status")
    op.drop_column("fact_sales_line", "cogs_amount")
