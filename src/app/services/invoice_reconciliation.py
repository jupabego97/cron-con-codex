import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from app.db.models import SyncRun
from app.domain.invoice_repository import upsert_invoice
from app.integrations.alegra.client import AlegraClient


class InvoiceReconciliationService:
    """Periodic safety net for events missed while webhooks or workers were unavailable."""

    def __init__(self, *, session: Session, alegra: AlegraClient) -> None:
        self._session = session
        self._alegra = alegra

    async def reconcile_recent(self, *, tenant_id: uuid.UUID, lookback_days: int = 30) -> SyncRun:
        if lookback_days < 1:
            raise ValueError("lookback_days must be positive")
        sync_run = SyncRun(
            tenant_id=tenant_id, resource="invoice", mode="reconcile", status="running"
        )
        self._session.add(sync_run)
        self._session.commit()
        try:
            first_day = date.today() - timedelta(days=lookback_days - 1)
            for offset in range(lookback_days):
                day = first_day + timedelta(days=offset)
                async for payload in self._alegra.iter_invoices_for_date(day.isoformat()):
                    sync_run.records_read += 1
                    _, created = upsert_invoice(
                        self._session,
                        tenant_id=tenant_id,
                        payload=payload,
                        sync_run_id=sync_run.id,
                    )
                    sync_run.records_written += int(created)
                self._session.commit()
            sync_run.status = "succeeded"
            sync_run.finished_at = datetime.now(UTC)
            self._session.commit()
            return sync_run
        except Exception as error:
            self._session.rollback()
            sync_run.status = "failed"
            sync_run.finished_at = datetime.now(UTC)
            sync_run.error_message = str(error)[:2000]
            self._session.add(sync_run)
            self._session.commit()
            raise
