import pytest

from app.domain.webhooks import UnsupportedWebhookEvent, parse_alegra_webhook


def test_invoice_webhook_is_queueable_and_identifies_document() -> None:
    event = parse_alegra_webhook(
        {
            "subject": "edit-invoice",
            "message": {"invoice": {"id": "INV-55", "items": [{"id": "ITEM-1"}]}},
        }
    )

    assert event.entity_type == "invoice"
    assert event.external_id == "INV-55"
    assert len(event.payload_hash) == 64


def test_non_invoice_webhook_is_ignored_by_the_invoice_scope() -> None:
    with pytest.raises(UnsupportedWebhookEvent):
        parse_alegra_webhook({"subject": "new-item", "message": {"item": {"id": "ITEM-1"}}})
