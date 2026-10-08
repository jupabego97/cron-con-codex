"""Low-cardinality timing logs; never log URL queries or SQL parameters."""

import logging
import time
from uuid import uuid4

from sqlalchemy import event
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


class RedactAccessQuery(logging.Filter):
    def filter(self, record):
        if isinstance(record.args, tuple) and len(record.args) == 5:
            args = list(record.args)
            args[2] = str(args[2]).split("?", 1)[0]
            record.args = tuple(args)
        return True


class RequestTimingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        request_id = uuid4().hex
        started = time.perf_counter()
        response = await call_next(request)
        route = request.scope.get("route")
        logger.info(
            "http_request id=%s method=%s route=%s status=%s duration_ms=%.1f",
            request_id,
            request.method,
            getattr(route, "path", "unmatched"),
            response.status_code,
            (time.perf_counter() - started) * 1000,
        )
        response.headers["X-Request-ID"] = request_id
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "frame-ancestors 'none'; base-uri 'self'; object-src 'none'"
        )
        return response


def instrument_engine(engine):
    @event.listens_for(engine, "before_cursor_execute")
    def before(conn, cursor, statement, parameters, context, executemany):
        context._query_started = time.perf_counter()

    @event.listens_for(engine, "after_cursor_execute")
    def after(conn, cursor, statement, parameters, context, executemany):
        elapsed = (time.perf_counter() - context._query_started) * 1000
        if elapsed >= 500:
            logger.warning(
                "slow_query operation=%s duration_ms=%.1f rows=%s",
                statement.lstrip().split(None, 1)[0],
                elapsed,
                cursor.rowcount,
            )

    return engine
