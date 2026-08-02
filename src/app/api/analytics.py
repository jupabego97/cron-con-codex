"""Authenticated, tenant-isolated read API for dashboard data."""

import csv
import io
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.dashboard import require_dashboard_session
from app.core.config import get_settings
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
    family: Annotated[str | None, Query(max_length=120)] = None,
    provider_key: Annotated[int | None, Query(ge=1)] = None,
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
        family=family,
        provider_key=provider_key,
    )
    if result.from_date > result.to_date:
        raise HTTPException(status_code=422, detail="from_date must not be after to_date")
    return result


def query_service(
    tenant_id: Annotated[UUID, Depends(require_dashboard_session)],
    session: Annotated[Session, Depends(get_db_session)],
) -> AnalyticsQueryService:
    settings = get_settings()
    return AnalyticsQueryService(
        session=session,
        tenant_id=tenant_id,
        monthly_sales_target_cop=settings.dashboard_monthly_sales_target_cop,
    )


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


@router.get("/suppliers")
def get_suppliers(
    filters: Annotated[AnalyticsFilters, Depends(build_filters)],
    service: Annotated[AnalyticsQueryService, Depends(query_service)],
) -> dict:
    return service.suppliers(filters)


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


@router.get("/customers")
def get_customers(
    filters: Annotated[AnalyticsFilters, Depends(build_filters)],
    service: Annotated[AnalyticsQueryService, Depends(query_service)],
) -> dict:
    return service.customers(filters)


@router.get("/products")
def get_products(
    filters: Annotated[AnalyticsFilters, Depends(build_filters)],
    service: Annotated[AnalyticsQueryService, Depends(query_service)],
) -> dict:
    return service.products(filters)


@router.get("/kpis")
def get_kpis(
    filters: Annotated[AnalyticsFilters, Depends(build_filters)],
    service: Annotated[AnalyticsQueryService, Depends(query_service)],
) -> dict:
    return service.kpis(filters)


ReviewStatus = Literal["pending", "reviewed", "snoozed", "purchased", "discarded"]


class ReplenishmentActionPayload(BaseModel):
    status: ReviewStatus
    note: str | None = Field(default=None, max_length=2000)
    snoozed_until: date | None = None


class SupplierPolicyPayload(BaseModel):
    currency_code: str = Field(default="COP", min_length=3, max_length=10)
    minimum_order_amount: Decimal | None = Field(default=None, ge=0)
    shipping_cost: Decimal = Field(default=Decimal("0"), ge=0)
    free_shipping_threshold: Decimal | None = Field(default=None, ge=0)
    default_lead_time_days: int = Field(default=7, ge=0, le=90)
    max_wait_days: int = Field(default=7, ge=0, le=90)
    priority: int = Field(default=100, ge=0, le=1000)
    active: bool = True
    notes: str | None = Field(default=None, max_length=2000)


class SupplierProductPolicyPayload(BaseModel):
    supplier_key: int = Field(ge=1)
    currency_code: str = Field(default="COP", min_length=3, max_length=10)
    minimum_order_quantity: Decimal | None = Field(default=None, ge=0)
    pack_size: Decimal = Field(default=Decimal("1"), gt=0)
    lead_time_days: int | None = Field(default=None, ge=0, le=90)
    max_wait_days: int | None = Field(default=None, ge=0, le=90)
    is_preferred: bool = False
    active: bool = True
    notes: str | None = Field(default=None, max_length=2000)


@router.get("/purchase-recommendations")
def get_purchase_recommendations(
    filters: Annotated[AnalyticsFilters, Depends(build_filters)],
    service: Annotated[AnalyticsQueryService, Depends(query_service)],
    target_coverage_days: Annotated[int, Query(ge=7, le=365)] = 30,
    lead_time_days: Annotated[int, Query(ge=0, le=90)] = 7,
    safety_days: Annotated[int, Query(ge=0, le=90)] = 7,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    review_status: Annotated[ReviewStatus | None, Query()] = None,
) -> dict:
    return service.purchase_recommendations(
        filters,
        target_coverage_days=target_coverage_days,
        lead_time_days=lead_time_days,
        safety_days=safety_days,
        limit=limit,
        review_status=review_status,
    )


