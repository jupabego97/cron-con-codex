"""Real PostgreSQL contracts; only an explicitly selected test database is allowed."""

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.api import dashboard
from app.backups import manifest
from app.core.business_time import business_today
from app.core.config import Settings, normalize_database_url
from app.db.models import InboundEvent
from app.db.session import get_db_session
from app.domain.batch_repository import persist_resource_batch
from app.main import create_app
from app.services.ai_agent import RetailAIAgent
from app.services.analytics_mart import AnalyticsMartService
from app.services.analytics_queries import AnalyticsFilters, AnalyticsQueryService
from app.services.event_queue import claim_next_event, owns_event
from app.services.operations_health import OperationsHealthService
from app.services.procurement_planning import ProcurementPlanningService
from app.services.product_workspace import ProductWorkspaceService
from app.services.receiving import ReceivingService
from app.services.repairs import RepairService
from app.services.sync_checkpoint import LostSyncLease, checkpoint, guard, resume_or_start
from app.services.treasury import TreasuryService


@pytest.fixture
def db():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is required for real PostgreSQL tests")
    parsed = make_url(normalize_database_url(url))
    if parsed.host not in {"127.0.0.1", "localhost", "postgres"} or not parsed.database.endswith(
        "_test"
    ):
        pytest.fail("Integration tests require an isolated local/CI *_test database")
    engine = create_engine(parsed)
    with engine.connect() as connection:
        transaction = connection.begin()
        with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
            tenant = uuid4()
            session.execute(
                text("INSERT INTO tenants(id,slug,name) VALUES (:id,:slug,'Test retailer')"),
                {"id": tenant, "slug": str(tenant)},
            )
            session.commit()
            yield session, tenant
        transaction.rollback()
    engine.dispose()


@pytest.fixture
def populated(db):
    session, tenant = db
    today = business_today().isoformat()
    payloads = {
        "contact": [{"id": "SUP", "name": "Proveedor", "type": "provider"}],
        "item": [
            {
                "id": "PC",
                "name": "Computador",
                "reference": "PC-1",
                "inventariable": True,
                "inventory": {"unitCost": 600, "availableQuantity": 2},
            }
        ],
        "invoice": [
            {
                "id": "S",
                "date": today,
                "status": "open",
                "currency": {"code": "COP"},
                "items": [{"id": "PC", "name": "Computador", "quantity": 1, "price": 1000}],
            },
            {
                "id": "VOID",
                "date": today,
                "status": "void",
                "currency": {"code": "COP"},
                "items": [{"id": "PC", "name": "Computador", "quantity": 1, "price": 5000}],
            },
        ],
        "credit_note": [
            {
                "id": "CN",
                "date": today,
                "status": "open",
                "currency": {"code": "COP"},
                "items": [{"id": "PC", "name": "Computador", "quantity": 1, "price": 200}],
            }
        ],
        "purchase_order": [
            {
                "id": "PO",
                "date": today,
                "status": "open",
                "provider": {"id": "SUP", "name": "Proveedor"},
                "currency": {"code": "COP"},
                "total": 2400,
                "purchases": {
                    "items": [
                        {"item": {"id": "PC", "name": "Computador"}, "quantity": 4, "price": 600}
                    ]
                },
            }
        ],
        "bill": [
            {
                "id": "B",
                "date": today,
                "dueDate": today,
                "status": "open",
                "provider": {"id": "SUP", "name": "Proveedor"},
                "currency": {"code": "COP"},
                "total": 600,
                "balance": 600,
                "purchaseOrder": {"id": "PO"},
                "purchases": {
                    "items": [
                        {"item": {"id": "PC", "name": "Computador"}, "quantity": 1, "price": 600}
                    ]
                },
            }
        ],
    }
    for resource, documents in payloads.items():
        persist_resource_batch(session, tenant_id=tenant, resource=resource, payloads=documents)
    session.commit()
    AnalyticsMartService(session=session).refresh(tenant_id=tenant)
    product = session.execute(
        text("SELECT key FROM dim_product WHERE tenant_id=:tenant"), {"tenant": tenant}
    ).scalar_one()
    return session, tenant, product


