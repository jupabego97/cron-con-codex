import argparse
import asyncio
import uuid
from contextlib import suppress

from alembic import command
from alembic.config import Config

from app.core.config import get_settings
from app.db.models import Tenant
from app.db.session import get_session_factory
from app.domain.batch_repository import rebuild_purchase_bill_lines
from app.integrations.alegra.client import AlegraClient
from app.integrations.alegra.resources import resolve_resources
from app.services.analytics_mart import AnalyticsMartService
from app.services.inventory_snapshot import InventorySnapshotService
from app.services.invoice_reconciliation import InvoiceReconciliationService
from app.services.invoice_sync import InvoiceSyncService
from app.services.resource_sync import BackfillProgress, HistoricalBackfillService
from app.services.webhook_worker import WebhookWorker


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="retail-platform")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("migrate", help="Apply all pending database migrations")

    tenant = subparsers.add_parser("create-tenant", help="Create a tenant if its slug is new")
    tenant.add_argument("slug")
    tenant.add_argument("name")

    sync = subparsers.add_parser(
        "sync-invoices", help="Run an explicit Alegra invoice synchronization"
    )
    sync.add_argument("tenant_id", type=uuid.UUID)
    sync.add_argument("--mode", choices=("initial", "reconcile"), default="initial")
    sync.add_argument("--lookback-days", type=int, default=30)

    worker = subparsers.add_parser("worker", help="Process the durable webhook queue")
    worker.add_argument("--poll-seconds", type=float, default=5.0)

    backfill = subparsers.add_parser(
        "backfill-all", help="Extract complete history for all supported business resources"
    )
    backfill.add_argument("tenant_id", type=uuid.UUID)
    backfill.add_argument(
        "--resources",
        default="all",
        help=(
            "all or comma-separated keys: contact,item,warehouse,seller,invoice,bill,payment,"
            "credit_note,inventory_adjustment,warehouse_transfer"
        ),
    )
    backfill.add_argument("--resource-concurrency", type=int, default=4)
    backfill.add_argument("--page-concurrency", type=int, default=6)
    backfill.add_argument("--detail-concurrency", type=int, default=8)
    backfill.add_argument("--write-batch-size", type=int, default=200)
    backfill.add_argument(
        "--requests-per-minute",
        type=int,
        default=130,
        help="shared API budget, capped by Alegra at 150 requests per minute",
    )
    backfill.add_argument(
        "--skip-details",
        action="store_true",
        help="store listing responses only; use only for a faster non-canonical bootstrap",
    )

    mart = subparsers.add_parser(
        "refresh-mart",
        help="Rebuild the tenant analytics data mart from operational PostgreSQL projections",
    )
    mart.add_argument("tenant_id", type=uuid.UUID)

    inventory_snapshot = subparsers.add_parser(
        "snapshot-inventory",
        help="Capture current Alegra stock by warehouse into immutable snapshots",
    )
    inventory_snapshot.add_argument("tenant_id", type=uuid.UUID)
    inventory_snapshot.add_argument("--warehouse-concurrency", type=int, default=3)

    repair_purchases = subparsers.add_parser(
        "repair-purchase-lines",
        help="Rebuild purchase lines from stored canonical bill payloads without calling Alegra",
    )
    repair_purchases.add_argument("tenant_id", type=uuid.UUID)
    repair_purchases.add_argument("--write-batch-size", type=int, default=200)
    return parser


def migrate() -> None:
    config = Config("alembic.ini")
    command.upgrade(config, "head")


def create_tenant(*, slug: str, name: str) -> None:
    with get_session_factory()() as session:
        existing = session.query(Tenant).filter(Tenant.slug == slug).one_or_none()
        if existing is not None:
            print(existing.id)
            return
        tenant = Tenant(slug=slug, name=name)
        session.add(tenant)
        session.commit()
        print(tenant.id)


async def sync_invoices(*, tenant_id: uuid.UUID, mode: str, lookback_days: int) -> None:
    settings = get_settings()
    if settings.alegra_api_basic_token is None:
        raise RuntimeError("ALEGRA_API_BASIC_TOKEN is required for sync-invoices")
    with get_session_factory()() as session:
        async with AlegraClient(
            basic_token=settings.alegra_api_basic_token.get_secret_value()
        ) as alegra:
            if mode == "initial":
                run = await InvoiceSyncService(session=session, alegra=alegra).run_initial_sync(
                    tenant_id=tenant_id
                )
            else:
                run = await InvoiceReconciliationService(
                    session=session, alegra=alegra
                ).reconcile_recent(tenant_id=tenant_id, lookback_days=lookback_days)
    print(f"{run.id} {run.status} read={run.records_read} created={run.records_written}")