@router.get("/purchase-recommendations/export")
def export_purchase_recommendations(
    filters: Annotated[AnalyticsFilters, Depends(build_filters)],
    service: Annotated[AnalyticsQueryService, Depends(query_service)],
    target_coverage_days: Annotated[int, Query(ge=7, le=365)] = 30,
    lead_time_days: Annotated[int, Query(ge=0, le=90)] = 7,
    safety_days: Annotated[int, Query(ge=0, le=90)] = 7,
    review_status: Annotated[ReviewStatus | None, Query()] = None,
) -> Response:
    result = service.purchase_recommendations(
        filters,
        target_coverage_days=target_coverage_days,
        lead_time_days=lead_time_days,
        safety_days=safety_days,
        limit=500,
        review_status=review_status,
    )
    output = io.StringIO()
    writer = csv.writer(output)
    order_by_supplier = {
        str(order.get("supplier_key")): order for order in result.get("supplier_orders", [])
    }
    writer.writerow(
        [
            "proveedor",
            "decision_pedido",
            "motivo_decision",
            "minimo_proveedor",
            "faltante_para_minimo",
            "fuente_proveedor",
            "prioridad",
            "estado",
            "producto",
            "referencia",
            "familia",
            "stock_actual",
            "velocidad_7_dias",
            "velocidad_30_dias",
            "velocidad_90_dias",
            "cobertura_dias",
            "cantidad_sugerida",
            "costo_unitario",
            "valor_estimado",
            "confianza_proveedor_pct",
            "ultimo_costo",
            "ultima_compra",
            "motivo",
            "nota",
        ]
    )
    for item in result["items"]:
        order = order_by_supplier.get(str(item.get("supplier_key")), {})
        writer.writerow(
            [
                item.get("preferred_supplier") or "Sin proveedor",
                order.get("decision"),
                order.get("decision_reason"),
                order.get("minimum_order_amount"),
                order.get("amount_to_minimum"),
                item.get("supplier_source"),
                item.get("priority"),
                item.get("review_status"),
                item.get("name"),
                item.get("reference"),
                item.get("family"),
                item.get("quantity_on_hand"),
                item.get("units_7d"),
                item.get("units_30d"),
                item.get("units_90d"),
                item.get("coverage_days"),
                item.get("recommended_quantity"),
                item.get("unit_cost"),
                (
                    (item.get("recommended_quantity") or 0)
                    * (item.get("unit_cost") or 0)
                ),
                item.get("supplier_confidence_pct"),
                item.get("last_unit_cost"),
                item.get("last_purchase_date"),
                item.get("reason"),
                item.get("review_note"),
            ]
        )
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="reponer.csv"',
        },
    )


@router.get("/purchase-recommendations/policies")
def get_replenishment_policies(
    service: Annotated[AnalyticsQueryService, Depends(query_service)],
    currency: Annotated[str, Query(min_length=3, max_length=10)] = "COP",
) -> dict:
    return service.replenishment_policies(currency.upper())


@router.put("/purchase-recommendations/policies/suppliers/{supplier_key}")
def update_supplier_replenishment_policy(
    supplier_key: int,
    payload: SupplierPolicyPayload,
    service: Annotated[AnalyticsQueryService, Depends(query_service)],
) -> dict:
    try:
        return service.update_supplier_replenishment_policy(
            supplier_key=supplier_key,
            currency_code=payload.currency_code,
            minimum_order_amount=payload.minimum_order_amount,
            shipping_cost=payload.shipping_cost,
            free_shipping_threshold=payload.free_shipping_threshold,
            default_lead_time_days=payload.default_lead_time_days,
            max_wait_days=payload.max_wait_days,
            priority=payload.priority,
            active=payload.active,
            notes=payload.notes,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.put("/purchase-recommendations/policies/products/{product_key}")
def update_supplier_product_policy(
    product_key: int,
    payload: SupplierProductPolicyPayload,
    service: Annotated[AnalyticsQueryService, Depends(query_service)],
) -> dict:
    try:
        return service.update_supplier_product_policy(
            supplier_key=payload.supplier_key,
            product_key=product_key,
            currency_code=payload.currency_code,
            minimum_order_quantity=payload.minimum_order_quantity,
            pack_size=payload.pack_size,
            lead_time_days=payload.lead_time_days,
            max_wait_days=payload.max_wait_days,
            is_preferred=payload.is_preferred,
            active=payload.active,
            notes=payload.notes,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.patch("/purchase-recommendations/{product_key}")
def update_purchase_recommendation(
    product_key: int,
    payload: ReplenishmentActionPayload,
    service: Annotated[AnalyticsQueryService, Depends(query_service)],
) -> dict:
    try:
        return service.update_replenishment_action(
            product_key=product_key,
            status=payload.status,
            note=payload.note,
            snoozed_until=payload.snoozed_until,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/alerts")
def get_alerts(service: Annotated[AnalyticsQueryService, Depends(query_service)]) -> dict:
    return service.alerts()


@router.get("/refresh-status")
def get_refresh_status(service: Annotated[AnalyticsQueryService, Depends(query_service)]) -> dict:
    return service.refresh_status()
