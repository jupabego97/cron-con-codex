"""Capture current stock from Alegra once for every active warehouse."""

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
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
                insert = pg_insert(InventorySnapshot).values(rows).on_conflict_do_nothing(
                    constraint="uq_inventory_snapshot_run_warehouse_item"
                )
                result = self._session.execute(insert)
                run.records_written = max(int(result.rowcount or 0), 0)
            else:
                run.records_written = 0
            run.status = "succeeded"
            run.finished_at = datetime.now(UTC)
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
