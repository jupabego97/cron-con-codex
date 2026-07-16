import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.webhooks import router as webhook_router
from app.core.config import get_settings


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.app_log_level)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        logging.getLogger(__name__).info("application_started environment=%s", settings.app_env)
        yield
        logging.getLogger(__name__).info("application_stopped")

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.app_env != "production" else None,
        redoc_url=None,
    )
    app.include_router(health_router)
    app.include_router(webhook_router)
    return app


app = create_app()
