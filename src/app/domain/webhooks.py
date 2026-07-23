from dataclasses import dataclass
from typing import Any

from app.domain.invoices import canonical_payload_hash


class UnsupportedWebhookEvent(ValueError):
    """Raised for an event not handled by the active integration scope."""


@dataclass(frozen=True)
class ParsedWebhookEvent:
    subject: str
    entity_type: str
    external_id: str | None
    payload: dict[str, Any]
    payload_hash: str


_EVENT_TARGETS: dict[str, tuple[str, str]] = {
    "new-invoice": ("invoice", "invoice"),
    "edit-invoice": ("invoice", "invoice"),
    "delete-invoice": ("invoice", "invoice"),
    "new-bill": ("bill", "bill"),
    "edit-bill": ("bill", "bill"),
    "delete-bill": ("bill", "bill"),
    "new-client": ("contact", "client"),
    "edit-client": ("contact", "client"),
    "delete-client": ("contact", "client"),
    "new-item": ("item", "item"),
    "edit-item": ("item", "item"),
    "delete-item": ("item", "item"),
}


def parse_alegra_webhook(payload: dict[str, Any]) -> ParsedWebhookEvent:
    """Extract a queueable event without trusting the webhook as the final source of truth."""
    subject = payload.get("subject")
    target = _EVENT_TARGETS.get(subject) if isinstance(subject, str) else None
    if target is None:
        raise UnsupportedWebhookEvent(f"Unsupported Alegra webhook subject: {subject!r}")

    message = payload.get("message")
    entity_type, message_key = target
    entity = message.get(message_key) if isinstance(message, dict) else None
    external_id = (
        str(entity["id"]) if isinstance(entity, dict) and entity.get("id") is not None else None
    )
    return ParsedWebhookEvent(
        subject=subject,
        entity_type=entity_type,
        external_id=external_id,
        payload=payload,
        payload_hash=canonical_payload_hash(payload),
    )
