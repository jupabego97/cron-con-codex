import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.services.procurement_planning import (
    SERVICE_LEVELS,
    ForecastResult,
    ProcurementPlanningService,
    _croston,
    _freight,
    _lead_time,
    _tsb,
)


def test_freight_without_free_shipping_threshold_counts_against_budget():
    policy = {"shipping_cost": Decimal(50), "free_shipping_threshold": None}
    assert _freight(Decimal(100), policy) == 50
    assert _freight(Decimal(0), policy) == 0
    assert _freight(Decimal(100), {**policy, "free_shipping_threshold": 100}) == 0
    service = ProcurementPlanningService(session=None, tenant_id=uuid.uuid4())
    supplier = {**_supplier(), **policy}
    lines = service._recommend(
        products={1: _product()},
        forecasts={1: _forecast()},
        supplier_options={1: [supplier]},
        weekly_budget=Decimal(1),
        review_cycle_days=7,
        blocked=False,
    )
    goods = lines[0]["estimated_value"]
    lines = service._recommend(
        products={1: _product()},
        forecasts={1: _forecast()},
        supplier_options={1: [supplier]},
        weekly_budget=goods,
        review_cycle_days=7,
        blocked=False,
    )
    assert lines[0]["decision"] == "deferred_budget"
    lines = service._recommend(
        products={1: _product()},
        forecasts={1: _forecast()},
        supplier_options={1: [supplier]},
        weekly_budget=goods + 50,
        review_cycle_days=7,
        blocked=False,
    )
    assert service._summary(lines, goods + 50)["remaining_budget"] == 0


def test_lead_times_keep_real_zero_and_round_observed_means_up():
    assert _lead_time({"lead_time_days": 0, "observed_lead_days": Decimal("4.5")}) == 0
    assert _lead_time({"observed_lead_days": Decimal("4.5")}) == 5
    assert _lead_time({}) == 7


def test_availability_censors_only_observed_stockouts_and_new_product_starts_late():
    service = ProcurementPlanningService(session=None, tenant_id=uuid.uuid4())
    as_of = date(2026, 10, 8)
    first = as_of - timedelta(days=29)
    product = _product()
    product.update(
        introduced_on=first,
        daily={as_of: Decimal(1)},
        availability={first + timedelta(days=i): i >= 15 for i in range(30)},
    )
    result = service._forecast_products({1: product}, as_of=as_of)[1]
    assert result.history_days == 30
    assert result.observed_days == 30
    assert result.censored_stockout_days == 15
    assert result.confidence == "baja"
    product["availability"] = {first: False}
    result = service._forecast_products({1: product}, as_of=as_of)[1]
    assert result.censored_stockout_days == 0


def _forecast() -> ForecastResult:
    return ForecastResult(
        abc_class="A",
        xyz_class="Z",
        demand_pattern="intermitente_irregular",
        model_name="croston_sba",
        daily_forecast=Decimal("0.5"),
        wape=Decimal("0.25"),
        bias=Decimal("0.02"),
        service_level=SERVICE_LEVELS[("A", "Z")],
        confidence="media",
        sale_days=5,
        gross_units=Decimal("12"),
    )


def _product(stock: str = "0") -> dict:
    return {
        "product_key": 1,
        "alegra_id": "I-1",
        "name": "Portatil",
        "reference": "PC-1",
        "family_name": "COMPUTADORES",
        "quantity_on_hand": Decimal(stock),
        "quantity_in_transit": Decimal("0"),
        "gross_units": Decimal("12"),
        "revenue": Decimal("12000000"),
        "margin": Decimal("1800000"),
        "returned_units": Decimal("2"),
        "last_purchase_date": date(2026, 8, 1),
        "last_purchase_quantity": Decimal("4"),
        "unit_cost": Decimal("800000"),
    }


def _supplier() -> dict:
    return {
        "supplier_key": 10,
        "supplier": "Proveedor",
        "supplier_score": Decimal("85"),
        "supplier_confidence": "media",
        "last_unit_cost": Decimal("800000"),
        "pack_size": Decimal("1"),
        "minimum_order_quantity": Decimal("1"),
        "default_lead_time_days": 7,
        "minimum_order_amount": Decimal("0"),
    }


def test_intermittent_forecasts_remain_positive_without_spreading_returns() -> None:
    series = [Decimal("0")] * 20 + [Decimal("2")] + [Decimal("0")] * 10

    assert _croston(series, sba=True) > 0
    assert _tsb(series) > 0


def test_largest_revenue_product_is_always_class_a() -> None:
    service = ProcurementPlanningService(session=None, tenant_id=uuid.uuid4())  # type: ignore[arg-type]
    first = _product("1")
    first.update(
        {"product_key": 1, "revenue": Decimal("900"), "daily": {date(2026, 8, 28): Decimal("1")}}
    )
    second = _product("1")
    second.update(
        {"product_key": 2, "revenue": Decimal("100"), "daily": {date(2026, 8, 28): Decimal("1")}}
    )

    forecasts = service._forecast_products({1: first, 2: second}, as_of=date(2026, 8, 28))

    assert forecasts[1].abc_class == "A"


