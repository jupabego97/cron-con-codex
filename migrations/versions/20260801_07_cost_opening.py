"""create certified inventory cost opening balances and layers

Revision ID: 20260801_07
Revises: 20260729_06
Create Date: 2026-08-01
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "20260801_07"
down_revision = "20260729_06"
branch_labels = None
depends_on = None


def _uuid() -> sa.Column:
    return sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False)


def upgrade() -> None:
    op.create_table(
        "inventory_cost_import_runs",
        _uuid(),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cutoff_date", sa.Date(), nullable=False),
        sa.Column("source_file_name", sa.String(length=500), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="running"),
        sa.Column("records_read", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("records_written", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("exception_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("error_message", sa.Text()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.UniqueConstraint(
            "tenant_id", "cutoff_date", "source_hash", name="uq_inventory_cost_import_source"
        ),
    )
    op.create_index(
        "ix_inventory_cost_import_runs_tenant_cutoff",
        "inventory_cost_import_runs",
        ["tenant_id", "cutoff_date", "started_at"],
    )

    op.create_table(
        "inventory_cost_opening_balances",
        _uuid(),
        sa.Column("import_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cutoff_date", sa.Date(), nullable=False),
        sa.Column("source_row_number", sa.Integer(), nullable=False),
        sa.Column("product_key", sa.BigInteger()),
        sa.Column("product_alegra_id", sa.String(length=100)),
        sa.Column("warehouse_key", sa.BigInteger()),
        sa.Column("warehouse_alegra_id", sa.String(length=100)),
        sa.Column("item_name", sa.String(length=500), nullable=False),
        sa.Column("reference", sa.String(length=200)),
        sa.Column("category", sa.String(length=300)),
        sa.Column("unit", sa.String(length=100)),
        sa.Column("item_status", sa.String(length=50)),
        sa.Column("reported_quantity", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("reserved_quantity", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("opening_quantity", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("unit_cost", sa.Numeric(precision=18, scale=4)),
        sa.Column("calculated_value", sa.Numeric(precision=18, scale=2)),
        sa.Column("source_total", sa.Numeric(precision=18, scale=2)),
        sa.Column("classification", sa.String(length=40), nullable=False),
        sa.Column("match_method", sa.String(length=40)),
        sa.Column("notes", sa.Text()),
        sa.ForeignKeyConstraint(["import_run_id"], ["inventory_cost_import_runs.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["product_key"], ["dim_product.key"]),
        sa.ForeignKeyConstraint(["warehouse_key"], ["dim_warehouse.key"]),
        sa.UniqueConstraint(
            "import_run_id", "source_row_number", name="uq_inventory_cost_opening_source_row"
        ),
    )
    op.create_index(
        "ix_inventory_cost_opening_tenant_cutoff",
        "inventory_cost_opening_balances",
        ["tenant_id", "cutoff_date", "classification"],
    )
    op.create_index(
        "ix_inventory_cost_opening_product",
        "inventory_cost_opening_balances",
        ["tenant_id", "product_key"],
    )

    op.create_table(
        "inventory_cost_movements",
        _uuid(),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_key", sa.BigInteger(), nullable=False),
        sa.Column("warehouse_key", sa.BigInteger(), nullable=False),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.Column("movement_type", sa.String(length=40), nullable=False),
        sa.Column("source_type", sa.String(length=60), nullable=False),
        sa.Column("source_id", sa.String(length=200), nullable=False),
        sa.Column("source_line_number", sa.Integer()),
        sa.Column("quantity_in", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("quantity_out", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("unit_cost", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("total_cost", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("cost_method", sa.String(length=40), nullable=False),
        sa.Column("confidence", sa.String(length=40), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["product_key"], ["dim_product.key"]),
        sa.ForeignKeyConstraint(["warehouse_key"], ["dim_warehouse.key"]),
        sa.UniqueConstraint(
            "tenant_id", "source_type", "source_id", "source_line_number",
            name="uq_inventory_cost_movement_source",
        ),
    )
    op.create_index(
        "ix_inventory_cost_movements_tenant_date",
        "inventory_cost_movements",
        ["tenant_id", "occurred_on"],
    )
    op.create_index(
        "ix_inventory_cost_movements_product_warehouse",
        "inventory_cost_movements",
        ["product_key", "warehouse_key", "occurred_on"],
    )

    op.create_table(
        "inventory_cost_layers",
        _uuid(),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_key", sa.BigInteger(), nullable=False),
        sa.Column("warehouse_key", sa.BigInteger(), nullable=False),
        sa.Column("movement_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("opened_on", sa.Date(), nullable=False),
        sa.Column("original_quantity", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("remaining_quantity", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("unit_cost", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("layer_status", sa.String(length=30), nullable=False, server_default="open"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["product_key"], ["dim_product.key"]),
        sa.ForeignKeyConstraint(["warehouse_key"], ["dim_warehouse.key"]),
        sa.ForeignKeyConstraint(["movement_id"], ["inventory_cost_movements.id"]),
        sa.UniqueConstraint("movement_id", name="uq_inventory_cost_layer_movement"),
    )
    op.create_index(
        "ix_inventory_cost_layers_open",
        "inventory_cost_layers",
        ["tenant_id", "product_key", "warehouse_key", "layer_status", "opened_on"],
    )


def downgrade() -> None:
    op.drop_index("ix_inventory_cost_layers_open", table_name="inventory_cost_layers")
    op.drop_table("inventory_cost_layers")
    op.drop_index(
        "ix_inventory_cost_movements_product_warehouse", table_name="inventory_cost_movements"
    )
    op.drop_index("ix_inventory_cost_movements_tenant_date", table_name="inventory_cost_movements")
    op.drop_table("inventory_cost_movements")
    op.drop_index("ix_inventory_cost_opening_product", table_name="inventory_cost_opening_balances")
    op.drop_index(
        "ix_inventory_cost_opening_tenant_cutoff", table_name="inventory_cost_opening_balances"
    )
    op.drop_table("inventory_cost_opening_balances")
    op.drop_index(
        "ix_inventory_cost_import_runs_tenant_cutoff", table_name="inventory_cost_import_runs"
    )
    op.drop_table("inventory_cost_import_runs")
