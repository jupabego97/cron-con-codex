from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from app.services.inventory_snapshot import _snapshot_row


def test_scheduled_capture_does_not_refresh_mart_or_allocate_costs(monkeypatch) -> None:
    import asyncio
    from types import SimpleNamespace

    from app import cli

    captured = []

    class Context:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class Capture:
        def __init__(self, **kwargs):
            pass

        async def capture(self, **kwargs):
            captured.append(kwargs)
            return SimpleNamespace(run_id="run", status="succeeded", records_written=1)

    def forbidden(**kwargs):
        raise AssertionError("Scheduled inventory capture must not trigger analytics")

    monkeypatch.setattr(cli, "get_settings", lambda: SimpleNamespace(
        alegra_api_basic_token=SimpleNamespace(get_secret_value=lambda: "test-only-token")
    ))
    monkeypatch.setattr(cli, "get_session_factory", lambda: Context)
    monkeypatch.setattr(cli, "AlegraClient", lambda **kwargs: Context())
    monkeypatch.setattr(cli, "InventorySnapshotService", Capture)
    monkeypatch.setattr(cli, "AnalyticsMartService", forbidden)
    monkeypatch.setattr(cli, "HistoricalSalesCostService", forbidden)
    tenant = UUID("4da4f10b-1fda-4e5e-91d1-17ef67502049")
    asyncio.run(cli.snapshot_inventory(tenant_id=tenant, warehouse_concurrency=3))
    assert captured == [{"tenant_id": tenant, "warehouse_concurrency": 3}]


def test_snapshot_row_reads_available_quantity_and_unit_cost() -> None:
    row = _snapshot_row(
        tenant_id=UUID("4da4f10b-1fda-4e5e-91d1-17ef67502049"),
        run_id=UUID("2f7b0eb5-789e-48e9-a04d-e3857da2fd3d"),
        captured_at=datetime(2026, 7, 29, tzinfo=UTC),
        warehouse_alegra_id="7",
        payload={
            "id": "190",
            "name": "Memoria SSD",
            "inventory": {"availableQuantity": "12.5", "unitCost": "85000"},
        },
    )

    assert row is not None
    assert row["item_alegra_id"] == "190"
    assert row["warehouse_alegra_id"] == "7"
    assert row["quantity_on_hand"] == Decimal("12.5")
    assert row["unit_cost"] == Decimal("85000")


def test_snapshot_row_skips_payloads_without_inventory_quantity() -> None:
    row = _snapshot_row(
        tenant_id=UUID("4da4f10b-1fda-4e5e-91d1-17ef67502049"),
        run_id=UUID("2f7b0eb5-789e-48e9-a04d-e3857da2fd3d"),
        captured_at=datetime(2026, 7, 29, tzinfo=UTC),
        warehouse_alegra_id="7",
        payload={"id": "190", "name": "Servicio sin inventario"},
    )

    assert row is None
