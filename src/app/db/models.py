import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    resource: Mapped[str] = mapped_column(String(50), nullable=False)
    mode: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    records_read: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_written: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)


class RawAlegraDocument(Base):
    __tablename__ = "raw_alegra_documents"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "entity_type",
            "external_id",
            "payload_hash",
            name="uq_raw_alegra_document_version",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    sync_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sync_runs.id"), index=True)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    external_id: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AlegraEntity(Base):
    """Latest canonical payload for every resource supported by the extractor.

    Immutable versions remain in ``raw_alegra_documents``; this table is the
    convenient current-state projection for resources not yet given a dedicated
    analytical model.
    """

    __tablename__ = "alegra_entities"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "resource", "external_id", name="uq_alegra_entity_tenant_resource_id"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    sync_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sync_runs.id"), index=True)
    resource: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ResourceSyncState(Base):
    """Operational state used to audit each tenant/resource extraction."""

    __tablename__ = "resource_sync_states"
    __table_args__ = (
        UniqueConstraint("tenant_id", "resource", name="uq_resource_sync_state_tenant_resource"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    resource: Mapped[str] = mapped_column(String(50), nullable=False)
    last_full_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SalesInvoice(Base):
    __tablename__ = "sales_invoices"
    __table_args__ = (
        UniqueConstraint("tenant_id", "alegra_id", name="uq_sales_invoice_tenant_alegra"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    alegra_id: Mapped[str] = mapped_column(String(100), nullable=False)
    issue_date: Mapped[date | None] = mapped_column(Date)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="unknown")
    client_alegra_id: Mapped[str | None] = mapped_column(String(100))
    client_name: Mapped[str | None] = mapped_column(String(300))
    seller_alegra_id: Mapped[str | None] = mapped_column(String(100))
    seller_name: Mapped[str | None] = mapped_column(String(300))
    currency_code: Mapped[str | None] = mapped_column(String(10))
    total: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    total_paid: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    balance: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    lines: Mapped[list["SalesInvoiceLine"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan"
    )


class SalesInvoiceLine(Base):
    __tablename__ = "sales_invoice_lines"
    __table_args__ = (
        UniqueConstraint("invoice_id", "line_number", name="uq_sales_invoice_line_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sales_invoices.id"), nullable=False, index=True
    )
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    item_alegra_id: Mapped[str | None] = mapped_column(String(100))
    item_name: Mapped[str] = mapped_column(String(500), nullable=False)
    item_reference: Mapped[str | None] = mapped_column(String(200))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)

    invoice: Mapped[SalesInvoice] = relationship(back_populates="lines")


class SourceProjectionMixin:
    """Common source-audited shape for typed operational projections.

    ``payload`` deliberately remains available because Alegra fields vary by country
    and account configuration. The typed columns are the stable analytical contract.
    """

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    alegra_id: Mapped[str] = mapped_column(String(100), nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Contact(SourceProjectionMixin, Base):
    __tablename__ = "contacts"
    __table_args__ = (UniqueConstraint("tenant_id", "alegra_id", name="uq_contact_tenant_alegra"),)

    name: Mapped[str] = mapped_column(String(300), nullable=False)
    identification: Mapped[str | None] = mapped_column(String(100))
    email: Mapped[str | None] = mapped_column(String(300))
    phone_primary: Mapped[str | None] = mapped_column(String(100))
    mobile: Mapped[str | None] = mapped_column(String(100))
    contact_type: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str | None] = mapped_column(String(30))
    seller_alegra_id: Mapped[str | None] = mapped_column(String(100))
    credit_limit: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))


class CatalogItem(SourceProjectionMixin, Base):
    __tablename__ = "catalog_items"
    __table_args__ = (
        UniqueConstraint("tenant_id", "alegra_id", name="uq_catalog_item_tenant_alegra"),
    )

    name: Mapped[str] = mapped_column(String(500), nullable=False)
    reference: Mapped[str | None] = mapped_column(String(200))
    item_type: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str | None] = mapped_column(String(30))
    inventory_enabled: Mapped[bool | None] = mapped_column(Boolean)
    unit: Mapped[str | None] = mapped_column(String(100))
    base_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))


class Warehouse(SourceProjectionMixin, Base):
    __tablename__ = "warehouses"
    __table_args__ = (
        UniqueConstraint("tenant_id", "alegra_id", name="uq_warehouse_tenant_alegra"),
    )

    name: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str | None] = mapped_column(String(30))
    description: Mapped[str | None] = mapped_column(Text)


