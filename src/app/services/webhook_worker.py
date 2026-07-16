import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import InboundEvent
from app.domain.invoice_repository import mark_invoice_deleted, upsert_invoice
from app.integrations.alegra.client import AlegraClient
from app.services.event_queue import claim_next_event, complete_event, retry_or_fail_event


class WebhookWorker:
    """Processes one durably claimed event at a time outside the webhook request path."""

    def __init__(self, *, session: Session, alegra: AlegraClient) -> None:
        self._session = session
        self._alegra = alegra

    async def run_once(self) -> bool:
        event = claim_next_event(self._session)
        if event is None:
            return False
        self._session.commit()
        try:
            await self._process(event)
            complete_event(event)
            self._session.commit()
        except Exception as error:
            self._session.rollback()
            retry_or_fail_event(event, error)
            self._session.commit()
        return True

    async def _process(self, event: InboundEvent) -> None:
        if event.entity_type != "invoice" or not event.external_id:
            raise ValueError("Inbound event cannot be mapped to an invoice")
        tenant_id = uuid.UUID(str(event.tenant_id))
        if event.subject == "delete-invoice":
            mark_invoice_deleted(self._session, tenant_id=tenant_id, alegra_id=event.external_id)
            return
        payload = await self._alegra.get_invoice(event.external_id)
        upsert_invoice(self._session, tenant_id=tenant_id, payload=_invoice_payload(payload))


def _invoice_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Support either an invoice object or an API wrapper while preserving the source payload."""
    invoice = payload.get("invoice")
    return invoice if isinstance(invoice, dict) else payload
