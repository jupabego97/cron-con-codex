import uuid

from sqlalchemy.orm import Session

from app.db.models import InboundEvent
from app.domain.entity_repository import mark_alegra_entity_deleted, upsert_alegra_entity
from app.domain.invoice_repository import mark_invoice_deleted, upsert_invoice
from app.integrations.alegra.client import AlegraClient
from app.integrations.alegra.resources import RESOURCE_BY_KEY
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
        if not event.external_id:
            raise ValueError("Inbound event does not contain an Alegra resource id")
        tenant_id = uuid.UUID(str(event.tenant_id))
        resource = RESOURCE_BY_KEY.get(event.entity_type)
        if resource is None:
            raise ValueError(f"Inbound event has unsupported resource {event.entity_type!r}")
        if event.subject.startswith("delete-"):
            mark_alegra_entity_deleted(
                self._session,
                tenant_id=tenant_id,
                resource=resource.key,
                external_id=event.external_id,
            )
            if resource.key == "invoice":
                mark_invoice_deleted(
                    self._session, tenant_id=tenant_id, alegra_id=event.external_id
                )
            return
        payload = await self._alegra.get_resource(resource, event.external_id)
        upsert_alegra_entity(
            self._session, tenant_id=tenant_id, resource=resource.key, payload=payload
        )
        if resource.key == "invoice":
            upsert_invoice(self._session, tenant_id=tenant_id, payload=payload)
