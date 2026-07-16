import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx


class AlegraClientError(RuntimeError):
    """Base integration error safe to record in a sync run."""


class AlegraAuthenticationError(AlegraClientError):
    """The configured credential is rejected and must not be retried."""


class AlegraPermanentError(AlegraClientError):
    """A non-retryable API request error."""


class AlegraRetryableError(AlegraClientError):
    """A temporary API or network error after retry exhaustion."""


@dataclass(frozen=True)
class InvoicePage:
    data: list[dict[str, Any]]
    total: int | None


class AlegraClient:
    """Small, rate-aware client for Alegra's read API.

    The token is a pre-built Basic credential value. It is intentionally supplied by
    configuration instead of being constructed or logged by the application.
    """

    base_url = "https://api.alegra.com/api/v1"

    def __init__(
        self,
        *,
        basic_token: str,
        client: httpx.AsyncClient | None = None,
        max_retries: int = 5,
        requests_per_minute: int = 120,
    ) -> None:
        if not basic_token:
            raise ValueError("Alegra basic token is required")
        if not 1 <= requests_per_minute <= 150:
            raise ValueError("requests_per_minute must be between 1 and 150")
        self._client = client or httpx.AsyncClient(base_url=self.base_url, timeout=30.0)
        self._owns_client = client is None
        self._headers = {"accept": "application/json", "authorization": f"Basic {basic_token}"}
        self._max_retries = max_retries
        self._minimum_interval = 60 / requests_per_minute
        self._next_request_at = 0.0
        self._request_lock = asyncio.Lock()

    async def __aenter__(self) -> "AlegraClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def list_invoice_page(
        self,
        *,
        start: int,
        limit: int = 30,
        metadata: bool = False,
        filters: dict[str, str] | None = None,
    ) -> InvoicePage:
        if start < 0:
            raise ValueError("start must be a pagination offset greater than or equal to zero")
        if not 1 <= limit <= 30:
            raise ValueError("Alegra invoice page limit must be between 1 and 30")
        params: dict[str, str | int] = {
            "start": start,
            "limit": limit,
            "order_field": "id",
            "order_direction": "ASC",
            "metadata": str(metadata).lower(),
        }
        params.update(filters or {})
        payload = await self._get_json("/invoices", params=params)
        if isinstance(payload, list):
            return InvoicePage(data=[row for row in payload if isinstance(row, dict)], total=None)
        if not isinstance(payload, dict):
            raise AlegraPermanentError("Alegra returned an unexpected invoice-list response")
        data = payload.get("data", [])
        metadata_payload = payload.get("metadata", {})
        total = metadata_payload.get("total") if isinstance(metadata_payload, dict) else None
        return InvoicePage(
            data=[row for row in data if isinstance(row, dict)] if isinstance(data, list) else [],
            total=int(total) if total is not None else None,
        )

    async def iter_all_invoices(self) -> AsyncIterator[dict[str, Any]]:
        """Read every initial-sync page by offset, never by Alegra document ID."""
        async for invoice in self._iter_invoice_pages():
            yield invoice

    async def iter_invoices_for_date(self, invoice_date: str) -> AsyncIterator[dict[str, Any]]:
        """Reconcile a single issue date using complete offset pagination."""
        async for invoice in self._iter_invoice_pages(filters={"date": invoice_date}):
            yield invoice

    async def get_invoice(self, invoice_id: str) -> dict[str, Any]:
        payload = await self._get_json(f"/invoices/{quote(invoice_id, safe='')}", params={})
        if not isinstance(payload, dict):
            raise AlegraPermanentError("Alegra returned an unexpected invoice response")
        return payload

    async def _iter_invoice_pages(
        self, *, filters: dict[str, str] | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        first_page = await self.list_invoice_page(start=0, metadata=True, filters=filters)
        for invoice in first_page.data:
            yield invoice

        if first_page.total is None:
            start = len(first_page.data)
            while first_page.data:
                page = await self.list_invoice_page(start=start, filters=filters)
                for invoice in page.data:
                    yield invoice
                start += len(page.data)
                if not page.data:
                    break
            return

        for start in range(30, first_page.total, 30):
            page = await self.list_invoice_page(start=start, filters=filters)
            for invoice in page.data:
                yield invoice

    async def _get_json(self, path: str, *, params: dict[str, str | int]) -> Any:
        for attempt in range(1, self._max_retries + 1):
            await self._wait_for_rate_slot()
            try:
                response = await self._client.get(path, params=params, headers=self._headers)
            except httpx.RequestError as error:
                if attempt == self._max_retries:
                    raise AlegraRetryableError(
                        "Network request to Alegra failed after retries"
                    ) from error
                await asyncio.sleep(min(2**attempt, 30))
                continue

            if response.status_code in (401, 403):
                raise AlegraAuthenticationError("Alegra rejected the configured credential")
            if response.status_code == 429:
                if attempt == self._max_retries:
                    raise AlegraRetryableError("Alegra rate limit persisted after retries")
                await asyncio.sleep(_rate_limit_delay(response))
                continue
            if 500 <= response.status_code <= 599:
                if attempt == self._max_retries:
                    raise AlegraRetryableError(
                        f"Alegra returned HTTP {response.status_code} after retries"
                    )
                await asyncio.sleep(min(2**attempt, 30))
                continue
            if response.is_error:
                raise AlegraPermanentError(f"Alegra returned HTTP {response.status_code}")
            try:
                return response.json()
            except ValueError as error:
                raise AlegraPermanentError("Alegra returned malformed JSON") from error
        raise AssertionError("retry loop must return or raise")

    async def _wait_for_rate_slot(self) -> None:
        async with self._request_lock:
            now = time.monotonic()
            wait_seconds = max(0.0, self._next_request_at - now)
            self._next_request_at = max(now, self._next_request_at) + self._minimum_interval
        if wait_seconds:
            await asyncio.sleep(wait_seconds)


def _rate_limit_delay(response: httpx.Response) -> float:
    try:
        return max(float(response.headers.get("X-Rate-Limit-Reset", "60")), 1.0)
    except ValueError:
        return 60.0
