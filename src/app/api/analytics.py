"""Authenticated, tenant-isolated read API for dashboard data."""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.dashboard import require_dashboard_session
from app.db.session import get_db_session
from app.services.analytics_queries import AnalyticsFilters, AnalyticsQueryService

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


def build_filters(
    from_date: Annotated[date | None, Query()] = None,
    to_date: Annotated[date | None, Query()] = None,
    currency: Annotated[str | None, Query(max_length=10)] = None,
    product_key: Annotated[int | None, Query(ge=1)] = None,
    seller_key: Annotated[int | None, Query(ge=1)] = None,
    warehouse_key: Annotated[int | None, Query(ge=1)] = None,
    document_status: Annotated[str | None, Query(max_length=30)] = None,
) -> AnalyticsFilters:
    defaults = AnalyticsFilters.default()
    result = AnalyticsFilters(
        from_date=from_date or defaults.from_date,
        to_date=to_date or defaults.to_date,
        currency=currency,
        product_key=product_key,
        seller_key=seller_key,
        warehouse_key=warehouse_key,
        document_status=document_status,
    )
    if result.from_date > result.to_date:
        raise HTTPException(status_code=422, detail="from_date must not be after to_date")
    return result


def query_service(
    tenant_id: Annotated[UUID, Depends(require_dashboard_session)],
    session: Annotated[Session, Depends(get_db_session)],
) -> AnalyticsQueryService:
    return AnalyticsQueryService(session=session, tenant_id=tenant_id)


@router.get("/filters")
def get_filters(service: Annotated[AnalyticsQueryService, Depends(query_service)]) -> dict:
    return service.filters()


@router.get("/overview")
def get_overview(
    filters: Annotated[AnalyticsFilters, Depends(build_filters)],
    service: Annotated[AnalyticsQueryService, Depends(query_service)],
) -> dict:
    return service.overview(filters)


@router.get("/sales")
def get_sales(
    filters: Annotated[AnalyticsFilters, Depends(build_filters)],
    service: Annotated[AnalyticsQueryService, Depends(query_service)],
) -> dict:
    return service.sales(filters)


@router.get("/purchases")
def get_purchases(
    filters: Annotated[AnalyticsFilters, Depends(build_filters)],
    service: Annotated[AnalyticsQueryService, Depends(query_service)],
) -> dict:
    return service.purchases(filters)


@router.get("/payments")
def get_payments(
    filters: Annotated[AnalyticsFilters, Depends(build_filters)],
    service: Annotated[AnalyticsQueryService, Depends(query_service)],
) -> dict:
    return service.payments(filters)


@router.get("/inventory")
def get_inventory(
    filters: Annotated[AnalyticsFilters, Depends(build_filters)],
    service: Annotated[AnalyticsQueryService, Depends(query_service)],
) -> dict:
    return service.inventory(filters)


@router.get("/refresh-status")
def get_refresh_status(service: Annotated[AnalyticsQueryService, Depends(query_service)]) -> dict:
    return service.refresh_status()
