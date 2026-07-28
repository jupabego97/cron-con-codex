from app.cli import build_parser
from app.services.analytics_mart import _DIMENSIONS, _FACTS


def test_refresh_mart_is_available_as_a_tenant_scoped_command() -> None:
    args = build_parser().parse_args(
        ["refresh-mart", "4da4f10b-1fda-4e5e-91d1-17ef67502049"]
    )

    assert args.command == "refresh-mart"
    assert str(args.tenant_id) == "4da4f10b-1fda-4e5e-91d1-17ef67502049"


def test_mart_queries_include_credit_notes_and_balanced_transfer_movements() -> None:
    statements = "\n".join(str(statement) for statement in _FACTS)

    assert "credit_note" in statements
    assert "-COALESCE(cnl.quantity, 0)" in statements
    assert "transfer_out" in statements
    assert "transfer_in" in statements


def test_mart_dimensions_are_loaded_from_operational_tables() -> None:
    statements = "\n".join(str(statement) for statement in _DIMENSIONS)

    for table in ("catalog_items", "contacts", "sellers", "warehouses", "sales_invoices"):
        assert table in statements
