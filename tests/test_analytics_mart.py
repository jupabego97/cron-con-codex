from pathlib import Path

from app.cli import build_parser
from app.services.analytics_mart import _DERIVED, _DIMENSIONS, _FACTS


def test_refresh_mart_is_available_as_a_tenant_scoped_command() -> None:
    args = build_parser().parse_args(
        ["refresh-mart", "4da4f10b-1fda-4e5e-91d1-17ef67502049"]
    )

    assert args.command == "refresh-mart"
    assert str(args.tenant_id) == "4da4f10b-1fda-4e5e-91d1-17ef67502049"


def test_inventory_snapshot_is_available_as_a_tenant_scoped_command() -> None:
    args = build_parser().parse_args(
        ["snapshot-inventory", "4da4f10b-1fda-4e5e-91d1-17ef67502049"]
    )

    assert args.command == "snapshot-inventory"
    assert args.warehouse_concurrency == 3


def test_purchase_line_repair_is_available_as_a_tenant_scoped_command() -> None:
    args = build_parser().parse_args(
        ["repair-purchase-lines", "4da4f10b-1fda-4e5e-91d1-17ef67502049"]
    )

    assert args.command == "repair-purchase-lines"
    assert args.write_batch_size == 200


def test_mart_queries_include_credit_notes_and_balanced_transfer_movements() -> None:
    statements = "\n".join(str(statement) for statement in _FACTS)

    assert "credit_note" in statements
    assert "-COALESCE(cnl.quantity, 0)" in statements
    assert "transfer_out" in statements
    assert "transfer_in" in statements
    assert "NULL::numeric(18, 2)" in statements
    assert "fact_inventory_snapshot" in statements
    assert "COALESCE(pb.currency_code, :default_currency_code)" in statements
    assert "COALESCE(si.currency_code, :default_currency_code)" in statements
    assert "COALESCE(cn.currency_code, :default_currency_code)" in statements
    assert "COALESCE(p.currency_code, :default_currency_code)" in statements
    dimensions = "\n".join(str(statement) for statement in _DIMENSIONS)
    assert "family_name" in dimensions
    assert "preferred_supplier_name" in dimensions
    derived = "\n".join(str(statement) for statement in _DERIVED)
    assert "product_supplier_modes" in derived
    assert "purchase_line_frequency" in derived
    assert "row_number()" in derived
    assert "supplier_product_stats" in derived
    assert "frequency_rank" in derived


def test_replenishment_policies_have_tenant_scoped_tables() -> None:
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260802_13_supplier_purchase_policies.py"
    )
    migration = migration_path.read_text()

    assert "supplier_replenishment_policies" in migration
    assert "supplier_product_policies" in migration
    assert "pk_supplier_replenishment_policies" in migration


def test_mart_dimensions_are_loaded_from_operational_tables() -> None:
    statements = "\n".join(str(statement) for statement in _DIMENSIONS)

    for table in (
        "catalog_items",
        "contacts",
        "sellers",
        "warehouses",
        "sales_invoices",
        "inventory_snapshots",
    ):
        assert table in statements
