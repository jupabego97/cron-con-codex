import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import RawAlegraDocument, SalesInvoice, SalesInvoiceLine
from app.domain.invoices import NormalizedInvoice, normalize_invoice


def upsert_invoice(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    payload: dict[str, Any],
    sync_run_id: uuid.UUID | None = None,
) -> tuple[SalesInvoice, bool]:
    """Persist one invoice and its immutable raw version idempotently in the current transaction."""
    invoice_data = normalize_invoice(payload)
    raw_version = session.scalar(
        select(RawAlegraDocument).where(
            RawAlegraDocument.tenant_id == tenant_id,
            RawAlegraDocument.entity_type == "invoice",
            RawAlegraDocument.external_id == invoice_data.alegra_id,
            RawAlegraDocument.payload_hash == invoice_data.source_hash,
        )
    )
    if raw_version is None:
        session.add(
            RawAlegraDocument(
                tenant_id=tenant_id,
                sync_run_id=sync_run_id,
                entity_type="invoice",
                external_id=invoice_data.alegra_id,
                payload=payload,
                payload_hash=invoice_data.source_hash,
            )
        )

    invoice = session.scalar(
        select(SalesInvoice).where(
            SalesInvoice.tenant_id == tenant_id,
            SalesInvoice.alegra_id == invoice_data.alegra_id,
        )
    )
    is_new = invoice is None
    if invoice is None:
        invoice = SalesInvoice(
            tenant_id=tenant_id, alegra_id=invoice_data.alegra_id, source_hash=""
        )
        session.add(invoice)

    _apply_invoice(invoice, invoice_data)
    invoice.lines.clear()
    invoice.lines.extend(
        SalesInvoiceLine(
            line_number=line.line_number,
            item_alegra_id=line.item_alegra_id,
            item_name=line.item_name,
            item_reference=line.item_reference,
            quantity=line.quantity,
            unit_price=line.unit_price,
            line_total=line.line_total,
        )
        for line in invoice_data.lines
    )
    return invoice, is_new


def mark_invoice_deleted(session: Session, *, tenant_id: uuid.UUID, alegra_id: str) -> bool:
    invoice = session.scalar(
        select(SalesInvoice).where(
            SalesInvoice.tenant_id == tenant_id,
            SalesInvoice.alegra_id == alegra_id,
        )
    )
    if invoice is None:
        return False
    invoice.is_deleted = True
    return True


def _apply_invoice(target: SalesInvoice, source: NormalizedInvoice) -> None:
    target.issue_date = source.issue_date
    target.issued_at = source.issued_at
    target.status = source.status
    target.client_alegra_id = source.client_alegra_id
    target.client_name = source.client_name
    target.seller_alegra_id = source.seller_alegra_id
    target.seller_name = source.seller_name
    target.currency_code = source.currency_code
    target.total = source.total
    target.total_paid = source.total_paid
    target.balance = source.balance
    target.source_hash = source.source_hash
    target.is_deleted = False
    target.updated_at = datetime.now(UTC)
