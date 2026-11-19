"""Import the durable inbox, refresh procurement inputs, and publish one daily cut."""

import asyncio
import time
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import httpx
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.business_time import business_today
from app.core.config import get_settings
from app.db.session import get_engine, get_session_factory
from app.domain.daily_report import DailyReport
from app.domain.webhooks import ALEGRA_WEBHOOK_SUBJECTS, parse_alegra_webhook
from app.integrations.alegra.client import AlegraClient
from app.services.analytics_mart import AnalyticsMartService
from app.services.event_queue import enqueue_event
from app.services.inventory_snapshot import InventorySnapshotService
from app.services.invoice_reconciliation import InvoiceReconciliationService
from app.services.procurement_planning import ProcurementPlanningService
from app.services.procurement_sync import ProcurementReconciliationService
from app.services.sales_cost_allocation import HistoricalSalesCostService
from app.services.webhook_worker import WebhookWorker


def build_report(preview: dict) -> DailyReport:
    quality = preview["data_quality"]
    if not quality["ready"]:
        raise RuntimeError("No se publicó el informe: " + "; ".join(quality["warnings"]))
    groups = preview["supplier_orders"]
    count = preview["summary"]["recommended_products"]
    if sum(len(group["products"]) for group in groups) != count:
        raise RuntimeError("El informe de proveedores está incompleto")
    return DailyReport(
        as_of_date=preview["as_of_date"],
        generated_at=datetime.now(UTC),
        snapshot_at=quality["snapshot_at"],
        currency_code=preview["currency_code"],
        weekly_budget=preview["weekly_budget"],
        recommended_products=count,
        recommended_value=preview["summary"]["recommended_value"],
        suppliers=groups,
    )


async def import_events(
    client: httpx.AsyncClient, session: Session, *, tenant_id: UUID, through: int
) -> int:
    imported = 0
    while through > 0:
        response = await client.get("/internal/events", params={"through": through})
        response.raise_for_status()
        events = response.json()
        if not events:
            break
        for row in events:
            _, created = enqueue_event(
                session, tenant_id=tenant_id, event=parse_alegra_webhook(row["payload"])
            )
            imported += int(created)
        # A failed commit leaves the source batch intact. A failed ack can be replayed.
        session.commit()
        (await client.post(
            "/internal/events/ack", json={"through": events[-1]["id"]}
        )).raise_for_status()
    return imported


async def drain_queue(session: Session, *, tenant_id: UUID, alegra: AlegraClient) -> None:
    deadline = time.monotonic() + 30 * 60
    worker = WebhookWorker(session=session, alegra=alegra, tenant_id=tenant_id)
    while time.monotonic() < deadline:
        await worker.run_batch(max_seconds=90)
        counts = dict(session.execute(text("""
          SELECT status,count(*) FROM inbound_events
          WHERE tenant_id=:tenant AND status<>'completed' GROUP BY status
        """), {"tenant": tenant_id}).all())
        session.rollback()
        if counts.get("failed", 0):
            raise RuntimeError(f"Hay {counts['failed']} webhooks fallidos; revisar la cola")
        if not counts:
            return
        await asyncio.sleep(5)
    raise RuntimeError("La cola no quedó al día durante la ventana nocturna")


async def wait_for_postgres() -> None:
    deadline = time.monotonic() + 180
    while True:
        try:
            with get_engine().connect() as connection:
                connection.execute(text("SELECT 1"))
            return
        except OperationalError:
            if time.monotonic() >= deadline:
                raise
            await asyncio.sleep(5)


