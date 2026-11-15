import asyncio
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.cli import build_parser
from app.services import forecast_memo, procurement_planning, webhook_worker
from app.services.forecast_memo import ForecastMemo
from app.services.procurement_planning import ProcurementPlanningService
from app.services.webhook_worker import WebhookWorker


def test_batch_cli_keeps_legacy_mode():
    assert not build_parser().parse_args(["worker"]).once
    args = build_parser().parse_args(["worker", "--once", "--max-events", "3"])
    assert args.once and args.max_events == 3 and args.max_seconds == 240


@pytest.mark.parametrize(
    "events,limit,attempted,reason",
    [
        ([False], 500, 0, "queue_empty"),
        ([True, True, False], 500, 2, "queue_empty"),
        ([True, True, True], 2, 2, "event_limit"),
    ],
)
def test_batch_terminates_without_waiting(events, limit, attempted, reason):
    worker = WebhookWorker(session=None, alegra=None)
    calls = iter(events)

    async def once():
        return next(calls)

    worker.run_once = once
    result = asyncio.run(worker.run_batch(max_events=limit))
    assert (result.attempted, result.stop_reason) == (attempted, reason)


def test_batch_soft_deadline_does_not_cancel_an_event(monkeypatch):
    worker = WebhookWorker(session=None, alegra=None)
    ticks = iter([0, 0, 241])
    monkeypatch.setattr(webhook_worker.time, "monotonic", lambda: next(ticks))

    async def once():
        return True

    worker.run_once = once
    coroutine = worker.run_batch(max_seconds=240)
    with pytest.raises(StopIteration) as stop:
        coroutine.send(None)
    assert stop.value.value.attempted == 1
    assert stop.value.value.stop_reason == "time_limit"


def _products(as_of):
    return {
        1: {
            "product_key": 1,
            "revenue": Decimal(300),
            "introduced_on": as_of - timedelta(days=29),
            "daily": {as_of - timedelta(days=i): Decimal(1) for i in range(0, 30, 3)},
            "availability": {as_of: True},
            "quantity_on_hand": 2,
        }
    }


def test_forecast_exact_cache_hits_detach_data_and_keep_current_stock(monkeypatch):
    monkeypatch.setattr(procurement_planning, "forecast_memo", ForecastMemo())
    service = ProcurementPlanningService(session=None, tenant_id=uuid4())
    as_of = date(2026, 11, 14)
    expected = service._forecast_products(_products(as_of), as_of=as_of)
    spy = Mock(wraps=service._forecast_products)
    monkeypatch.setattr(service, "_forecast_products", spy)
    first = service._cached_forecast_products(_products(as_of), as_of=as_of, currency_code="COP")
    fresh = _products(as_of)
    fresh[1].update(quantity_on_hand=0, unit_cost=200, lifecycle="discontinued")
    second = service._cached_forecast_products(fresh, as_of=as_of, currency_code="COP")
    assert first == second == expected and spy.call_count == 1
    assert fresh[1]["quantity_on_hand"] == 0
    assert fresh[1]["forecast_history"]["history_days"] == 30
    second.clear()
    fresh[1]["forecast_history"].clear()
    assert (
        service._cached_forecast_products(_products(as_of), as_of=as_of, currency_code="COP")
        == expected
    )
    assert spy.call_count == 1


@pytest.mark.parametrize(
    "change",
    ["sales", "revenue", "availability", "introduced", "date", "currency", "tenant", "catalog"],
)
def test_forecast_invalidates_each_demand_input(monkeypatch, change):
    monkeypatch.setattr(procurement_planning, "forecast_memo", ForecastMemo())
    service = ProcurementPlanningService(session=None, tenant_id=uuid4())
    as_of = date(2026, 11, 14)
    spy = Mock(wraps=service._forecast_products)
    monkeypatch.setattr(service, "_forecast_products", spy)
    service._cached_forecast_products(_products(as_of), as_of=as_of, currency_code="COP")
    fresh, currency = _products(as_of), "COP"
    if change == "sales":
        fresh[1]["daily"][as_of] = Decimal(9)
    elif change == "revenue":
        fresh[1]["revenue"] = Decimal(400)
    elif change == "availability":
        fresh[1]["availability"][as_of] = False
    elif change == "introduced":
        fresh[1]["introduced_on"] = as_of
    elif change == "date":
        as_of += timedelta(days=1)
    elif change == "currency":
        currency = "USD"
    elif change == "tenant":
        service._tenant_id = uuid4()
    else:
        fresh[2] = deepcopy(fresh[1])
        fresh[2]["product_key"] = 2
    actual = service._cached_forecast_products(fresh, as_of=as_of, currency_code=currency)
    assert spy.call_count == 2
    assert actual == service._forecast_products(deepcopy(fresh), as_of=as_of)


def test_memo_eviction_expiry_size_and_errors(monkeypatch):
    memo = ForecastMemo(max_entries=1, max_products=2, ttl_seconds=10)
    compute = Mock(return_value="value")
    monkeypatch.setattr(forecast_memo.time, "monotonic", lambda: 0)
    for key in ("a", "b", "a"):
        memo.get_or_compute((key,), product_count=2, compute=compute)
    assert compute.call_count == 3 and len(memo._entries) == 1
    monkeypatch.setattr(forecast_memo.time, "monotonic", lambda: 11)
    memo.get_or_compute(("a",), product_count=2, compute=compute)
    assert compute.call_count == 4
    for _ in range(2):
        memo.get_or_compute(("large",), product_count=3, compute=compute)
    assert compute.call_count == 6 and len(memo._entries) == 1
    failure = Mock(side_effect=RuntimeError("not cached"))
    for _ in range(2):
        with pytest.raises(RuntimeError):
            memo.get_or_compute(("failed",), product_count=1, compute=failure)
    assert failure.call_count == 2
