"""Concurrent historical extraction of the business resource catalog."""

import asyncio
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ResourceSyncState, SyncRun
from app.domain.entity_repository import upsert_alegra_entity
from app.domain.invoice_repository import upsert_invoice
from app.integrations.alegra.client import AlegraClient
from app.integrations.alegra.resources import AlegraResource


@dataclass(frozen=True)
class BackfillResult:
    resource: str
    run_id: str
    status: str
    records_read: int
    records_written: int
    error_message: str | None = None


class ResourceSyncService:
    """Backfill one resource with durable run and state records."""

    def __init__(self, *, session: Session, alegra: AlegraClient) -> None:
        self._session = session
        self._alegra = alegra

    async def run_initial_sync(
        self,
        *,
        tenant_id: uuid.UUID,
        resource: AlegraResource,
        page_concurrency: int,
        detail_concurrency: int,
        hydrate_details: bool,
    ) -> BackfillResult:
        sync_run = SyncRun(
            tenant_id=tenant_id, resource=resource.key, mode="initial", status="running"
        )
        self._session.add(sync_run)
        self._session.commit()
        try:
            async for payload in self._alegra.iter_all_resource(
                resource,
                page_concurrency=page_concurrency,
                detail_concurrency=detail_concurrency,
                hydrate_details=hydrate_details,
            ):
                sync_run.records_read += 1
                _, created = upsert_alegra_entity(
                    self._session,
                    tenant_id=tenant_id,
                    resource=resource.key,
                    payload=payload,
                    sync_run_id=sync_run.id,
                )
                if resource.key == "invoice":
                    upsert_invoice(
                        self._session,
                        tenant_id=tenant_id,
                        payload=payload,
                        sync_run_id=sync_run.id,
                    )
                sync_run.records_written += int(created)
                if sync_run.records_read % 25 == 0:
                    self._session.commit()

            self._finish_success(sync_run, tenant_id=tenant_id, resource=resource.key)
            self._session.commit()
            return _result(sync_run)
        except Exception as error:
            self._session.rollback()
            self._finish_failure(sync_run, tenant_id=tenant_id, resource=resource.key, error=error)
            self._session.commit()
            return _result(sync_run)

    def _finish_success(self, sync_run: SyncRun, *, tenant_id: uuid.UUID, resource: str) -> None:
        now = datetime.now(UTC)
        sync_run.status = "succeeded"
        sync_run.finished_at = now
        state = self._state(tenant_id=tenant_id, resource=resource)
        state.last_full_sync_at = now
        state.last_success_at = now
        state.last_error = None

    def _finish_failure(
        self, sync_run: SyncRun, *, tenant_id: uuid.UUID, resource: str, error: Exception
    ) -> None:
        sync_run.status = "failed"
        sync_run.finished_at = datetime.now(UTC)
        sync_run.error_message = str(error)[:2000]
        state = self._state(tenant_id=tenant_id, resource=resource)
        state.last_error = sync_run.error_message

    def _state(self, *, tenant_id: uuid.UUID, resource: str) -> ResourceSyncState:
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


class HistoricalBackfillService:
    """Run resources concurrently while a shared client enforces one API budget."""

    def __init__(self, *, session_factory: Callable[[], Session], alegra: AlegraClient) -> None:
        self._session_factory = session_factory
        self._alegra = alegra

    async def run(
        self,
        *,
        tenant_id: uuid.UUID,
        resources: Sequence[AlegraResource],
        resource_concurrency: int = 2,
        page_concurrency: int = 4,
        detail_concurrency: int = 6,
        hydrate_details: bool = True,
    ) -> list[BackfillResult]:
        if resource_concurrency < 1:
            raise ValueError("resource_concurrency must be positive")
        semaphore = asyncio.Semaphore(resource_concurrency)

        async def run_resource(resource: AlegraResource) -> BackfillResult:
            async with semaphore:
                with self._session_factory() as session:
                    resource_sync = ResourceSyncService(session=session, alegra=self._alegra)
                    return await resource_sync.run_initial_sync(
                        tenant_id=tenant_id,
                        resource=resource,
                        page_concurrency=page_concurrency,
                        detail_concurrency=detail_concurrency,
                        hydrate_details=hydrate_details,
                    )

        return list(await asyncio.gather(*(run_resource(resource) for resource in resources)))


def _result(sync_run: SyncRun) -> BackfillResult:
    return BackfillResult(
        resource=sync_run.resource,
        run_id=str(sync_run.id),
        status=sync_run.status,
        records_read=sync_run.records_read,
        records_written=sync_run.records_written,
        error_message=sync_run.error_message,
    )
