"""Allocate historical inventory cost to sales lines using an auditable FIFO ledger."""

from __future__ import annotations

import json
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class SalesCostAllocationResult:
    run_id: uuid.UUID | None
    status: str
    lines_read: int
    lines_costed: int
    lines_partial: int
    lines_unavailable: int
    sales_units: Decimal
    costed_units: Decimal
    cogs_amount: Decimal


@dataclass
class _CostLayer:
    layer_id: uuid.UUID
    movement_id: uuid.UUID
    product_key: int
    warehouse_key: int
    opened_on: date
    source_type: str
    remaining_quantity: Decimal
    unit_cost: Decimal


@dataclass(frozen=True)
class _SaleLine:
    sale_date: date
    document_type: str
    document_alegra_id: str
    document_number: str | None
    line_number: int
    product_key: int | None
    warehouse_key: int | None
    quantity: Decimal
    net_sales_amount: Decimal


@dataclass
class _LineSummary:
    sale: _SaleLine
    costed_quantity: Decimal = Decimal("0")
    cogs_amount: Decimal = Decimal("0")
    uncosted_quantity: Decimal = Decimal("0")
    status: str = "unavailable"
    confidence: str = "unavailable"
    unit_cost: Decimal | None = None
    margin_amount: Decimal | None = None