def test_negative_stock_is_quarantined_from_automatic_purchase() -> None:
    service = ProcurementPlanningService(session=None, tenant_id=uuid.uuid4())  # type: ignore[arg-type]

    lines = service._recommend(
        products={1: _product("-3")},
        forecasts={1: _forecast()},
        supplier_options={1: [_supplier()]},
        weekly_budget=Decimal("10000000"),
        review_cycle_days=7,
        blocked=False,
    )

    assert lines[0]["decision"] == "reconcile"
    assert lines[0]["explanation"]["gross_demand_not_net_of_returns"] is True


def test_budget_defers_a_complete_line_instead_of_exceeding_cash_limit() -> None:
    service = ProcurementPlanningService(session=None, tenant_id=uuid.uuid4())  # type: ignore[arg-type]

    lines = service._recommend(
        products={1: _product("0")},
        forecasts={1: _forecast()},
        supplier_options={1: [_supplier()]},
        weekly_budget=Decimal("100000"),
        review_cycle_days=7,
        blocked=False,
    )

    assert lines[0]["decision"] == "deferred_budget"


def test_candidate_without_cost_is_sent_to_manual_review() -> None:
    service = ProcurementPlanningService(session=None, tenant_id=uuid.uuid4())  # type: ignore[arg-type]
    product = _product("0")
    product["unit_cost"] = Decimal("0")
    supplier = _supplier()
    supplier["last_unit_cost"] = Decimal("0")

    lines = service._recommend(
        products={1: product},
        forecasts={1: _forecast()},
        supplier_options={1: [supplier]},
        weekly_budget=Decimal("10000000"),
        review_cycle_days=7,
        blocked=False,
    )

    assert lines[0]["decision"] == "review_cost"
    assert lines[0]["estimated_value"] == 0


def test_data_quality_block_prevents_purchase_selection() -> None:
    service = ProcurementPlanningService(session=None, tenant_id=uuid.uuid4())  # type: ignore[arg-type]

    lines = service._recommend(
        products={1: _product("0")},
        forecasts={1: _forecast()},
        supplier_options={1: [_supplier()]},
        weekly_budget=Decimal("10000000"),
        review_cycle_days=7,
        blocked=True,
    )

    assert lines[0]["decision"] == "blocked_data"


def test_recommendation_respects_supplier_minimum_and_pack_size() -> None:
    service = ProcurementPlanningService(session=None, tenant_id=uuid.uuid4())  # type: ignore[arg-type]
    supplier = _supplier()
    supplier.update({"minimum_order_quantity": Decimal("3"), "pack_size": Decimal("2")})

    lines = service._recommend(
        products={1: _product("0")},
        forecasts={1: _forecast()},
        supplier_options={1: [supplier]},
        weekly_budget=Decimal("10000000"),
        review_cycle_days=7,
        blocked=False,
    )

    assert lines[0]["recommended_quantity"] >= Decimal("3")
    assert lines[0]["recommended_quantity"] % Decimal("2") == 0


def test_critical_supplier_order_below_minimum_is_flagged_and_includes_freight() -> None:
    service = ProcurementPlanningService(session=None, tenant_id=uuid.uuid4())  # type: ignore[arg-type]
    line = {
        "decision": "buy_now",
        "supplier_key": 10,
        "supplier": "Proveedor urgente",
        "recommended_quantity": Decimal("1"),
        "estimated_value": Decimal("50000"),
        "priority": "critical",
        "minimum_order_amount": Decimal("200000"),
        "shipping_cost": Decimal("15000"),
        "free_shipping_threshold": Decimal("300000"),
    }

    orders = service._supplier_orders([line])

    assert orders[0]["decision"] == "urgent_below_minimum"
    assert orders[0]["amount_to_minimum"] == Decimal("150000")
    assert orders[0]["shipping_cost_due"] == Decimal("15000")
    assert orders[0]["estimated_total"] == Decimal("65000")


def test_purchase_inputs_bound_last_purchase_to_as_of_date_and_use_selected_snapshot() -> None:
    service = ProcurementPlanningService(session=None, tenant_id=uuid.uuid4())  # type: ignore[arg-type]
    statements: list[tuple[str, dict]] = []

    def capture(statement: str, params: dict | None = None) -> list[dict]:
        statements.append((statement, params or {}))
        return []

    service._rows = capture  # type: ignore[method-assign]
    snapshot_id = uuid.uuid4()

    service._product_inputs(
        as_of=date(2026, 8, 20), currency_code="COP", snapshot_run_id=snapshot_id
    )

    purchase_sql, purchase_params = statements[0]
    assert "d.calendar_date BETWEEN DATE '2025-01-01' AND :as_of" in purchase_sql
    assert "sum(p.quantity) purchase_quantity" in purchase_sql
    assert purchase_params["as_of"] == date(2026, 8, 20)
    assert purchase_params["snapshot_run_id"] == snapshot_id


