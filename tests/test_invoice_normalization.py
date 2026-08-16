from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.domain.invoices import normalize_invoice


def test_naive_alegra_datetime_is_interpreted_as_colombia_local_time() -> None:
    normalized = normalize_invoice(
        {"id": "INV-TIME", "datetime": "2026-08-15 10:17:42", "items": []}
    )

    assert normalized.issued_at == datetime(
        2026, 8, 15, 10, 17, 42, tzinfo=ZoneInfo("America/Bogota")
    )


def test_timezone_aware_alegra_datetime_keeps_its_declared_timezone() -> None:
    normalized = normalize_invoice(
        {"id": "INV-TIME-AWARE", "datetime": "2026-08-15T15:17:42+00:00", "items": []}
    )

    assert normalized.issued_at == datetime.fromisoformat("2026-08-15T15:17:42+00:00")


def test_normalize_invoice_preserves_financial_state_and_line_order() -> None:
    normalized = normalize_invoice(
        {
            "id": "INV-100",
            "date": "2026-07-10",
            "status": "closed",
            "total": "100.00",
            "totalPaid": "40.00",
            "balance": "60.00",
            "client": {"id": "C-1", "name": "Cliente Uno"},
            "seller": {"id": "S-1", "name": "Vendedor Uno"},
            "items": [
                {"id": "ITEM-7", "name": "Cable", "price": "10", "quantity": "2"},
                {"id": "ITEM-7", "name": "Cable", "price": "20", "quantity": "4"},
            ],
        }
    )

    assert normalized.alegra_id == "INV-100"
    assert normalized.total == Decimal("100.00")
    assert normalized.total_paid == Decimal("40.00")
    assert normalized.balance == Decimal("60.00")
    assert [(line.line_number, line.item_alegra_id) for line in normalized.lines] == [
        (1, "ITEM-7"),
        (2, "ITEM-7"),
    ]
    assert normalized.lines[0].line_total == Decimal("20")
    assert normalized.lines[1].line_total == Decimal("80")
