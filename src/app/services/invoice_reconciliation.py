import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.business_time import business_today
from app.db.models import SyncRun
from app.domain.invoice_repository import upsert_invoice
from app.integrations.alegra.client import AlegraClient
from app.services.sync_checkpoint import LostSyncLease, checkpoint, guard, resume_or_start


class InvoiceReconciliationService:
    """Periodic safety net for events missed while webhooks or workers were unavailable."""

    def __init__(self, *, session: Session, alegra: AlegraClient) -> None:
        self._session = session
        self._alegra = alegra

    async def reconcile_recent(self, *, tenant_id: uuid.UUID, lookback_days: int = 60) -> SyncRun:
        if lookback_days < 1:
            raise ValueError("lookback_days must be positive")
        today = business_today()
        first_day = today - timedelta(days=lookback_days - 1)
        sync_run, next_day = resume_or_start(
            self._session,
            tenant_id=tenant_id,
            resource="invoice",
            first=first_day,
            last=today,
        )
        lease = sync_run.lease_token
        try:
            for offset in range((today - next_day).days + 1):
                day = next_day + timedelta(days=offset)
                async for payload in self._alegra.iter_invoices_for_date(day.isoformat()):
                    guard(self._session, sync_run, lease)
                    sync_run.records_read += 1
                    _, created = upsert_invoice(
                        self._session,
                        tenant_id=tenant_id,
                        payload=payload,
                        sync_run_id=sync_run.id,
                    )
                    sync_run.records_written += int(created)
                checkpoint(self._session, sync_run, day, lease)
            guard(self._session, sync_run, lease)
            sync_run.status = "succeeded"
            sync_run.finished_at = datetime.now(UTC)
            self._session.commit()
            return sync_run
        except Exception as error:
            self._session.rollback()
            try:
                guard(self._session, sync_run, lease)
            except LostSyncLease:
                self._session.rollback()
                raise error from None
            sync_run.status = "failed"
            sync_run.finished_at = datetime.now(UTC)
            sync_run.error_message = str(error)[:2000]
            self._session.add(sync_run)
            self._session.commit()
            raise
