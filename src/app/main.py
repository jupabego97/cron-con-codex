import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.api.analytics import router as analytics_router
from app.api.dashboard import router as dashboard_router
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
    app.add_middleware(
        SessionMiddleware,
        secret_key=(
            settings.app_secret_key.get_secret_value()
            if settings.app_secret_key is not None
            else "local-dashboard-development-secret"
        ),
        session_cookie="retail_dashboard_session",
        same_site="lax",
        https_only=settings.app_env == "production",
        max_age=60 * 60 * 24 * 7,
    )
    app.include_router(health_router)
    app.include_router(webhook_router)
    app.include_router(dashboard_router)
    app.include_router(analytics_router)
    _mount_dashboard(app)
    return app


def _mount_dashboard(app: FastAPI, static_dir: Path | None = None) -> None:
    """Serve the compiled SPA only when it is included in the image."""
    static_dir = static_dir or Path(__file__).resolve().parent / "static"
    index_file = static_dir / "index.html"
    if not index_file.is_file():
        return
    app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="dashboard-assets")

    @app.get("/", include_in_schema=False)
    def dashboard_root() -> FileResponse:
        return FileResponse(index_file)

    @app.get("/{path:path}", include_in_schema=False)
    def dashboard_spa(path: str) -> FileResponse:
        del path
        return FileResponse(index_file)


app = create_app()
