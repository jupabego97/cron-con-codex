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


_INVOICE_SUBJECTS = {"new-invoice", "edit-invoice", "delete-invoice"}


def parse_alegra_webhook(payload: dict[str, Any]) -> ParsedWebhookEvent:
    """Extract a queueable event without trusting the webhook as the final source of truth."""
    subject = payload.get("subject")
    if subject not in _INVOICE_SUBJECTS:
        raise UnsupportedWebhookEvent(f"Unsupported Alegra webhook subject: {subject!r}")

    message = payload.get("message")
    invoice = message.get("invoice") if isinstance(message, dict) else None
    external_id = (
        str(invoice["id"]) if isinstance(invoice, dict) and invoice.get("id") is not None else None
    )
    return ParsedWebhookEvent(
        subject=subject,
        entity_type="invoice",
        external_id=external_id,
        payload=payload,
        payload_hash=canonical_payload_hash(payload),
    )