class Seller(SourceProjectionMixin, Base):
    __tablename__ = "sellers"
    __table_args__ = (UniqueConstraint("tenant_id", "alegra_id", name="uq_seller_tenant_alegra"),)

    name: Mapped[str] = mapped_column(String(300), nullable=False)
    email: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str | None] = mapped_column(String(30))


class PurchaseBill(SourceProjectionMixin, Base):
    __tablename__ = "purchase_bills"
    __table_args__ = (
        UniqueConstraint("tenant_id", "alegra_id", name="uq_purchase_bill_tenant_alegra"),
    )

    issue_date: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str | None] = mapped_column(String(30))
    document_number: Mapped[str | None] = mapped_column(String(100))
    provider_alegra_id: Mapped[str | None] = mapped_column(String(100))
    provider_name: Mapped[str | None] = mapped_column(String(300))
    currency_code: Mapped[str | None] = mapped_column(String(10))
    total: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    total_paid: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    balance: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))


class Payment(SourceProjectionMixin, Base):
    __tablename__ = "payments"
    __table_args__ = (UniqueConstraint("tenant_id", "alegra_id", name="uq_payment_tenant_alegra"),)

    payment_date: Mapped[date | None] = mapped_column(Date)
    payment_type: Mapped[str | None] = mapped_column(String(30))
    document_number: Mapped[str | None] = mapped_column(String(100))
    contact_alegra_id: Mapped[str | None] = mapped_column(String(100))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    currency_code: Mapped[str | None] = mapped_column(String(10))


class CreditNote(SourceProjectionMixin, Base):
    __tablename__ = "credit_notes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "alegra_id", name="uq_credit_note_tenant_alegra"),
    )

    issue_date: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str | None] = mapped_column(String(30))
    document_number: Mapped[str | None] = mapped_column(String(100))
    client_alegra_id: Mapped[str | None] = mapped_column(String(100))
    warehouse_alegra_id: Mapped[str | None] = mapped_column(String(100))
    currency_code: Mapped[str | None] = mapped_column(String(10))
    total: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))


class InventoryAdjustment(SourceProjectionMixin, Base):
    __tablename__ = "inventory_adjustments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "alegra_id", name="uq_inventory_adjustment_tenant_alegra"),
    )

    adjustment_date: Mapped[date | None] = mapped_column(Date)
    document_number: Mapped[str | None] = mapped_column(String(100))
    warehouse_alegra_id: Mapped[str | None] = mapped_column(String(100))
    observations: Mapped[str | None] = mapped_column(Text)


class WarehouseTransfer(SourceProjectionMixin, Base):
    __tablename__ = "warehouse_transfers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "alegra_id", name="uq_warehouse_transfer_tenant_alegra"),
    )

    transfer_date: Mapped[date | None] = mapped_column(Date)
    document_number: Mapped[str | None] = mapped_column(String(100))
    source_warehouse_alegra_id: Mapped[str | None] = mapped_column(String(100))
    destination_warehouse_alegra_id: Mapped[str | None] = mapped_column(String(100))
    observations: Mapped[str | None] = mapped_column(Text)


class DocumentLineMixin:
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    document_alegra_id: Mapped[str] = mapped_column(String(100), nullable=False)
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    item_alegra_id: Mapped[str | None] = mapped_column(String(100))
    item_name: Mapped[str | None] = mapped_column(String(500))
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    line_total: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)


class PurchaseBillLine(DocumentLineMixin, Base):
    __tablename__ = "purchase_bill_lines"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "document_alegra_id", "line_number", name="uq_purchase_bill_line"
        ),
    )


class CreditNoteLine(DocumentLineMixin, Base):
    __tablename__ = "credit_note_lines"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "document_alegra_id", "line_number", name="uq_credit_note_line"
        ),
    )


class InventoryAdjustmentLine(DocumentLineMixin, Base):
    __tablename__ = "inventory_adjustment_lines"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "document_alegra_id", "line_number", name="uq_inventory_adjustment_line"
        ),
    )


class WarehouseTransferLine(DocumentLineMixin, Base):
    __tablename__ = "warehouse_transfer_lines"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "document_alegra_id", "line_number", name="uq_warehouse_transfer_line"
        ),
    )


class InboundEvent(Base):
    __tablename__ = "inbound_events"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "subject", "payload_hash", name="uq_inbound_event_deduplication"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    subject: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(100), index=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MartRefreshRun(Base):
    """Audit record for a complete, tenant-scoped data mart refresh."""

    __tablename__ = "mart_refresh_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    records_written: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
