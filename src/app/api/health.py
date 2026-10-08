from fastapi import APIRouter, HTTPException, status
from sqlalchemy import text

from app.db.session import get_engine

router = APIRouter(tags=["platform"])


@router.get("/healthz", status_code=status.HTTP_200_OK)
def health_check() -> dict[str, str]:
    """Liveness check. It never exposes configuration or credentials."""
    return {"status": "ok"}


@router.get("/readyz", status_code=status.HTTP_200_OK)
def readiness_check() -> dict[str, str]:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            if revision != "20261008_20":
                raise RuntimeError("Pending database migrations")
    except Exception as error:
        raise HTTPException(status_code=503, detail="Database or schema is not ready") from error
    return {"status": "ready"}