class HistoricalSalesCostService:
    """Build the sales cost projection from opening stock and purchase receipts.

    The source facts remain untouched except for the cost fields added for
    dashboard consumption. Every allocation segment is retained in
    ``sales_cost_allocations`` for the latest projection run, so a product's
    margin can be explained back to an opening balance, purchase receipt, or
    estimated credit-note return. The run summary remains historical; detailed
    allocation rows are rebuilt because the FIFO ledger is a current projection.
    """

    def __init__(self, *, session: Session) -> None:
        self._session = session

    def allocate(
        self,
        *,
        tenant_id: uuid.UUID,
        cutoff_date: date = date(2026, 1, 1),
    ) -> SalesCostAllocationResult:
        if not self._has_opening_layers(tenant_id=tenant_id, cutoff_date=cutoff_date):
            return SalesCostAllocationResult(
                run_id=None,
                status="skipped_no_opening_basis",
                lines_read=0,
                lines_costed=0,
                lines_partial=0,
                lines_unavailable=0,
                sales_units=Decimal("0"),
                costed_units=Decimal("0"),
                cogs_amount=Decimal("0"),
            )

        run_id = uuid.uuid4()
        self._session.execute(
            text(
                """
                INSERT INTO sales_cost_allocation_runs
                  (id, tenant_id, cutoff_date, method, status)
                VALUES (:id, :tenant_id, :cutoff_date, 'fifo', 'running')
                """
            ),
            {"id": run_id, "tenant_id": tenant_id, "cutoff_date": cutoff_date},
        )
        self._session.commit()

        try:
            self._session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(CAST(:tenant_id AS text)))"),
                {"tenant_id": str(tenant_id)},
            )
            warehouse_key = self._default_warehouse_key(tenant_id=tenant_id)
            self._reset_ledger(tenant_id=tenant_id)
            self._create_purchase_layers(
                tenant_id=tenant_id,
                cutoff_date=cutoff_date,
                warehouse_key=warehouse_key,
            )
            layers = self._load_layers(tenant_id=tenant_id)
            sales = self._load_sales(tenant_id=tenant_id, cutoff_date=cutoff_date)
            allocation_rows, summaries, return_layers = self._allocate_sales(
                sales=sales,
                layers=layers,
                warehouse_key=warehouse_key,
            )
            self._persist_return_layers(tenant_id=tenant_id, return_layers=return_layers)
            self._persist_layer_balances(layers=layers)
            self._insert_allocations(
                run_id=run_id, tenant_id=tenant_id, allocations=allocation_rows
            )
            self._reset_fact_costs(
                tenant_id=tenant_id,
                cutoff_date=cutoff_date,
                summaries=summaries,
            )
            result = self._result_from_summaries(run_id=run_id, summaries=summaries)
            self._session.execute(
                text(
                    """
                    UPDATE sales_cost_allocation_runs
                    SET status=:status, lines_read=:lines_read, lines_costed=:lines_costed,
                        lines_partial=:lines_partial, lines_unavailable=:lines_unavailable,
                        sales_units=:sales_units, costed_units=:costed_units,
                        cogs_amount=:cogs_amount, finished_at=now()
                    WHERE id=:id
                    """
                ),
                {
                    "id": run_id,
                    "status": result.status,
                    "lines_read": result.lines_read,
                    "lines_costed": result.lines_costed,
                    "lines_partial": result.lines_partial,
                    "lines_unavailable": result.lines_unavailable,
                    "sales_units": result.sales_units,
                    "costed_units": result.costed_units,
                    "cogs_amount": result.cogs_amount,
                },
            )
            self._session.commit()
            return result
        except Exception as error:
            self._session.rollback()
            self._session.execute(
                text(
                    """
                    UPDATE sales_cost_allocation_runs
                    SET status='failed', error_message=:error_message, finished_at=now()
                    WHERE id=:id
                    """
                ),
                {"id": run_id, "error_message": str(error)[:4000]},
            )
            self._session.commit()
            raise

    def _has_opening_layers(self, *, tenant_id: uuid.UUID, cutoff_date: date) -> bool:
        return bool(
            self._session.execute(
                text(
                    """
                    SELECT 1 FROM inventory_cost_layers
                    WHERE tenant_id=:tenant_id AND opened_on=:cutoff_date
                    LIMIT 1
                    """
                ),
                {"tenant_id": tenant_id, "cutoff_date": cutoff_date},
            ).scalar_one_or_none()
        )

    def _default_warehouse_key(self, *, tenant_id: uuid.UUID) -> int:
        value = self._session.execute(
            text(
                """
                SELECT key FROM dim_warehouse
                WHERE tenant_id=:tenant_id AND is_deleted=false
                ORDER BY key LIMIT 1
                """
            ),
            {"tenant_id": tenant_id},
        ).scalar_one_or_none()
        if value is None:
            raise ValueError("A cost allocation requires at least one active warehouse")
        return int(value)

    def _reset_ledger(self, *, tenant_id: uuid.UUID) -> None:
        self._session.execute(
            text(
                """
                DELETE FROM inventory_cost_layers
                WHERE tenant_id=:tenant_id
                  AND movement_id IN (
                    SELECT id FROM inventory_cost_movements
                    WHERE tenant_id=:tenant_id
                      AND source_type IN ('purchase_bill', 'sales_credit_note')
                  )
                """
            ),
            {"tenant_id": tenant_id},
        )
        self._session.execute(
            text(
                """
                DELETE FROM inventory_cost_movements
                WHERE tenant_id=:tenant_id
                  AND source_type IN ('purchase_bill', 'sales_credit_note')
                """
            ),
            {"tenant_id": tenant_id},
        )
        self._session.execute(
            text(
                """
                UPDATE inventory_cost_layers
                SET remaining_quantity=original_quantity, layer_status='open'
                WHERE tenant_id=:tenant_id
                """
            ),
            {"tenant_id": tenant_id},
        )
        self._session.execute(
            text("DELETE FROM sales_cost_allocations WHERE tenant_id=:tenant_id"),
            {"tenant_id": tenant_id},
        )

    def _create_purchase_layers(
        self,
        *,
        tenant_id: uuid.UUID,
        cutoff_date: date,
        warehouse_key: int,
    ) -> None:
        rows = self._session.execute(
            text(
                """
                SELECT p.product_key, d.calendar_date AS occurred_on,
                       p.document_alegra_id, p.document_number, p.line_number,
                       p.quantity, p.unit_cost, p.purchase_amount, p.currency_code
                FROM fact_purchase_line p
                JOIN dim_date d ON d.date_key=p.date_key
                WHERE p.tenant_id=:tenant_id AND p.is_deleted=false
                  AND p.product_key IS NOT NULL AND p.quantity>0
                  AND p.unit_cost IS NOT NULL AND d.calendar_date>=:cutoff_date
                ORDER BY d.calendar_date, p.document_alegra_id, p.line_number
                """
            ),
            {"tenant_id": tenant_id, "cutoff_date": cutoff_date},
        ).mappings()
        for row in rows:
            quantity = Decimal(row["quantity"])
            unit_cost = Decimal(row["unit_cost"])
            movement_id = self._session.execute(
                text(
                    """
                    INSERT INTO inventory_cost_movements
                      (id, tenant_id, product_key, warehouse_key, occurred_on,
                       movement_type, source_type, source_id, source_line_number,
                       quantity_in, quantity_out, unit_cost, total_cost, cost_method,
                       confidence, metadata)
                    VALUES
                      (:id, :tenant_id, :product_key, :warehouse_key, :occurred_on,
                       'purchase_receipt', 'purchase_bill', :source_id, :source_line_number,
                       :quantity_in, 0, :unit_cost, :total_cost, 'source', 'source',
                       CAST(:metadata AS jsonb))
                    ON CONFLICT (tenant_id, source_type, source_id, source_line_number)
                    DO UPDATE SET quantity_in=EXCLUDED.quantity_in,
                                  unit_cost=EXCLUDED.unit_cost,
                                  total_cost=EXCLUDED.total_cost,
                                  occurred_on=EXCLUDED.occurred_on,
                                  metadata=EXCLUDED.metadata
                    RETURNING id
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant_id": tenant_id,
                    "product_key": int(row["product_key"]),
                    "warehouse_key": warehouse_key,
                    "occurred_on": row["occurred_on"],
                    "source_id": str(row["document_alegra_id"]),
                    "source_line_number": int(row["line_number"]),
                    "quantity_in": quantity,
                    "unit_cost": unit_cost,
                    "total_cost": quantity * unit_cost,
                    "metadata": json.dumps(
                        {
                            "document_number": row["document_number"],
                            "currency_code": row["currency_code"],
                        }
                    ),
                },
            ).scalar_one()
            self._session.execute(
                text(
                    """
                    INSERT INTO inventory_cost_layers
                      (id, tenant_id, product_key, warehouse_key, movement_id, opened_on,
                       original_quantity, remaining_quantity, unit_cost, layer_status)
                    VALUES
                      (:id, :tenant_id, :product_key, :warehouse_key, :movement_id, :opened_on,
                       :quantity, :quantity, :unit_cost, 'open')
                    ON CONFLICT (movement_id) DO UPDATE SET
                      original_quantity=EXCLUDED.original_quantity,
                      remaining_quantity=EXCLUDED.remaining_quantity,
                      unit_cost=EXCLUDED.unit_cost,
                      opened_on=EXCLUDED.opened_on,
                      layer_status='open'
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant_id": tenant_id,
                    "product_key": int(row["product_key"]),
                    "warehouse_key": warehouse_key,
                    "movement_id": movement_id,
                    "opened_on": row["occurred_on"],
                    "quantity": quantity,
                    "unit_cost": unit_cost,
                },
            )

    def _load_layers(self, *, tenant_id: uuid.UUID) -> dict[int, list[_CostLayer]]:
        rows = self._session.execute(
            text(
                """
                SELECT l.id AS layer_id, m.id AS movement_id, l.product_key,
                       l.warehouse_key, l.opened_on, m.source_type,
                       l.remaining_quantity, l.unit_cost
                FROM inventory_cost_layers l
                JOIN inventory_cost_movements m ON m.id=l.movement_id
                WHERE l.tenant_id=:tenant_id AND l.remaining_quantity>0
                ORDER BY l.product_key, l.opened_on, m.created_at, m.id
                """
            ),
            {"tenant_id": tenant_id},
        ).mappings()
        result: dict[int, list[_CostLayer]] = defaultdict(list)
        for row in rows:
            result[int(row["product_key"])].append(
                _CostLayer(
                    layer_id=row["layer_id"],
                    movement_id=row["movement_id"],
                    product_key=int(row["product_key"]),
                    warehouse_key=int(row["warehouse_key"]),
                    opened_on=row["opened_on"],
                    source_type=str(row["source_type"]),
                    remaining_quantity=Decimal(row["remaining_quantity"]),
                    unit_cost=Decimal(row["unit_cost"]),
                )
            )
        return result

    def _load_sales(self, *, tenant_id: uuid.UUID, cutoff_date: date) -> list[_SaleLine]:
        rows = self._session.execute(
            text(
                """
                SELECT d.calendar_date AS sale_date, f.document_type,
                       f.document_alegra_id, f.document_number, f.line_number,
                       f.product_key, f.warehouse_key, f.quantity, f.net_sales_amount
                FROM fact_sales_line f
                JOIN dim_date d ON d.date_key=f.date_key
                WHERE f.tenant_id=:tenant_id AND f.is_deleted=false
                  AND d.calendar_date>=:cutoff_date AND f.quantity<>0
                ORDER BY d.calendar_date,
                         CASE WHEN f.document_type='invoice' THEN 0 ELSE 1 END,
                         f.document_alegra_id, f.line_number
                """
            ),
            {"tenant_id": tenant_id, "cutoff_date": cutoff_date},
        ).mappings()
        return [
            _SaleLine(
                sale_date=row["sale_date"],
                document_type=str(row["document_type"]),
                document_alegra_id=str(row["document_alegra_id"]),
                document_number=row["document_number"],
                line_number=int(row["line_number"]),
                product_key=int(row["product_key"]) if row["product_key"] is not None else None,
                warehouse_key=(
                    int(row["warehouse_key"])
                    if row["warehouse_key"] is not None
                    else None
                ),
                quantity=Decimal(row["quantity"]),
                net_sales_amount=Decimal(row["net_sales_amount"]),
            )
            for row in rows
        ]

    def _allocate_sales(
        self,
        *,
        sales: list[_SaleLine],
        layers: dict[int, list[_CostLayer]],
        warehouse_key: int,
    ) -> tuple[list[dict[str, Any]], list[_LineSummary], list[dict[str, Any]]]:
        allocations: list[dict[str, Any]] = []
        summaries: list[_LineSummary] = []
        return_layers: list[dict[str, Any]] = []
        cost_history: dict[int, tuple[Decimal, Decimal]] = defaultdict(
            lambda: (Decimal("0"), Decimal("0"))
        )

        for sale in sales:
            summary = _LineSummary(sale=sale)
            product_layers = layers.get(sale.product_key or -1, [])
            if sale.product_key is None:
                summary.uncosted_quantity = sale.quantity
                self._append_unavailable(
                    allocations, sale, sequence=0, note="Sales line has no product dimension"
                )
            elif sale.quantity > 0:
                self._allocate_positive(
                    allocations=allocations,
                    summary=summary,
                    product_layers=product_layers,
                )
                if summary.costed_quantity > 0:
                    old_quantity, old_cost = cost_history[sale.product_key]
                    cost_history[sale.product_key] = (
                        old_quantity + summary.costed_quantity,
                        old_cost + summary.cogs_amount,
                    )
            else:
                self._allocate_credit_note(
                    allocations=allocations,
                    summary=summary,
                    product_layers=product_layers,
                    cost_history=cost_history,
                    warehouse_key=warehouse_key,
                    return_layers=return_layers,
                )
            self._finish_summary(summary)
            summaries.append(summary)
        return allocations, summaries, return_layers

    def _allocate_positive(
        self,
        *,
        allocations: list[dict[str, Any]],
        summary: _LineSummary,
        product_layers: list[_CostLayer],
    ) -> None:
        required = summary.sale.quantity
        sequence = 0
        for layer in product_layers:
            if required <= 0:
                break
            if layer.remaining_quantity <= 0:
                continue
            quantity = min(required, layer.remaining_quantity)
            layer.remaining_quantity -= quantity
            required -= quantity
            summary.costed_quantity += quantity
            summary.cogs_amount += quantity * layer.unit_cost
            allocations.append(
                self._allocation_row(
                    summary.sale,
                    sequence=sequence,
                    quantity=quantity,
                    unit_cost=layer.unit_cost,
                    cost_amount=quantity * layer.unit_cost,
                    allocation_type="fifo",
                    confidence=(
                        "certified"
                        if layer.source_type == "inventory_cost_opening"
                        else "source"
                    ),
                    layer=layer,
                    notes=f"Consumed {layer.source_type} cost layer",
                )
            )
            sequence += 1
        if required > 0:
            summary.uncosted_quantity = required
            allocations.append(
                self._allocation_row(
                    summary.sale,
                    sequence=sequence,
                    quantity=required,
                    unit_cost=None,
                    cost_amount=Decimal("0"),
                    allocation_type="unavailable",
                    confidence="unavailable",
                    layer=None,
                    notes="Insufficient cost layers for the complete sales quantity",
                )
            )

    def _allocate_credit_note(
        self,
        *,
        allocations: list[dict[str, Any]],
        summary: _LineSummary,
        product_layers: list[_CostLayer],
        cost_history: dict[int, tuple[Decimal, Decimal]],
        warehouse_key: int,
        return_layers: list[dict[str, Any]],
    ) -> None:
        return_quantity = abs(summary.sale.quantity)
        product_key = summary.sale.product_key
        assert product_key is not None
        historical_quantity, historical_cost = cost_history[product_key]
        if historical_quantity > 0:
            unit_cost = historical_cost / historical_quantity
        else:
            available_quantity = sum(
                layer.remaining_quantity for layer in product_layers if layer.remaining_quantity > 0
            )
            available_value = sum(
                layer.remaining_quantity * layer.unit_cost
                for layer in product_layers
                if layer.remaining_quantity > 0
            )
            unit_cost = available_value / available_quantity if available_quantity else None
        if unit_cost is None:
            summary.uncosted_quantity = summary.sale.quantity
            self._append_unavailable(
                allocations,
                summary.sale,
                sequence=0,
                note="Credit note has no recoverable historical cost basis",
            )
            return

        movement_id = uuid.uuid4()
        layer_id = uuid.uuid4()
        return_layers.append(
            {
                "movement_id": movement_id,
                "layer_id": layer_id,
                "tenant_id": None,
                "product_key": product_key,
                "warehouse_key": summary.sale.warehouse_key or warehouse_key,
                "occurred_on": summary.sale.sale_date,
                "quantity": return_quantity,
                "unit_cost": unit_cost,
                "source_id": summary.sale.document_alegra_id,
                "source_line_number": summary.sale.line_number,
                "document_number": summary.sale.document_number,
            }
        )
        return_layer = _CostLayer(
            layer_id=layer_id,
            movement_id=movement_id,
            product_key=product_key,
            warehouse_key=summary.sale.warehouse_key or warehouse_key,
            opened_on=summary.sale.sale_date,
            source_type="sales_credit_note",
            remaining_quantity=return_quantity,
            unit_cost=unit_cost,
        )
        product_layers.append(return_layer)
        summary.costed_quantity = summary.sale.quantity
        summary.cogs_amount = -return_quantity * unit_cost
        allocations.append(
            self._allocation_row(
                summary.sale,
                sequence=0,
                quantity=summary.sale.quantity,
                unit_cost=unit_cost,
                cost_amount=summary.cogs_amount,
                allocation_type="credit_note_return",
                confidence="estimated",
                layer=return_layer,
                notes="Credit note return cost estimated from prior FIFO cost basis",
            )
        )

    @staticmethod
    def _append_unavailable(
        allocations: list[dict[str, Any]], sale: _SaleLine, *, sequence: int, note: str
    ) -> None:
        allocations.append(
            HistoricalSalesCostService._allocation_row(
                sale,
                sequence=sequence,
                quantity=sale.quantity,
                unit_cost=None,
                cost_amount=Decimal("0"),
                allocation_type="unavailable",
                confidence="unavailable",
                layer=None,
                notes=note,
            )
        )

    @staticmethod
    def _allocation_row(
        sale: _SaleLine,
        *,
        sequence: int,
        quantity: Decimal,
        unit_cost: Decimal | None,
        cost_amount: Decimal,
        allocation_type: str,
        confidence: str,
        layer: _CostLayer | None,
        notes: str,
    ) -> dict[str, Any]:
        return {
            "id": uuid.uuid4(),
            "sale_date": sale.sale_date,
            "document_type": sale.document_type,
            "document_alegra_id": sale.document_alegra_id,
            "line_number": sale.line_number,
            "allocation_sequence": sequence,
            "product_key": sale.product_key,
            "warehouse_key": (layer.warehouse_key if layer else sale.warehouse_key),
            "source_movement_id": layer.movement_id if layer else None,
            "source_layer_id": layer.layer_id if layer else None,
            "quantity_allocated": quantity,
            "unit_cost": unit_cost,
            "cost_amount": cost_amount,
            "allocation_type": allocation_type,
            "confidence": confidence,
            "notes": notes,
        }

    @staticmethod
    def _finish_summary(summary: _LineSummary) -> None:
        if summary.sale.product_key is None or summary.uncosted_quantity != 0:
            summary.status = "partial" if summary.costed_quantity != 0 else "unavailable"
        elif summary.sale.document_type == "credit_note":
            summary.status = "estimated"
        else:
            summary.status = "costed"
        if summary.status in {"costed", "estimated"} and summary.sale.quantity != 0:
            summary.unit_cost = summary.cogs_amount / summary.sale.quantity
            summary.margin_amount = summary.sale.net_sales_amount - summary.cogs_amount
        if summary.status == "costed":
            summary.confidence = "certified_or_source"
        elif summary.status == "estimated":
            summary.confidence = "estimated"
        elif summary.status == "partial":
            summary.confidence = "partial"

    def _persist_return_layers(
        self, *, tenant_id: uuid.UUID, return_layers: list[dict[str, Any]]
    ) -> None:
        for row in return_layers:
            row["tenant_id"] = tenant_id
            self._session.execute(
                text(
                    """
                    INSERT INTO inventory_cost_movements
                      (id, tenant_id, product_key, warehouse_key, occurred_on,
                       movement_type, source_type, source_id, source_line_number,
                       quantity_in, quantity_out, unit_cost, total_cost, cost_method,
                       confidence, metadata)
                    VALUES
                      (:movement_id, :tenant_id, :product_key, :warehouse_key, :occurred_on,
                       'sales_return', 'sales_credit_note', :source_id, :source_line_number,
                       :quantity, 0, :unit_cost, :total_cost, 'estimated', 'estimated',
                       CAST(:metadata AS jsonb))
                    """
                ),
                {
                    **row,
                    "total_cost": row["quantity"] * row["unit_cost"],
                    "metadata": json.dumps({"document_number": row["document_number"]}),
                },
            )
            self._session.execute(
                text(
                    """
                    INSERT INTO inventory_cost_layers
                      (id, tenant_id, product_key, warehouse_key, movement_id, opened_on,
                       original_quantity, remaining_quantity, unit_cost, layer_status)
                    VALUES (:layer_id, :tenant_id, :product_key, :warehouse_key, :movement_id,
                            :occurred_on, :quantity, :quantity, :unit_cost, 'open')
                    """
                ),
                row,
            )

    def _persist_layer_balances(self, *, layers: dict[int, list[_CostLayer]]) -> None:
        """Persist the in-memory FIFO consumption for the current ledger."""
        updates = [
            {
                "layer_id": layer.layer_id,
                "remaining_quantity": max(layer.remaining_quantity, Decimal("0")),
                "layer_status": (
                    "depleted" if layer.remaining_quantity <= 0 else "open"
                ),
            }
            for product_layers in layers.values()
            for layer in product_layers
        ]
        if updates:
            self._session.execute(
                text(
                    """
                    UPDATE inventory_cost_layers
                    SET remaining_quantity=:remaining_quantity,
                        layer_status=:layer_status
                    WHERE id=:layer_id
                    """
                ),
                updates,
            )

    def _insert_allocations(
        self,
        *,
        run_id: uuid.UUID,
        tenant_id: uuid.UUID,
        allocations: list[dict[str, Any]],
    ) -> None:
        if not allocations:
            return
        self._session.execute(
            text(
                """
                INSERT INTO sales_cost_allocations
                  (id, run_id, tenant_id, sale_date, document_type, document_alegra_id,
                   line_number, allocation_sequence, product_key, warehouse_key,
                   source_movement_id, source_layer_id, quantity_allocated, unit_cost,
                   cost_amount, allocation_type, confidence, notes)
                VALUES
                  (:id, :run_id, :tenant_id, :sale_date, :document_type, :document_alegra_id,
                   :line_number, :allocation_sequence, :product_key, :warehouse_key,
                   :source_movement_id, :source_layer_id, :quantity_allocated, :unit_cost,
                   :cost_amount, :allocation_type, :confidence, :notes)
                """
            ),
            [{**row, "run_id": run_id, "tenant_id": tenant_id} for row in allocations],
        )

    def _reset_fact_costs(
        self,
        *,
        tenant_id: uuid.UUID,
        cutoff_date: date,
        summaries: list[_LineSummary],
    ) -> None:
        self._session.execute(
            text(
                """
                UPDATE fact_sales_line
                SET unit_cost=NULL, margin_amount=NULL, cogs_amount=NULL,
                    cost_status=CASE
                      WHEN date_key < :cutoff_key THEN 'outside_scope'
                      ELSE 'unavailable'
                    END,
                    cost_confidence=NULL, cost_method=NULL
                WHERE tenant_id=:tenant_id
                """
            ),
            {
                "tenant_id": tenant_id,
                "cutoff_key": int(cutoff_date.strftime("%Y%m%d")),
            },
        )
        updates = []
        for summary in summaries:
            updates.append(
                {
                    "tenant_id": tenant_id,
                    "document_type": summary.sale.document_type,
                    "document_alegra_id": summary.sale.document_alegra_id,
                    "line_number": summary.sale.line_number,
                    "unit_cost": summary.unit_cost,
                    "margin_amount": summary.margin_amount,
                    "cogs_amount": summary.cogs_amount,
                    "cost_status": summary.status,
                    "cost_confidence": summary.confidence,
                    "cost_method": "fifo",
                }
            )
        if updates:
            self._session.execute(
                text(
                    """
                    UPDATE fact_sales_line
                    SET unit_cost=:unit_cost, margin_amount=:margin_amount,
                        cogs_amount=:cogs_amount, cost_status=:cost_status,
                        cost_confidence=:cost_confidence, cost_method=:cost_method
                    WHERE tenant_id=:tenant_id AND document_type=:document_type
                      AND document_alegra_id=:document_alegra_id AND line_number=:line_number
                    """
                ),
                updates,
            )

    @staticmethod
    def _result_from_summaries(
        *, run_id: uuid.UUID, summaries: list[_LineSummary]
    ) -> SalesCostAllocationResult:
        costed = sum(summary.status in {"costed", "estimated"} for summary in summaries)
        partial = sum(summary.status == "partial" for summary in summaries)
        unavailable = sum(summary.status == "unavailable" for summary in summaries)
        return SalesCostAllocationResult(
            run_id=run_id,
            status=(
                "succeeded"
                if partial == 0 and unavailable == 0
                else "succeeded_with_exceptions"
            ),
            lines_read=len(summaries),
            lines_costed=costed,
            lines_partial=partial,
            lines_unavailable=unavailable,
            sales_units=sum((summary.sale.quantity for summary in summaries), Decimal("0")),
            costed_units=sum(
                (summary.costed_quantity for summary in summaries), Decimal("0")
            ),
            cogs_amount=sum((summary.cogs_amount for summary in summaries), Decimal("0")),
        )
