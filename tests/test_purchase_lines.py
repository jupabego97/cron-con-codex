from app.domain.batch_repository import _document_items


def test_bill_lines_are_read_from_purchases_items() -> None:
    items = _document_items(
        {
            "purchases": {
                "items": [
                    {"id": "1", "name": "Producto", "quantity": 2, "price": 15000}
                ]
            }
        }
    )

    assert items == [{"id": "1", "name": "Producto", "quantity": 2, "price": 15000}]


def test_document_items_keeps_standard_items_behavior() -> None:
    assert _document_items({"items": [{"id": "1"}]}) == [{"id": "1"}]
