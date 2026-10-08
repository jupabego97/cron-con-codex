"""Private retail workflows; all identities come from the authenticated tenant."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.analytics import build_filters
from app.api.dashboard import require_dashboard_session
from app.core.business_time import business_today
from app.core.config import get_settings
from app.db.session import get_db_session
from app.integrations.alegra.client import AlegraClient, AlegraClientError
from app.integrations.alegra.resources import RESOURCE_BY_KEY
from app.services.analytics_queries import AnalyticsFilters
from app.services.operations_health import OperationsHealthService
from app.services.operations_repository import OperationsRepository
from app.services.product_workspace import ProductWorkspaceService
from app.services.receiving import ReceivingService
from app.services.repairs import RepairService
from app.services.treasury import TreasuryService

router = APIRouter(prefix="/api/v1/operations", tags=["operations"])


def repository(
    tenant: Annotated[UUID, Depends(require_dashboard_session)],
    session: Annotated[Session, Depends(get_db_session)],
) -> OperationsRepository:
    return OperationsRepository(session=session, tenant_id=tenant)


Repo = Annotated[OperationsRepository, Depends(repository)]
Filters = Annotated[AnalyticsFilters, Depends(build_filters)]
Offset = Annotated[int, Query(ge=0, le=1000000)]


def invoke(repo, service, method, *args, **kwargs):
    try:
        return getattr(service(session=repo.session, tenant_id=repo.tenant_id), method)(
            *args, **kwargs
        )
    except LookupError as error:
        repo.session.rollback()
        raise HTTPException(404, detail=str(error)) from error
    except ValueError as error:
        repo.session.rollback()
        raise HTTPException(422, detail=str(error)) from error


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProductProfile(Payload):
    lifecycle: Literal["active", "replaced", "discontinued", "on_request"] = "active"
    introduced_on: date | None = None
    replacement_product_key: int | None = Field(default=None, gt=0)
    notes: str | None = Field(default=None, max_length=2000)


class OrderTracking(Payload):
    stage: Literal["pending_confirmation", "confirmed", "in_transit", "closed", "cancelled"]
    expected_on: date | None = None
    notes: str | None = Field(default=None, max_length=2000)


class Receipt(Payload):
    id: UUID
    line_number: int = Field(ge=0)
    received_on: date = Field(default_factory=business_today)
    accepted_quantity: Decimal = Field(default=Decimal(0), ge=0, max_digits=18, decimal_places=4)
    rejected_quantity: Decimal = Field(default=Decimal(0), ge=0, max_digits=18, decimal_places=4)
    notes: str | None = Field(default=None, max_length=2000)


class ResolveOrder(Payload):
    alegra_id: str = Field(min_length=1, max_length=100)


class Balance(Payload):
    currency_code: str = Field(default="COP", pattern=r"^[A-Z]{3,10}$")
    as_of_date: date = Field(default_factory=business_today)
    amount: Decimal = Field(max_digits=18, decimal_places=2)
    notes: str | None = Field(default=None, max_length=2000)


class CashEntry(Payload):
    id: UUID
    currency_code: str = Field(default="COP", pattern=r"^[A-Z]{3,10}$")
    entry_date: date = Field(default_factory=business_today)
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    direction: Literal["in", "out"] = "out"
    stage: Literal["planned", "posted"] = "planned"
    category: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=1000)


class RepairCreate(Payload):
    id: UUID
    customer_name: str = Field(min_length=1, max_length=300)
    customer_phone: str | None = Field(default=None, max_length=100)
    contact_key: int | None = Field(default=None, gt=0)
    product_key: int | None = Field(default=None, gt=0)
    device_description: str = Field(min_length=1, max_length=1000)
    serial_number: str | None = Field(default=None, max_length=300)
    reported_issue: str = Field(min_length=1, max_length=4000)
    currency_code: str = Field(default="COP", pattern=r"^[A-Z]{3,10}$")
    warranty_days: int = Field(default=30, ge=0, le=1095)
    warranty_parent_id: UUID | None = None
    received_on: date = Field(default_factory=business_today)
    promised_on: date | None = None


class RepairUpdate(Payload):
    costs_complete: bool | None = None
    status: (
        Literal[
            "received",
            "diagnosing",
            "awaiting_approval",
            "approved",
            "in_progress",
            "ready",
            "delivered",
            "cancelled",
        ]
        | None
    ) = None
    diagnosis: str | None = Field(default=None, max_length=4000)
    quoted_amount: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    charged_amount: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    labour_cost: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    promised_on: date | None = None
    delivered_on: date | None = None
    warranty_days: int | None = Field(default=None, ge=0, le=1095)
    invoice_alegra_id: str | None = Field(default=None, max_length=100)


class RepairPart(Payload):
    id: UUID
    product_key: int | None = Field(default=None, gt=0)
    description: str = Field(min_length=1, max_length=1000)
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=4)
    unit_cost: Decimal = Field(ge=0, max_digits=18, decimal_places=2)


@router.get("/today")
def today(repo: Repo):
    return invoke(repo, OperationsHealthService, "today")


@router.get("/status")
def system_status(repo: Repo):
    return invoke(repo, OperationsHealthService, "status")


@router.get("/events")
def events(repo: Repo, offset: Offset = 0):
    return invoke(repo, OperationsHealthService, "events", offset=offset)


@router.post("/events/{event_id}/retry")
def retry_event(event_id: UUID, repo: Repo):
    return invoke(repo, OperationsHealthService, "retry", event_id)


@router.get("/products")
def products(repo: Repo, q: Annotated[str, Query(max_length=200)] = "", offset: Offset = 0):
    return invoke(repo, ProductWorkspaceService, "search", query=q, offset=offset)


@router.get("/products/{key}")
def product(key: int, repo: Repo, filters: Filters, offset: Offset = 0):
    return invoke(repo, ProductWorkspaceService, "detail", key, filters, offset=offset)


@router.put("/products/{key}/profile")
def product_profile(key: int, payload: ProductProfile, repo: Repo):
    return invoke(repo, ProductWorkspaceService, "update_profile", key, payload.model_dump())


@router.get("/orders")
def orders(repo: Repo, q: Annotated[str, Query(max_length=200)] = "", offset: Offset = 0):
    return invoke(repo, ReceivingService, "orders", query=q, offset=offset)


@router.post("/orders/resolve/{intent_id}")
async def resolve_order(intent_id: UUID, payload: ResolveOrder, repo: Repo):
    settings = get_settings()
    if not settings.alegra_api_basic_token:
        raise HTTPException(503, detail="Alegra no está configurado")
    try:
        async with AlegraClient(
            basic_token=settings.alegra_api_basic_token.get_secret_value()
        ) as client:
            canonical = await client.get_resource(
                RESOURCE_BY_KEY["purchase_order"], payload.alegra_id
            )
    except AlegraClientError as error:
        raise HTTPException(
            502, detail="No se pudo verificar la orden en Alegra; no se creó ninguna orden"
        ) from error
    return invoke(repo, ReceivingService, "resolve", intent_id, canonical)


@router.get("/orders/{order_id}")
def order(order_id: str, repo: Repo):
    return invoke(repo, ReceivingService, "detail", order_id)


@router.put("/orders/{order_id}/tracking")
def order_tracking(order_id: str, payload: OrderTracking, repo: Repo):
    return invoke(repo, ReceivingService, "track", order_id, payload.model_dump())


@router.post("/orders/{order_id}/receipts", status_code=201)
def receipt(order_id: str, payload: Receipt, repo: Repo):
    return invoke(repo, ReceivingService, "receive", order_id, payload.model_dump())


@router.get("/treasury")
def treasury(repo: Repo, currency: Annotated[str, Query(pattern=r"^[A-Z]{3,10}$")] = "COP"):
    return invoke(repo, TreasuryService, "projection", currency)


@router.post("/treasury/balance")
def balance(payload: Balance, repo: Repo):
    return invoke(repo, TreasuryService, "balance", payload.model_dump())


@router.post("/treasury/entries", status_code=201)
def cash_entry(payload: CashEntry, repo: Repo):
    return invoke(repo, TreasuryService, "entry", payload.model_dump())


@router.post("/treasury/entries/{entry_id}/post")
def post_cash_entry(entry_id: UUID, repo: Repo):
    return invoke(repo, TreasuryService, "post_entry", entry_id)


@router.get("/repairs")
def repairs(
    repo: Repo, filters: Filters, q: Annotated[str, Query(max_length=200)] = "", offset: Offset = 0
):
    return invoke(repo, RepairService, "list", filters, query=q, offset=offset)


@router.post("/repairs", status_code=201)
def create_repair(payload: RepairCreate, repo: Repo):
    return invoke(repo, RepairService, "create", payload.model_dump())


@router.get("/repairs/{job_id}")
def repair(job_id: UUID, repo: Repo):
    return invoke(repo, RepairService, "detail", job_id)


@router.patch("/repairs/{job_id}")
def update_repair(job_id: UUID, payload: RepairUpdate, repo: Repo):
    data = payload.model_dump(exclude_unset=True)
    if any(
        data.get(key) is None
        for key in ("status", "warranty_days", "costs_complete")
        if key in data
    ):
        raise HTTPException(422, detail="Estado y garantía no pueden ser nulos")
    return invoke(repo, RepairService, "update", job_id, data)


@router.post("/repairs/{job_id}/parts", status_code=201)
def repair_part(job_id: UUID, payload: RepairPart, repo: Repo):
    return invoke(repo, RepairService, "add_part", job_id, payload.model_dump())
