import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.db.models import SyncRun
from app.domain.invoice_repository import upsert_invoice
from app.integrations.alegra.client import AlegraClient


class InvoiceSyncService:
    def __init__(self, *, session: Session, alegra: AlegraClient) -> None:
        self._session = session
        self._alegra = alegra

    async def run_initial_sync(self, *, tenant_id: uuid.UUID) -> SyncRun:
        """Run a complete, resumable-at-run-level invoice backfill.

        A later reconciliation worker covers changes while this initial offset-based
        scan is running. This method deliberately never derives page offsets from IDs.
        """
        sync_run = SyncRun(
            tenant_id=tenant_id, resource="invoice", mode="initial", status="running"
        )
        self._session.add(sync_run)
        self._session.commit()

        try:
            async for payload in self._alegra.iter_all_invoices():
                sync_run.records_read += 1
                _, created = upsert_invoice(
                    self._session,
                    tenant_id=tenant_id,
                    payload=payload,
                    sync_run_id=sync_run.id,
                )
                sync_run.records_written += int(created)
                if sync_run.records_read % 50 == 0:
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
