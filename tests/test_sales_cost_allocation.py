from datetime import date
from decimal import Decimal
from uuid import uuid4

from app.cli import build_parser
from app.services.sales_cost_allocation import (
    HistoricalSalesCostService,
    _CostLayer,
    _SaleLine,
)


def layer(
    *, product_key: int, quantity: str, unit_cost: str, source: str = "inventory_cost_opening"
) -> _CostLayer:
    return _CostLayer(
        layer_id=uuid4(),
        movement_id=uuid4(),
        product_key=product_key,
        warehouse_key=1,
        opened_on=date(2026, 1, 1),
        source_type=source,
        remaining_quantity=Decimal(quantity),
        unit_cost=Decimal(unit_cost),
    )


def sale(*, document_type: str = "invoice", quantity: str = "1") -> _SaleLine:
    return _SaleLine(
        sale_date=date(2026, 1, 2),
        document_type=document_type,
        document_alegra_id=str(uuid4()),
        document_number="FV-1",
        line_number=1,
        product_key=10,
        warehouse_key=1,
        quantity=Decimal(quantity),
        net_sales_amount=Decimal("500"),
    )


def test_fifo_consumes_opening_then_purchase_layer() -> None:
    service = HistoricalSalesCostService(session=None)  # type: ignore[arg-type]
    opening = layer(product_key=10, quantity="3", unit_cost="100")
    purchase = layer(product_key=10, quantity="4", unit_cost="120", source="purchase_bill")

    allocations, summaries, _ = service._allocate_sales(
        sales=[sale(quantity="5")],
        layers={10: [opening, purchase]},
        warehouse_key=1,
    )

    assert len(allocations) == 2
    assert summaries[0].status == "costed"
    assert summaries[0].cogs_amount == Decimal("540")
    assert opening.remaining_quantity == Decimal("0")
    assert purchase.remaining_quantity == Decimal("2")


def test_fifo_marks_shortage_as_partial_without_inventing_cost() -> None:
    service = HistoricalSalesCostService(session=None)  # type: ignore[arg-type]
    opening = layer(product_key=10, quantity="3", unit_cost="100")

    allocations, summaries, _ = service._allocate_sales(
        sales=[sale(quantity="5")],
        layers={10: [opening]},
        warehouse_key=1,
    )

    assert allocations[-1]["allocation_type"] == "unavailable"
    assert summaries[0].status == "partial"
    assert summaries[0].costed_quantity == Decimal("3")
    assert summaries[0].uncosted_quantity == Decimal("2")
    assert summaries[0].cogs_amount == Decimal("300")


def test_credit_note_creates_estimated_return_layer() -> None:
    service = HistoricalSalesCostService(session=None)  # type: ignore[arg-type]
    opening = layer(product_key=10, quantity="3", unit_cost="100")

    allocations, summaries, return_layers = service._allocate_sales(
        sales=[sale(document_type="credit_note", quantity="-2")],
        layers={10: [opening]},
        warehouse_key=1,
    )

    assert allocations[0]["allocation_type"] == "credit_note_return"
    assert summaries[0].status == "estimated"
    assert summaries[0].cogs_amount == Decimal("-200")
    assert len(return_layers) == 1
    assert return_layers[0]["quantity"] == Decimal("2")


def test_cli_exposes_cost_allocation_command() -> None:
    args = build_parser().parse_args(
        ["allocate-sales-costs", "23332716-6b46-41d4-bc9b-03613fbab6df"]
    )

    assert args.command == "allocate-sales-costs"
    assert args.cutoff_date == date(2026, 1, 1)
