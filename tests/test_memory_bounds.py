import hashlib
import json
from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.db import session as database
from app.services.procurement_planning import ProcurementPlanningService, _forecast_fingerprint


@pytest.mark.parametrize("products", [{}, {1: {}}, {
    9: {"revenue": 123, "introduced_on": date(2026, 1, 1),
        "daily": {date(2026, 1, 1) + timedelta(days=i): i for i in range(100)},
        "availability": {date(2026, 1, 2): True, date(2026, 1, 1): False}},
    2: {"revenue": 0, "introduced_on": None},
}])
def test_bounded_forecast_hash_is_byte_compatible_with_full_catalog(products):
    inputs = [
        [key, str(product.get("revenue") or 0), str(product.get("introduced_on")),
         sorted((str(day), str(units)) for day, units in product.get("daily", {}).items()),
         sorted((str(day), bool(value)) for day, value in product.get("availability", {}).items())]
        for key, product in products.items()
    ]
    previous = hashlib.sha256(json.dumps(inputs, separators=(",", ":")).encode()).hexdigest()
    assert _forecast_fingerprint(products) == previous


@pytest.mark.parametrize("kwargs,expected", [({}, (2, 2, 30)), (
    {"database_pool_size": 3, "database_max_overflow": 0, "database_pool_timeout_seconds": 10},
    (3, 0, 10),
)])
def test_engine_has_a_small_configurable_connection_bound(monkeypatch, kwargs, expected):
    settings = Settings(app_env="test", database_url="postgresql://localhost/unused", **kwargs)
    captured = {}
    engine = object()

    def create(url, **options):
        captured.update(options)
        return engine

    database.get_engine.cache_clear()
    monkeypatch.setattr(database, "get_settings", lambda: settings)
    monkeypatch.setattr(database, "create_engine", create)
    monkeypatch.setattr(database, "instrument_engine", lambda value: value)
    try:
        assert database.get_engine() is engine
        assert (
            captured["pool_size"], captured["max_overflow"], captured["pool_timeout"]
        ) == expected
        assert captured["pool_pre_ping"] and captured["hide_parameters"]
    finally:
        database.get_engine.cache_clear()


@pytest.mark.parametrize("field,value", [
    ("database_pool_size", 0), ("database_pool_size", 51),
    ("database_max_overflow", -1), ("database_pool_timeout_seconds", 0),
])
def test_unbounded_or_invalid_pool_configuration_is_rejected(field, value):
    with pytest.raises(ValidationError):
        Settings(app_env="test", _env_file=None, **{field: value})


def test_streamed_input_closes_cursor_even_when_consumer_stops():
    class Result:
        closed = False

        def mappings(self):
            yield {"product_key": 1}
            yield {"product_key": 2}

        def close(self):
            self.closed = True

    result = Result()

    class Session:
        def execute(self, statement, params, execution_options):
            assert params["tenant_id"] == "tenant"
            assert execution_options == {"yield_per": 500}
            return result

    rows = ProcurementPlanningService(session=Session(), tenant_id="tenant")._iter_rows("SELECT 1")
    assert next(rows) == {"product_key": 1}
    rows.close()
    assert result.closed
