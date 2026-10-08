from fastapi import APIRouter, HTTPException, status
from sqlalchemy import text

from app.db.session import get_engine

router = APIRouter(tags=["platform"])

# The API's schema contract is unchanged by the additive CLI-only mart ledger.
# Explicit compatibility allows an expand-first rollout without accepting an
# unknown/older schema or making the previous API unhealthy during migration.
_COMPATIBLE_SCHEMA_REVISIONS = {"20261008_20", "20261008_21"}


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
            if revision not in _COMPATIBLE_SCHEMA_REVISIONS:
                raise RuntimeError("Pending database migrations")
    except Exception as error:
        raise HTTPException(status_code=503, detail="Database or schema is not ready") from error
    return {"status": "ready"}
