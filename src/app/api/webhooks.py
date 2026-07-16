import hmac
import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Tenant
from app.db.session import get_db_session
from app.domain.webhooks import UnsupportedWebhookEvent, parse_alegra_webhook
from app.services.event_queue import enqueue_event

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _verify_secret(token: str | None) -> None:
    configured_secret = get_settings().alegra_webhook_secret
    if configured_secret is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Webhook is not configured"
        )
    if token is None or not hmac.compare_digest(token, configured_secret.get_secret_value()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook token"
        )


@router.post(
    "/alegra/{tenant_slug}",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=None,
)
async def receive_alegra_webhook(
    tenant_slug: str,
    request: Request,
    session: Annotated[Session, Depends(get_db_session)],
    token: str | None = Query(default=None, min_length=16),
) -> Response | dict[str, str]:
    """Acknowledge fast after durable storage; processing is performed by a separate worker."""
    _verify_secret(token)
    body = await request.body()
    if not body:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    try:
        payload: Any = json.loads(body)
    except json.JSONDecodeError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed JSON"
        ) from error
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Webhook payload must be an object"
        )

    try:
        parsed = parse_alegra_webhook(payload)
    except UnsupportedWebhookEvent:
        return {"status": "ignored"}

    tenant_id = session.scalar(select(Tenant.id).where(Tenant.slug == tenant_slug))
    if tenant_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown tenant")
    event, created = enqueue_event(session, tenant_id=uuid.UUID(str(tenant_id)), event=parsed)
    session.commit()
    return {"status": "queued" if created else "duplicate", "event_id": str(event.id)}
