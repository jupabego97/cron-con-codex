"""Seed and serve an isolated local database for browser tests, never production."""

import os
from pathlib import Path
from uuid import UUID

import uvicorn
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.business_time import business_today
from app.core.config import normalize_database_url
from app.domain.batch_repository import persist_resource_batch
from app.main import _mount_dashboard, create_app
from app.services.analytics_mart import AnalyticsMartService

TENANT = UUID("11111111-1111-4111-8111-111111111111")


def main():
    url = os.environ.get("TEST_DATABASE_URL", "")
    parsed = make_url(normalize_database_url(url))
    if parsed.host not in {"127.0.0.1", "localhost", "postgres"} or not parsed.database.endswith(
        "_test"
    ):
        raise RuntimeError("Browser tests require an isolated local/CI *_test database")
    os.environ.update(
        DATABASE_URL=url,
        DASHBOARD_TENANT_ID=str(TENANT),
        APP_ENV="test",
        DASHBOARD_PASSWORD="browser-test-password",
        APP_SECRET_KEY="browser-test-session-secret",
        OPENAI_API_KEY="",
        GEMINI_API_KEY="",
        ALEGRA_API_BASIC_TOKEN="",
    )
    from app.core.config import get_settings

    get_settings.cache_clear()
    with Session(create_engine(parsed)) as session:
        session.execute(
            text("""INSERT INTO tenants(id,slug,name) VALUES (:id,'browser-test','Test retailer')
          ON CONFLICT(id) DO NOTHING"""),
            {"id": TENANT},
        )
        today = business_today().isoformat()
        data = {
            "contact": [{"id": "SUP", "name": "Proveedor de prueba", "type": "provider"}],
            "item": [{"id": "PC", "name": "Computador de prueba", "inventariable": True}],
            "invoice": [
                {
                    "id": "S",
                    "date": today,
                    "status": "open",
                    "currency": {"code": "COP"},
                    "items": [
                        {
                            "id": "PC",
                            "name": "Computador de prueba",
                            "quantity": 1,
                            "price": 1000000,
                        }
                    ],
                }
            ],
            "purchase_order": [
                {
                    "id": "PO",
                    "date": today,
                    "status": "open",
                    "provider": {"id": "SUP", "name": "Proveedor de prueba"},
                    "currency": {"code": "COP"},
                    "total": 2400000,
                    "purchases": {
                        "items": [
                            {
                                "item": {"id": "PC", "name": "Computador de prueba"},
                                "quantity": 4,
                                "price": 600000,
                            }
                        ]
                    },
                }
            ],
        }
        for resource, payloads in data.items():
            persist_resource_batch(session, tenant_id=TENANT, resource=resource, payloads=payloads)
        session.commit()
        AnalyticsMartService(session=session).refresh(tenant_id=TENANT)
    app = create_app()
    _mount_dashboard(app, Path(__file__).resolve().parents[1] / "frontend" / "dist")
    uvicorn.run(app, host="127.0.0.1", port=8891, access_log=False)


if __name__ == "__main__":
    main()