def test_metrics_credit_notes_ticket_and_unknown_cost(populated):
    session, tenant, product = populated
    query = AnalyticsQueryService(session=session, tenant_id=tenant)
    filters = AnalyticsFilters(
        business_today(), business_today(), currency="COP", metric_scope="commercial"
    )
    summary = query._sales_kpis(filters)[0]
    assert summary["net_sales"] == Decimal(800)
    assert summary["average_ticket"] == Decimal(800)
    assert summary["invoice_documents"] == 1
    assert summary["gross_margin"] is None
    assert summary["gross_margin_pct"] is None
    session.execute(
        text(
            "UPDATE fact_sales_line SET cost_status='costed',cogs_amount=600,margin_amount=400 "
            "WHERE tenant_id=:tenant AND document_alegra_id='S'"
        ),
        {"tenant": tenant},
    )
    summary = query._sales_kpis(filters)[0]
    assert summary["cost_coverage_value_pct"] == pytest.approx(Decimal(1000) / Decimal(1200) * 100)
    assert len(query.product_sales_page(filters)["items"]) == 1
    assert (
        query._sales_kpis(AnalyticsFilters(business_today(), business_today()))[0]["net_sales"]
        == 5800
    )
    only_credit = query._sales_kpis(
        AnalyticsFilters(
            business_today(), business_today(), document_status="credit-only", metric_scope="audit"
        )
    )
    assert only_credit == []
    session.execute(
        text(
            "UPDATE fact_sales_line SET document_status='credit-only' "
            "WHERE tenant_id=:tenant AND document_type='credit_note'"
        ),
        {"tenant": tenant},
    )
    only_credit = query._sales_kpis(
        AnalyticsFilters(business_today(), business_today(), document_status="credit-only")
    )[0]
    assert only_credit["average_ticket"] is None
    assert only_credit["invoice_documents"] == 0


@pytest.mark.parametrize(
    "method",
    [
        "overview",
        "sales",
        "purchases",
        "suppliers",
        "payments",
        "inventory",
        "customers",
        "products",
        "kpis",
        "margin_diagnostics",
        "data_quality",
    ],
)
@pytest.mark.parametrize("scoped", [False, True])
def test_analytical_queries_use_real_postgresql_and_filters(populated, method, scoped):
    session, tenant, product = populated
    filters = AnalyticsFilters(
        business_today(),
        business_today(),
        currency="COP",
        product_key=product if scoped else None,
        provider_key=99999999 if scoped else None,
        family="not-a-family" if scoped else None,
        metric_scope="commercial",
    )
    result = getattr(AnalyticsQueryService(session=session, tenant_id=tenant), method)(filters)
    assert isinstance(result, dict)


def test_expired_reconciler_cannot_commit_after_takeover(db):
    session, tenant = db
    today = business_today()
    run, _ = resume_or_start(session, tenant_id=tenant, resource="invoice", first=today, last=today)
    previous_lease = run.lease_token
    run.heartbeat_at = datetime.now(UTC) - timedelta(hours=2)
    session.commit()
    replacement, _ = resume_or_start(
        session, tenant_id=tenant, resource="invoice", first=today, last=today
    )
    assert replacement.id == run.id
    assert replacement.lease_token != previous_lease
    with pytest.raises(LostSyncLease):
        guard(session, run, previous_lease)
    guard(session, replacement, replacement.lease_token)


def test_all_new_read_queries_execute_and_are_tenant_scoped(populated):
    session, tenant, product = populated
    filters = AnalyticsFilters(business_today(), business_today(), currency="COP")
    product_service = ProductWorkspaceService(session=session, tenant_id=tenant)
    assert product_service.detail(product, filters)["profile"]["key"] == product
    assert product_service.search()["total"] == 1
    assert ReceivingService(session=session, tenant_id=tenant).orders()["total"] == 1
    assert (
        TreasuryService(session=session, tenant_id=tenant).projection()["reconstructed_balance"]
        is None
    )
    assert RepairService(session=session, tenant_id=tenant).list(filters)["items"] == []
    health = OperationsHealthService(session=session, tenant_id=tenant)
    assert health.today()["as_of_date"] == business_today()
    assert health.events()["total"] == 0
    planning = ProcurementPlanningService(session=session, tenant_id=tenant)
    inputs = planning._product_inputs(
        as_of=business_today(), currency_code="COP", snapshot_run_id=None
    )
    assert product in inputs
    assert planning._supplier_options(as_of=business_today(), currency_code="COP")
    with pytest.raises(LookupError):
        ProductWorkspaceService(session=session, tenant_id=uuid4()).detail(product, filters)


