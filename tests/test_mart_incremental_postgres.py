"""Incremental projection contracts against isolated, real PostgreSQL."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text

import test_operations_postgres as contracts
from app.db.models import InventorySnapshot, InventorySnapshotRun
from app.domain.batch_repository import persist_resource_batch
from app.services import analytics_mart
from app.services.analytics_mart import AnalyticsMartService
from app.services.mart_incremental import prepare_snapshot_runs

db = contracts.db
populated = contracts.populated


def capture(session, tenant, *, item="PC", warehouse="W", status="succeeded", day=2):
    run = InventorySnapshotRun(tenant_id=tenant, status=status, records_written=1)
    session.add(run)
    session.flush()
    session.add(InventorySnapshot(
        tenant_id=tenant, snapshot_run_id=run.id,
        captured_at=datetime(2026, 1, day, tzinfo=UTC),
        warehouse_alegra_id=warehouse, item_alegra_id=item, item_name=item,
        quantity_on_hand=Decimal(3), unit_cost=Decimal(600), payload={"id": item},
    ))
    session.commit()
    return run.id


def warehouse(session, tenant, external_id="W"):
    persist_resource_batch(session, tenant_id=tenant, resource="warehouse",
                           payloads=[{"id": external_id, "name": external_id}])
    session.commit()


def snapshots(session, tenant):
    return [dict(row) for row in session.execute(text(
        "SELECT * FROM fact_inventory_snapshot WHERE tenant_id=:tenant ORDER BY key"
    ), {"tenant": tenant}).mappings()]


def test_noop_preserves_fact_keys_dimension_timestamps_and_fifo_costs(populated):
    session, tenant, _ = populated
    session.execute(text("""UPDATE fact_sales_line SET unit_cost=600,margin_amount=400,
      cogs_amount=600,cost_status='costed',cost_method='fifo'
      WHERE tenant_id=:tenant AND document_alegra_id='S'"""), {"tenant": tenant})
    session.commit()
    before = list(session.execute(text(
        "SELECT key,unit_cost,margin_amount,cogs_amount,cost_status,cost_method "
        "FROM fact_sales_line WHERE tenant_id=:tenant ORDER BY key"
    ), {"tenant": tenant}))
    timestamps = list(session.execute(text(
        "SELECT key,updated_at FROM dim_product WHERE tenant_id=:tenant"
    ), {"tenant": tenant}))
    result = AnalyticsMartService(session=session).refresh(tenant_id=tenant)
    assert result.records_written == 0
    assert before == list(session.execute(text(
        "SELECT key,unit_cost,margin_amount,cogs_amount,cost_status,cost_method "
        "FROM fact_sales_line WHERE tenant_id=:tenant ORDER BY key"
    ), {"tenant": tenant}))
    assert timestamps == list(session.execute(text(
        "SELECT key,updated_at FROM dim_product WHERE tenant_id=:tenant"
    ), {"tenant": tenant}))


def test_line_repair_without_header_hash_change_and_removed_line(populated):
    session, tenant, _ = populated
    invoice = session.execute(text(
        "SELECT id FROM sales_invoices WHERE tenant_id=:tenant AND alegra_id='S'"
    ), {"tenant": tenant}).scalar_one()
    session.execute(text("""UPDATE sales_invoice_lines SET quantity=2,line_total=2000
      WHERE invoice_id=:invoice"""), {"invoice": invoice})
    session.commit()
    AnalyticsMartService(session=session).refresh(tenant_id=tenant)
    assert session.execute(text("""SELECT quantity,net_sales_amount FROM fact_sales_line
      WHERE tenant_id=:tenant AND document_alegra_id='S'"""), {"tenant": tenant}).one() == (
        Decimal(2), Decimal(2000)
    )
    session.execute(text("DELETE FROM sales_invoice_lines WHERE invoice_id=:invoice"),
                    {"invoice": invoice})
    session.commit()
    AnalyticsMartService(session=session).refresh(tenant_id=tenant)
    assert session.execute(text("""SELECT count(*) FROM fact_sales_line
      WHERE tenant_id=:tenant AND document_alegra_id='S'"""), {"tenant": tenant}).scalar() == 0
    assert session.execute(text("SELECT count(*) FROM fact_sales_line WHERE tenant_id=:tenant"),
                           {"tenant": tenant}).scalar() == 2


def test_historical_edits_dates_annulment_deletion_and_negative_credit(populated):
    session, tenant, _ = populated
    session.execute(text("""UPDATE sales_invoices SET issue_date='2020-01-01',
      status='void',is_deleted=true WHERE tenant_id=:tenant AND alegra_id='S'"""),
                    {"tenant": tenant})
    session.execute(text("""UPDATE credit_note_lines SET quantity=2,line_total=400
      WHERE tenant_id=:tenant"""), {"tenant": tenant})
    session.commit()
    AnalyticsMartService(session=session).refresh(tenant_id=tenant)
    sale = session.execute(text("""SELECT date_key,document_status,is_deleted
      FROM fact_sales_line WHERE tenant_id=:tenant AND document_alegra_id='S'"""),
                           {"tenant": tenant}).one()
    assert sale == (20200101, "void", True)
    credit = session.execute(text("""SELECT quantity,net_sales_amount FROM fact_sales_line
      WHERE tenant_id=:tenant AND document_type='credit_note'"""), {"tenant": tenant}).one()
    assert credit == (Decimal(-2), Decimal(-400))
    session.execute(text("UPDATE sales_invoices SET is_deleted=false WHERE tenant_id=:tenant"),
                    {"tenant": tenant})
    session.commit()
    AnalyticsMartService(session=session).refresh(tenant_id=tenant)
    assert not session.execute(text("""SELECT is_deleted FROM fact_sales_line
      WHERE tenant_id=:tenant AND document_alegra_id='S'"""), {"tenant": tenant}).scalar()


def test_purchase_changes_refresh_supplier_projection_but_noop_does_not(populated):
    session, tenant, _ = populated
    session.execute(text("""UPDATE purchase_bill_lines SET quantity=2,line_total=1200
      WHERE tenant_id=:tenant"""), {"tenant": tenant})
    session.commit()
    AnalyticsMartService(session=session).refresh(tenant_id=tenant)
    assert session.execute(text("""SELECT purchased_units FROM product_supplier_modes
      WHERE tenant_id=:tenant"""), {"tenant": tenant}).scalar_one() == Decimal(2)
    assert AnalyticsMartService(session=session).refresh(tenant_id=tenant).records_written == 0


def test_legacy_inventory_adoption_and_new_or_late_runs_keep_old_keys(populated):
    session, tenant, _ = populated
    warehouse(session, tenant)
    first = capture(session, tenant, day=20)
    AnalyticsMartService(session=session).refresh(tenant_id=tenant)
    before = snapshots(session, tenant)
    session.execute(text("DELETE FROM mart_inventory_snapshot_runs WHERE tenant_id=:tenant"),
                    {"tenant": tenant})
    session.commit()
    assert AnalyticsMartService(session=session).refresh(tenant_id=tenant).records_written == 0
    assert snapshots(session, tenant) == before
    second = capture(session, tenant, day=1)
    AnalyticsMartService(session=session).refresh(tenant_id=tenant)
    after = snapshots(session, tenant)
    assert after[0] == before[0]
    assert {row["snapshot_run_id"] for row in after} == {first, second}
    assert AnalyticsMartService(session=session).refresh(tenant_id=tenant).records_written == 0
    assert len(snapshots(session, tenant)) == 2


def test_unresolved_product_and_warehouse_are_repaired_when_dimensions_arrive(populated):
    session, tenant, _ = populated
    run = capture(session, tenant, item="LATE", warehouse="LATE-W")
    AnalyticsMartService(session=session).refresh(tenant_id=tenant)
    assert snapshots(session, tenant)[0]["product_key"] is None
    assert session.execute(text("""SELECT has_unresolved_dimensions
      FROM mart_inventory_snapshot_runs WHERE snapshot_run_id=:run"""), {"run": run}).scalar()
    persist_resource_batch(session, tenant_id=tenant, resource="item",
                           payloads=[{"id": "LATE", "name": "Late product"}])
    warehouse(session, tenant, "LATE-W")
    AnalyticsMartService(session=session).refresh(tenant_id=tenant)
    row = snapshots(session, tenant)[0]
    assert row["product_key"] is not None
    assert row["warehouse_key"] is not None
    assert not session.execute(text("""SELECT has_unresolved_dimensions
      FROM mart_inventory_snapshot_runs WHERE snapshot_run_id=:run"""), {"run": run}).scalar()
    assert AnalyticsMartService(session=session).refresh(tenant_id=tenant).records_written == 0


def test_failed_capture_is_not_projected_and_full_verification_repairs_only_bad_run(populated):
    session, tenant, _ = populated
    warehouse(session, tenant)
    capture(session, tenant, status="failed")
    first = capture(session, tenant)
    second = capture(session, tenant, day=3)
    AnalyticsMartService(session=session).refresh(tenant_id=tenant)
    before = snapshots(session, tenant)
    assert len(before) == 2
    session.execute(text("""UPDATE fact_inventory_snapshot SET quantity_on_hand=999
      WHERE snapshot_run_id=:run"""), {"run": second})
    session.commit()
    AnalyticsMartService(session=session).refresh(tenant_id=tenant, full=True)
    after = snapshots(session, tenant)
    assert next(row for row in after if row["snapshot_run_id"] == first) == next(
        row for row in before if row["snapshot_run_id"] == first
    )
    assert next(row for row in after if row["snapshot_run_id"] == second)["quantity_on_hand"] == 3


def test_snapshot_rounding_unknown_keys_and_identical_unknown_products_are_idempotent(populated):
    session, tenant, _ = populated
    run = capture(session, tenant, item="UNKNOWN-A", warehouse="UNKNOWN-W")
    session.execute(text("""UPDATE inventory_snapshots SET quantity_on_hand=1.2345,unit_cost=1.23
      WHERE snapshot_run_id=:run"""), {"run": run})
    session.add(InventorySnapshot(
        tenant_id=tenant, snapshot_run_id=run,
        captured_at=datetime(2026, 1, 2, tzinfo=UTC),
        warehouse_alegra_id="UNKNOWN-W", item_alegra_id="UNKNOWN-B", item_name="B",
        quantity_on_hand=Decimal("1.2345"), unit_cost=Decimal("1.23"), payload={},
    ))
    session.commit()
    AnalyticsMartService(session=session).refresh(tenant_id=tenant)
    before = snapshots(session, tenant)
    assert len(before) == 2
    assert {row["inventory_value"] for row in before} == {Decimal("1.52")}
    assert AnalyticsMartService(session=session).refresh(tenant_id=tenant).records_written == 0
    AnalyticsMartService(session=session).refresh(tenant_id=tenant, full=True)
    assert snapshots(session, tenant) == before


def test_payments_and_balanced_transfers_update_and_remove_obsolete_lines(populated):
    session, tenant, _ = populated
    warehouse(session, tenant)
    warehouse(session, tenant, "W2")
    persist_resource_batch(session, tenant_id=tenant, resource="payment", payloads=[{
        "id": "PAY", "date": "2026-01-02", "type": "in", "amount": 500,
        "client": {"id": "SUP"}, "currency": {"code": "COP"},
    }])
    persist_resource_batch(session, tenant_id=tenant, resource="warehouse_transfer", payloads=[{
        "id": "TRANSFER", "date": "2026-01-02", "sourceWarehouse": {"id": "W"},
        "destinationWarehouse": {"id": "W2"},
        "items": [{"id": "PC", "quantity": 4, "price": 600}],
    }])
    session.commit()
    AnalyticsMartService(session=session).refresh(tenant_id=tenant)
    assert session.execute(text("SELECT sum(quantity_delta),count(*) FROM fact_inventory_movement "
                                "WHERE tenant_id=:tenant"), {"tenant": tenant}).one() == (0, 2)
    session.execute(text("UPDATE payments SET amount=700,is_deleted=true WHERE tenant_id=:tenant"),
                    {"tenant": tenant})
    session.execute(text("UPDATE warehouse_transfer_lines SET quantity=2 WHERE tenant_id=:tenant"),
                    {"tenant": tenant})
    session.commit()
    AnalyticsMartService(session=session).refresh(tenant_id=tenant)
    assert session.execute(text(
        "SELECT amount,is_deleted FROM fact_payment WHERE tenant_id=:tenant"
    ), {"tenant": tenant}).one() == (700, True)
    assert session.execute(text("SELECT sum(quantity_delta),sum(abs(quantity_delta)) "
                                "FROM fact_inventory_movement WHERE tenant_id=:tenant"),
                           {"tenant": tenant}).one() == (0, 4)
    session.execute(text("DELETE FROM warehouse_transfer_lines WHERE tenant_id=:tenant"),
                    {"tenant": tenant})
    session.commit()
    AnalyticsMartService(session=session).refresh(tenant_id=tenant)
    assert session.execute(text(
        "SELECT count(*) FROM fact_inventory_movement WHERE tenant_id=:tenant"
    ), {"tenant": tenant}).scalar() == 0


def test_failed_refresh_rolls_back_facts_dimensions_and_completion_ledger(populated, monkeypatch):
    session, tenant, _ = populated
    run = capture(session, tenant)
    session.execute(text("UPDATE sales_invoices SET status='changed' WHERE tenant_id=:tenant"),
                    {"tenant": tenant})
    session.commit()
    original = analytics_mart.project_snapshots

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("Injected transactional failure")

    monkeypatch.setattr(analytics_mart, "project_snapshots", fail)
    with pytest.raises(RuntimeError, match="Injected"):
        AnalyticsMartService(session=session).refresh(tenant_id=tenant)
    assert snapshots(session, tenant) == []
    assert session.execute(text("""SELECT count(*) FROM mart_inventory_snapshot_runs
      WHERE snapshot_run_id=:run"""), {"run": run}).scalar() == 0
    assert session.execute(text("""SELECT document_status FROM fact_sales_line
      WHERE tenant_id=:tenant AND document_alegra_id='S'"""), {"tenant": tenant}).scalar() == "open"
    monkeypatch.setattr(analytics_mart, "project_snapshots", original)
    AnalyticsMartService(session=session).refresh(tenant_id=tenant)
    assert len(snapshots(session, tenant)) == 1


def test_incremental_projection_does_not_touch_other_tenants(populated):
    session, tenant, _ = populated
    other = uuid4()
    session.execute(text("INSERT INTO tenants(id,slug,name) VALUES(:id,:slug,'Other')"),
                    {"id": other, "slug": str(other)})
    persist_resource_batch(session, tenant_id=other, resource="invoice", payloads=[{
        "id": "OTHER", "date": "2020-01-01", "status": "open", "items": [
            {"id": "UNKNOWN", "name": "Unknown", "quantity": 1, "price": 7}
        ],
    }])
    session.commit()
    AnalyticsMartService(session=session).refresh(tenant_id=other)
    before = list(session.execute(text("SELECT * FROM fact_sales_line WHERE tenant_id=:tenant"),
                                  {"tenant": other}))
    AnalyticsMartService(session=session).refresh(tenant_id=tenant, full=True)
    assert before == list(session.execute(text(
        "SELECT * FROM fact_sales_line WHERE tenant_id=:tenant"
    ), {"tenant": other}))


def test_metadata_edits_do_not_revisit_unresolved_inventory_history(populated, monkeypatch):
    session, tenant, _ = populated
    capture(session, tenant, item="UNKNOWN")
    AnalyticsMartService(session=session).refresh(tenant_id=tenant)
    pending_counts = []

    def observe(session, params):
        prepare_snapshot_runs(session, params)
        pending_counts.append(session.execute(
            text("SELECT count(*) FROM mart_pending_snapshot_runs")
        ).scalar())

    monkeypatch.setattr(analytics_mart, "prepare_snapshot_runs", observe)
    session.execute(text("UPDATE catalog_items SET name='New name' WHERE tenant_id=:tenant"),
                    {"tenant": tenant})
    session.commit()
    AnalyticsMartService(session=session).refresh(tenant_id=tenant)
    assert pending_counts == [0]
