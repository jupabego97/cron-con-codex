"""create typed operational projections for multi-resource backfill

Revision ID: 20260726_04
Revises: 20260722_03
Create Date: 2026-07-26
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260726_04"
down_revision = "20260722_03"
branch_labels = None
depends_on = None


def _source_columns() -> list[sa.Column]:
    return [
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("alegra_id", sa.String(length=100), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    ]


def _create_projection(
    name: str, unique_name: str, columns: list[sa.Column], indexes: list[str] | None = None
) -> None:
    op.create_table(
        name,
        *_source_columns(),
        *columns,
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "alegra_id", name=unique_name),
    )
    op.create_index(f"ix_{name}_tenant_id", name, ["tenant_id"])
    for column in indexes or []:
        op.create_index(f"ix_{name}_{column}", name, [column])


def _create_lines(name: str, unique_name: str) -> None:
    op.create_table(
        name,
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_alegra_id", sa.String(length=100), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("item_alegra_id", sa.String(length=100), nullable=True),
        sa.Column("item_name", sa.String(length=500), nullable=True),
        sa.Column("quantity", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("unit_price", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("line_total", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "document_alegra_id", "line_number", name=unique_name),
    )
    op.create_index(f"ix_{name}_tenant_id", name, ["tenant_id"])
    op.create_index(f"ix_{name}_document_alegra_id", name, ["document_alegra_id"])


def upgrade() -> None:
    _create_projection(
        "contacts",
        "uq_contact_tenant_alegra",
        [
            sa.Column("name", sa.String(length=300), nullable=False),
            sa.Column("identification", sa.String(length=100)),
            sa.Column("email", sa.String(length=300)),
            sa.Column("phone_primary", sa.String(length=100)),
            sa.Column("mobile", sa.String(length=100)),
            sa.Column("contact_type", sa.String(length=50)),
            sa.Column("status", sa.String(length=30)),
            sa.Column("seller_alegra_id", sa.String(length=100)),
            sa.Column("credit_limit", sa.Numeric(precision=18, scale=2)),
        ],
        ["identification", "email"],
    )
    _create_projection(
        "catalog_items",
        "uq_catalog_item_tenant_alegra",
        [
            sa.Column("name", sa.String(length=500), nullable=False),
            sa.Column("reference", sa.String(length=200)),
            sa.Column("item_type", sa.String(length=50)),
            sa.Column("status", sa.String(length=30)),
            sa.Column("inventory_enabled", sa.Boolean()),
            sa.Column("unit", sa.String(length=100)),
            sa.Column("base_price", sa.Numeric(precision=18, scale=2)),
            sa.Column("cost", sa.Numeric(precision=18, scale=2)),
        ],
        ["reference", "status"],
    )
    _create_projection(
        "warehouses",
        "uq_warehouse_tenant_alegra",
        [
            sa.Column("name", sa.String(length=300), nullable=False),
            sa.Column("status", sa.String(length=30)),
            sa.Column("description", sa.Text()),
        ],
        ["status"],
    )
    _create_projection(
        "sellers",
        "uq_seller_tenant_alegra",
        [
            sa.Column("name", sa.String(length=300), nullable=False),
            sa.Column("email", sa.String(length=300)),
            sa.Column("status", sa.String(length=30)),
        ],
        ["email", "status"],
    )
    _create_projection(
        "purchase_bills",
        "uq_purchase_bill_tenant_alegra",
        [
            sa.Column("issue_date", sa.Date()),
            sa.Column("due_date", sa.Date()),
            sa.Column("status", sa.String(length=30)),
            sa.Column("document_number", sa.String(length=100)),
            sa.Column("provider_alegra_id", sa.String(length=100)),
            sa.Column("provider_name", sa.String(length=300)),
            sa.Column("currency_code", sa.String(length=10)),
            sa.Column("total", sa.Numeric(precision=18, scale=2)),
            sa.Column("total_paid", sa.Numeric(precision=18, scale=2)),
            sa.Column("balance", sa.Numeric(precision=18, scale=2)),
        ],
        ["issue_date", "provider_alegra_id", "status"],
    )
    _create_projection(
        "payments",
        "uq_payment_tenant_alegra",
        [
            sa.Column("payment_date", sa.Date()),
            sa.Column("payment_type", sa.String(length=30)),
            sa.Column("document_number", sa.String(length=100)),
            sa.Column("contact_alegra_id", sa.String(length=100)),
            sa.Column("amount", sa.Numeric(precision=18, scale=2)),
            sa.Column("currency_code", sa.String(length=10)),
        ],
        ["payment_date", "contact_alegra_id", "payment_type"],
    )
    _create_projection(
        "credit_notes",
        "uq_credit_note_tenant_alegra",
        [
            sa.Column("issue_date", sa.Date()),
            sa.Column("due_date", sa.Date()),
            sa.Column("status", sa.String(length=30)),
            sa.Column("document_number", sa.String(length=100)),
            sa.Column("client_alegra_id", sa.String(length=100)),
            sa.Column("warehouse_alegra_id", sa.String(length=100)),
            sa.Column("currency_code", sa.String(length=10)),
            sa.Column("total", sa.Numeric(precision=18, scale=2)),
        ],
        ["issue_date", "client_alegra_id", "status"],
    )
    _create_projection(
        "inventory_adjustments",
        "uq_inventory_adjustment_tenant_alegra",
        [
            sa.Column("adjustment_date", sa.Date()),
            sa.Column("document_number", sa.String(length=100)),
            sa.Column("warehouse_alegra_id", sa.String(length=100)),
            sa.Column("observations", sa.Text()),
        ],
        ["adjustment_date", "warehouse_alegra_id"],
    )
    _create_projection(
        "warehouse_transfers",
        "uq_warehouse_transfer_tenant_alegra",
        [
            sa.Column("transfer_date", sa.Date()),
            sa.Column("document_number", sa.String(length=100)),
            sa.Column("source_warehouse_alegra_id", sa.String(length=100)),
            sa.Column("destination_warehouse_alegra_id", sa.String(length=100)),
            sa.Column("observations", sa.Text()),
        ],
        ["transfer_date", "source_warehouse_alegra_id", "destination_warehouse_alegra_id"],
    )

    _create_lines("purchase_bill_lines", "uq_purchase_bill_line")
    _create_lines("credit_note_lines", "uq_credit_note_line")
    _create_lines("inventory_adjustment_lines", "uq_inventory_adjustment_line")
    _create_lines("warehouse_transfer_lines", "uq_warehouse_transfer_line")


def downgrade() -> None:
    for name in (
        "warehouse_transfer_lines",
        "inventory_adjustment_lines",
        "credit_note_lines",
        "purchase_bill_lines",
    ):
        op.drop_index(f"ix_{name}_document_alegra_id", table_name=name)
        op.drop_index(f"ix_{name}_tenant_id", table_name=name)
        op.drop_table(name)

    projection_indexes = {
        "contacts": ["identification", "email"],
        "catalog_items": ["reference", "status"],
        "warehouses": ["status"],
        "sellers": ["email", "status"],
        "purchase_bills": ["issue_date", "provider_alegra_id", "status"],
        "payments": ["payment_date", "contact_alegra_id", "payment_type"],
        "credit_notes": ["issue_date", "client_alegra_id", "status"],
        "inventory_adjustments": ["adjustment_date", "warehouse_alegra_id"],
        "warehouse_transfers": [
            "transfer_date",
            "source_warehouse_alegra_id",
            "destination_warehouse_alegra_id",
        ],
    }
    for name, indexes in projection_indexes.items():
        for column in indexes:
            op.drop_index(f"ix_{name}_{column}", table_name=name)
        op.drop_index(f"ix_{name}_tenant_id", table_name=name)
        op.drop_table(name)
