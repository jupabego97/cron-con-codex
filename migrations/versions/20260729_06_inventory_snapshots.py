"""store inventory snapshots by product and warehouse

Revision ID: 20260729_06
Revises: 20260728_05
Create Date: 2026-07-29
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "20260729_06"
down_revision = "20260728_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inventory_snapshot_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="running"),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("records_read", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("records_written", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
    )
    op.create_index(
        "ix_inventory_snapshot_runs_tenant_started",
        "inventory_snapshot_runs",
        ["tenant_id", "started_at"],
    )
    op.create_table(
        "inventory_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("snapshot_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("warehouse_alegra_id", sa.String(length=100), nullable=False),
        sa.Column("item_alegra_id", sa.String(length=100), nullable=False),
        sa.Column("item_name", sa.String(length=500), nullable=False),
        sa.Column("quantity_on_hand", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("unit_cost", sa.Numeric(precision=18, scale=2)),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["snapshot_run_id"], ["inventory_snapshot_runs.id"]),
        sa.UniqueConstraint(
            "snapshot_run_id", "warehouse_alegra_id", "item_alegra_id",
            name="uq_inventory_snapshot_run_warehouse_item",
        ),
    )
    op.create_index("ix_inventory_snapshots_tenant_captured", "inventory_snapshots", ["tenant_id", "captured_at"])

    op.create_table(
        "fact_inventory_snapshot",
        sa.Column("key", sa.BigInteger(), sa.Identity(), primary_key=True, nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_key", sa.BigInteger(), nullable=False),
        sa.Column("date_key", sa.Integer(), nullable=False),
        sa.Column("product_key", sa.BigInteger()),
        sa.Column("warehouse_key", sa.BigInteger()),
        sa.Column("snapshot_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quantity_on_hand", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("unit_cost", sa.Numeric(precision=18, scale=2)),
        sa.Column("inventory_value", sa.Numeric(precision=18, scale=2)),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["tenant_key"], ["dim_tenant.key"]),
        sa.ForeignKeyConstraint(["date_key"], ["dim_date.date_key"]),
        sa.ForeignKeyConstraint(["product_key"], ["dim_product.key"]),
        sa.ForeignKeyConstraint(["warehouse_key"], ["dim_warehouse.key"]),
        sa.UniqueConstraint("snapshot_run_id", "product_key", "warehouse_key", name="uq_fact_inventory_snapshot_source"),
    )
    op.create_index("ix_fact_inventory_snapshot_tenant_captured", "fact_inventory_snapshot", ["tenant_id", "captured_at"])
    op.create_index("ix_fact_inventory_snapshot_dimensions", "fact_inventory_snapshot", ["product_key", "warehouse_key"])


def downgrade() -> None:
    op.drop_index("ix_fact_inventory_snapshot_dimensions", table_name="fact_inventory_snapshot")
    op.drop_index("ix_fact_inventory_snapshot_tenant_captured", table_name="fact_inventory_snapshot")
    op.drop_table("fact_inventory_snapshot")
    op.drop_index("ix_inventory_snapshots_tenant_captured", table_name="inventory_snapshots")
    op.drop_table("inventory_snapshots")
    op.drop_index("ix_inventory_snapshot_runs_tenant_started", table_name="inventory_snapshot_runs")
    op.drop_table("inventory_snapshot_runs")
