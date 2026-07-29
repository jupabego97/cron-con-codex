"""Single-user dashboard session endpoints and access controls."""

import hmac
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.core.config import get_settings

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


def require_dashboard_session(request: Request) -> UUID:
    tenant_id = configured_dashboard_tenant()
    if request.session.get("dashboard_tenant_id") != str(tenant_id):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required"
        )
    return tenant_id


@router.post("/session", status_code=status.HTTP_204_NO_CONTENT)
def create_session(payload: LoginPayload, request: Request) -> None:
    settings = get_settings()
    tenant_id = configured_dashboard_tenant()
    if settings.dashboard_password is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Dashboard is not configured",
        )
    if not hmac.compare_digest(payload.password, settings.dashboard_password.get_secret_value()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid password")
    request.session.clear()
    request.session["dashboard_tenant_id"] = str(tenant_id)


@router.get("/session")
def get_session(request: Request) -> dict[str, bool]:
    try:
        require_dashboard_session(request)
    except HTTPException as error:
        if error.status_code == status.HTTP_401_UNAUTHORIZED:
            return {"authenticated": False}
        raise
    return {"authenticated": True}


@router.delete("/session", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(request: Request) -> None:
    request.session.clear()