def test_supplier_candidates_and_performance_are_bounded_by_as_of_date() -> None:
    service = ProcurementPlanningService(session=None, tenant_id=uuid.uuid4())  # type: ignore[arg-type]
    statements: list[tuple[str, dict]] = []

    def capture(statement: str, params: dict | None = None) -> list[dict]:
        statements.append((statement, params or {}))
        return []

    service._rows = capture  # type: ignore[method-assign]

    service._supplier_options(as_of=date(2026, 8, 20), currency_code="COP")

    sql, params = statements[0]
    assert "d.calendar_date BETWEEN DATE '2025-01-01' AND :as_of" in sql
    assert "po.order_date<=:as_of" in sql
    assert "pb.issue_date<=:as_of" in sql
    assert "ON c.tenant_id=s.tenant_id AND c.key=s.supplier_key" in " ".join(sql.split())
    assert params["as_of"] == date(2026, 8, 20)


def test_data_quality_rejects_a_mart_older_than_latest_source_sync(monkeypatch) -> None:
    today = date(2026, 10, 6)
    now = datetime.now(UTC)
    monkeypatch.setattr("app.services.procurement_planning.business_today", lambda: today)
    service = ProcurementPlanningService(session=None, tenant_id=uuid.uuid4())  # type: ignore[arg-type]
    service._one = lambda statement, params=None: {  # type: ignore[method-assign]
        "snapshot_run_id": uuid.uuid4(),
        "snapshot_at": now - timedelta(hours=1),
        "invoice_sync_at": now - timedelta(minutes=20),
        "bill_sync_at": now - timedelta(hours=2),
        "po_sync_at": now - timedelta(hours=2),
        "item_sync_at": now - timedelta(minutes=10),
        "contact_sync_at": now - timedelta(minutes=15),
        "mart_at": now - timedelta(minutes=30),
        "unmapped_supplier_documents": 0,
    }

    quality = service.data_quality(as_of=today)

    assert quality["ready"] is False
    assert "El mart es anterior a la ultima sincronizacion de Alegra" in quality["warnings"]


def test_ambiguous_purchase_order_response_is_never_resent() -> None:
    class SessionStub:
        def __init__(self) -> None:
            self.order: dict | None = None

        def execute(self, statement, params=None):
            if "status='unknown'" in str(statement) and self.order is not None:
                self.order["status"] = "unknown"
            return None

        def commit(self) -> None:
            return None

        def rollback(self) -> None:
            return None

    class AlegraStub:
        calls = 0

        async def create_purchase_order(self, payload: dict) -> dict:
            del payload
            self.calls += 1
            raise TimeoutError("simulated timeout after request")

    session = SessionStub()
    service = ProcurementPlanningService(session=session, tenant_id=uuid.uuid4())  # type: ignore[arg-type]
    plan_id = uuid.uuid4()
    order_id = uuid.uuid4()
    plan = {
        "status": "approved",
        "lines": [
            {
                "decision": "approved",
                "supplier_key": 10,
                "approved_quantity": Decimal("2"),
                "unit_cost": Decimal("100"),
                "alegra_id": "ITEM-1",
            }
        ],
    }
    service.get_plan = lambda requested_plan_id: plan  # type: ignore[method-assign]

    def one(statement: str, params: dict | None = None):
        query = str(statement)
        values = params or {}
        if "purchase_plan_runs" in query and "FOR UPDATE" in query:
            return {"status": "approved"}
        if "FROM dim_warehouse" in query:
            return {"alegra_id": "WH-1"}
        if "FROM dim_contact" in query and "alegra_id,name" in query:
            return {"alegra_id": "SUP-10", "name": "Proveedor"}
        if "INSERT INTO purchase_plan_orders" in query:
            session.order = {
                "id": order_id,
                "status": "submitting",
                "alegra_order_id": None,
                "request_hash": values["hash"],
            }
            return session.order.copy()
        if "SELECT * FROM purchase_plan_orders WHERE tenant_id" in query:
            return session.order.copy() if session.order else None
        if "SELECT * FROM purchase_plan_orders WHERE id" in query:
            return session.order.copy() if session.order else None
        return None

    service._one = one  # type: ignore[method-assign]
    alegra = AlegraStub()

    import asyncio

    first_result = asyncio.run(service.submit_to_alegra(plan_id=plan_id, alegra=alegra))
    second_result = asyncio.run(service.submit_to_alegra(plan_id=plan_id, alegra=alegra))

    assert alegra.calls == 1
    assert first_result["complete"] is False
    assert second_result["complete"] is False
    assert second_result["warnings"]
