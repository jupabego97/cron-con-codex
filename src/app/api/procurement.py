"""Authenticated procurement planning and approval API."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.dashboard import require_dashboard_session
from app.core.business_time import business_today
from app.core.config import get_settings
from app.db.session import get_db_session
from app.integrations.alegra.client import AlegraClient
from app.services.procurement_planning import ProcurementPlanningService

router = APIRouter(prefix="/api/v1/procurement", tags=["procurement"])


def planning_service(
    tenant_id: Annotated[UUID, Depends(require_dashboard_session)],
    session: Annotated[Session, Depends(get_db_session)],
) -> ProcurementPlanningService:
    return ProcurementPlanningService(session=session, tenant_id=tenant_id)


class PlanPayload(BaseModel):
    as_of_date: date = Field(default_factory=business_today)
    weekly_budget: Decimal = Field(ge=0)
    currency_code: str = Field(default="COP", min_length=3, max_length=10)
    review_cycle_days: int = Field(default=7, ge=1, le=31)


class PlanLinePayload(BaseModel):
    decision: Literal["approved", "discarded", "snoozed"]
    approved_quantity: Decimal = Field(default=Decimal("0"), ge=0)
    supplier_key: int | None = Field(default=None, gt=0)
    note: str | None = Field(default=None, max_length=2000)


class SubmitPlanPayload(BaseModel):
    confirm: Literal[True]


class ApprovePlanPayload(BaseModel):
    allow_below_minimum: bool = False


@router.get("/data-quality")
def get_data_quality(
    service: Annotated[ProcurementPlanningService, Depends(planning_service)],
) -> dict:
    return service.data_quality()


@router.get("/preview")
def preview_plan(
    service: Annotated[ProcurementPlanningService, Depends(planning_service)],
    as_of_date: Annotated[date | None, Query()] = None,
    weekly_budget: Annotated[Decimal, Query(ge=0)] = Decimal("15000000"),
    currency_code: Annotated[str, Query(min_length=3, max_length=10)] = "COP",
    review_cycle_days: Annotated[int, Query(ge=1, le=31)] = 7,
    decision: Annotated[
        Literal[
            "buy_now",
            "buy_weekly",
            "reconcile",
            "review_supplier",
            "review_cost",
            "deferred_budget",
            "blocked_data",
            "covered",
            "no_reorder",
        ] | None,
        Query(),
    ] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=5000)] = 1000,
    product_key: Annotated[int | None, Query(gt=0)] = None,
    family: Annotated[str | None, Query(max_length=120)] = None,
    provider_key: Annotated[int | None, Query(gt=0)] = None,
) -> dict:
    return service.preview(
        as_of=as_of_date or business_today(),
        weekly_budget=weekly_budget,
        currency_code=currency_code,
        review_cycle_days=review_cycle_days,
        decision=decision,
        offset=offset,
        limit=limit,
        product_key=product_key, family=family, supplier_key=provider_key,
    )


@router.post("/plans", status_code=201)
def create_plan(
    payload: PlanPayload,
    service: Annotated[ProcurementPlanningService, Depends(planning_service)],
) -> dict:
    try:
        return service.create_plan(
            as_of=payload.as_of_date,
            weekly_budget=payload.weekly_budget,
            currency_code=payload.currency_code,
            review_cycle_days=payload.review_cycle_days,
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/plans/{plan_id}")
def get_plan(
    plan_id: UUID,
    service: Annotated[ProcurementPlanningService, Depends(planning_service)],
) -> dict:
    try:
        return service.get_plan(plan_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.patch("/plans/{plan_id}/lines/{line_id}")
def update_plan_line(
    plan_id: UUID,
    line_id: UUID,
    payload: PlanLinePayload,
    service: Annotated[ProcurementPlanningService, Depends(planning_service)],
) -> dict:
    try:
        return service.update_line(
            plan_id=plan_id,
            line_id=line_id,
            decision=payload.decision,
            approved_quantity=payload.approved_quantity,
            note=payload.note,
            supplier_key=payload.supplier_key,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/plans/{plan_id}/approve")
def approve_plan(
    plan_id: UUID,
    payload: ApprovePlanPayload,
    service: Annotated[ProcurementPlanningService, Depends(planning_service)],
) -> dict:
    try:
        return service.approve_plan(
            plan_id, allow_below_minimum=payload.allow_below_minimum
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/plans/{plan_id}/submit-to-alegra")
async def submit_plan(
    plan_id: UUID,
    payload: SubmitPlanPayload,
    service: Annotated[ProcurementPlanningService, Depends(planning_service)],
) -> dict:
    del payload
    settings = get_settings()
    if settings.alegra_api_basic_token is None:
        raise HTTPException(status_code=503, detail="Alegra is not configured")
    try:
        async with AlegraClient(
            basic_token=settings.alegra_api_basic_token.get_secret_value()
        ) as alegra:
            return await service.submit_to_alegra(plan_id=plan_id, alegra=alegra)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/suppliers/{supplier_key}/performance")
def supplier_performance(
    supplier_key: int,
    service: Annotated[ProcurementPlanningService, Depends(planning_service)],
) -> dict:
    try:
        return service.supplier_performance(supplier_key)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
