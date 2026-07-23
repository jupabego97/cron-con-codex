import asyncio

import httpx

from app.integrations.alegra.client import AlegraClient
from app.integrations.alegra.resources import RESOURCE_BY_KEY, resolve_resources


def test_resource_backfill_hydrates_details_after_offset_pagination() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.path.endswith("/contacts") and request.url.params["start"] == "0":
            return httpx.Response(
                200, json={"metadata": {"total": 31}, "data": [{"id": "C-1"}]}
            )
        if request.url.path.endswith("/contacts") and request.url.params["start"] == "30":
            return httpx.Response(200, json=[{"id": "C-2"}])
        if request.url.path.endswith("/contacts/C-1"):
            return httpx.Response(200, json={"contact": {"id": "C-1", "name": "Uno"}})
        if request.url.path.endswith("/contacts/C-2"):
            return httpx.Response(200, json={"contact": {"id": "C-2", "name": "Dos"}})
        return httpx.Response(404)

    async def collect() -> list[dict[str, str]]:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(
            base_url="https://api.alegra.com/api/v1", transport=transport
        ) as http:
            alegra = AlegraClient(basic_token="test-token", client=http, requests_per_minute=150)
            return [
                record
                async for record in alegra.iter_all_resource(
                    RESOURCE_BY_KEY["contact"], page_concurrency=2, detail_concurrency=2
                )
            ]

    contacts = asyncio.run(collect())

    assert contacts == [{"id": "C-1", "name": "Uno"}, {"id": "C-2", "name": "Dos"}]
    assert any("/contacts?" in request and "start=0" in request for request in requests)
    assert any("/contacts?" in request and "start=30" in request for request in requests)


def test_resource_selection_is_explicit_and_rejects_unknown_values() -> None:
    assert [resource.key for resource in resolve_resources("contact,item")] == ["contact", "item"]
    assert len(resolve_resources("all")) == len(RESOURCE_BY_KEY)
