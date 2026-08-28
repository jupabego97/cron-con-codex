import uuid
from datetime import date
from decimal import Decimal

from app.services.procurement_planning import (
    SERVICE_LEVELS,
    ForecastResult,
    ProcurementPlanningService,
    _croston,
    _tsb,
)


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
