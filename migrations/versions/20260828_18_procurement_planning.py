"""add procurement planning and purchase orders

Revision ID: 20260828_18
Revises: 20260816_17
Create Date: 2026-08-28
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260828_18"
down_revision = "20260816_17"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "purchase_bills",
        sa.Column("purchase_order_alegra_id", sa.String(length=100)),
    )
    op.create_index(
        "ix_purchase_bills_purchase_order_alegra_id",
        "purchase_bills",
        ["purchase_order_alegra_id"],
    )

    op.create_table(
        "purchase_orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("alegra_id", sa.String(length=100), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("order_date", sa.Date()),
        sa.Column("delivery_date", sa.Date()),
        sa.Column("status", sa.String(length=30)),
        sa.Column("document_number", sa.String(length=100)),
        sa.Column("provider_alegra_id", sa.String(length=100)),
        sa.Column("provider_name", sa.String(length=300)),
        sa.Column("warehouse_alegra_id", sa.String(length=100)),
        sa.Column("currency_code", sa.String(length=10)),
        sa.Column("total", sa.Numeric(18, 2)),
        sa.Column("observations", sa.Text()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "alegra_id", name="uq_purchase_order_tenant_alegra"),
    )
    op.create_index("ix_purchase_orders_tenant_id", "purchase_orders", ["tenant_id"])
    op.create_index(
        "ix_purchase_orders_tenant_status",
        "purchase_orders",
        ["tenant_id", "status"],
    )

    op.create_table(
        "purchase_order_lines",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_alegra_id", sa.String(length=100), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("item_alegra_id", sa.String(length=100)),
        sa.Column("item_name", sa.String(length=500)),
        sa.Column("quantity", sa.Numeric(18, 4)),
        sa.Column("unit_price", sa.Numeric(18, 2)),
        sa.Column("line_total", sa.Numeric(18, 2)),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "document_alegra_id", "line_number", name="uq_purchase_order_line"
        ),
    )
    op.create_index("ix_purchase_order_lines_tenant_id", "purchase_order_lines", ["tenant_id"])

    op.create_table(
        "replenishment_forecasts",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_key", sa.BigInteger(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("abc_class", sa.String(length=1), nullable=False),
        sa.Column("xyz_class", sa.String(length=1), nullable=False),
        sa.Column("demand_pattern", sa.String(length=30), nullable=False),
        sa.Column("model_name", sa.String(length=50), nullable=False),
        sa.Column("daily_forecast", sa.Numeric(18, 6), nullable=False),
        sa.Column("wape", sa.Numeric(12, 6)),
        sa.Column("bias", sa.Numeric(12, 6)),
        sa.Column("service_level", sa.Numeric(6, 4), nullable=False),
        sa.Column("confidence", sa.String(length=20), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["product_key"], ["dim_product.key"]),
        sa.PrimaryKeyConstraint("tenant_id", "product_key", "as_of_date"),
    )
    op.create_index(
        "ix_replenishment_forecasts_tenant_date",
        "replenishment_forecasts",
        ["tenant_id", "as_of_date"],
    )

    op.create_table(
        "purchase_plan_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("currency_code", sa.String(length=10), nullable=False, server_default="COP"),
        sa.Column("weekly_budget", sa.Numeric(18, 2), nullable=False),
        sa.Column("review_cycle_days", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="draft"),
        sa.Column("snapshot_run_id", postgresql.UUID(as_uuid=True)),
        sa.Column("data_quality", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("recommended_value", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("approved_value", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["snapshot_run_id"], ["inventory_snapshot_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_purchase_plan_runs_tenant_created",
        "purchase_plan_runs",
        ["tenant_id", "created_at"],
    )

    op.create_table(
        "purchase_plan_lines",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_key", sa.BigInteger(), nullable=False),
        sa.Column("supplier_key", sa.BigInteger()),
        sa.Column("priority", sa.String(length=30), nullable=False),
        sa.Column("decision", sa.String(length=30), nullable=False),
        sa.Column("quantity_on_hand", sa.Numeric(18, 4), nullable=False),
        sa.Column("quantity_in_transit", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("daily_forecast", sa.Numeric(18, 6), nullable=False),
        sa.Column("forecast_horizon_days", sa.Integer(), nullable=False),
        sa.Column("base_quantity", sa.Numeric(18, 4), nullable=False),
        sa.Column("recommended_quantity", sa.Numeric(18, 4), nullable=False),
        sa.Column("approved_quantity", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("unit_cost", sa.Numeric(18, 2), nullable=False),
        sa.Column("estimated_value", sa.Numeric(18, 2), nullable=False),
        sa.Column("supplier_score", sa.Numeric(8, 4)),
        sa.Column("confidence", sa.String(length=20), nullable=False),
        sa.Column("explanation", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("note", sa.Text()),
        sa.ForeignKeyConstraint(["plan_id"], ["purchase_plan_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["product_key"], ["dim_product.key"]),
        sa.ForeignKeyConstraint(["supplier_key"], ["dim_contact.key"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id", "product_key", name="uq_purchase_plan_line_product"),
    )
    op.create_index(
        "ix_purchase_plan_lines_plan_decision",
        "purchase_plan_lines",
        ["plan_id", "decision"],
    )

    op.create_table(
        "purchase_plan_orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("supplier_key", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="draft"),
        sa.Column("estimated_value", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("alegra_order_id", sa.String(length=100)),
        sa.Column("response_payload", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["plan_id"], ["purchase_plan_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["supplier_key"], ["dim_contact.key"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id", "supplier_key", name="uq_purchase_plan_order_supplier"),
        sa.UniqueConstraint("tenant_id", "request_hash", name="uq_purchase_plan_order_request"),
    )


def downgrade() -> None:
    op.drop_table("purchase_plan_orders")
    op.drop_index("ix_purchase_plan_lines_plan_decision", table_name="purchase_plan_lines")
    op.drop_table("purchase_plan_lines")
    op.drop_index("ix_purchase_plan_runs_tenant_created", table_name="purchase_plan_runs")
    op.drop_table("purchase_plan_runs")
    op.drop_index("ix_replenishment_forecasts_tenant_date", table_name="replenishment_forecasts")
    op.drop_table("replenishment_forecasts")
    op.drop_index("ix_purchase_order_lines_tenant_id", table_name="purchase_order_lines")
    op.drop_table("purchase_order_lines")
    op.drop_index("ix_purchase_orders_tenant_status", table_name="purchase_orders")
    op.drop_index("ix_purchase_orders_tenant_id", table_name="purchase_orders")
    op.drop_table("purchase_orders")
    op.drop_index("ix_purchase_bills_purchase_order_alegra_id", table_name="purchase_bills")
    op.drop_column("purchase_bills", "purchase_order_alegra_id")
