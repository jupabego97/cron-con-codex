import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

import test_operations_postgres as operations
import test_sales_cost_postgres as fifo
from app.db.models import InboundEvent
from app.services.sales_cost_allocation import HistoricalSalesCostService
from app.services.webhook_worker import WebhookWorker

db = operations.db


def test_unchanged_fifo_preserves_every_physical_projection_row(db):
    session, tenant = fifo.seed(db, [fifo.bill(), fifo.bill("B2", costs=(200,))])
    service = HistoricalSalesCostService(session=session)
    first = service.allocate(tenant_id=tenant, only_if_changed=True)

    def physical():
        return [
            session.execute(
                text(
                    f"SELECT to_jsonb(f)::text FROM {table} f WHERE tenant_id=:tenant ORDER BY id"
                ),
                {"tenant": tenant},
            ).all()
            for table in (
                "inventory_cost_layers",
                "inventory_cost_movements",
                "sales_cost_allocations",
            )
        ]

    before = physical()
    second = service.allocate(tenant_id=tenant, only_if_changed=True)
    assert second.status == "skipped_unchanged" and second.cogs_amount == first.cogs_amount
    assert second.lines_costed == first.lines_costed
    assert physical() == before
    forced = service.allocate(tenant_id=tenant)
    assert forced.status == "succeeded" and forced.cogs_amount == first.cogs_amount
    assert physical() != before


@pytest.mark.parametrize(
    "change",
    ["sale", "purchase", "opening", "deletion", "cost_damage", "ledger_damage", "failed_run"],
)
def test_fifo_source_changes_and_damaged_results_never_skip(db, change):
    session, tenant = fifo.seed(db, [fifo.bill(), fifo.bill("B2", costs=(200,))], credit=True)
    service = HistoricalSalesCostService(session=session)
    service.allocate(tenant_id=tenant, only_if_changed=True)
    sql = {
        "sale": "UPDATE fact_sales_line SET quantity=4 WHERE tenant_id=:tenant "
        "AND document_type='invoice'",
        "purchase": "UPDATE fact_purchase_line SET unit_cost=120 WHERE tenant_id=:tenant",
        "opening": "UPDATE inventory_cost_layers SET unit_cost=60 WHERE tenant_id=:tenant "
        "AND movement_id IN (SELECT id FROM inventory_cost_movements "
        "WHERE source_type='inventory_cost_opening')",
        "deletion": "UPDATE fact_sales_line SET is_deleted=true WHERE tenant_id=:tenant",
        "cost_damage": "UPDATE fact_sales_line SET cogs_amount=NULL WHERE tenant_id=:tenant",
        "ledger_damage": "DELETE FROM sales_cost_allocations WHERE tenant_id=:tenant",
        "failed_run": "UPDATE sales_cost_allocation_runs SET status='failed' "
                      "WHERE tenant_id=:tenant",
    }[change]
    session.execute(text(sql), {"tenant": tenant})
    session.commit()
    recalculated = service.allocate(tenant_id=tenant, only_if_changed=True)
    assert recalculated.status != "skipped_unchanged"
    after = fifo.projection(session, tenant)
    repeated = service.allocate(tenant_id=tenant, only_if_changed=True)
    assert repeated.status == "skipped_unchanged"
    assert repeated.cogs_amount == recalculated.cogs_amount
    assert fifo.projection(session, tenant) == after


def test_batch_retries_remain_durable_across_executions(db):
    session, tenant = db
    event = InboundEvent(
        tenant_id=tenant,
        subject="edit-item",
        entity_type="item",
        external_id="PC",
        payload={"id": "PC"},
        payload_hash="batch-retry",
        available_at=datetime.now(UTC)-timedelta(seconds=60),
    )
    session.add(event)
    session.commit()

    class FailingAlegra:
        async def get_resource(self, *args):
            raise RuntimeError("Temporary upstream failure")

    worker = WebhookWorker(session=session, alegra=FailingAlegra())
    first = asyncio.run(worker.run_batch())
    assert first.attempted == 1 and first.stop_reason == "queue_empty"
    session.refresh(event)
    assert event.status == "retry_wait" and event.attempt_count == 1
    assert asyncio.run(worker.run_batch()).attempted == 0
    session.execute(
        text("UPDATE inbound_events SET available_at=now()-interval '1 second' WHERE id=:id"),
        {"id": event.id},
    )
    session.commit()

    class WorkingAlegra:
        async def get_resource(self, *args):
            return {"id": "PC", "name": "New computer"}

    second = asyncio.run(WebhookWorker(session=session, alegra=WorkingAlegra()).run_batch())
    assert second.attempted == 1
    session.refresh(event)
    assert event.status == "completed" and event.attempt_count == 2
