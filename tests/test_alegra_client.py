import asyncio

import httpx

from app.integrations.alegra.client import AlegraClient


def test_initial_sync_uses_offsets_not_document_ids() -> None:
    offsets: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        offsets.append(request.url.params["start"])
        if request.url.params["start"] == "0":
            return httpx.Response(
                200,
                json={"metadata": {"total": 31}, "data": [{"id": "900"}]},
            )
        return httpx.Response(200, json=[{"id": "901"}])

    async def collect() -> list[dict[str, str]]:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(
            base_url="https://api.alegra.com/api/v1", transport=transport
        ) as http:
            alegra = AlegraClient(basic_token="test-token", client=http, requests_per_minute=150)
            return [invoice async for invoice in alegra.iter_all_invoices()]

    invoices = asyncio.run(collect())

    assert offsets == ["0", "30"]
    assert invoices == [{"id": "900"}, {"id": "901"}]
