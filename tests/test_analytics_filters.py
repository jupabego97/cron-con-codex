from datetime import date

import pytest
from fastapi import HTTPException

from app.api.analytics import build_filters
from app.services.analytics_queries import AnalyticsFilters


def test_previous_period_has_the_same_length() -> None:
    current = AnalyticsFilters(from_date=date(2026, 7, 1), to_date=date(2026, 7, 31))

    assert current.previous_period() == AnalyticsFilters(
        from_date=date(2026, 5, 31), to_date=date(2026, 6, 30)
    )


def test_date_filter_rejects_an_inverted_range() -> None:
    with pytest.raises(HTTPException, match="from_date"):
        build_filters(from_date=date(2026, 7, 31), to_date=date(2026, 7, 1))
