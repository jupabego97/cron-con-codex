"""Small publication contract shared by the nightly job and the read-only page."""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field

MAX_EVENT_BYTES = 256 * 1024
EVENT_BATCH_SIZE = 25
MAX_REPORT_BYTES = 4 * 1024 * 1024


class DailyProduct(BaseModel):
    name: str
    reference: str | None = None
    family: str
    quantity_on_hand: Decimal
    quantity_in_transit: Decimal
    recommended_quantity: Decimal = Field(gt=0)
    unit_cost: Decimal
    estimated_value: Decimal
    priority: str


class DailySupplier(BaseModel):
    supplier: str
    products: list[DailyProduct]
    estimated_total: Decimal
    shipping_cost_due: Decimal
    amount_to_minimum: Decimal
    decision: str


class DailyReport(BaseModel):
    version: Literal[1] = 1
    as_of_date: date
    generated_at: AwareDatetime
    snapshot_at: AwareDatetime
    currency_code: str
    weekly_budget: Decimal
    recommended_products: int
    recommended_value: Decimal
    suppliers: list[DailySupplier]