def test_agent_context_pagination_and_figures_match_sql(populated):
    session, tenant, product = populated
    query = AnalyticsQueryService(session=session, tenant_id=tenant)
    agent = RetailAIAgent(
        session=session,
        tenant_id=tenant,
        analytics=query,
        api_key="test-only-not-sent",
        model="offline-evaluation",
        weekly_budget=Decimal(1234),
        review_cycle_days=9,
    )
    filters = AnalyticsFilters(
        business_today() - timedelta(days=1000),
        business_today(),
        currency="COP",
        metric_scope="commercial",
    )

    class Responses:
        def __init__(self):
            self.calls = 0

        def create(self, **arguments):
            assert '"weekly_budget": "1234"' in arguments["instructions"]
            assert '"metric_scope": "commercial"' in arguments["instructions"]
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(
                    output=[
                        SimpleNamespace(
                            type="function_call",
                            name="get_product_sales_page",
                            arguments='{"limit":1}',
                            call_id="page",
                        )
                    ]
                )
            return SimpleNamespace(output=[], output_text="Resultado controlado de evaluación")

    client = SimpleNamespace(responses=Responses())
    agent._client = lambda: client
    result = agent.ask(message="Revisa ventas históricas por producto", base_filters=filters)
    assert result["context"]["weekly_budget"] == "1234"
    assert result["context"]["review_cycle_days"] == 9
    assert result["evidence"][0]["pagination"]["total"] == 1
    assert result["evidence"][0]["filters"]["metric_scope"] == "commercial"
    page = agent._dispatch_tool("get_product_sales_page", {"limit": 1}, filters)
    assert page["items"][0]["net_sales"] == 800
    assert page["items"][0]["margin"] is None
    plan = agent._dispatch_tool("get_replenishment_plan", {}, filters)
    assert plan["budget"] == Decimal(1234)
    assert plan["review_cycle_days"] == 9


def test_backup_manifest_detects_changes_in_tenant_and_shared_tables(populated):
    session, tenant, _ = populated
    before = manifest(session.connection(), tenant)
    assert "dim_date" in before["tables"]
    session.execute(
        text("UPDATE sales_invoices SET client_name='changed' WHERE tenant_id=:tenant"),
        {"tenant": tenant},
    )
    after = manifest(session.connection(), tenant)
    assert before["tables"]["sales_invoices"] != after["tables"]["sales_invoices"]


def test_receipts_do_not_follow_a_replaced_line_reference(populated):
    session, tenant, _ = populated
    service = ReceivingService(session=session, tenant_id=tenant)
    line = service.detail("PO")["lines"][0]["line_number"]
    service.receive(
        "PO",
        {
            "id": uuid4(),
            "line_number": line,
            "received_on": business_today(),
            "accepted_quantity": Decimal(1),
            "rejected_quantity": Decimal(0),
            "notes": None,
        },
    )
    session.execute(
        text(
            "UPDATE purchase_order_lines SET item_alegra_id='OTHER' "
            "WHERE tenant_id=:tenant AND document_alegra_id='PO'"
        ),
        {"tenant": tenant},
    )
    result = service.detail("PO")
    assert result["unmatched_receipts"] == 1
    assert result["lines"][0]["accepted_quantity"] == 0


def test_partial_receipts_idempotency_over_receipt_and_transit(populated):
    session, tenant, product = populated
    service = ReceivingService(session=session, tenant_id=tenant)
    line = service.detail("PO")["lines"][0]["line_number"]
    receipt = {
        "id": uuid4(),
        "line_number": line,
        "received_on": business_today(),
        "accepted_quantity": Decimal(2),
        "rejected_quantity": Decimal(0),
        "notes": None,
    }
    assert service.receive("PO", receipt)["accepted_quantity"] == 2
    assert service.receive("PO", receipt)["id"] == receipt["id"]
    assert service.detail("PO")["receipt_status"] == "partial"
    inputs = ProcurementPlanningService(session=session, tenant_id=tenant)._product_inputs(
        as_of=business_today(), currency_code="COP", snapshot_run_id=None
    )
    assert inputs[product]["quantity_in_transit"] == 2  # max(billed=1,physically received=2)
    with pytest.raises(ValueError):
        service.receive("PO", {**receipt, "id": uuid4(), "accepted_quantity": Decimal(3)})
    receipt2 = {**receipt, "id": uuid4()}
    service.receive("PO", receipt2)
    assert service.detail("PO")["receipt_status"] == "complete"


def test_certified_cash_and_posting_do_not_double_count(populated):
    session, tenant, _ = populated
    service = TreasuryService(session=session, tenant_id=tenant)
    service.balance(
        {
            "currency_code": "COP",
            "as_of_date": business_today() - timedelta(days=1),
            "amount": Decimal(5000),
            "notes": "Certified",
        }
    )
    entry = {
        "id": uuid4(),
        "currency_code": "COP",
        "entry_date": business_today(),
        "amount": Decimal(100),
        "direction": "out",
        "stage": "planned",
        "category": "rent",
        "description": "Not in ERP",
    }
    service.entry(entry)
    first = service.projection()
    assert first["reconstructed_balance"] == 5000
    service.post_entry(entry["id"])
    service.post_entry(entry["id"])
    assert service.projection()["reconstructed_balance"] == 4900
    assert len(service.projection()["weeks"]) == 4


