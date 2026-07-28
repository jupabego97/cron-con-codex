"""create the operationally-derived analytics data mart

Revision ID: 20260728_05
Revises: 20260726_04
Create Date: 2026-07-28
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "20260728_05"
down_revision = "20260726_04"
branch_labels = None
depends_on = None


def _key() -> sa.Column:
    return sa.Column("key", sa.BigInteger(), sa.Identity(), primary_key=True, nullable=False)


def _dimension_columns() -> list[sa.Column]:
    return [
        _key(),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("alegra_id", sa.String(length=100), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    ]


def _create_dimension(name: str, columns: list[sa.Column]) -> None:
    op.create_table(
        name,
        *_dimension_columns(),
        *columns,
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.UniqueConstraint("tenant_id", "alegra_id", name=f"uq_{name}_tenant_alegra"),
    )
    op.create_index(f"ix_{name}_tenant_alegra", name, ["tenant_id", "alegra_id"])


def upgrade() -> None:
    op.create_table(
        "mart_refresh_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="running"),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("records_written", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
    )
    op.create_index("ix_mart_refresh_runs_tenant_started", "mart_refresh_runs", ["tenant_id", "started_at"])

    op.create_table(
        "dim_date",
        sa.Column("date_key", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("calendar_date", sa.Date(), nullable=False, unique=True),
        sa.Column("year", sa.SmallInteger(), nullable=False),
        sa.Column("quarter", sa.SmallInteger(), nullable=False),
        sa.Column("month", sa.SmallInteger(), nullable=False),
        sa.Column("day", sa.SmallInteger(), nullable=False),
        sa.Column("iso_week", sa.SmallInteger(), nullable=False),
        sa.Column("day_of_week", sa.SmallInteger(), nullable=False),
        sa.Column("is_weekend", sa.Boolean(), nullable=False),
    )

    op.create_table(
        "dim_tenant",
        _key(),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
    )
    op.create_index("ix_dim_tenant_tenant_id", "dim_tenant", ["tenant_id"])

    _create_dimension(
        "dim_product",
        [
            sa.Column("name", sa.String(length=500), nullable=False),
            sa.Column("reference", sa.String(length=200)),
            sa.Column("item_type", sa.String(length=50)),
            sa.Column("status", sa.String(length=30)),
            sa.Column("inventory_enabled", sa.Boolean()),
            sa.Column("unit", sa.String(length=100)),
            sa.Column("base_price", sa.Numeric(precision=18, scale=2)),
            sa.Column("current_cost", sa.Numeric(precision=18, scale=2)),
        ],
    )
    _create_dimension(
        "dim_contact",
        [
            sa.Column("name", sa.String(length=300), nullable=False),
            sa.Column("identification", sa.String(length=100)),
            sa.Column("email", sa.String(length=300)),
            sa.Column("contact_type", sa.String(length=50)),
            sa.Column("status", sa.String(length=30)),
        ],
    )
    _create_dimension(
        "dim_seller",
        [
            sa.Column("name", sa.String(length=300), nullable=False),
            sa.Column("email", sa.String(length=300)),
            sa.Column("status", sa.String(length=30)),
        ],
    )
    _create_dimension(
        "dim_warehouse",
        [
            sa.Column("name", sa.String(length=300), nullable=False),
            sa.Column("status", sa.String(length=30)),
            sa.Column("description", sa.Text()),
        ],
    )

    op.create_table(
        "fact_sales_line",
        _key(),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_key", sa.BigInteger(), nullable=False),
        sa.Column("date_key", sa.Integer()),
        sa.Column("product_key", sa.BigInteger()),
        sa.Column("contact_key", sa.BigInteger()),
        sa.Column("seller_key", sa.BigInteger()),
        sa.Column("warehouse_key", sa.BigInteger()),
        sa.Column("document_type", sa.String(length=30), nullable=False),
        sa.Column("document_alegra_id", sa.String(length=100), nullable=False),
        sa.Column("document_number", sa.String(length=100)),
        sa.Column("document_status", sa.String(length=30)),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("currency_code", sa.String(length=10)),
        sa.Column("quantity", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("unit_price", sa.Numeric(precision=18, scale=2)),
        sa.Column("discount_amount", sa.Numeric(precision=18, scale=2), nullable=False, server_default="0"),
        sa.Column("tax_amount", sa.Numeric(precision=18, scale=2), nullable=False, server_default="0"),
        sa.Column("net_sales_amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("unit_cost", sa.Numeric(precision=18, scale=2)),
        sa.Column("margin_amount", sa.Numeric(precision=18, scale=2)),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["tenant_key"], ["dim_tenant.key"]),
        sa.ForeignKeyConstraint(["date_key"], ["dim_date.date_key"]),
        sa.ForeignKeyConstraint(["product_key"], ["dim_product.key"]),
        sa.ForeignKeyConstraint(["contact_key"], ["dim_contact.key"]),
        sa.ForeignKeyConstraint(["seller_key"], ["dim_seller.key"]),
        sa.ForeignKeyConstraint(["warehouse_key"], ["dim_warehouse.key"]),
        sa.UniqueConstraint("tenant_id", "document_type", "document_alegra_id", "line_number", name="uq_fact_sales_line_source"),
    )
    op.create_index("ix_fact_sales_line_tenant_date", "fact_sales_line", ["tenant_id", "date_key"])
    op.create_index("ix_fact_sales_line_dimensions", "fact_sales_line", ["product_key", "contact_key", "seller_key", "warehouse_key"])

    op.create_table(
        "fact_purchase_line",
        _key(),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_key", sa.BigInteger(), nullable=False),
        sa.Column("date_key", sa.Integer()),
        sa.Column("product_key", sa.BigInteger()),
        sa.Column("provider_key", sa.BigInteger()),
        sa.Column("warehouse_key", sa.BigInteger()),
        sa.Column("document_alegra_id", sa.String(length=100), nullable=False),
        sa.Column("document_number", sa.String(length=100)),
        sa.Column("document_status", sa.String(length=30)),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("currency_code", sa.String(length=10)),
        sa.Column("quantity", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("unit_cost", sa.Numeric(precision=18, scale=2)),
        sa.Column("purchase_amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["tenant_key"], ["dim_tenant.key"]),
        sa.ForeignKeyConstraint(["date_key"], ["dim_date.date_key"]),
        sa.ForeignKeyConstraint(["product_key"], ["dim_product.key"]),
        sa.ForeignKeyConstraint(["provider_key"], ["dim_contact.key"]),
        sa.ForeignKeyConstraint(["warehouse_key"], ["dim_warehouse.key"]),
        sa.UniqueConstraint("tenant_id", "document_alegra_id", "line_number", name="uq_fact_purchase_line_source"),
    )
    op.create_index("ix_fact_purchase_line_tenant_date", "fact_purchase_line", ["tenant_id", "date_key"])
    op.create_index("ix_fact_purchase_line_dimensions", "fact_purchase_line", ["product_key", "provider_key", "warehouse_key"])

    op.create_table(
        "fact_payment",
        _key(),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_key", sa.BigInteger(), nullable=False),
        sa.Column("date_key", sa.Integer()),
        sa.Column("contact_key", sa.BigInteger()),
        sa.Column("payment_alegra_id", sa.String(length=100), nullable=False),
        sa.Column("payment_type", sa.String(length=30)),
        sa.Column("document_number", sa.String(length=100)),
        sa.Column("currency_code", sa.String(length=10)),
        sa.Column("amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["tenant_key"], ["dim_tenant.key"]),
        sa.ForeignKeyConstraint(["date_key"], ["dim_date.date_key"]),
        sa.ForeignKeyConstraint(["contact_key"], ["dim_contact.key"]),
        sa.UniqueConstraint("tenant_id", "payment_alegra_id", name="uq_fact_payment_source"),
    )
    op.create_index("ix_fact_payment_tenant_date", "fact_payment", ["tenant_id", "date_key"])
    op.create_index("ix_fact_payment_contact", "fact_payment", ["contact_key"])

    op.create_table(
        "fact_inventory_movement",
        _key(),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_key", sa.BigInteger(), nullable=False),
        sa.Column("date_key", sa.Integer()),
        sa.Column("product_key", sa.BigInteger()),
        sa.Column("warehouse_key", sa.BigInteger()),
        sa.Column("source_warehouse_key", sa.BigInteger()),
        sa.Column("destination_warehouse_key", sa.BigInteger()),
        sa.Column("document_type", sa.String(length=30), nullable=False),
        sa.Column("document_alegra_id", sa.String(length=100), nullable=False),
        sa.Column("document_number", sa.String(length=100)),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("movement_direction", sa.String(length=30), nullable=False),
        sa.Column("quantity_delta", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("unit_cost", sa.Numeric(precision=18, scale=2)),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["tenant_key"], ["dim_tenant.key"]),
        sa.ForeignKeyConstraint(["date_key"], ["dim_date.date_key"]),
        sa.ForeignKeyConstraint(["product_key"], ["dim_product.key"]),
        sa.ForeignKeyConstraint(["warehouse_key"], ["dim_warehouse.key"]),
        sa.ForeignKeyConstraint(["source_warehouse_key"], ["dim_warehouse.key"]),
        sa.ForeignKeyConstraint(["destination_warehouse_key"], ["dim_warehouse.key"]),
        sa.UniqueConstraint("tenant_id", "document_type", "document_alegra_id", "line_number", "movement_direction", name="uq_fact_inventory_movement_source"),
    )
    op.create_index("ix_fact_inventory_movement_tenant_date", "fact_inventory_movement", ["tenant_id", "date_key"])
    op.create_index("ix_fact_inventory_movement_dimensions", "fact_inventory_movement", ["product_key", "warehouse_key"])


def downgrade() -> None:
    for name, indexes in (
        ("fact_inventory_movement", ["ix_fact_inventory_movement_dimensions", "ix_fact_inventory_movement_tenant_date"]),
        ("fact_payment", ["ix_fact_payment_contact", "ix_fact_payment_tenant_date"]),
        ("fact_purchase_line", ["ix_fact_purchase_line_dimensions", "ix_fact_purchase_line_tenant_date"]),
        ("fact_sales_line", ["ix_fact_sales_line_dimensions", "ix_fact_sales_line_tenant_date"]),
    ):
        for index in indexes:
            op.drop_index(index, table_name=name)
        op.drop_table(name)
    for name in ("dim_warehouse", "dim_seller", "dim_contact", "dim_product"):
        op.drop_index(f"ix_{name}_tenant_alegra", table_name=name)
        op.drop_table(name)
    op.drop_index("ix_dim_tenant_tenant_id", table_name="dim_tenant")
    op.drop_table("dim_tenant")
    op.drop_table("dim_date")
    op.drop_index("ix_mart_refresh_runs_tenant_started", table_name="mart_refresh_runs")
    op.drop_table("mart_refresh_runs")
