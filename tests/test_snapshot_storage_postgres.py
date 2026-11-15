"""Non-destructive snapshot storage and midnight freshness on real PostgreSQL."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

import test_operations_postgres as contracts
from app.db.models import InventorySnapshot, InventorySnapshotRun
from app.domain.batch_repository import persist_resource_batch
from app.services.analytics_mart import AnalyticsMartService
from app.services.inventory_snapshot import (
    InventorySnapshotService,
    _snapshot_row,
    _store_snapshot_rows,
    load_snapshot_payload,
)
from app.services.procurement_planning import ProcurementPlanningService

db = contracts.db
populated = contracts.populated


def observation(session, tenant, stamp, *, payload=None, legacy=False):
    payload = payload or {
        "id": "PC", "name": "Computador",
        "inventory": {"availableQuantity": "3.1250", "unitCost": "600.25"},
    }
    run = InventorySnapshotRun(tenant_id=tenant, status="succeeded", finished_at=stamp)
    session.add(run)
    session.flush()
    row = _snapshot_row(tenant_id=tenant, run_id=run.id, captured_at=stamp,
                        warehouse_alegra_id="W", payload=payload)
    assert row is not None
    if legacy:
        session.add(InventorySnapshot(**row))
        session.flush()
    else:
        _store_snapshot_rows(session, [row])
    return run.id, row["id"], payload


def test_repeated_payload_preserves_every_observation_and_legacy_source(populated):
    session, tenant, _ = populated
    stamp = datetime(2026, 11, 14, 23, tzinfo=UTC)
    legacy_run, legacy_id, original = observation(session, tenant, stamp, legacy=True)
    run_a, id_a, _ = observation(session, tenant, stamp + timedelta(hours=4))
    reordered = {"inventory": original["inventory"], "name": "Computador", "id": "PC"}
    run_b, id_b, _ = observation(session, tenant, stamp + timedelta(hours=8), payload=reordered)
    session.commit()
    assert session.scalar(text(
        "SELECT count(*) FROM inventory_snapshot_payloads WHERE tenant_id=:t"
    ), {"t": tenant}) == 1
    rows = session.execute(text("""SELECT payload,payload_hash FROM inventory_snapshots
      WHERE tenant_id=:t ORDER BY captured_at"""), {"t": tenant}).all()
    assert rows[0] == (original, None)
    assert rows[1].payload is None and rows[1].payload_hash == rows[2].payload_hash
    for snapshot_id in (legacy_id, id_a, id_b):
        assert load_snapshot_payload(session, tenant_id=tenant, snapshot_id=snapshot_id) == original
        assert load_snapshot_payload(session, tenant_id=uuid4(), snapshot_id=snapshot_id) is None
    AnalyticsMartService(session=session).refresh(tenant_id=tenant)
    facts = session.execute(text("""SELECT snapshot_run_id,quantity_on_hand,unit_cost
      FROM fact_inventory_snapshot WHERE tenant_id=:t ORDER BY captured_at"""), {"t": tenant}).all()
    assert [row.snapshot_run_id for row in facts] == [legacy_run, run_a, run_b]
    assert {(row.quantity_on_hand, row.unit_cost) for row in facts} == {
        (Decimal("3.1250"), Decimal("600.25"))
    }
    assert AnalyticsMartService(session=session).refresh(tenant_id=tenant).records_written == 0


def test_quantity_and_cost_changes_create_distinct_exact_sources(populated):
    session, tenant, _ = populated
    stamp = datetime(2026, 11, 15, tzinfo=UTC)
    originals = []
    for quantity, cost in ((3, "600.25"), (2, "600.25"), (2, "650.99")):
        payload = {"id": "PC", "inventory": {"availableQuantity": quantity, "unitCost": cost}}
        _, snapshot_id, original = observation(session, tenant, stamp, payload=payload)
        originals.append((snapshot_id, original))
    assert session.scalar(text(
        "SELECT count(*) FROM inventory_snapshot_payloads WHERE tenant_id=:t"
    ), {"t": tenant}) == 3
    for snapshot_id, payload in originals:
        assert load_snapshot_payload(session, tenant_id=tenant, snapshot_id=snapshot_id) == payload


def test_failed_observation_rolls_back_shared_payload_and_forbids_cross_tenant_reference(db):
    session, tenant = db
    run = InventorySnapshotRun(tenant_id=tenant, status="running")
    session.add(run)
    session.flush()
    row = _snapshot_row(tenant_id=tenant, run_id=run.id, captured_at=datetime.now(UTC),
                        warehouse_alegra_id="W", payload={
                            "id": "PC", "inventory": {"availableQuantity": 1},
                        })
    assert row is not None
    with pytest.raises(IntegrityError), session.begin_nested():
        _store_snapshot_rows(session, [{**row, "warehouse_alegra_id": None}])
    assert session.scalar(text(
        "SELECT count(*) FROM inventory_snapshot_payloads WHERE tenant_id=:t"
    ), {"t": tenant}) == 0
    _store_snapshot_rows(session, [row])
    payload_hash = session.scalar(text(
        "SELECT payload_hash FROM inventory_snapshots WHERE id=:id"
    ), {"id": row["id"]})
    other = uuid4()
    session.execute(text("INSERT INTO tenants(id,slug,name) VALUES (:t,:s,'Other')"),
                    {"t": other, "s": str(other)})
    other_run = InventorySnapshotRun(tenant_id=other, status="running")
    session.add(other_run)
    session.flush()
    with pytest.raises(IntegrityError), session.begin_nested():
        session.execute(text("""INSERT INTO inventory_snapshots
          (id,tenant_id,snapshot_run_id,captured_at,warehouse_alegra_id,item_alegra_id,
           item_name,quantity_on_hand,payload,payload_hash)
          VALUES (:id,:t,:run,now(),'W','PC','PC',1,NULL,:h)"""),
                        {"id": uuid4(), "t": other, "run": other_run.id, "h": payload_hash})


def test_capture_retains_daily_samples_with_shared_payloads(populated):
    session, tenant, _ = populated
    persist_resource_batch(session, tenant_id=tenant, resource="warehouse",
                           payloads=[{"id": "W", "name": "Principal"}])
    session.commit()

    class Alegra:
        async def iter_all_resource(self, *args, **kwargs):
            yield {"id": "PC", "inventory": {"availableQuantity": 2, "unitCost": 600}}

    service = InventorySnapshotService(session=session, alegra=Alegra())
    for _ in range(2):
        result = asyncio.run(service.capture(tenant_id=tenant))
        assert result.status == "succeeded" and result.records_written == 1
    assert session.scalar(text(
        "SELECT count(*) FROM inventory_snapshot_payloads WHERE tenant_id=:t"
    ), {"t": tenant}) == 1
    assert session.execute(text("""SELECT sample_count,positive_samples,last_quantity
      FROM product_daily_availability WHERE tenant_id=:t AND item_alegra_id='PC'"""),
                           {"t": tenant}).one() == (2, 2, Decimal(2))


@pytest.mark.parametrize("today", [date(2026, 11, 15), date(2027, 1, 1)])
@pytest.mark.parametrize("age,stale", [(1, False), (6, False), (7, True)])
def test_current_plan_survives_midnight_but_never_accepts_expired_stock(
    populated, monkeypatch, today, age, stale
):
    session, tenant, _ = populated
    now = datetime(today.year, today.month, today.day, 5, 30, tzinfo=UTC)  # 00:30 Bogota

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr("app.services.procurement_planning.datetime", Clock)
    monkeypatch.setattr("app.services.procurement_planning.business_today", lambda: today)
    run, _, _ = observation(session, tenant, now - timedelta(hours=age))
    # A future timestamp must never displace the last real capture.
    observation(session, tenant, now + timedelta(hours=1))
    AnalyticsMartService(session=session).refresh(tenant_id=tenant)
    quality = ProcurementPlanningService(session=session, tenant_id=tenant).data_quality()
    assert quality["snapshot_run_id"] == run
    assert any("6 horas" in warning for warning in quality["warnings"]) is stale
    assert not any("No hay captura" in warning for warning in quality["warnings"])
    previous_day = ProcurementPlanningService(session=session, tenant_id=tenant).data_quality(
        as_of=today - timedelta(days=1)
    )
    assert previous_day["snapshot_run_id"] == run
    assert not previous_day["is_current_date"]
    historical = ProcurementPlanningService(session=session, tenant_id=tenant).data_quality(
        as_of=today - timedelta(days=2)
    )
    assert historical["snapshot_run_id"] is None
    assert not historical["is_current_date"] and historical["ready"] is False
