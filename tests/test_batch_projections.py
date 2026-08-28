import uuid
from decimal import Decimal

from app.domain.batch_repository import _line_value, _projection_fields


def test_contact_projection_keeps_typed_analytics_fields() -> None:
    result = _projection_fields(
        "contact",
        {
            "name": "Cliente Uno",
            "identification": "900123456",
            "email": "cliente@example.com",
            "phonePrimary": "6010000000",
            "mobile": "3000000000",
            "type": "client",
            "status": "active",
            "seller": {"id": "9"},
            "creditLimit": "250000.50",
        },
        "1",
    )

    assert result["name"] == "Cliente Uno"
    assert result["seller_alegra_id"] == "9"
    assert result["credit_limit"] == Decimal("250000.50")


def test_item_projection_extracts_family_from_alegra_custom_fields() -> None:
    result = _projection_fields(
        "item",
        {
            "name": "Teclado gamer",
            "customFields": [{"label": "FAMILIA", "value": "GAMING"}],
        },
        "1",
    )

    assert result["family_name"] == "GAMING"


def test_item_projection_extracts_preferred_supplier_from_alegra_custom_fields() -> None:
    result = _projection_fields(
        "item",
        {
            "name": "Teclado gamer",
            "customFields": [{"label": "PROVEEDOR", "value": "JALTECH"}],
        },
        "1",
    )

    assert result["preferred_supplier_name"] == "JALTECH"


def test_document_line_projection_calculates_total_when_the_api_does_not_send_one() -> None:
    result = _line_value(
        tenant_id=uuid.uuid4(),
        document_alegra_id="B-1",
        line_number=1,
        payload={"id": "I-1", "name": "Cable", "quantity": "3", "price": "12500"},
    )

    assert result["item_alegra_id"] == "I-1"
    assert result["line_total"] == Decimal("37500")


def test_purchase_order_projection_keeps_supplier_delivery_and_warehouse() -> None:
    result = _projection_fields(
        "purchase_order",
        {
            "date": "2026-08-28",
            "deliveryDate": "2026-09-04",
            "status": "open",
            "provider": {"id": "P-1", "name": "Proveedor Uno"},
            "warehouse": {"id": "W-1"},
            "currency": {"code": "COP"},
            "total": "2500000",
        },
        "PO-1",
    )

    assert result["provider_alegra_id"] == "P-1"
    assert result["delivery_date"].isoformat() == "2026-09-04"
    assert result["warehouse_alegra_id"] == "W-1"
    assert result["total"] == Decimal("2500000")
