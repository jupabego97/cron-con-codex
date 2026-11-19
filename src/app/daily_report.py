"""Private report and durable webhook inbox on a volume, independent of PostgreSQL."""

import hmac
import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from app.domain.daily_report import (
    EVENT_BATCH_SIZE,
    MAX_EVENT_BYTES,
    MAX_REPORT_BYTES,
    DailyReport,
)
from app.domain.webhooks import UnsupportedWebhookEvent, parse_alegra_webhook

ASSETS = Path(__file__).resolve().parent / "daily_static"
SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS report (
  id INTEGER PRIMARY KEY CHECK (id=1),
  generated_at TEXT NOT NULL,
  payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS inbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  payload_hash TEXT UNIQUE NOT NULL,
  payload TEXT NOT NULL
);
"""


@contextmanager
def connect(path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(path, timeout=2)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


async def bounded_body(request: Request, maximum: int) -> bytes:
    buffer = bytearray()
    async for chunk in request.stream():
        if len(buffer) + len(chunk) > maximum:
            raise HTTPException(413, "Solicitud demasiado grande")
        buffer.extend(chunk)
    return bytes(buffer)


class InboxAck(BaseModel):
    through: int = Field(gt=0)


def create_app(
    *, state_path: Path, password: str, publish_token: str, webhook_token: str, tenant_slug: str
) -> FastAPI:
    if min(len(password), len(webhook_token)) < 16 or len(publish_token) < 32:
        raise ValueError("Daily report requires strong credentials")
    auth = HTTPBasic(realm="Reponer", auto_error=False)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        with connect(state_path) as connection:
            connection.executescript(SQLITE_SCHEMA)
        os.chmod(state_path, 0o600)
        yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def protect_response(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
        )
        return response

    def reader(credentials: Annotated[HTTPBasicCredentials | None, Depends(auth)]) -> None:
        if (
            credentials is None
            or credentials.username != "reponer"
            or not hmac.compare_digest(credentials.password.encode(), password.encode())
        ):
            raise HTTPException(
                401, "Acceso privado", {"WWW-Authenticate": 'Basic realm="Reponer"'}
            )

    def writer(authorization: Annotated[str | None, Header()] = None) -> None:
        expected = f"Bearer {publish_token}".encode()
        if authorization is None or not hmac.compare_digest(authorization.encode(), expected):
            raise HTTPException(401, "Credencial de publicación inválida")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        with connect(state_path) as connection:
            connection.execute("SELECT id FROM report LIMIT 1")
        return {"status": "ok"}

    @app.get("/", dependencies=[Depends(reader)])
    def index() -> FileResponse:
        return FileResponse(ASSETS / "index.html")

    @app.get("/assets/{name}")
    def asset(name: str) -> FileResponse:
        media_types = {"report.js": "text/javascript", "report.css": "text/css"}
        if name not in media_types:
            raise HTTPException(404)
        return FileResponse(ASSETS / name, media_type=media_types[name])

    @app.get("/report.json", dependencies=[Depends(reader)])
    def get_report() -> Response:
        with connect(state_path) as connection:
            row = connection.execute("SELECT payload FROM report WHERE id=1").fetchone()
        if row is None:
            raise HTTPException(404, "Aún no se ha publicado un informe")
        return Response(row[0], media_type="application/json")

    @app.get("/internal/status", dependencies=[Depends(writer)])
    def get_status() -> dict:
        with connect(state_path) as connection:
            row = connection.execute("SELECT payload FROM report WHERE id=1").fetchone()
            through = connection.execute("SELECT coalesce(max(id),0) FROM inbox").fetchone()[0]
        report = json.loads(row[0]) if row else {}
        return {"as_of_date": report.get("as_of_date"), "through": through}

    def store_report(report: DailyReport) -> bool:
        with connect(state_path) as connection:
            updated = connection.execute(
                """INSERT INTO report(id,generated_at,payload) VALUES(1,?,?)
                   ON CONFLICT(id) DO UPDATE SET generated_at=excluded.generated_at,
                     payload=excluded.payload WHERE excluded.generated_at >= report.generated_at""",
                (
                    report.generated_at.astimezone(UTC).isoformat(timespec="microseconds"),
                    report.model_dump_json(),
                ),
            )
            return updated.rowcount == 1

    @app.put("/internal/report", dependencies=[Depends(writer)], status_code=204)
    async def publish(request: Request) -> Response:
        body = await bounded_body(request, MAX_REPORT_BYTES)
        try:
            report = DailyReport.model_validate_json(body)
        except ValidationError:
            raise HTTPException(422, "Formato de informe inválido") from None
        if not await run_in_threadpool(store_report, report):
            raise HTTPException(409, "Ya existe un informe más reciente")
        return Response(status_code=204)

    def persist_event(payload_hash: str, body: str) -> bool:
        with connect(state_path) as connection:
            result = connection.execute(
                "INSERT INTO inbox(payload_hash,payload) VALUES(?,?) ON CONFLICT DO NOTHING",
                (payload_hash, body),
            )
            return result.rowcount == 1

    @app.post("/webhooks/alegra/{slug}", status_code=202, response_model=None)
    async def webhook(slug: str, request: Request, token: str | None = None) -> Response | dict:
        if slug != tenant_slug or token is None or not hmac.compare_digest(
            token.encode(), webhook_token.encode()
        ):
            raise HTTPException(401, "Credencial de webhook inválida")
        body = await bounded_body(request, MAX_EVENT_BYTES)
        try:
            payload = json.loads(body) if body else {}
        except ValueError:
            raise HTTPException(400, "JSON inválido") from None
        if not isinstance(payload, dict):
            raise HTTPException(400, "El evento debe ser un objeto")
        if not payload:
            return Response(status_code=204)
        try:
            event = parse_alegra_webhook(payload)
        except UnsupportedWebhookEvent:
            return {"status": "ignored"}
        if not event.external_id:
            raise HTTPException(422, "Falta el identificador del recurso")
        try:
            created = await run_in_threadpool(
                persist_event, event.payload_hash, json.dumps(payload, ensure_ascii=True)
            )
        except sqlite3.Error:
            raise HTTPException(503, "No se pudo guardar el evento") from None
        return {"status": "queued" if created else "duplicate"}

    @app.get("/internal/events", dependencies=[Depends(writer)])
    def pending_events(through: Annotated[int, Query(gt=0)]) -> list[dict]:
        with connect(state_path) as connection:
            rows = connection.execute(
                "SELECT id,payload FROM inbox WHERE id <= ? ORDER BY id LIMIT ?",
                (through, EVENT_BATCH_SIZE),
            ).fetchall()
        return [{"id": id_, "payload": json.loads(payload)} for id_, payload in rows]

    @app.post("/internal/events/ack", dependencies=[Depends(writer)], status_code=204)
    def acknowledge(batch: InboxAck) -> Response:
        with connect(state_path) as connection:
            connection.execute("DELETE FROM inbox WHERE id <= ?", (batch.through,))
        return Response(status_code=204)

    return app


def create_from_env() -> FastAPI:
    mount = Path(os.environ["RAILWAY_VOLUME_MOUNT_PATH"])
    if not mount.is_mount():
        raise RuntimeError("Daily report volume is not mounted")
    return create_app(
        state_path=mount / "daily-report.sqlite3",
        password=os.environ["DASHBOARD_PASSWORD"],
        publish_token=os.environ["DAILY_REPORT_PUBLISH_TOKEN"],
        webhook_token=os.environ["ALEGRA_WEBHOOK_SECRET"],
        tenant_slug=os.environ["DAILY_REPORT_TENANT_SLUG"],
    )
