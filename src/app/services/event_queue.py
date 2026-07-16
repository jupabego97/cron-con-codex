import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import InboundEvent
from app.domain.webhooks import ParsedWebhookEvent


def enqueue_event(
    session: Session, *, tenant_id: uuid.UUID, event: ParsedWebhookEvent
) -> tuple[InboundEvent, bool]:
    """Persist an event before acknowledging the webhook; duplicates are harmless."""
    existing = session.scalar(
        select(InboundEvent).where(
            InboundEvent.tenant_id == tenant_id,
            InboundEvent.subject == event.subject,
            InboundEvent.payload_hash == event.payload_hash,
        )
    )
    if existing is not None:
        return existing, False

    queued = InboundEvent(
        tenant_id=tenant_id,
        subject=event.subject,
        entity_type=event.entity_type,
        external_id=event.external_id,
        payload=event.payload,
        payload_hash=event.payload_hash,
    )
    try:
        with session.begin_nested():
            session.add(queued)
            session.flush()
    except IntegrityError:
        duplicate = session.scalar(
            select(InboundEvent).where(
                InboundEvent.tenant_id == tenant_id,
                InboundEvent.subject == event.subject,
                InboundEvent.payload_hash == event.payload_hash,
            )
        )
        if duplicate is None:
            raise
        return duplicate, False
    return queued, True


def claim_next_event(session: Session) -> InboundEvent | None:
    """Claim one available event using PostgreSQL row locking for safe multi-worker processing."""
    now = datetime.now(UTC)
    event = session.scalar(
        select(InboundEvent)
        .where(
            InboundEvent.status.in_(("pending", "retry_wait")),
            InboundEvent.available_at <= now,
        )
        .order_by(InboundEvent.available_at, InboundEvent.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if event is None:
        return None
    event.status = "processing"
    event.attempt_count += 1
    event.locked_at = now
    return event


def complete_event(event: InboundEvent) -> None:
    event.status = "completed"
    event.processed_at = datetime.now(UTC)
    event.last_error = None


def retry_or_fail_event(event: InboundEvent, error: Exception, *, max_attempts: int = 8) -> None:
    event.last_error = str(error)[:2000]
    event.locked_at = None
    if event.attempt_count >= max_attempts:
        event.status = "failed"
        return
    event.status = "retry_wait"
    event.available_at = datetime.now(UTC) + timedelta(seconds=min(2**event.attempt_count, 3600))
