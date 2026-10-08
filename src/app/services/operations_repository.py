"""Tenant-scoped persistence and audit helpers for locally owned business records."""

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


class OperationsRepository:
    def __init__(self, *, session: Session, tenant_id: uuid.UUID) -> None:
        self.session = session
        self.tenant_id = tenant_id

    def rows(self, sql: str, params: dict | None = None) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.session.execute(
                text(sql), {**(params or {}), "tenant_id": self.tenant_id}
            ).mappings()
        ]

    def one(self, sql: str, params: dict | None = None) -> dict[str, Any] | None:
        rows = self.rows(sql, params)
        return rows[0] if rows else None

    def dimension(self, table: str, key: int | None) -> None:
        if key is None:
            return
        if table not in {"dim_product", "dim_contact"}:
            raise ValueError("Invalid catalog")
        if not self.one(
            f"SELECT key FROM {table} WHERE tenant_id=:tenant_id AND key=:key AND is_deleted=false",
            {"key": key},
        ):
            raise LookupError("El producto o contacto no existe en esta empresa")

    def audit(self, action: str, entity: str, entity_id: Any, details: dict) -> None:
        self.session.execute(
            text("""INSERT INTO operational_audit
          (id,tenant_id,action,entity_type,entity_id,details)
          VALUES (:id,:tenant,:action,:entity,:entity_id,CAST(:details AS jsonb))"""),
            {
                "id": uuid.uuid4(),
                "tenant": self.tenant_id,
                "action": action,
                "entity": entity,
                "entity_id": str(entity_id),
                "details": json.dumps(details, default=str),
            },
        )
