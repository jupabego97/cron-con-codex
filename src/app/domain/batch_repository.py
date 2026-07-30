"""PostgreSQL batch persistence for historical Alegra imports.

The extractor keeps the raw and current generic layers, but persists selected
operational projections in the same transaction. This makes a re-run safe while
avoiding ORM round trips for every document.
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models import (
    AlegraEntity,
    CatalogItem,
    Contact,
    CreditNote,
    CreditNoteLine,
    InventoryAdjustment,
    InventoryAdjustmentLine,
    Payment,
    PurchaseBill,
    PurchaseBillLine,
    RawAlegraDocument,
    SalesInvoice,
    SalesInvoiceLine,
    Seller,
    Warehouse,
    WarehouseTransfer,
    WarehouseTransferLine,
)
from app.domain.invoices import canonical_payload_hash, normalize_invoice


@dataclass(frozen=True)
class BatchWriteResult:
    records_written: int
    raw_versions_inserted: int


ProjectionModel = (
    type[Contact]
    | type[CatalogItem]
    | type[Warehouse]
    | type[Seller]
    | type[PurchaseBill]
    | type[Payment]
    | type[CreditNote]
    | type[InventoryAdjustment]
    | type[WarehouseTransfer]
)
LineModel = (
    type[PurchaseBillLine]
    | type[CreditNoteLine]
    | type[InventoryAdjustmentLine]
    | type[WarehouseTransferLine]
)


_PROJECTIONS: dict[str, tuple[ProjectionModel, str]] = {
    "contact": (Contact, "uq_contact_tenant_alegra"),
    "item": (CatalogItem, "uq_catalog_item_tenant_alegra"),
    "warehouse": (Warehouse, "uq_warehouse_tenant_alegra"),
    "seller": (Seller, "uq_seller_tenant_alegra"),
    "bill": (PurchaseBill, "uq_purchase_bill_tenant_alegra"),
    "payment": (Payment, "uq_payment_tenant_alegra"),
    "credit_note": (CreditNote, "uq_credit_note_tenant_alegra"),
    "inventory_adjustment": (InventoryAdjustment, "uq_inventory_adjustment_tenant_alegra"),
    "warehouse_transfer": (WarehouseTransfer, "uq_warehouse_transfer_tenant_alegra"),
}
_LINE_PROJECTIONS: dict[str, tuple[LineModel, str]] = {
    "bill": (PurchaseBillLine, "uq_purchase_bill_line"),
    "credit_note": (CreditNoteLine, "uq_credit_note_line"),
    "inventory_adjustment": (InventoryAdjustmentLine, "uq_inventory_adjustment_line"),
    "warehouse_transfer": (WarehouseTransferLine, "uq_warehouse_transfer_line"),
}


def persist_resource_batch(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    resource: str,
    payloads: Iterable[dict[str, Any]],
    sync_run_id: uuid.UUID | None = None,
) -> BatchWriteResult:
    """Store a batch with one database round trip per layer, not per record."""
    records_by_id = {_external_id(payload, resource): payload for payload in payloads}
    records = list(records_by_id.values())
    if not records:
        return BatchWriteResult(records_written=0, raw_versions_inserted=0)

    prepared = [_prepare_record(payload, resource) for payload in records]
    raw_versions_inserted = _upsert_raw_versions(
        session, tenant_id=tenant_id, resource=resource, records=prepared, sync_run_id=sync_run_id
    )
    _upsert_current_entities(
        session, tenant_id=tenant_id, resource=resource, records=prepared, sync_run_id=sync_run_id
    )

    if resource == "invoice":
        _upsert_sales_invoices(session, tenant_id=tenant_id, records=records)
    elif resource in _PROJECTIONS:
        _upsert_projection(
            session,
            tenant_id=tenant_id,
            resource=resource,
            records=prepared,
        )
        _replace_document_lines(
            session,
            tenant_id=tenant_id,
            resource=resource,
            records=records,
        )

    return BatchWriteResult(
        records_written=len(records), raw_versions_inserted=raw_versions_inserted
    )


def rebuild_purchase_bill_lines(
    session: Session, *, tenant_id: uuid.UUID, write_batch_size: int = 200
) -> tuple[int, int]:
    """Rebuild bill lines from durable canonical payloads without calling Alegra.

    Alegra returns bill details under ``purchases.items`` rather than the
    ``items`` field used by invoice-like documents. This repair is safe to run
    repeatedly because each batch replaces lines for its own source documents.
    """
    if write_batch_size < 1:
        raise ValueError("write_batch_size must be positive")
    records: list[dict[str, Any]] = []
    documents_processed = 0
    lines_written = 0
    statement = (
        select(PurchaseBill.payload)
        .where(PurchaseBill.tenant_id == tenant_id)
        .order_by(PurchaseBill.alegra_id)
    )
    for payload in session.scalars(statement).yield_per(write_batch_size):
        if not isinstance(payload, dict):
            continue
        records.append(payload)
        if len(records) >= write_batch_size:
            documents, lines = _rebuild_purchase_line_batch(session, tenant_id, records)
            documents_processed += documents
            lines_written += lines
            records = []
    if records:
        documents, lines = _rebuild_purchase_line_batch(session, tenant_id, records)
        documents_processed += documents
        lines_written += lines
    return documents_processed, lines_written


def _rebuild_purchase_line_batch(
    session: Session, tenant_id: uuid.UUID, records: list[dict[str, Any]]
) -> tuple[int, int]:
    lines = sum(len(_document_items(payload) or []) for payload in records)
    _replace_document_lines(session, tenant_id=tenant_id, resource="bill", records=records)
    session.commit()
    return len(records), lines


def _prepare_record(payload: dict[str, Any], resource: str) -> dict[str, Any]:
    external_id = _external_id(payload, resource)
    return {
        "external_id": external_id,
        "payload": payload,
        "source_hash": canonical_payload_hash(payload),
    }


def _upsert_raw_versions(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    resource: str,
    records: list[dict[str, Any]],
    sync_run_id: uuid.UUID | None,
) -> int:
    values = [
        {
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "sync_run_id": sync_run_id,
            "entity_type": resource,
            "external_id": record["external_id"],
            "payload": record["payload"],
            "payload_hash": record["source_hash"],
        }
        for record in records
    ]
    statement = (
        pg_insert(RawAlegraDocument)
        .values(values)
        .on_conflict_do_nothing(constraint="uq_raw_alegra_document_version")
    )
    result = session.execute(statement)
    return max(int(result.rowcount or 0), 0)


def _upsert_current_entities(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    resource: str,
    records: list[dict[str, Any]],
    sync_run_id: uuid.UUID | None,
) -> None:
    values = [
        {
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "sync_run_id": sync_run_id,
            "resource": resource,
            "external_id": record["external_id"],
            "payload": record["payload"],
            "source_hash": record["source_hash"],
            "is_deleted": False,
        }
        for record in records
    ]
    statement = pg_insert(AlegraEntity).values(values)
    excluded = statement.excluded
    session.execute(
        statement.on_conflict_do_update(
            constraint="uq_alegra_entity_tenant_resource_id",
            set_={
                "sync_run_id": excluded.sync_run_id,
                "payload": excluded.payload,
                "source_hash": excluded.source_hash,
                "is_deleted": False,
                "last_seen_at": func.now(),
            },
        )
    )


def _upsert_projection(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    resource: str,
    records: list[dict[str, Any]],
) -> None:
    model, constraint = _PROJECTIONS[resource]
    values = [
        {
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "alegra_id": record["external_id"],
            "source_hash": record["source_hash"],
            "payload": record["payload"],
            "is_deleted": False,
            **_projection_fields(resource, record["payload"], record["external_id"]),
        }
        for record in records
    ]
    statement = pg_insert(model).values(values)
    excluded = statement.excluded
    update_columns = {
        key: getattr(excluded, key)
        for key in values[0]
        if key not in {"id", "tenant_id", "alegra_id", "created_at"}
    }
    update_columns["updated_at"] = func.now()
    session.execute(statement.on_conflict_do_update(constraint=constraint, set_=update_columns))


def mark_resource_projection_deleted(
    session: Session, *, tenant_id: uuid.UUID, resource: str, external_id: str
) -> bool:
    """Soft-delete the generic and typed current-state projections for a webhook deletion."""
    entity_result = session.execute(
        update(AlegraEntity)
        .where(
            AlegraEntity.tenant_id == tenant_id,
            AlegraEntity.resource == resource,
            AlegraEntity.external_id == external_id,
        )
        .values(is_deleted=True, last_seen_at=func.now())
    )
    if resource == "invoice":
        session.execute(
            update(SalesInvoice)
            .where(SalesInvoice.tenant_id == tenant_id, SalesInvoice.alegra_id == external_id)
            .values(is_deleted=True, updated_at=func.now())
        )
    elif resource in _PROJECTIONS:
        model, _ = _PROJECTIONS[resource]
        session.execute(
            update(model)
            .where(model.tenant_id == tenant_id, model.alegra_id == external_id)
            .values(is_deleted=True, updated_at=func.now())
        )
    return bool(entity_result.rowcount)


def _replace_document_lines(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    resource: str,
    records: list[dict[str, Any]],
) -> None:
    projection = _LINE_PROJECTIONS.get(resource)
    if projection is None:
        return
    model, constraint = projection
    documents_with_lines = [
        (_external_id(payload, resource), _document_items(payload)) for payload in records
    ]
    documents_with_lines = [pair for pair in documents_with_lines if pair[1] is not None]
    if not documents_with_lines:
        return

    document_ids = [document_id for document_id, _ in documents_with_lines]
    session.execute(
        delete(model).where(
            model.tenant_id == tenant_id,
            model.document_alegra_id.in_(document_ids),
        )
    )
    values = [
        _line_value(
            tenant_id=tenant_id,
            document_alegra_id=document_id,
            line_number=line_number,
            payload=item,
        )
        for document_id, items in documents_with_lines
        for line_number, item in enumerate(items, start=1)
    ]
    if not values:
        return
    statement = pg_insert(model).values(values)
    excluded = statement.excluded
    session.execute(
        statement.on_conflict_do_update(
            constraint=constraint,
            set_={
                "item_alegra_id": excluded.item_alegra_id,
                "item_name": excluded.item_name,
                "quantity": excluded.quantity,
                "unit_price": excluded.unit_price,
                "line_total": excluded.line_total,
                "payload": excluded.payload,
            },
        )
    )


def _upsert_sales_invoices(
    session: Session, *, tenant_id: uuid.UUID, records: list[dict[str, Any]]
) -> None:
    normalized = [normalize_invoice(payload) for payload in records]
    values = [
        {
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "alegra_id": invoice.alegra_id,
            "issue_date": invoice.issue_date,
            "issued_at": invoice.issued_at,
            "status": invoice.status,
            "client_alegra_id": invoice.client_alegra_id,
            "client_name": invoice.client_name,
            "seller_alegra_id": invoice.seller_alegra_id,
            "seller_name": invoice.seller_name,
            "currency_code": invoice.currency_code,
            "total": invoice.total,
            "total_paid": invoice.total_paid,
            "balance": invoice.balance,
            "source_hash": invoice.source_hash,
            "is_deleted": False,
        }
        for invoice in normalized
    ]
    statement = pg_insert(SalesInvoice).values(values)
    excluded = statement.excluded
    session.execute(
        statement.on_conflict_do_update(
            constraint="uq_sales_invoice_tenant_alegra",
            set_={
                key: getattr(excluded, key)
                for key in values[0]
                if key not in {"id", "tenant_id", "alegra_id", "created_at"}
            }
            | {"updated_at": func.now()},
        )
    )
    invoice_ids = dict(
        session.execute(
            select(SalesInvoice.alegra_id, SalesInvoice.id).where(
                SalesInvoice.tenant_id == tenant_id,
                SalesInvoice.alegra_id.in_([invoice.alegra_id for invoice in normalized]),
            )
        ).all()
    )
    session.execute(
        delete(SalesInvoiceLine).where(SalesInvoiceLine.invoice_id.in_(invoice_ids.values()))
    )
    line_values = [
        {
            "id": uuid.uuid4(),
            "invoice_id": invoice_ids[invoice.alegra_id],
            "line_number": line.line_number,
            "item_alegra_id": line.item_alegra_id,
            "item_name": line.item_name,
            "item_reference": line.item_reference,
            "quantity": line.quantity,
            "unit_price": line.unit_price,
            "line_total": line.line_total,
        }
        for invoice in normalized
        for line in invoice.lines
    ]
    if line_values:
        session.execute(pg_insert(SalesInvoiceLine).values(line_values))


def _projection_fields(resource: str, payload: dict[str, Any], external_id: str) -> dict[str, Any]:
    if resource == "contact":
        return {
            "name": _text(payload.get("name")) or external_id,
            "identification": _text(payload.get("identification")),
            "email": _text(payload.get("email")),
            "phone_primary": _text(payload.get("phonePrimary")),
            "mobile": _text(payload.get("mobile")),
            "contact_type": _text(payload.get("type")),
            "status": _text(payload.get("status")),
            "seller_alegra_id": _object_id(payload.get("seller")),
            "credit_limit": _decimal(payload.get("creditLimit")),
        }
    if resource == "item":
        inventory = payload.get("inventory")
        return {
            "name": _text(payload.get("name")) or external_id,
            "reference": _reference(payload.get("reference")),
            "item_type": _text(payload.get("type")),
            "status": _text(payload.get("status")),
            "inventory_enabled": _bool(payload.get("inventariable"), inventory),
            "unit": _unit(payload.get("unit")),
            "base_price": _decimal(payload.get("price")),
            "cost": _decimal(payload.get("cost")),
        }
    if resource == "warehouse":
        return {
            "name": _text(payload.get("name")) or external_id,
            "status": _text(payload.get("status")),
            "description": _text(payload.get("description")),
        }
    if resource == "seller":
        return {
            "name": _text(payload.get("name")) or external_id,
            "email": _text(payload.get("email")),
            "status": _text(payload.get("status")),
        }
    if resource == "bill":
        provider = payload.get("provider") or payload.get("supplier") or payload.get("client")
        return {
            "issue_date": _date(payload.get("date")),
            "due_date": _date(payload.get("dueDate")),
            "status": _text(payload.get("status")),
            "document_number": _document_number(payload),
            "provider_alegra_id": _object_id(provider),
            "provider_name": _object_name(provider),
            "currency_code": _currency_code(payload.get("currency")),
            "total": _decimal(payload.get("total")),
            "total_paid": _decimal(payload.get("totalPaid")),
            "balance": _decimal(payload.get("balance")),
        }
    if resource == "payment":
        return {
            "payment_date": _date(payload.get("date")),
            "payment_type": _text(payload.get("type")),
            "document_number": _text(payload.get("number")),
            "contact_alegra_id": _object_id(payload.get("client") or payload.get("contact")),
            "amount": _decimal(payload.get("amount") or payload.get("total")),
            "currency_code": _currency_code(payload.get("currency")),
        }
    if resource == "credit_note":
        return {
            "issue_date": _date(payload.get("date")),
            "due_date": _date(payload.get("dueDate")),
            "status": _text(payload.get("status")),
            "document_number": _document_number(payload),
            "client_alegra_id": _object_id(payload.get("client")),
            "warehouse_alegra_id": _object_id(payload.get("warehouse")),
            "currency_code": _currency_code(payload.get("currency")),
            "total": _decimal(payload.get("total")),
        }
    if resource == "inventory_adjustment":
        return {
            "adjustment_date": _date(payload.get("date")),
            "document_number": _document_number(payload),
            "warehouse_alegra_id": _object_id(payload.get("warehouse")),
            "observations": _text(payload.get("observations")),
        }
    if resource == "warehouse_transfer":
        return {
            "transfer_date": _date(payload.get("date")),
            "document_number": _document_number(payload),
            "source_warehouse_alegra_id": _object_id(
                payload.get("sourceWarehouse") or payload.get("originWarehouse")
            ),
            "destination_warehouse_alegra_id": _object_id(
                payload.get("destinationWarehouse") or payload.get("targetWarehouse")
            ),
            "observations": _text(payload.get("observations")),
        }
    raise ValueError(f"No projection registered for resource {resource}")


def _line_value(
    *, tenant_id: uuid.UUID, document_alegra_id: str, line_number: int, payload: dict[str, Any]
) -> dict[str, Any]:
    item = payload.get("item") if isinstance(payload.get("item"), dict) else payload
    quantity = _decimal(payload.get("quantity"))
    unit_price = _decimal(payload.get("price") or payload.get("unitPrice"))
    line_total = _decimal(payload.get("total") or payload.get("amount"))
    if line_total is None and quantity is not None and unit_price is not None:
        line_total = quantity * unit_price
    return {
        "id": uuid.uuid4(),
        "tenant_id": tenant_id,
        "document_alegra_id": document_alegra_id,
        "line_number": line_number,
        "item_alegra_id": _object_id(item),
        "item_name": _text(payload.get("name")) or _object_name(item),
        "quantity": quantity,
        "unit_price": unit_price,
        "line_total": line_total,
        "payload": payload,
    }


def _document_items(payload: dict[str, Any]) -> list[dict[str, Any]] | None:
    items = payload.get("items")
    if not isinstance(items, list):
        purchases = payload.get("purchases")
        if isinstance(purchases, dict):
            items = purchases.get("items")
    if not isinstance(items, list):
        return None
    return [item for item in items if isinstance(item, dict)]


def _external_id(payload: dict[str, Any], resource: str) -> str:
    external_id = payload.get("id")
    if external_id is None:
        raise ValueError(f"Alegra {resource} payload does not contain an id")
    return str(external_id)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, (int, float, Decimal)):
        return str(value)
    return None


def _object_id(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("id")
    return _text(value)


def _object_name(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    return _text(value.get("name"))


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, dict):
        value = value.get("amount") or value.get("value")
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _date(value: Any) -> date | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _bool(value: Any, fallback: Any = None) -> bool | None:
    candidate = value if value is not None else fallback
    if isinstance(candidate, dict):
        candidate = candidate.get("enabled") or candidate.get("inventory")
    if isinstance(candidate, bool):
        return candidate
    if isinstance(candidate, str):
        if candidate.lower() in {"true", "1", "yes"}:
            return True
        if candidate.lower() in {"false", "0", "no"}:
            return False
    return None


def _reference(value: Any) -> str | None:
    if isinstance(value, dict):
        return _text(value.get("value") or value.get("code"))
    return _text(value)


def _unit(value: Any) -> str | None:
    if isinstance(value, dict):
        return _text(value.get("name") or value.get("id"))
    return _text(value)


def _currency_code(value: Any) -> str | None:
    if isinstance(value, dict):
        return _text(value.get("code"))
    return _text(value)


def _document_number(payload: dict[str, Any]) -> str | None:
    template = payload.get("numberTemplate")
    if isinstance(template, dict):
        return _text(template.get("fullNumber") or template.get("number"))
    return _text(payload.get("number"))