async def process_webhooks(*, poll_seconds: float) -> None:
    settings = get_settings()
    if settings.alegra_api_basic_token is None:
        raise RuntimeError("ALEGRA_API_BASIC_TOKEN is required for worker")
    if poll_seconds <= 0:
        raise ValueError("poll_seconds must be positive")
    with get_session_factory()() as session:
        async with AlegraClient(
            basic_token=settings.alegra_api_basic_token.get_secret_value()
        ) as alegra:
            worker = WebhookWorker(session=session, alegra=alegra)
            while True:
                processed = await worker.run_once()
                if not processed:
                    await asyncio.sleep(poll_seconds)


async def backfill_all(
    *,
    tenant_id: uuid.UUID,
    resources: str,
    resource_concurrency: int,
    page_concurrency: int,
    detail_concurrency: int,
    write_batch_size: int,
    requests_per_minute: int,
    skip_details: bool,
) -> bool:
    settings = get_settings()
    if settings.alegra_api_basic_token is None:
        raise RuntimeError("ALEGRA_API_BASIC_TOKEN is required for backfill-all")
    selected_resources = resolve_resources(resources)

    def report_progress(progress: BackfillProgress) -> None:
        print(
            f"{progress.resource} progress read={progress.records_read} "
            f"written={progress.records_written}",
            flush=True,
        )

    async with AlegraClient(
        basic_token=settings.alegra_api_basic_token.get_secret_value(),
        requests_per_minute=requests_per_minute,
    ) as alegra:
        results = await HistoricalBackfillService(
            session_factory=get_session_factory(), alegra=alegra
        ).run(
            tenant_id=tenant_id,
            resources=selected_resources,
            resource_concurrency=resource_concurrency,
            page_concurrency=page_concurrency,
            detail_concurrency=detail_concurrency,
            hydrate_details=not skip_details,
            write_batch_size=write_batch_size,
            progress_callback=report_progress,
        )
    for result in results:
        message = (
            f"{result.resource} {result.status} run={result.run_id} "
            f"read={result.records_read} written={result.records_written}"
        )
        if result.error_message:
            message += f" error={result.error_message}"
        print(message)
    return all(result.status == "succeeded" for result in results)


def refresh_mart(*, tenant_id: uuid.UUID) -> None:
    with get_session_factory()() as session:
        result = AnalyticsMartService(session=session).refresh(tenant_id=tenant_id)
    print(f"{result.run_id} {result.status} written={result.records_written}")


def repair_purchase_lines(*, tenant_id: uuid.UUID, write_batch_size: int) -> None:
    with get_session_factory()() as session:
        documents, lines = rebuild_purchase_bill_lines(
            session, tenant_id=tenant_id, write_batch_size=write_batch_size
        )
    print(f"documents={documents} lines={lines}")


async def snapshot_inventory(
    *, tenant_id: uuid.UUID, warehouse_concurrency: int
) -> None:
    settings = get_settings()
    if settings.alegra_api_basic_token is None:
        raise RuntimeError("ALEGRA_API_BASIC_TOKEN is required for snapshot-inventory")
    with get_session_factory()() as session:
        async with AlegraClient(
            basic_token=settings.alegra_api_basic_token.get_secret_value(),
            requests_per_minute=110,
        ) as alegra:
            result = await InventorySnapshotService(session=session, alegra=alegra).capture(
                tenant_id=tenant_id, warehouse_concurrency=warehouse_concurrency
            )
    print(
        f"{result.run_id} {result.status} read={result.records_read} "
        f"written={result.records_written}"
    )


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "migrate":
        migrate()
    elif args.command == "create-tenant":
        create_tenant(slug=args.slug, name=args.name)
    elif args.command == "sync-invoices":
        asyncio.run(
            sync_invoices(
                tenant_id=args.tenant_id,
                mode=args.mode,
                lookback_days=args.lookback_days,
            )
        )
    elif args.command == "worker":
        with suppress(KeyboardInterrupt):
            asyncio.run(process_webhooks(poll_seconds=args.poll_seconds))
    elif args.command == "backfill-all":
        succeeded = asyncio.run(
            backfill_all(
                tenant_id=args.tenant_id,
                resources=args.resources,
                resource_concurrency=args.resource_concurrency,
                page_concurrency=args.page_concurrency,
                detail_concurrency=args.detail_concurrency,
                write_batch_size=args.write_batch_size,
                requests_per_minute=args.requests_per_minute,
                skip_details=args.skip_details,
            )
        )
        if not succeeded:
            raise SystemExit(1)
    elif args.command == "refresh-mart":
        refresh_mart(tenant_id=args.tenant_id)
    elif args.command == "repair-purchase-lines":
        repair_purchase_lines(
            tenant_id=args.tenant_id,
            write_batch_size=args.write_batch_size,
        )
    elif args.command == "snapshot-inventory":
        asyncio.run(
            snapshot_inventory(
                tenant_id=args.tenant_id,
                warehouse_concurrency=args.warehouse_concurrency,
            )
        )


if __name__ == "__main__":
    main()
