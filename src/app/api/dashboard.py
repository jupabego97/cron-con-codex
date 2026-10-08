"""Single-user dashboard session endpoints and access controls."""

import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db_session

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])


class LoginPayload(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


def configured_dashboard_tenant() -> UUID:
    tenant_id = get_settings().dashboard_tenant_id
    if tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Dashboard is not configured",
        )
    return tenant_id


def _password_version() -> str:
    password = get_settings().dashboard_password
    settings = get_settings()
    secret = (
        settings.app_secret_key.get_secret_value()
        if settings.app_secret_key
        else "local-dashboard-development-secret"
    )
    return (
        hmac.new(secret.encode(), password.get_secret_value().encode(), hashlib.sha256).hexdigest()
        if password
        else ""
    )


def require_dashboard_session(
    request: Request, session: Annotated[Session, Depends(get_db_session)]
) -> UUID:
    tenant_id = configured_dashboard_tenant()
    if request.session.get("dashboard_tenant_id") != str(tenant_id):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required"
        )
    if request.session.get("password_version") != _password_version():
        request.session.clear()
        raise HTTPException(401, detail="La sesión expiró; ingresa de nuevo")
    generation = (
        session.execute(
            text("SELECT session_generation FROM dashboard_security_state WHERE tenant_id=:tenant"),
            {"tenant": tenant_id},
        ).scalar_one_or_none()
        or 0
    )
    if request.session.get("generation", 0) != generation:
        request.session.clear()
        raise HTTPException(401, detail="La sesión fue revocada")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if request.headers.get("sec-fetch-site") == "cross-site" or (
            origin
            and (
                urlsplit(origin).netloc != request.headers.get("host")
                or (get_settings().app_env == "production" and urlsplit(origin).scheme != "https")
            )
        ):
            raise HTTPException(403, detail="Origen no permitido")
    return tenant_id


@router.post("/session", status_code=status.HTTP_204_NO_CONTENT)
def create_session(
    payload: LoginPayload, request: Request, session: Annotated[Session, Depends(get_db_session)]
) -> None:
    settings = get_settings()
    tenant_id = configured_dashboard_tenant()
    if settings.dashboard_password is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Dashboard is not configured",
        )
    identity = f"dashboard:{tenant_id}"
    session.execute(
        text("""INSERT INTO dashboard_login_limits(identity,attempted_at,attempt_count)
      VALUES (:id,now(),0) ON CONFLICT(identity) DO NOTHING"""),
        {"id": identity},
    )
    row = (
        session.execute(
            text("SELECT * FROM dashboard_login_limits WHERE identity=:id FOR UPDATE"),
            {"id": identity},
        )
        .mappings()
        .one()
    )
    now = datetime.now(UTC)
    if row["locked_until"] and row["locked_until"] > now:
        session.commit()
        raise HTTPException(
            429, detail="Demasiados intentos; espera 15 minutos", headers={"Retry-After": "900"}
        )
    if not hmac.compare_digest(
        payload.password.encode(), settings.dashboard_password.get_secret_value().encode()
    ):
        count = row["attempt_count"] + 1 if now - row["attempted_at"] < timedelta(minutes=15) else 1
        session.execute(
            text("""UPDATE dashboard_login_limits SET attempt_count=:count,
          attempted_at=:now,locked_until=:until WHERE identity=:id"""),
            {
                "id": identity,
                "count": count,
                "now": now,
                "until": now + timedelta(minutes=15) if count >= 10 else None,
            },
        )
        session.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid password")
    session.execute(
        text(
            "UPDATE dashboard_login_limits SET attempt_count=0,locked_until=NULL WHERE identity=:id"
        ),
        {"id": identity},
    )
    generation = (
        session.execute(
            text("SELECT session_generation FROM dashboard_security_state WHERE tenant_id=:tenant"),
            {"tenant": tenant_id},
        ).scalar_one_or_none()
        or 0
    )
    session.commit()
    request.session.clear()
    request.session["dashboard_tenant_id"] = str(tenant_id)
    request.session["password_version"] = _password_version()
    request.session["generation"] = generation


@router.get("/session")
def get_session(
    request: Request, session: Annotated[Session, Depends(get_db_session)]
) -> dict[str, bool]:
    try:
        require_dashboard_session(request, session)
    except HTTPException as error:
        if error.status_code == status.HTTP_401_UNAUTHORIZED:
            return {"authenticated": False}
        raise
    return {"authenticated": True}


@router.delete("/session", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(request: Request) -> None:
    request.session.clear()


@router.post("/sessions/revoke", status_code=204)
def revoke_sessions(
    request: Request,
    tenant: Annotated[UUID, Depends(require_dashboard_session)],
    session: Annotated[Session, Depends(get_db_session)],
) -> None:
    session.execute(
        text("""INSERT INTO dashboard_security_state(tenant_id,session_generation)
      VALUES (:tenant,1) ON CONFLICT(tenant_id) DO UPDATE
        SET session_generation=dashboard_security_state.session_generation+1"""),
        {"tenant": tenant},
    )
    session.commit()
    request.session.clear()
