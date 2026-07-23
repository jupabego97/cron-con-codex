"""Idempotent persistence for generic Alegra resources."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AlegraEntity, RawAlegraDocument
from app.domain.invoices import canonical_payload_hash


def upsert_alegra_entity(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    resource: str,
    payload: dict[str, Any],
    sync_run_id: uuid.UUID | None = None,
) -> tuple[AlegraEntity, bool]:
    """Store an immutable raw revision and update the resource's current snapshot."""
    external_id = _external_id(payload, resource)
    payload_hash = canonical_payload_hash(payload)
    raw_version = session.scalar(
        select(RawAlegraDocument).where(
            RawAlegraDocument.tenant_id == tenant_id,
            RawAlegraDocument.entity_type == resource,
            RawAlegraDocument.external_id == external_id,
            RawAlegraDocument.payload_hash == payload_hash,
        )
    )
    if raw_version is None:
        session.add(
            RawAlegraDocument(
                tenant_id=tenant_id,
                sync_run_id=sync_run_id,
                entity_type=resource,
                external_id=external_id,
                payload=payload,
                payload_hash=payload_hash,
            )
        )

    entity = session.scalar(
        select(AlegraEntity).where(
            AlegraEntity.tenant_id == tenant_id,
            AlegraEntity.resource == resource,
            AlegraEntity.external_id == external_id,
        )
    )
    is_new = entity is None
    if entity is None:
        entity = AlegraEntity(
            tenant_id=tenant_id,
            resource=resource,
            external_id=external_id,
            payload=payload,
            source_hash=payload_hash,
            sync_run_id=sync_run_id,
        )
        session.add(entity)
    else:
        entity.payload = payload
        entity.source_hash = payload_hash
        entity.sync_run_id = sync_run_id
        entity.is_deleted = False
        entity.last_seen_at = datetime.now(UTC)
    return entity, is_new


def mark_alegra_entity_deleted(
    session: Session, *, tenant_id: uuid.UUID, resource: str, external_id: str
) -> bool:
    entity = session.scalar(
        select(AlegraEntity).where(
            AlegraEntity.tenant_id == tenant_id,
            AlegraEntity.resource == resource,
            AlegraEntity.external_id == external_id,
        )
    )
    if entity is None:
        return False
    entity.is_deleted = True
    entity.last_seen_at = datetime.now(UTC)
    return True


def _external_id(payload: dict[str, Any], resource: str) -> str:
    external_id = payload.get("id")
    if external_id is None:
        raise ValueError(f"Alegra {resource} payload does not contain an id")
    return str(external_id)
