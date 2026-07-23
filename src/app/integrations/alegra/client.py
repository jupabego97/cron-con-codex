import asyncio
import time
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from app.integrations.alegra.resources import AlegraResource


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


@dataclass(frozen=True)
class ResourcePage:
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

    async def list_resource_page(
        self,
        resource: AlegraResource,
        *,
        start: int,
        limit: int = 30,
        metadata: bool = False,
    ) -> ResourcePage:
        """Read a page from any catalogued, offset-paginated Alegra resource."""
        if start < 0:
            raise ValueError("start must be greater than or equal to zero")
        if not 1 <= limit <= 30:
            raise ValueError("Alegra page limit must be between 1 and 30")
        params: dict[str, str | int] = {"start": start, "limit": limit}
        if resource.supports_metadata:
            params["metadata"] = str(metadata).lower()
        if resource.order_field is not None:
            params["order_field"] = resource.order_field
            params["order_direction"] = "ASC"
        payload = await self._get_json(resource.collection_path, params=params)
        return _parse_resource_page(payload)

    async def get_resource(self, resource: AlegraResource, external_id: str) -> dict[str, Any]:
        """Fetch the canonical resource detail and unwrap known API response envelopes."""
        payload = await self._get_json(
            f"{resource.collection_path}/{quote(external_id, safe='')}", params={}
        )
        if not isinstance(payload, dict):
            raise AlegraPermanentError(
                f"Alegra returned an unexpected {resource.key} detail response"
            )
        for key in resource.response_keys:
            nested = payload.get(key)
            if isinstance(nested, dict):
                return nested
        return payload

    async def iter_all_resource(
        self,
        resource: AlegraResource,
        *,
        page_concurrency: int = 4,
        hydrate_details: bool = True,
        detail_concurrency: int = 6,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream a complete resource history with bounded concurrent page/detail reads.

        All requests use this client's single rate limiter, so multiple resource
        tasks cannot exceed the configured per-credential request budget.
        """
        if page_concurrency < 1 or detail_concurrency < 1:
            raise ValueError("concurrency values must be positive")
        first_page = await self.list_resource_page(resource, start=0, metadata=True)
        async for record in self._hydrate_records(
            resource,
            first_page.data,
            hydrate_details=hydrate_details,
            detail_concurrency=detail_concurrency,
        ):
            yield record

        if first_page.total is None:
            start = len(first_page.data)
            page = first_page
            while page.data:
                page = await self.list_resource_page(resource, start=start)
                async for record in self._hydrate_records(
                    resource,
                    page.data,
                    hydrate_details=hydrate_details,
                    detail_concurrency=detail_concurrency,
                ):
                    yield record
                start += len(page.data)
            return

        offsets = range(30, first_page.total, 30)
        for offset_batch in _batches(offsets, page_concurrency):
            pages = await asyncio.gather(
                *(self.list_resource_page(resource, start=offset) for offset in offset_batch)
            )
            for page in pages:
                async for record in self._hydrate_records(
                    resource,
                    page.data,
                    hydrate_details=hydrate_details,
                    detail_concurrency=detail_concurrency,
                ):
                    yield record

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

    async def _hydrate_records(
        self,
        resource: AlegraResource,
        records: list[dict[str, Any]],
        *,
        hydrate_details: bool,
        detail_concurrency: int,
    ) -> AsyncIterator[dict[str, Any]]:
        if not hydrate_details or not resource.supports_detail:
            for record in records:
                yield record
            return
        for record_batch in _batches(records, detail_concurrency):
            hydrated = await asyncio.gather(
                *(self._hydrate_one(resource, record) for record in record_batch)
            )
            for record in hydrated:
                yield record

    async def _hydrate_one(
        self, resource: AlegraResource, listing_payload: dict[str, Any]
    ) -> dict[str, Any]:
        external_id = listing_payload.get("id")
        if external_id is None:
            raise AlegraPermanentError(f"Alegra {resource.key} listing returned a row without id")
        return await self.get_resource(resource, str(external_id))

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


def _parse_resource_page(payload: Any) -> ResourcePage:
    if isinstance(payload, list):
        return ResourcePage(data=[row for row in payload if isinstance(row, dict)], total=None)
    if not isinstance(payload, dict):
        raise AlegraPermanentError("Alegra returned an unexpected collection response")
    data = payload.get("data", [])
    metadata = payload.get("metadata", {})
    total = metadata.get("total") if isinstance(metadata, dict) else None
    return ResourcePage(
        data=[row for row in data if isinstance(row, dict)] if isinstance(data, list) else [],
        total=int(total) if total is not None else None,
    )


def _batches(values: Iterable[Any], size: int) -> Iterable[list[Any]]:
    batch: list[Any] = []
    for value in values:
        batch.append(value)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch
