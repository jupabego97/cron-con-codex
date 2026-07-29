from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import _mount_dashboard, create_app


def test_liveness_endpoint() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_endpoint() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_dashboard_root_serves_the_compiled_spa(tmp_path) -> None:
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<h1>Dashboard</h1>", encoding="utf-8")
    app = FastAPI()
    _mount_dashboard(app, static_dir=tmp_path)

    with TestClient(app) as client:
        root = client.get("/")
        nested = client.get("/ventas")

    assert root.status_code == 200
    assert nested.status_code == 200
    assert "Dashboard" in root.text