async def publish_daily(
    *, tenant_id: UUID, weekly_budget: Decimal, lookback_days: int, force: bool = False
) -> None:
    settings = get_settings()
    base = settings.daily_report_url
    token = settings.daily_report_publish_token
    if not base or not base.startswith("https://") or token is None:
        raise RuntimeError("Configura DAILY_REPORT_URL (HTTPS) y DAILY_REPORT_PUBLISH_TOKEN")
    if settings.alegra_api_basic_token is None or tenant_id != settings.dashboard_tenant_id:
        raise RuntimeError("Credenciales de Alegra o empresa del informe inválidas")
    if not weekly_budget.is_finite() or weekly_budget < 0 or not 1 <= lookback_days <= 90:
        raise ValueError("Presupuesto o ventana de conciliación inválidos")
    currency_code = settings.analytics_default_currency_code
    async with httpx.AsyncClient(
        base_url=base.rstrip("/"),
        headers={"Authorization": f"Bearer {token.get_secret_value()}"},
        timeout=30,
    ) as viewer:
        response = await viewer.get("/internal/status")
        response.raise_for_status()
        if not force and response.json()["as_of_date"] == business_today().isoformat():
            print("daily-report skipped=already_published", flush=True)
            return
        await wait_for_postgres()
        lock_key = {"key": f"daily-replenishment:{tenant_id}"}
        with get_engine().connect().execution_options(isolation_level="AUTOCOMMIT") as lock:
            acquired = lock.scalar(
                text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))"), lock_key
            )
            if not acquired:
                raise RuntimeError("Ya hay una ejecución nocturna en curso")
            try:
                await _refresh_and_publish(
                    viewer, tenant_id=tenant_id, weekly_budget=weekly_budget,
                    currency_code=currency_code, lookback_days=lookback_days,
                    alegra_token=settings.alegra_api_basic_token.get_secret_value(),
                )
            finally:
                lock.execute(text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"), lock_key)


async def _refresh_and_publish(
    viewer: httpx.AsyncClient, *, tenant_id: UUID, weekly_budget: Decimal,
    currency_code: str, lookback_days: int, alegra_token: str,
) -> None:
    response = await viewer.get("/internal/status")
    response.raise_for_status()
    through = response.json()["through"]
    with get_session_factory()() as session:
        async with AlegraClient(basic_token=alegra_token) as alegra:
            subscriptions = await alegra.list_webhook_subscriptions()
            active = {
                record.get("event") for record in subscriptions
                if str(record.get("status") or "ACTIVE").upper() == "ACTIVE"
            }
            if missing := ALEGRA_WEBHOOK_SUBJECTS - active:
                raise RuntimeError("Faltan webhooks activos: " + ", ".join(sorted(missing)))
            imported = await import_events(viewer, session, tenant_id=tenant_id, through=through)
            await drain_queue(session, tenant_id=tenant_id, alegra=alegra)
            print(f"daily-report inbox_imported={imported}", flush=True)
            await InvoiceReconciliationService(session=session, alegra=alegra).reconcile_recent(
                tenant_id=tenant_id, lookback_days=lookback_days,
            )
            await ProcurementReconciliationService(session=session, alegra=alegra).run(
                tenant_id=tenant_id, lookback_days=lookback_days,
            )
            snapshot = await InventorySnapshotService(session=session, alegra=alegra).capture(
                tenant_id=tenant_id,
            )
            if not snapshot.records_written:
                raise RuntimeError("Alegra no devolvió observaciones de inventario")
            print(f"daily-report snapshot_rows={snapshot.records_written}", flush=True)
        mart = AnalyticsMartService(session=session, default_currency_code=currency_code).refresh(
            tenant_id=tenant_id,
        )
        costs = HistoricalSalesCostService(session=session).allocate(
            tenant_id=tenant_id, only_if_changed=True,
        )
        print(f"daily-report mart_rows={mart.records_written} cost={costs.status}", flush=True)
        preview = ProcurementPlanningService(session=session, tenant_id=tenant_id).preview(
            as_of=business_today(), weekly_budget=weekly_budget, currency_code=currency_code,
            limit=5000,
        )
        if preview["data_quality"].get("snapshot_run_id") != snapshot.run_id:
            raise RuntimeError("El mart no contiene la captura de esta ejecución")
        report = build_report(preview)
    (await viewer.put("/internal/report", content=report.model_dump_json())).raise_for_status()
    print(
        f"daily-report published cut={report.as_of_date} "
        f"suppliers={len(report.suppliers)} products={report.recommended_products}",
        flush=True,
    )
