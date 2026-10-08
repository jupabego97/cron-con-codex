# ruff: noqa: E501
"""Capture current stock from Alegra once for every active warehouse."""

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models import InventorySnapshot, InventorySnapshotRun, Warehouse
from app.integrations.alegra.client import AlegraClient
from app.integrations.alegra.resources import RESOURCE_BY_KEY


@dataclass(frozen=True)
class InventorySnapshotResult:
    run_id: uuid.UUID
    status: str
    records_read: int
    records_written: int


class InventorySnapshotService:
    """Build a point-in-time stock snapshot; it never infers stock from movements."""

    def __init__(self, *, session: Session, alegra: AlegraClient) -> None:
        self._session = session
        self._alegra = alegra

    async def capture(
        self, *, tenant_id: uuid.UUID, warehouse_concurrency: int = 3
    ) -> InventorySnapshotResult:
        if warehouse_concurrency < 1:
            raise ValueError("warehouse_concurrency must be positive")
        run = InventorySnapshotRun(tenant_id=tenant_id)
        self._session.add(run)
        self._session.commit()
        captured_at = datetime.now(UTC)
        semaphore = asyncio.Semaphore(warehouse_concurrency)
        items_resource = RESOURCE_BY_KEY["item"]

        async def read_warehouse(warehouse: Warehouse) -> tuple[int, list[dict[str, Any]]]:
            async with semaphore:
                records: list[dict[str, Any]] = []
                records_read = 0
                async for payload in self._alegra.iter_all_resource(
                    items_resource,
                    page_concurrency=3,
                    detail_concurrency=1,
                    hydrate_details=False,
                    filters={
                        "idWarehouse": warehouse.alegra_id,
                        "inventariable": "true",
                        "mode": "advanced",
                    },
                ):
                    records_read += 1
                    snapshot = _snapshot_row(
                        tenant_id=tenant_id,
                        run_id=run.id,
                        captured_at=captured_at,
                        warehouse_alegra_id=warehouse.alegra_id,
                        payload=payload,
                    )
                    if snapshot is not None:
                        records.append(snapshot)
                return records_read, records

        try:
            warehouses = list(
                self._session.scalars(
                    select(Warehouse).where(
                        Warehouse.tenant_id == tenant_id,
                        Warehouse.is_deleted.is_(False),
                    )
                )
            )
            if not warehouses:
                raise ValueError("No active warehouses are available for the inventory snapshot")
            batches = await asyncio.gather(*(read_warehouse(warehouse) for warehouse in warehouses))
            rows = [row for _, batch in batches for row in batch]
            run.records_read = sum(records_read for records_read, _ in batches)
            if rows:
                insert = (
                    pg_insert(InventorySnapshot)
                    .values(rows)
                    .on_conflict_do_nothing(constraint="uq_inventory_snapshot_run_warehouse_item")
                )
                self._session.execute(insert)
                # PostgreSQL may report rowcount=-1 for an INSERT .. ON CONFLICT
                # statement even when it inserted rows. Rows are unique per new run.
                run.records_written = len(rows)
            else:
                run.records_written = 0
            run.status = "succeeded"
            run.finished_at = datetime.now(UTC)
            self._session.flush()
            self._session.execute(
                text("""INSERT INTO product_daily_availability
              (tenant_id,item_alegra_id,observed_on,sample_count,positive_samples,last_quantity,captured_at)
              SELECT tenant_id,item_alegra_id,observed_on,count(*),count(*) FILTER(WHERE quantity>0),
                (array_agg(quantity ORDER BY captured_at DESC))[1],max(captured_at)
              FROM (SELECT s.tenant_id,s.item_alegra_id,s.snapshot_run_id,
                  (max(s.captured_at) AT TIME ZONE 'America/Bogota')::date observed_on,
                  max(s.captured_at) captured_at,sum(s.quantity_on_hand) quantity
                FROM inventory_snapshots s JOIN inventory_snapshot_runs r
                  ON r.tenant_id=s.tenant_id AND r.id=s.snapshot_run_id AND r.status='succeeded'
                WHERE s.tenant_id=:tenant AND (s.captured_at AT TIME ZONE 'America/Bogota')::date
                  =(:captured AT TIME ZONE 'America/Bogota')::date
                GROUP BY s.tenant_id,s.item_alegra_id,s.snapshot_run_id) daily
              GROUP BY tenant_id,item_alegra_id,observed_on
              ON CONFLICT(tenant_id,item_alegra_id,observed_on) DO UPDATE SET
                sample_count=excluded.sample_count,positive_samples=excluded.positive_samples,
                last_quantity=excluded.last_quantity,captured_at=excluded.captured_at"""),
                {"tenant": tenant_id, "captured": captured_at},
            )
            self._session.commit()
        except Exception as error:
            self._session.rollback()
            failed = self._session.get(InventorySnapshotRun, run.id)
            if failed is None:
                raise
            failed.status = "failed"
            failed.error_message = str(error)[:2000]
            failed.finished_at = datetime.now(UTC)
            self._session.commit()
            raise
        return InventorySnapshotResult(
            run_id=run.id,
            status=run.status,
            records_read=run.records_read,
            records_written=run.records_written,
        )


def _snapshot_row(
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    captured_at: datetime,
    warehouse_alegra_id: str,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    item_id = payload.get("id")
    inventory = payload.get("inventory")
    if item_id is None or not isinstance(inventory, dict):
        return None
    quantity = _decimal(inventory.get("availableQuantity"))
    if quantity is None:
        return None
    return {
        "id": uuid.uuid4(),
        "tenant_id": tenant_id,
        "snapshot_run_id": run_id,
        "captured_at": captured_at,
        "warehouse_alegra_id": warehouse_alegra_id,
        "item_alegra_id": str(item_id),
        "item_name": _text(payload.get("name")) or str(item_id),
        "quantity_on_hand": quantity,
        "unit_cost": _decimal(inventory.get("unitCost")),
        "payload": payload,
    }


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _text(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, (int, Decimal)):
        return str(value)
    return None
