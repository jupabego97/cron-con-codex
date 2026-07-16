import argparse
import asyncio
import uuid
from contextlib import suppress

from alembic import command
from alembic.config import Config

from app.core.config import get_settings
from app.db.models import Tenant
from app.db.session import get_session_factory
from app.integrations.alegra.client import AlegraClient
from app.services.invoice_reconciliation import InvoiceReconciliationService
from app.services.invoice_sync import InvoiceSyncService
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


if __name__ == "__main__":
    main()
