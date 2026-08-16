from datetime import date
from decimal import Decimal
from uuid import UUID

from app.services.ai_agent import TOOLS, RetailAIAgent, _filters_from_arguments, _json_safe
from app.services.analytics_queries import AnalyticsFilters


def test_ai_tools_are_read_only_functions() -> None:
    names = {tool["name"] for tool in TOOLS}

    assert names == {
        "get_inventory_analysis",
        "get_replenishment_plan",
        "get_sales_analysis",
        "get_purchase_supplier_analysis",
        "get_payments_analysis",
        "get_customer_analysis",
        "get_business_kpis",
        "get_data_status",
    }
    assert all(tool["type"] == "function" for tool in TOOLS)


def test_tool_filters_inherit_dashboard_context_and_bound_date_range() -> None:
    base = AnalyticsFilters(
        from_date=date(2026, 7, 1),
        to_date=date(2026, 7, 31),
        currency="COP",
        family="Computadores",
    )

    result = _filters_from_arguments(
        {"to_date": "2026-08-09", "product_key": 42},
        base,
    )

    assert result.from_date == date(2026, 7, 1)
    assert result.to_date == date(2026, 8, 9)
    assert result.currency == "COP"
    assert result.family == "Computadores"
    assert result.product_key == 42


def test_tool_filters_allow_full_historical_range() -> None:
    base = AnalyticsFilters(
        from_date=date(2022, 11, 9),
        to_date=date(2026, 8, 16),
    )

    result = _filters_from_arguments({}, base)

    assert result.from_date == date(2022, 11, 9)
    assert result.to_date == date(2026, 8, 16)


def test_json_safe_preserves_decimal_as_text() -> None:
    value = {"amount": Decimal("123.45"), "date": date.today(), "items": [Decimal("2")]}

    result = _json_safe(value)

    assert result["amount"] == "123.45"
    assert result["date"] == date.today().isoformat()
    assert result["items"] == ["2"]


class _Result:
    def first(self):
        return None

    def mappings(self):
        return []


class _Session:
    def __init__(self) -> None:
        self.commits = 0

    def execute(self, *_args, **_kwargs):
        return _Result()

    def commit(self) -> None:
        self.commits += 1


class _Analytics:
    def refresh_status(self):
        return {"status": "succeeded"}

    def _one(self, *_args, **_kwargs):
        return {"status": "succeeded"}


class _SalesAnalytics(_Analytics):
    def sales(self, _filters):
        return {
            "summary": [{"currency_code": "COP", "net_sales": "1000"}],
            "by_hour": [{"hour": 10, "period": "10:00", "currency_code": "COP"}],
            "time_coverage": [{"currency_code": "COP", "documents_with_time": 1}],
        }


class _Call:
    type = "function_call"
    name = "get_data_status"
    arguments = "{}"
    call_id = "call_1"


class _Response:
    def __init__(self, output, output_text=None):
        self.output = output
        self.output_text = output_text


class _Responses:
    def __init__(self):
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            return _Response([_Call()])
        return _Response([], "El mart esta actualizado.")


class _Client:
    def __init__(self):
        self.responses = _Responses()


def test_agent_executes_tools_before_returning_answer() -> None:
    session = _Session()
    agent = RetailAIAgent(
        session=session,
        tenant_id=UUID("23332716-6b46-41d4-bc9b-03613fbab6df"),
        analytics=_Analytics(),
        api_key="test-key",
        model="test-model",
    )
    client = _Client()
    agent._client = lambda: client

    result = agent.ask(
        message="Esta actualizado el mart?",
        base_filters=AnalyticsFilters.default(),
    )

    assert result["answer"] == "El mart esta actualizado."
    assert len(result["tools_used"]) == 1
    assert result["tools_used"][0]["tool"] == "get_data_status"
    assert client.responses.calls == 2
    assert session.commits == 1


def test_sales_tool_exposes_business_hours_to_the_agent() -> None:
    agent = RetailAIAgent(
        session=_Session(),
        tenant_id=UUID("23332716-6b46-41d4-bc9b-03613fbab6df"),
        analytics=_SalesAnalytics(),
        api_key="test-key",
        model="test-model",
    )

    result = agent._dispatch_tool(
        "get_sales_analysis",
        {},
        AnalyticsFilters(from_date=date(2026, 8, 1), to_date=date(2026, 8, 15)),
    )

    assert result["by_hour"][0]["period"] == "10:00"
    assert result["time_coverage"][0]["documents_with_time"] == 1
    assert result["business_hours"] == {
        "timezone": "America/Bogota",
        "opens_at": "10:00",
        "closes_at": "20:00",
        "included_hour_buckets": "10:00-19:00",
    }


class _GeminiCall:
    type = "function_call"
    name = "get_data_status"
    arguments = {}
    id = "gemini-call-1"


class _GeminiResponse:
    def __init__(self, steps, output_text=None, interaction_id="interaction-1"):
        self.steps = steps
        self.output_text = output_text
        self.id = interaction_id


class _Interactions:
    def __init__(self):
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            return _GeminiResponse([_GeminiCall()])
        return _GeminiResponse([], "El mart esta actualizado.", "interaction-2")


class _GeminiClient:
    def __init__(self):
        self.interactions = _Interactions()


def test_agent_executes_gemini_interactions_tools_before_returning_answer() -> None:
    session = _Session()
    agent = RetailAIAgent(
        session=session,
        tenant_id=UUID("23332716-6b46-41d4-bc9b-03613fbab6df"),
        analytics=_Analytics(),
        api_key="test-key",
        model="gemini-3.6-flash",
        provider="gemini",
    )
    client = _GeminiClient()
    agent._client = lambda: client

    result = agent.ask(
        message="Esta actualizado el mart?",
        base_filters=AnalyticsFilters.default(),
    )

    assert result["answer"] == "El mart esta actualizado."
    assert result["provider"] == "gemini"
    assert len(result["tools_used"]) == 1
    assert result["tools_used"][0]["tool"] == "get_data_status"
    assert client.interactions.calls == 2
    assert session.commits == 1
