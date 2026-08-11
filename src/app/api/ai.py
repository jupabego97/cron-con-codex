"""Authenticated conversational analytics assistant."""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.dashboard import require_dashboard_session
from app.core.config import get_settings
from app.db.session import get_db_session
from app.services.ai_agent import AIAgentError, AIAgentNotConfigured, RetailAIAgent
from app.services.analytics_queries import AnalyticsFilters, AnalyticsQueryService

router = APIRouter(prefix="/api/v1/ai", tags=["ai"])


class AIContextPayload(BaseModel):
    from_date: date | None = None
    to_date: date | None = None
    currency: str | None = Field(default=None, max_length=10)
    product_key: int | None = Field(default=None, ge=1)
    seller_key: int | None = Field(default=None, ge=1)
    warehouse_key: int | None = Field(default=None, ge=1)
    document_status: str | None = Field(default=None, max_length=30)
    family: str | None = Field(default=None, max_length=120)
    provider_key: int | None = Field(default=None, ge=1)


class AIChatPayload(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: UUID | None = None
    context: AIContextPayload | None = None


def _context_filters(context: AIContextPayload | None) -> AnalyticsFilters:
    defaults = AnalyticsFilters.default()
    context = context or AIContextPayload()
    filters = AnalyticsFilters(
        from_date=context.from_date or defaults.from_date,
        to_date=context.to_date or defaults.to_date,
        currency=context.currency.upper() if context.currency else None,
        product_key=context.product_key,
        seller_key=context.seller_key,
        warehouse_key=context.warehouse_key,
        document_status=context.document_status,
        family=context.family,
        provider_key=context.provider_key,
    )
    if filters.from_date > filters.to_date:
        raise HTTPException(status_code=422, detail="El rango de fechas no es valido")
    if (filters.to_date - filters.from_date).days > 730:
        raise HTTPException(status_code=422, detail="El rango maximo del asistente es de 731 dias")
    return filters


@router.get("/status")
def get_ai_status(
    _: Annotated[UUID, Depends(require_dashboard_session)],
) -> dict[str, object]:
    settings = get_settings()
    if settings.ai_provider == "gemini":
        configured = bool(
            settings.gemini_api_key
            and settings.gemini_api_key.get_secret_value().strip()
        )
        model = settings.gemini_model
        key_name = "GEMINI_API_KEY"
    else:
        configured = bool(
            settings.openai_api_key
            and settings.openai_api_key.get_secret_value().strip()
        )
        model = settings.openai_model
        key_name = "OPENAI_API_KEY"
    return {
        "configured": configured,
        "provider": settings.ai_provider,
        "model": model,
        "key_name": key_name,
        "mode": "read_only",
    }


@router.post("/chat")
def chat(
    payload: AIChatPayload,
    tenant_id: Annotated[UUID, Depends(require_dashboard_session)],
    session: Annotated[Session, Depends(get_db_session)],
) -> dict[str, object]:
    settings = get_settings()
    if settings.ai_provider == "gemini":
        api_key = settings.gemini_api_key
        model = settings.gemini_model
        key_name = "GEMINI_API_KEY"
    else:
        api_key = settings.openai_api_key
        model = settings.openai_model
        key_name = "OPENAI_API_KEY"
    if not api_key or not api_key.get_secret_value().strip():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"El asistente no esta configurado. Agrega {key_name} en Railway.",
        )
    filters = _context_filters(payload.context)
    analytics = AnalyticsQueryService(
        session=session,
        tenant_id=tenant_id,
        monthly_sales_target_cop=settings.dashboard_monthly_sales_target_cop,
    )
    try:
        agent = RetailAIAgent(
            session=session,
            tenant_id=tenant_id,
            analytics=analytics,
            api_key=api_key.get_secret_value(),
            model=model,
            provider=settings.ai_provider,
            max_tool_rounds=settings.openai_max_tool_rounds,
        )
        return agent.ask(
            message=payload.message.strip(),
            base_filters=filters,
            conversation_id=payload.conversation_id,
        )
    except AIAgentNotConfigured as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except AIAgentError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
