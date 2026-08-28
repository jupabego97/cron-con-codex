"""Incremental, idempotent synchronization for procurement resources."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.business_time import business_today
from app.db.models import ResourceSyncState, SyncRun
from app.domain.batch_repository import persist_resource_batch
from app.integrations.alegra.client import AlegraClient
from app.integrations.alegra.resources import RESOURCE_BY_KEY, AlegraResource


class ProcurementReconciliationService:
    """Refresh recent bills and purchase orders without replaying all history."""

    def __init__(self, *, session: Session, alegra: AlegraClient) -> None:
        self._session = session
        self._alegra = alegra

    async def run(
        self,
        *,
        tenant_id: uuid.UUID,
        lookback_days: int = 45,
        write_batch_size: int = 100,
    ) -> list[SyncRun]:
        if lookback_days < 1:
            raise ValueError("lookback_days must be positive")
        runs = []
        for resource_key in ("bill", "purchase_order"):
            runs.append(
                await self._run_resource(
                    tenant_id=tenant_id,
                    resource=RESOURCE_BY_KEY[resource_key],
                    lookback_days=lookback_days,
                    write_batch_size=write_batch_size,
                )
            )
        return runs

    async def _run_resource(
        self,
        *,
        tenant_id: uuid.UUID,
        resource: AlegraResource,
        lookback_days: int,
        write_batch_size: int,
    ) -> SyncRun:
        run = SyncRun(
            tenant_id=tenant_id,
            resource=resource.key,
            mode="reconcile",
            status="running",
        )
        self._session.add(run)
        self._session.commit()
        seen: set[str] = set()
        buffer: list[dict] = []
        try:
            first_day = business_today() - timedelta(days=lookback_days - 1)
            for offset in range(lookback_days):
                current = first_day + timedelta(days=offset)
                async for payload in self._alegra.iter_all_resource(
                    resource,
                    hydrate_details=True,
                    page_concurrency=1,
                    detail_concurrency=6,
                    filters={"date": current.isoformat()},
                ):
                    external_id = str(payload.get("id", ""))
                    if not external_id or external_id in seen:
                        continue
                    seen.add(external_id)
                    run.records_read += 1
                    buffer.append(payload)
                    if len(buffer) >= write_batch_size:
                        run.records_written += self._write(
                            tenant_id=tenant_id,
                            resource=resource,
                            run=run,
                            payloads=buffer,
                        )
                        buffer = []

            if resource.key == "purchase_order":
                async for payload in self._alegra.iter_all_resource(
                    resource,
                    hydrate_details=True,
                    page_concurrency=1,
                    detail_concurrency=6,
                    filters={"status": "open"},
                ):
                    external_id = str(payload.get("id", ""))
                    if not external_id or external_id in seen:
                        continue
                    seen.add(external_id)
                    run.records_read += 1
                    buffer.append(payload)
                    if len(buffer) >= write_batch_size:
                        run.records_written += self._write(
                            tenant_id=tenant_id,
                            resource=resource,
                            run=run,
                            payloads=buffer,
                        )
                        buffer = []
            if buffer:
                run.records_written += self._write(
                    tenant_id=tenant_id,
                    resource=resource,
                    run=run,
                    payloads=buffer,
                )

            now = datetime.now(UTC)
            run.status = "succeeded"
            run.finished_at = now
            state = self._state(tenant_id, resource.key)
            state.last_success_at = now
            state.last_error = None
            self._session.commit()
            return run
        except Exception as error:
            self._session.rollback()
            failed = self._session.get(SyncRun, run.id)
            if failed is None:
                raise
            failed.status = "failed"
            failed.finished_at = datetime.now(UTC)
            failed.error_message = str(error)[:2000]
            state = self._state(tenant_id, resource.key)
            state.last_error = failed.error_message
            self._session.commit()
            raise

    def _write(
        self,
        *,
        tenant_id: uuid.UUID,
        resource: AlegraResource,
        run: SyncRun,
        payloads: list[dict],
    ) -> int:
        result = persist_resource_batch(
            self._session,
            tenant_id=tenant_id,
            resource=resource.key,
            payloads=payloads,
            sync_run_id=run.id,
        )
        self._session.commit()
        return result.records_written

    def _state(self, tenant_id: uuid.UUID, resource: str) -> ResourceSyncState:
        state = self._session.scalar(
            select(ResourceSyncState).where(
                ResourceSyncState.tenant_id == tenant_id,
                ResourceSyncState.resource == resource,
            )
        )
        if state is None:
            state = ResourceSyncState(tenant_id=tenant_id, resource=resource)
            self._session.add(state)
        return state
