from datetime import date, timedelta
from decimal import Decimal

from app.services.analytics_queries import AnalyticsQueryService


def _service() -> AnalyticsQueryService:
    return AnalyticsQueryService.__new__(AnalyticsQueryService)


def _item() -> dict[str, object]:
    return {
        "product_key": 10,
        "quantity_on_hand": Decimal("-4"),
        "daily_velocity": Decimal("1"),
        "unit_cost": Decimal("100"),
        "supplier_options": [],
        "supplier_confidence_pct": Decimal("0"),
        "priority": "critical",
    }


def test_negative_stock_is_not_used_as_extra_replenishment_demand() -> None:
    item = _item()
    result = _service()._decorate_replenishment_items(
        [item],
        policy_context={"suppliers": {}, "products": {}, "preferred": {}},
        target_coverage_days=30,
        lead_time_days=7,
        safety_days=7,
    )[0]

    assert result["stock_for_replenishment"] == Decimal("0")
    assert result["stock_discrepancy_quantity"] == Decimal("4")
    assert result["base_recommended_quantity"] == Decimal("44")
    assert result["inventory_exception"] == "stock_negativo"
    assert result["recommendation_confidence"] == "baja"
    assert result["replenishment_warning"] == "Reconciliar stock negativo antes de comprar"


def test_recent_purchase_lot_is_exposed_without_overriding_policy_quantity() -> None:
    item = _item()
    item["quantity_on_hand"] = Decimal("5")
    today = date.today()
    result = _service()._decorate_replenishment_items(
        [item],
        policy_context={"suppliers": {}, "products": {}, "preferred": {}},
        target_coverage_days=30,
        lead_time_days=7,
        safety_days=7,
        purchase_context={
            "history_from": date(2025, 1, 1),
            "history_to": today,
            "products": {
                10: {
                    "purchase_events_365d": 4,
                    "purchase_events_90d": 2,
                    "first_purchase_date": today - timedelta(days=100),
                    "last_purchase_date": today - timedelta(days=10),
                    "last_purchase_quantity": Decimal("10"),
                    "median_purchase_quantity": Decimal("8"),
                    "average_purchase_quantity": Decimal("9"),
                }
            },
        },
        snapshot_age_days=2,
    )[0]

    assert result["last_purchase_quantity"] == Decimal("10")
    assert result["purchase_lot_reference"] == Decimal("8")
    assert result["purchase_cycle_days"] == Decimal("30")
    assert result["suggested_quantity_by_historical_lot"] == Decimal("40")
    assert result["recommended_quantity"] == Decimal("39")
    assert result["purchase_history_confidence"] == "alta"
    assert result["recommendation_confidence"] == "alta"


def test_supplier_order_is_review_when_stock_needs_reconciliation() -> None:
    item = _item()
    item.update(
        {
            "recommended_quantity": Decimal("10"),
            "estimated_purchase_value": Decimal("1000"),
            "supplier_key": 7,
            "preferred_supplier": "Proveedor de prueba",
            "currency_code": "COP",
            "name": "Producto de prueba",
            "inventory_exception": "stock_negativo",
        }
    )

    order = _service()._supplier_purchase_plans([item], date.today())[0]

    assert order["decision"] == "review"
    assert "stock negativo" in order["decision_reason"]
