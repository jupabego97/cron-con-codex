"""Durable date checkpoints with ownership fencing for restarted reconcilers."""

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.db.models import SyncRun


class LostSyncLease(RuntimeError):
    """An expired worker must not overwrite its replacement's results."""


def guard(session: Session, run: SyncRun, token: uuid.UUID) -> None:
    # Do not flush a stale ORM instance before verifying ownership in PostgreSQL.
    with session.no_autoflush:
        owned = session.scalar(
            select(SyncRun.id)
            .where(
                SyncRun.id == run.id,
                SyncRun.status == "running",
                SyncRun.lease_token == token,
            )
            .with_for_update()
        )
    if owned is None:
        raise LostSyncLease("Reconciliation ownership expired; stop this worker")
    run.heartbeat_at = datetime.now(UTC)


def resume_or_start(
    session: Session, *, tenant_id: uuid.UUID, resource: str, first: date, last: date
) -> tuple[SyncRun, date]:
    now = datetime.now(UTC)
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"sync:{tenant_id}:{resource}"},
    )
    scope = (
        SyncRun.tenant_id == tenant_id,
        SyncRun.resource == resource,
        SyncRun.mode == "reconcile",
        SyncRun.status == "running",
    )
    cutoff = now - timedelta(hours=1)
    active = session.scalar(
        select(SyncRun.id)
        .where(*scope, text("COALESCE(heartbeat_at,started_at) > :cutoff"))
        .params(cutoff=cutoff)
        .limit(1)
    )
    if active is not None:
        raise RuntimeError(f"Reconciliation of {resource} already has an active run")
    session.execute(
        update(SyncRun)
        .where(*scope)
        .values(
            status="failed",
            finished_at=now,
            lease_token=None,
            error_message="Previous worker lease expired",
        )
    )
    run = session.scalar(
        select(SyncRun)
        .where(
            SyncRun.tenant_id == tenant_id,
            SyncRun.resource == resource,
            SyncRun.mode == "reconcile",
            SyncRun.status == "failed",
            SyncRun.window_from == first,
            SyncRun.window_to == last,
        )
        .order_by(SyncRun.started_at.desc())
        .limit(1)
        .with_for_update()
    )
    if run is None:
        run = SyncRun(
            tenant_id=tenant_id,
            resource=resource,
            mode="reconcile",
            status="running",
            window_from=first,
            window_to=last,
        )
        session.add(run)
    run.status = "running"
    run.finished_at = None
    run.error_message = None
    run.heartbeat_at = now
    run.lease_token = uuid.uuid4()
    next_day = run.checkpoint_date + timedelta(days=1) if run.checkpoint_date else first
    session.commit()
    return run, next_day


def checkpoint(session: Session, run: SyncRun, day: date, token: uuid.UUID | None = None) -> None:
    guard(session, run, token or run.lease_token)
    run.checkpoint_date = day
    session.commit()
