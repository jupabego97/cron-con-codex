"""Business-calendar helpers shared by operational workflows."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

BUSINESS_TIMEZONE = ZoneInfo("America/Bogota")


def business_today() -> date:
    return datetime.now(BUSINESS_TIMEZONE).date()