def test_repairs_transitions_costs_and_warranty(populated):
    session, tenant, product = populated
    service = RepairService(session=session, tenant_id=tenant)
    job = {
        "id": uuid4(),
        "customer_name": "Cliente",
        "customer_phone": None,
        "contact_key": None,
        "product_key": product,
        "device_description": "PC",
        "serial_number": "SERIAL",
        "reported_issue": "No inicia",
        "currency_code": "COP",
        "warranty_days": 30,
        "warranty_parent_id": None,
        "received_on": business_today(),
        "promised_on": None,
    }
    service.create(job)
    assert service.create(job)["id"] == job["id"]
    with pytest.raises(ValueError):
        service.update(job["id"], {"status": "delivered"})
    for state in ("diagnosing", "approved", "in_progress"):
        service.update(job["id"], {"status": state})
    service.add_part(
        job["id"],
        {
            "id": uuid4(),
            "product_key": product,
            "description": "SSD",
            "quantity": Decimal(1),
            "unit_cost": Decimal(100),
        },
    )
    service.update(
        job["id"],
        {
            "status": "ready",
            "charged_amount": Decimal(300),
            "labour_cost": Decimal(50),
            "costs_complete": True,
        },
    )
    service.update(job["id"], {"status": "delivered"})
    result = service.list(AnalyticsFilters(business_today(), business_today()))
    assert result["metrics"][0]["recorded_margin"] == 150
    warranty = {**job, "id": uuid4(), "warranty_parent_id": job["id"]}
    service.create(warranty)
    with pytest.raises(ValueError):
        service.create({**warranty, "id": uuid4(), "serial_number": "OTHER"})


def test_expired_event_lease_and_manual_retry_are_fenced(db):
    session, tenant = db
    event = InboundEvent(
        tenant_id=tenant,
        subject="edit-item",
        entity_type="item",
        external_id="PC",
        payload={},
        payload_hash="a" * 64,
        status="processing",
        locked_at=datetime.now(UTC) - timedelta(minutes=20),
        lease_token=uuid4(),
    )
    old_token = event.lease_token
    session.add(event)
    session.commit()
    claimed = claim_next_event(session)
    session.flush()
    assert claimed.id == event.id
    assert not owns_event(session, event.id, old_token)
    assert owns_event(session, event.id, claimed.lease_token)
    event.status = "failed"
    session.commit()
    assert (
        OperationsHealthService(session=session, tenant_id=tenant).retry(event.id)["status"]
        == "retry_wait"
    )
    assert claim_next_event(session).id == event.id


def test_reconcile_resumes_only_after_last_completed_day(db):
    session, tenant = db
    today = business_today()
    run, _ = resume_or_start(
        session, tenant_id=tenant, resource="invoice", first=today - timedelta(days=2), last=today
    )
    checkpoint(session, run, today - timedelta(days=2))
    with pytest.raises(RuntimeError):
        resume_or_start(
            session,
            tenant_id=tenant,
            resource="invoice",
            first=today - timedelta(days=3),
            last=today,
        )
    run.status = "failed"
    session.commit()
    resumed, next_day = resume_or_start(
        session, tenant_id=tenant, resource="invoice", first=today - timedelta(days=2), last=today
    )
    assert resumed.id == run.id
    assert next_day == today - timedelta(days=1)


def test_private_api_validates_ownership_csrf_revocation_and_throttle(populated, monkeypatch):
    session, tenant, product = populated
    settings = Settings(
        app_secret_key="integration-session-secret",
        dashboard_password="contraseña-segura",
        dashboard_tenant_id=tenant,
    )
    monkeypatch.setattr(dashboard, "get_settings", lambda: settings)
    app = create_app()
    app.dependency_overrides[get_db_session] = lambda: session
    with TestClient(app) as client:
        assert client.get("/api/v1/operations/products").status_code == 401
        assert (
            client.post(
                "/api/v1/dashboard/session", json={"password": "contraseña-segura"}
            ).status_code
            == 204
        )
        response = client.get(f"/api/v1/operations/products/{product}")
        assert response.status_code == 200
        assert (
            client.put(
                f"/api/v1/operations/products/{product}/profile",
                json={"lifecycle": "active"},
                headers={"Origin": "https://evil.example"},
            ).status_code
            == 403
        )
        assert (
            client.put(
                f"/api/v1/operations/products/{product}/profile",
                json={"lifecycle": "active", "tenant_id": str(uuid4())},
            ).status_code
            == 422
        )
        assert client.post("/api/v1/dashboard/sessions/revoke").status_code == 204
        assert client.get("/api/v1/operations/products").status_code == 401
        for _ in range(10):
            assert (
                client.post("/api/v1/dashboard/session", json={"password": "wrong"}).status_code
                == 401
            )
        assert (
            client.post(
                "/api/v1/dashboard/session", json={"password": "contraseña-segura"}
            ).status_code
            == 429
        )
