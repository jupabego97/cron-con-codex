from fastapi.testclient import TestClient

from app.api import dashboard
from app.core.config import Settings
from app.main import create_app


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
