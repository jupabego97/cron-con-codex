from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.api import dashboard
from app.core.config import Settings
from app.main import create_app


@pytest.fixture(autouse=True)
def isolated_session_factory(monkeypatch):
    """Authentication unit tests never write to a DATABASE_URL from local .env."""
    from app.db import session as db_session

    class Result:
        def mappings(self):
            return self

        def one(self):
            return {"attempted_at": datetime.now(UTC), "attempt_count": 0, "locked_until": None}

        def scalar_one_or_none(self):
            return 0

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def execute(self, *_args, **_kwargs):
            return Result()

        def commit(self):
            pass

    monkeypatch.setattr(db_session, "get_session_factory", lambda: Session)


def test_dashboard_session_requires_the_configured_password(monkeypatch) -> None:
    settings = Settings(
        app_secret_key="test-session-secret",
        dashboard_password="dashboard-password",
        dashboard_tenant_id="23332716-6b46-41d4-bc9b-03613fbab6df",
    )
    monkeypatch.setattr(dashboard, "get_settings", lambda: settings)

    with TestClient(create_app()) as client:
        denied = client.post("/api/v1/dashboard/session", json={"password": "wrong"})
        assert denied.status_code == 401

        accepted = client.post("/api/v1/dashboard/session", json={"password": "dashboard-password"})
        assert accepted.status_code == 204
        assert client.get("/api/v1/dashboard/session").json() == {"authenticated": True}


def test_analytics_api_rejects_requests_without_a_dashboard_session(monkeypatch) -> None:
    settings = Settings(
        app_secret_key="test-session-secret",
        dashboard_password="dashboard-password",
        dashboard_tenant_id="23332716-6b46-41d4-bc9b-03613fbab6df",
    )
    monkeypatch.setattr(dashboard, "get_settings", lambda: settings)
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/analytics/overview")

    assert response.status_code == 401


def test_kpis_api_rejects_requests_without_a_dashboard_session(monkeypatch) -> None:
    settings = Settings(
        app_secret_key="test-session-secret",
        dashboard_password="dashboard-password",
        dashboard_tenant_id="23332716-6b46-41d4-bc9b-03613fbab6df",
    )
    monkeypatch.setattr(dashboard, "get_settings", lambda: settings)
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/analytics/kpis")

    assert response.status_code == 401


def test_ai_api_rejects_requests_without_a_dashboard_session(monkeypatch) -> None:
    settings = Settings(
        app_secret_key="test-session-secret",
        dashboard_password="dashboard-password",
        dashboard_tenant_id="23332716-6b46-41d4-bc9b-03613fbab6df",
    )
    monkeypatch.setattr(dashboard, "get_settings", lambda: settings)
    with TestClient(create_app()) as client:
        assert client.get("/api/v1/ai/status").status_code == 401
        response = client.post("/api/v1/ai/chat", json={"message": "Analiza el inventario"})
        assert response.status_code == 401


def test_replenishment_exports_and_actions_require_a_dashboard_session(monkeypatch) -> None:
    settings = Settings(
        app_secret_key="test-session-secret",
        dashboard_password="dashboard-password",
        dashboard_tenant_id="23332716-6b46-41d4-bc9b-03613fbab6df",
    )
    monkeypatch.setattr(dashboard, "get_settings", lambda: settings)
    with TestClient(create_app()) as client:
        assert client.get("/api/v1/analytics/purchase-recommendations/export").status_code == 401
        assert client.get("/api/v1/analytics/purchase-recommendations/policies").status_code == 401
        assert (
            client.put(
                "/api/v1/analytics/purchase-recommendations/policies/suppliers/1",
                json={"minimum_order_amount": 1000000},
            ).status_code
            == 401
        )
        assert (
            client.patch(
                "/api/v1/analytics/purchase-recommendations/1",
                json={"status": "reviewed"},
            ).status_code
            == 401
        )
        assert client.get("/api/v1/procurement/preview").status_code == 401
        assert (
            client.post(
                "/api/v1/procurement/plans",
                json={"weekly_budget": 10000000},
            ).status_code
            == 401
        )
