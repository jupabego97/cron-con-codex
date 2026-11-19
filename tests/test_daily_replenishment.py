import asyncio
from datetime import UTC, date, datetime
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app.daily_report import create_app
from app.services import nightly_replenishment
from app.services.nightly_replenishment import build_report

PASSWORD = "dashboard-test-only-password"
PUBLISH = {"Authorization": "Bearer " + "test-nightly-token-" * 3}
WEBHOOK_TOKEN = "test-webhook-credential"
WEBHOOK = "/webhooks/alegra/shop"


def reader(path):
    return create_app(
        state_path=path, password=PASSWORD, publish_token=PUBLISH["Authorization"][7:],
        webhook_token=WEBHOOK_TOKEN, tenant_slug="shop",
    )


def report(stamp):
    return {
        "as_of_date": "2026-11-18",
        "generated_at": stamp,
        "snapshot_at": "2026-11-18T07:05:00Z",
        "currency_code": "COP",
        "weekly_budget": "15000000",
        "recommended_products": 0,
        "recommended_value": "0",
        "suppliers": [],
    }


def test_report_is_private_and_latest_good_cut_survives_restart(tmp_path):
    path = tmp_path / "reader.sqlite"
    old = report("2026-11-18T07:15:00Z")
    newest = report("2026-11-18T07:20:00Z")
    with TestClient(reader(path)) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/").status_code == 401
        assert client.get("/report.json", headers=PUBLISH).status_code == 401
        assert client.get("/report.json", auth=("reponer", PASSWORD)).status_code == 404
        assert client.put("/internal/report", json=newest).status_code == 401
        assert client.put("/internal/report", json=newest, headers=PUBLISH).status_code == 204
        assert client.put("/internal/report", json=old, headers=PUBLISH).status_code == 409
        assert client.put("/internal/report", json={}, headers=PUBLISH).status_code == 422
    with TestClient(reader(path)) as client:
        result = client.get("/report.json", auth=("reponer", PASSWORD))
        assert result.status_code == 200
        assert result.headers["cache-control"] == "no-store"
        assert result.json()["generated_at"] == newest["generated_at"]
        assert client.get("/", auth=("reponer", PASSWORD)).status_code == 200


def test_webhook_persists_until_authenticated_ack_at_a_fixed_cutoff(tmp_path):
    path = tmp_path / "reader.sqlite"
    query = {"token": WEBHOOK_TOKEN}
    edit = {"subject": "edit-item", "message": {"item": {"id": 14}}}
    delete = {"subject": "delete-client", "message": {"client": {"id": 91}}}
    with TestClient(reader(path)) as client:
        assert client.post(WEBHOOK, json=edit).status_code == 401
        assert client.post(WEBHOOK, params=query, json={}).status_code == 204
        assert client.post(WEBHOOK, params=query, json=edit).json()["status"] == "queued"
        assert client.post(WEBHOOK, params=query, json=edit).json()["status"] == "duplicate"
        through = client.get("/internal/status", headers=PUBLISH).json()["through"]
        assert client.post(WEBHOOK, params=query, json=delete).status_code == 202
    with TestClient(reader(path)) as client:
        assert client.get("/internal/events", params={"through": 999}).status_code == 401
        rows = client.get("/internal/events", params={"through": through}, headers=PUBLISH).json()
        assert [row["payload"] for row in rows] == [edit]
        assert client.post("/internal/events/ack", json={"through": through}).status_code == 401
        assert client.post(
            "/internal/events/ack", json={"through": through}, headers=PUBLISH
        ).status_code == 204
        rows = client.get("/internal/events", params={"through": 999}, headers=PUBLISH).json()
        assert [row["payload"] for row in rows] == [delete]


@pytest.mark.parametrize("commit_fails", [False, True])
def test_import_only_acknowledges_after_postgres_commit(monkeypatch, commit_fails):
    order = []
    session = Mock()

    def commit():
        order.append("commit")
        if commit_fails:
            raise RuntimeError("commit failed")

    session.commit.side_effect = commit
    monkeypatch.setattr(nightly_replenishment, "enqueue_event", lambda *a, **kw: (None, True))
    event = {"subject": "delete-item", "message": {"item": {"id": 1}}}

    def upstream(request):
        if request.method == "POST":
            order.append("ack")
            return httpx.Response(204)
        rows = [] if "ack" in order else [{"id": 7, "payload": event}]
        return httpx.Response(200, json=rows)

    async def run():
        async with httpx.AsyncClient(
            base_url="https://reader.test", transport=httpx.MockTransport(upstream)
        ) as client:
            return await nightly_replenishment.import_events(
                client, session, tenant_id=uuid4(), through=7
            )

    if commit_fails:
        with pytest.raises(RuntimeError, match="commit failed"):
            asyncio.run(run())
        assert order == ["commit"]
    else:
        assert asyncio.run(run()) == 1
        assert order == ["commit", "ack"]


def test_blocked_and_incomplete_recommendations_are_not_published():
    preview = {
        "data_quality": {"ready": False, "warnings": ["Datos atrasados"]},
        "supplier_orders": [],
        "summary": {"recommended_products": 1, "recommended_value": 50},
        "as_of_date": date(2026, 11, 18),
        "currency_code": "COP",
        "weekly_budget": 100,
    }
    with pytest.raises(RuntimeError, match="Datos atrasados"):
        build_report(preview)
    preview["data_quality"] = {"ready": True, "snapshot_at": datetime.now(UTC)}
    with pytest.raises(RuntimeError, match="incompleto"):
        build_report(preview)
    preview["summary"] = {"recommended_products": 0, "recommended_value": 0}
    assert build_report(preview).suppliers == []
