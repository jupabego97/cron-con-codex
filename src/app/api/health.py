from fastapi import APIRouter, status

router = APIRouter(tags=["platform"])


@router.get("/healthz", status_code=status.HTTP_200_OK)
def health_check() -> dict[str, str]:
    """Liveness check. It never exposes configuration or credentials."""
    return {"status": "ok"}


@router.get("/readyz", status_code=status.HTTP_200_OK)
def readiness_check() -> dict[str, str]:
    """Phase 0 readiness check; infrastructure checks arrive with Phase 1."""
    return {"status": "ready"}
