from decimal import Decimal

from openpyxl import Workbook

from app.cli import build_parser
from app.services.inventory_cost_opening import read_opening_inventory_xlsx


def test_opening_inventory_reader_normalizes_report_headers(tmp_path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(
        [
            "Categoría",
            "Ítem",
            "Referencia",
            "Descripción",
            "Cantidad mínima",
            "Cantidad máxima",
            "Cantidad",
            "Cantidad en remisiones",
            "Unidad",
            "Estado",
            "Costo promedio",
            "Total",
        ]
    )
    sheet.append(
        [
            "Computadores",
            "Laptop de prueba",
            "LP-001",
            None,
            0,
            10,
            2,
            0,
            "unidad",
            "activo",
            1500000,
            3000000,
        ]
    )
    sheet.append(
        [
            None,
            "Ajuste negativo de prueba",
            None,
            None,
            0,
            0,
            -1,
            0,
            "unidad",
            "activo",
            250000,
            -250000,
        ]
    )
    path = tmp_path / "opening.xlsx"
    workbook.save(path)

    rows = read_opening_inventory_xlsx(path)

    assert len(rows) == 2
    assert rows[0].item_name == "Laptop de prueba"
    assert rows[0].reported_quantity == Decimal("2")
    assert rows[0].unit_cost == Decimal("1500000")
    assert rows[1].reported_quantity == Decimal("-1")
    assert rows[1].unit_cost == Decimal("250000")


def test_opening_inventory_command_defaults_to_certified_cutoff() -> None:
    args = build_parser().parse_args(
        ["import-opening-inventory", "00000000-0000-0000-0000-000000000001", "report.xlsx"]
    )

    assert args.cutoff_date.isoformat() == "2026-01-01"
    assert args.dry_run is False
