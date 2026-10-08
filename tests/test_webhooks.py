import asyncio
from unittest.mock import MagicMock

import pytest

from app.domain.webhooks import UnsupportedWebhookEvent, parse_alegra_webhook
from app.services.webhook_worker import WebhookWorker


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


def test_supported_item_webhook_is_queueable() -> None:
    event = parse_alegra_webhook({"subject": "new-item", "message": {"item": {"id": "ITEM-1"}}})

    assert event.entity_type == "item"
    assert event.external_id == "ITEM-1"


def test_unknown_webhook_is_rejected() -> None:
    with pytest.raises(UnsupportedWebhookEvent):
        parse_alegra_webhook({"subject": "new-payment", "message": {"payment": {"id": "PAY-1"}}})


def test_idle_worker_releases_polling_transaction() -> None:
    session = MagicMock()
    session.scalar.return_value = None
    worker = WebhookWorker(session=session, alegra=MagicMock())

    assert asyncio.run(worker.run_once()) is False
    session.rollback.assert_called_once_with()
    session.commit.assert_not_called()
