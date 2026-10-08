"""FIFO must be reproducible even when regenerated ledger UUIDs change order."""

from itertools import chain, count
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

import test_operations_postgres as contracts
from app.domain.batch_repository import persist_resource_batch
from app.services import sales_cost_allocation
from app.services.analytics_mart import AnalyticsMartService
from app.services.sales_cost_allocation import HistoricalSalesCostService

db = contracts.db


def bill(document="B1", day="2026-01-02", costs=(100,)):
    return {
        "id": document,
        "date": day,
        "status": "open",
        "currency": {"code": "COP"},
        "purchases": {
            "items": [{"item": {"id": "PC"}, "quantity": 2, "price": cost} for cost in costs]
        },
    }


def seed(db, bills, *, quantity=3, credit=False):
    session, tenant = db
    documents = {
        "item": [{"id": "PC", "name": "Computador", "inventariable": True}],
        "warehouse": [{"id": "W", "name": "Principal"}],
        "bill": bills,
        "invoice": [
            {
                "id": "S",
                "date": "2026-01-04",
                "status": "open",
                "currency": {"code": "COP"},
                "items": [{"id": "PC", "quantity": quantity, "price": 300}],
            }
        ],
    }
    if credit:
        documents["credit_note"] = [
            {
                "id": "CN",
                "date": "2026-01-05",
                "status": "open",
                "currency": {"code": "COP"},
                "items": [{"id": "PC", "quantity": 1, "price": 300}],
            }
        ]
    for resource, payloads in documents.items():
        persist_resource_batch(session, tenant_id=tenant, resource=resource, payloads=payloads)
    session.commit()
    AnalyticsMartService(session=session).refresh(tenant_id=tenant)
    product = session.execute(
        text("SELECT key FROM dim_product WHERE tenant_id=:tenant"), {"tenant": tenant}
    ).scalar_one()
    warehouse = session.execute(
        text("SELECT key FROM dim_warehouse WHERE tenant_id=:tenant"), {"tenant": tenant}
    ).scalar_one()
    movement, layer = uuid4(), uuid4()
    # A late import timestamp must not put opening stock after same-day receipts.
    session.execute(
        text("""
        INSERT INTO inventory_cost_movements
          (id,tenant_id,product_key,warehouse_key,occurred_on,movement_type,
           source_type,source_id,source_line_number,quantity_in,quantity_out,
           unit_cost,total_cost,cost_method,confidence,metadata,created_at)
        VALUES (:id,:tenant,:product,:warehouse,'2026-01-01','opening',
                'inventory_cost_opening','opening',1,1,0,50,50,'source','certified',
                '{}','2099-01-01')
    """),
        {"id": movement, "tenant": tenant, "product": product, "warehouse": warehouse},
    )
    session.execute(
        text("""
        INSERT INTO inventory_cost_layers
          (id,tenant_id,product_key,warehouse_key,movement_id,opened_on,
           original_quantity,remaining_quantity,unit_cost,layer_status)
        VALUES (:id,:tenant,:product,:warehouse,:movement,'2026-01-01',1,1,50,'open')
    """),
        {
            "id": layer,
            "tenant": tenant,
            "product": product,
            "warehouse": warehouse,
            "movement": movement,
        },
    )
    session.commit()
    return session, tenant


def projection(session, tenant):
    """Compare business keys and values, not run IDs or rebuilt ledger UUIDs."""
    queries = [
        """SELECT document_type,document_alegra_id,line_number,quantity,net_sales_amount,
                  unit_cost,cogs_amount,margin_amount,cost_status,cost_confidence,cost_method
           FROM fact_sales_line WHERE tenant_id=:tenant
           ORDER BY document_type,document_alegra_id,line_number""",
        """SELECT a.document_type,a.document_alegra_id,a.line_number,a.allocation_sequence,
                  m.source_type,m.source_id,m.source_line_number,a.quantity_allocated,
                  a.unit_cost,a.cost_amount,a.allocation_type,a.confidence
           FROM sales_cost_allocations a
           LEFT JOIN inventory_cost_movements m ON m.id=a.source_movement_id
           WHERE a.tenant_id=:tenant
           ORDER BY a.document_type,a.document_alegra_id,a.line_number,a.allocation_sequence""",
        """SELECT m.source_type,m.source_id,m.source_line_number,l.original_quantity,
                  l.remaining_quantity,l.unit_cost,l.layer_status
           FROM inventory_cost_layers l JOIN inventory_cost_movements m ON m.id=l.movement_id
           WHERE l.tenant_id=:tenant
           ORDER BY m.source_type,m.source_id,m.source_line_number""",
    ]
    return [session.execute(text(query), {"tenant": tenant}).all() for query in queries]


def allocate_with_ids(session, tenant, monkeypatch, *, reversed_ids, offset):
    # Run UUID, then two purchase UUIDs: invert ties deliberately, never randomly.
    purchases = [2, 1] if reversed_ids else [1, 2]
    ids = (UUID(int=value) for value in chain([offset, *purchases], count(offset + 1)))
    monkeypatch.setattr(sales_cost_allocation.uuid, "uuid4", lambda: next(ids))
    return HistoricalSalesCostService(session=session).allocate(tenant_id=tenant)


@pytest.mark.parametrize(
    "bills,expected",
    [
        ([bill(), bill("B2", costs=(200,))], 250),
        ([bill(costs=(100, 200))], 250),
        ([bill(day="2026-01-03"), bill("B2", costs=(200,))], 450),
        ([bill(day="2026-01-01"), bill("B2", "2026-01-01", (200,))], 250),
    ],
    ids=["document-tie", "line-tie", "date-first", "opening-first"],
)
def test_fifo_replays_identical_costs_despite_reversed_uuids(db, monkeypatch, bills, expected):
    session, tenant = seed(db, bills)
    first = allocate_with_ids(session, tenant, monkeypatch, reversed_ids=True, offset=100)
    before = projection(session, tenant)
    second = allocate_with_ids(session, tenant, monkeypatch, reversed_ids=False, offset=200)
    assert first.run_id != second.run_id
    assert first.cogs_amount == second.cogs_amount == expected
    assert first.lines_costed == second.lines_costed == 1
    assert first.status == second.status == "succeeded"
    assert projection(session, tenant) == before


def test_credit_notes_and_missing_cost_remain_reproducible(db, monkeypatch):
    session, tenant = seed(db, [bill(), bill("B2", costs=(200,))], quantity=7, credit=True)
    first = allocate_with_ids(session, tenant, monkeypatch, reversed_ids=True, offset=300)
    before = projection(session, tenant)
    second = allocate_with_ids(session, tenant, monkeypatch, reversed_ids=False, offset=400)
    assert first.cogs_amount == second.cogs_amount
    assert first.status == second.status == "succeeded_with_exceptions"
    assert first.lines_partial == second.lines_partial == 1
    assert projection(session, tenant) == before
    assert session.execute(
        text("""
        SELECT cogs_amount<0 AND cost_status='estimated' FROM fact_sales_line
        WHERE tenant_id=:tenant AND document_type='credit_note'
    """),
        {"tenant": tenant},
    ).scalar_one()


def test_purchase_cost_change_is_applied_then_replays_stably(db, monkeypatch):
    session, tenant = seed(db, [bill(), bill("B2", costs=(200,))])
    original = allocate_with_ids(session, tenant, monkeypatch, reversed_ids=False, offset=500)
    assert original.cogs_amount == 250
    # Restore real UUID generation for the normal operational ingestion path.
    monkeypatch.undo()
    persist_resource_batch(
        session, tenant_id=tenant, resource="bill", payloads=[bill(costs=(150,))]
    )
    session.commit()
    AnalyticsMartService(session=session).refresh(tenant_id=tenant)
    changed = allocate_with_ids(session, tenant, monkeypatch, reversed_ids=True, offset=600)
    before = projection(session, tenant)
    repeated = allocate_with_ids(session, tenant, monkeypatch, reversed_ids=False, offset=700)
    assert changed.cogs_amount == repeated.cogs_amount == 350
    assert projection(session, tenant) == before
    assert session.execute(
        text("""
        SELECT l.original_quantity=1 AND l.unit_cost=50 AND m.confidence='certified'
        FROM inventory_cost_layers l JOIN inventory_cost_movements m ON m.id=l.movement_id
        WHERE l.tenant_id=:tenant AND m.source_type='inventory_cost_opening'
    """),
        {"tenant": tenant},
    ).scalar_one()
