# ruff: noqa: E501
"""Explainable demand forecasting and budget-aware procurement planning."""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.business_time import business_today
from app.integrations.alegra.client import AlegraClient

SERVICE_LEVELS = {
    ("A", "X"): Decimal("0.97"),
    ("A", "Y"): Decimal("0.95"),
    ("A", "Z"): Decimal("0.92"),
    ("B", "X"): Decimal("0.95"),
    ("B", "Y"): Decimal("0.92"),
    ("B", "Z"): Decimal("0.88"),
    ("C", "X"): Decimal("0.90"),
    ("C", "Y"): Decimal("0.85"),
    ("C", "Z"): Decimal("0.75"),
}
Z_VALUES = {
    Decimal("0.75"): Decimal("0.674"),
    Decimal("0.85"): Decimal("1.036"),
    Decimal("0.88"): Decimal("1.175"),
    Decimal("0.90"): Decimal("1.282"),
    Decimal("0.92"): Decimal("1.405"),
    Decimal("0.95"): Decimal("1.645"),
    Decimal("0.97"): Decimal("1.881"),
}


@dataclass(frozen=True)
class ForecastResult:
    abc_class: str
    xyz_class: str
    demand_pattern: str
    model_name: str
    daily_forecast: Decimal
    wape: Decimal | None
    bias: Decimal | None
    service_level: Decimal
    confidence: str
    sale_days: int
    gross_units: Decimal


class ProcurementPlanningService:
    def __init__(self, *, session: Session, tenant_id: uuid.UUID) -> None:
        self._session = session
        self._tenant_id = tenant_id

    def preview(
        self,
        *,
        as_of: date,
        weekly_budget: Decimal,
        currency_code: str = "COP",
        review_cycle_days: int = 7,
    ) -> dict[str, Any]:
        return self._build(
            as_of=as_of,
            weekly_budget=weekly_budget,
            currency_code=currency_code,
            review_cycle_days=review_cycle_days,
            persist=False,
        )

    def create_plan(
        self,
        *,
        as_of: date,
        weekly_budget: Decimal,
        currency_code: str = "COP",
        review_cycle_days: int = 7,
    ) -> dict[str, Any]:
        return self._build(
            as_of=as_of,
            weekly_budget=weekly_budget,
            currency_code=currency_code,
            review_cycle_days=review_cycle_days,
            persist=True,
        )

    def _build(
        self,
        *,
        as_of: date,
        weekly_budget: Decimal,
        currency_code: str,
        review_cycle_days: int,
        persist: bool,
    ) -> dict[str, Any]:
        if weekly_budget < 0:
            raise ValueError("weekly_budget must not be negative")
        if not 1 <= review_cycle_days <= 31:
            raise ValueError("review_cycle_days must be between 1 and 31")
        currency_code = currency_code.strip().upper() or "COP"
        quality = self.data_quality(as_of=as_of)
        products = self._product_inputs(as_of=as_of, currency_code=currency_code)
        forecasts = self._forecast_products(products, as_of=as_of)
        supplier_options = self._supplier_options(as_of=as_of, currency_code=currency_code)
        lines = self._recommend(
            products=products,
            forecasts=forecasts,
            supplier_options=supplier_options,
            weekly_budget=weekly_budget,
            review_cycle_days=review_cycle_days,
            blocked=not quality["ready"],
        )
        supplier_orders = self._supplier_orders(lines)
        result = {
            "plan_id": None,
            "as_of_date": as_of,
            "currency_code": currency_code,
            "weekly_budget": weekly_budget,
            "review_cycle_days": review_cycle_days,
            "status": "draft" if quality["ready"] else "blocked_data",
            "data_quality": quality,
            "summary": self._summary(lines, weekly_budget),
            "supplier_orders": supplier_orders,
            "lines": lines,
            "service_levels": {
                f"{abc}{xyz}": value for (abc, xyz), value in SERVICE_LEVELS.items()
            },
        }
        if persist:
            result["plan_id"] = self._persist_plan(result, forecasts)
        return result

    def data_quality(self, *, as_of: date | None = None) -> dict[str, Any]:
        del as_of
        row = (
            self._one(
                """
            WITH materialized AS (
              SELECT r.id, r.finished_at
              FROM inventory_snapshot_runs r
              WHERE r.tenant_id=:tenant_id AND r.status='succeeded'
                AND EXISTS (
                  SELECT 1 FROM fact_inventory_snapshot f
                  WHERE f.tenant_id=r.tenant_id AND f.snapshot_run_id=r.id
                )
              ORDER BY r.finished_at DESC LIMIT 1
            ), states AS (
              SELECT
                max(last_success_at) FILTER (WHERE resource='bill') AS bill_sync_at,
                max(last_success_at) FILTER (WHERE resource='purchase_order') AS po_sync_at
              FROM resource_sync_states WHERE tenant_id=:tenant_id
            )
            SELECT materialized.id AS snapshot_run_id,
                   materialized.finished_at AS snapshot_at,
                   states.bill_sync_at, states.po_sync_at,
                   (SELECT max(finished_at) FROM mart_refresh_runs
                    WHERE tenant_id=:tenant_id AND status='succeeded') AS mart_at
            FROM states LEFT JOIN materialized ON true
            """
            )
            or {}
        )
        now = datetime.now(UTC)
        snapshot_at = row.get("snapshot_at")
        bill_sync_at = row.get("bill_sync_at")
        po_sync_at = row.get("po_sync_at")
        warnings = []
        if snapshot_at is None or now - snapshot_at > timedelta(hours=6):
            warnings.append("Inventario sin una captura materializada en las ultimas 6 horas")
        if bill_sync_at is None or now - bill_sync_at > timedelta(hours=24):
            warnings.append("Facturas de proveedor sin reconciliar en las ultimas 24 horas")
        if po_sync_at is None or now - po_sync_at > timedelta(hours=24):
            warnings.append("Ordenes de compra sin reconciliar en las ultimas 24 horas")
        if row.get("mart_at") is None or (snapshot_at is not None and row["mart_at"] < snapshot_at):
            warnings.append("El mart no contiene la captura de inventario mas reciente")
        return {
            **row,
            "ready": not warnings,
            "warnings": warnings,
            "checked_at": now,
        }

    def _product_inputs(self, *, as_of: date, currency_code: str) -> dict[int, dict[str, Any]]:
        rows = self._rows(
            """
            WITH latest AS (
              SELECT r.id FROM inventory_snapshot_runs r
              WHERE r.tenant_id=:tenant_id AND r.status='succeeded'
                AND EXISTS (SELECT 1 FROM fact_inventory_snapshot f
                            WHERE f.tenant_id=r.tenant_id AND f.snapshot_run_id=r.id)
              ORDER BY r.finished_at DESC LIMIT 1
            ), stock AS (
              SELECT f.product_key, sum(f.quantity_on_hand) AS quantity_on_hand,
                     max(f.unit_cost) FILTER (WHERE f.unit_cost>0) AS snapshot_cost
              FROM fact_inventory_snapshot f JOIN latest ON latest.id=f.snapshot_run_id
              WHERE f.tenant_id=:tenant_id AND f.product_key IS NOT NULL
              GROUP BY f.product_key
            ), sales AS (
              SELECT f.product_key, d.calendar_date, sum(f.quantity) AS units,
                     sum(f.net_sales_amount) AS revenue,
                     sum(COALESCE(f.margin_amount,0)) AS margin
              FROM fact_sales_line f JOIN dim_date d ON d.date_key=f.date_key
              WHERE f.tenant_id=:tenant_id AND f.is_deleted=false
                AND f.document_type='invoice' AND f.product_key IS NOT NULL
                AND d.calendar_date BETWEEN :history_from AND :as_of
                AND f.currency_code=:currency_code
              GROUP BY f.product_key,d.calendar_date
            ), sales_totals AS (
              SELECT product_key,sum(units) gross_units,sum(revenue) revenue,
                     sum(margin) margin,count(*) sale_days
              FROM sales GROUP BY product_key
            ), returns AS (
              SELECT f.product_key, abs(sum(f.quantity)) AS returned_units
              FROM fact_sales_line f JOIN dim_date d ON d.date_key=f.date_key
              WHERE f.tenant_id=:tenant_id AND f.is_deleted=false
                AND f.document_type='credit_note' AND f.product_key IS NOT NULL
                AND d.calendar_date BETWEEN :history_from AND :as_of
                AND f.currency_code=:currency_code
              GROUP BY f.product_key
            ), received AS (
              SELECT p.product_key,max(d.calendar_date) last_purchase_date,
                     (array_agg(p.quantity ORDER BY d.calendar_date DESC,
                                p.document_alegra_id DESC))[1] last_purchase_quantity,
                     (array_agg(p.unit_cost ORDER BY d.calendar_date DESC,
                                p.document_alegra_id DESC)
                      FILTER (WHERE p.unit_cost>0))[1] last_unit_cost
              FROM fact_purchase_line p JOIN dim_date d ON d.date_key=p.date_key
              WHERE p.tenant_id=:tenant_id AND p.is_deleted=false
                AND p.product_key IS NOT NULL AND d.calendar_date>=DATE '2025-01-01'
              GROUP BY p.product_key
            ), billed_po AS (
              SELECT pb.purchase_order_alegra_id,pbl.item_alegra_id,sum(pbl.quantity) billed
              FROM purchase_bills pb JOIN purchase_bill_lines pbl
                ON pbl.tenant_id=pb.tenant_id AND pbl.document_alegra_id=pb.alegra_id
              WHERE pb.tenant_id=:tenant_id AND pb.is_deleted=false
                AND pb.purchase_order_alegra_id IS NOT NULL
              GROUP BY pb.purchase_order_alegra_id,pbl.item_alegra_id
            ), transit AS (
              SELECT dp.key product_key,
                     sum(GREATEST(COALESCE(pol.quantity,0)-COALESCE(bp.billed,0),0)) in_transit
              FROM purchase_orders po JOIN purchase_order_lines pol
                ON pol.tenant_id=po.tenant_id AND pol.document_alegra_id=po.alegra_id
              JOIN dim_product dp ON dp.tenant_id=po.tenant_id
                AND dp.alegra_id=pol.item_alegra_id
              LEFT JOIN billed_po bp ON bp.purchase_order_alegra_id=po.alegra_id
                AND bp.item_alegra_id=pol.item_alegra_id
              WHERE po.tenant_id=:tenant_id AND po.is_deleted=false AND po.status='open'
              GROUP BY dp.key
            )
            SELECT p.key product_key,p.alegra_id,p.name,p.reference,p.family_name,
                   COALESCE(stock.quantity_on_hand,0) quantity_on_hand,
                   COALESCE(transit.in_transit,0) quantity_in_transit,
                   COALESCE(sales_totals.gross_units,0) gross_units,
                   COALESCE(sales_totals.revenue,0) revenue,
                   COALESCE(sales_totals.margin,0) margin,
                   COALESCE(sales_totals.sale_days,0) sale_days,
                   COALESCE(returns.returned_units,0) returned_units,
                   received.last_purchase_date,received.last_purchase_quantity,
                   COALESCE(received.last_unit_cost,stock.snapshot_cost,p.current_cost,0) unit_cost
            FROM dim_product p
            LEFT JOIN stock ON stock.product_key=p.key
            LEFT JOIN sales_totals ON sales_totals.product_key=p.key
            LEFT JOIN returns ON returns.product_key=p.key
            LEFT JOIN received ON received.product_key=p.key
            LEFT JOIN transit ON transit.product_key=p.key
            WHERE p.tenant_id=:tenant_id AND p.is_deleted=false
              AND (stock.product_key IS NOT NULL OR p.inventory_enabled=true)
            """,
            {
                "as_of": as_of,
                "history_from": as_of - timedelta(days=364),
                "currency_code": currency_code,
            },
        )
        products = {int(row["product_key"]): row for row in rows}
        daily_rows = self._rows(
            """
            SELECT f.product_key,d.calendar_date,sum(f.quantity) units
            FROM fact_sales_line f JOIN dim_date d ON d.date_key=f.date_key
            WHERE f.tenant_id=:tenant_id AND f.is_deleted=false
              AND f.document_type='invoice' AND f.product_key IS NOT NULL
              AND f.currency_code=:currency_code
              AND d.calendar_date BETWEEN :history_from AND :as_of
            GROUP BY f.product_key,d.calendar_date
            """,
            {
                "as_of": as_of,
                "history_from": as_of - timedelta(days=364),
                "currency_code": currency_code,
            },
        )
        for row in daily_rows:
            product = products.get(int(row["product_key"]))
            if product is not None:
                product.setdefault("daily", {})[row["calendar_date"]] = Decimal(
                    str(row["units"] or 0)
                )
        return products

    def _forecast_products(
        self, products: dict[int, dict[str, Any]], *, as_of: date
    ) -> dict[int, ForecastResult]:
        ranked = sorted(
            products.values(), key=lambda row: Decimal(str(row.get("revenue") or 0)), reverse=True
        )
        total_revenue = sum((Decimal(str(row.get("revenue") or 0)) for row in ranked), Decimal(0))
        cumulative = Decimal(0)
        abc: dict[int, str] = {}
        for row in ranked:
            prior_share = cumulative / total_revenue if total_revenue > 0 else Decimal(1)
            abc[int(row["product_key"])] = (
                "A"
                if prior_share < Decimal("0.80")
                else "B"
                if prior_share < Decimal("0.95")
                else "C"
            )
            cumulative += Decimal(str(row.get("revenue") or 0))

        dates = [as_of - timedelta(days=offset) for offset in range(364, -1, -1)]
        results = {}
        for key, product in products.items():
            series = [Decimal(str(product.get("daily", {}).get(day, 0))) for day in dates]
            nonzero = [value for value in series if value > 0]
            if not nonzero:
                results[key] = ForecastResult(
                    abc_class=abc.get(key, "C"),
                    xyz_class="Z",
                    demand_pattern="sin_demanda",
                    model_name="sin_demanda",
                    daily_forecast=Decimal(0),
                    wape=None,
                    bias=None,
                    service_level=SERVICE_LEVELS[(abc.get(key, "C"), "Z")],
                    confidence="baja",
                    sale_days=0,
                    gross_units=Decimal(0),
                )
                continue
            adi = Decimal(len(series)) / Decimal(len(nonzero))
            mean_nonzero = sum(nonzero, Decimal(0)) / Decimal(len(nonzero))
            variance = sum(
                ((value - mean_nonzero) ** 2 for value in nonzero), Decimal(0)
            ) / Decimal(len(nonzero))
            cv2 = variance / (mean_nonzero**2) if mean_nonzero else Decimal(999)
            if adi <= Decimal("1.32") and cv2 <= Decimal("0.49"):
                xyz, pattern = "X", "regular"
            elif adi > Decimal("1.32") and cv2 > Decimal("0.49"):
                xyz, pattern = "Z", "intermitente_irregular"
            else:
                xyz, pattern = "Y", "variable"
            candidates = (
                ("mean30", "ewma", "weekday") if xyz == "X" else ("croston_sba", "tsb", "mean90")
            )
            scored = [self._backtest(name, series, dates) for name in candidates]
            selected = min(scored, key=lambda result: (result[1], abs(result[2])))
            forecast = self._forecast_value(selected[0], series, dates)
            actual_total = sum(series[-56:], Decimal(0))
            confidence = (
                "alta"
                if len(nonzero) >= 20 and selected[1] <= Decimal("0.35")
                else "media"
                if len(nonzero) >= 4
                else "baja"
            )
            results[key] = ForecastResult(
                abc_class=abc.get(key, "C"),
                xyz_class=xyz,
                demand_pattern=pattern,
                model_name=selected[0],
                daily_forecast=max(forecast, Decimal(0)),
                wape=selected[1] if actual_total > 0 else None,
                bias=selected[2],
                service_level=SERVICE_LEVELS[(abc.get(key, "C"), xyz)],
                confidence=confidence,
                sale_days=len(nonzero),
                gross_units=sum(series, Decimal(0)),
            )
        return results

    def _backtest(
        self, name: str, series: list[Decimal], dates: list[date]
    ) -> tuple[str, Decimal, Decimal]:
        errors = Decimal(0)
        signed = Decimal(0)
        actual = Decimal(0)
        for cutoff in (309, 323, 337, 351):
            train = series[:cutoff]
            train_dates = dates[:cutoff]
            for position in range(cutoff, min(cutoff + 14, len(series))):
                prediction = self._forecast_value(name, train, train_dates, dates[position])
                observation = series[position]
                errors += abs(observation - prediction)
                signed += prediction - observation
                actual += observation
        denominator = actual if actual > 0 else Decimal(len(series))
        return name, errors / denominator, signed / max(Decimal(56), actual)

    def _forecast_value(
        self,
        name: str,
        series: list[Decimal],
        dates: list[date],
        target_date: date | None = None,
    ) -> Decimal:
        if not series:
            return Decimal(0)
        if name == "mean30":
            window = series[-30:]
            return sum(window, Decimal(0)) / Decimal(len(window))
        if name == "mean90":
            window = series[-90:]
            return sum(window, Decimal(0)) / Decimal(len(window))
        if name == "ewma":
            level = series[0]
            alpha = Decimal("0.30")
            for value in series[1:]:
                level = alpha * value + (Decimal(1) - alpha) * level
            return level
        if name == "weekday":
            weekday = (target_date or (dates[-1] + timedelta(days=1))).weekday()
            values = [
                value
                for day, value in zip(dates[-84:], series[-84:], strict=True)
                if day.weekday() == weekday
            ]
            return sum(values, Decimal(0)) / Decimal(len(values)) if values else Decimal(0)
        if name == "croston_sba":
            return _croston(series, sba=True)
        if name == "tsb":
            return _tsb(series)
        raise ValueError(f"Unknown forecast model {name}")

    def _supplier_options(
        self, *, as_of: date, currency_code: str
    ) -> dict[int, list[dict[str, Any]]]:
        rows = self._rows(
            """
            WITH po_performance AS (
              SELECT dp.key product_key,dc.key supplier_key,
                     count(DISTINCT po.alegra_id) completed_orders,
                     avg(CASE WHEN pb.issue_date<=po.delivery_date THEN 1.0 ELSE 0.0 END)
                       FILTER (WHERE pb.issue_date IS NOT NULL AND po.delivery_date IS NOT NULL) on_time_rate,
                     sum(COALESCE(pbl.quantity,0))/NULLIF(sum(COALESCE(pol.quantity,0)),0) fill_rate,
                     avg(pb.issue_date-po.order_date)
                       FILTER (WHERE pb.issue_date IS NOT NULL) observed_lead_days
              FROM purchase_orders po
              JOIN purchase_order_lines pol ON pol.tenant_id=po.tenant_id
                AND pol.document_alegra_id=po.alegra_id
              JOIN dim_product dp ON dp.tenant_id=po.tenant_id AND dp.alegra_id=pol.item_alegra_id
              JOIN dim_contact dc ON dc.tenant_id=po.tenant_id AND dc.alegra_id=po.provider_alegra_id
              LEFT JOIN purchase_bills pb ON pb.tenant_id=po.tenant_id
                AND pb.purchase_order_alegra_id=po.alegra_id AND pb.is_deleted=false
              LEFT JOIN purchase_bill_lines pbl ON pbl.tenant_id=pb.tenant_id
                AND pbl.document_alegra_id=pb.alegra_id AND pbl.item_alegra_id=pol.item_alegra_id
              WHERE po.tenant_id=:tenant_id AND po.is_deleted=false
              GROUP BY dp.key,dc.key
            ), terms AS (
              SELECT dc.key supplier_key,avg(pb.due_date-pb.issue_date) payment_days
              FROM purchase_bills pb JOIN dim_contact dc
                ON dc.tenant_id=pb.tenant_id AND dc.alegra_id=pb.provider_alegra_id
              WHERE pb.tenant_id=:tenant_id AND pb.is_deleted=false
                AND pb.issue_date IS NOT NULL AND pb.due_date IS NOT NULL
              GROUP BY dc.key
            )
            SELECT s.product_key,s.supplier_key,c.name supplier,s.average_unit_cost,
                   s.last_unit_cost,s.last_purchase_date,s.purchase_line_count,
                   s.line_share_pct,s.frequency_rank,
                   COALESCE(perf.completed_orders,0) completed_orders,
                   perf.on_time_rate,perf.fill_rate,perf.observed_lead_days,
                   terms.payment_days,
                   spp.minimum_order_quantity,spp.pack_size,spp.lead_time_days,
                   COALESCE(spp.is_preferred,false) is_preferred,
                   srp.minimum_order_amount,srp.shipping_cost,srp.free_shipping_threshold,
                   srp.default_lead_time_days,srp.max_wait_days
            FROM supplier_product_stats s JOIN dim_contact c ON c.key=s.supplier_key
            LEFT JOIN po_performance perf ON perf.product_key=s.product_key
              AND perf.supplier_key=s.supplier_key
            LEFT JOIN terms ON terms.supplier_key=s.supplier_key
            LEFT JOIN supplier_product_policies spp ON spp.tenant_id=s.tenant_id
              AND spp.product_key=s.product_key AND spp.supplier_key=s.supplier_key
              AND spp.currency_code=s.currency_code AND spp.active=true
            LEFT JOIN supplier_replenishment_policies srp ON srp.tenant_id=s.tenant_id
              AND srp.supplier_key=s.supplier_key AND srp.currency_code=s.currency_code
              AND srp.active=true
            WHERE s.tenant_id=:tenant_id AND s.currency_code=:currency_code
            """,
            {"currency_code": currency_code},
        )
        grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[int(row["product_key"])].append(row)
        for options in grouped.values():
            valid_costs = [
                Decimal(str(row["last_unit_cost"] or row["average_unit_cost"] or 0))
                for row in options
                if Decimal(str(row["last_unit_cost"] or row["average_unit_cost"] or 0)) > 0
            ]
            minimum_cost = min(valid_costs) if valid_costs else Decimal(0)
            for row in options:
                cost = Decimal(str(row["last_unit_cost"] or row["average_unit_cost"] or 0))
                cost_score = (
                    minimum_cost / cost if cost > 0 and minimum_cost > 0 else Decimal("0.5")
                )
                fill = Decimal(str(row["fill_rate"] if row["fill_rate"] is not None else "0.5"))
                on_time = Decimal(
                    str(row["on_time_rate"] if row["on_time_rate"] is not None else "0.5")
                )
                lead = Decimal(
                    str(
                        row["observed_lead_days"]
                        or row["lead_time_days"]
                        or row["default_lead_time_days"]
                        or 7
                    )
                )
                lead_score = Decimal(1) / (Decimal(1) + lead / Decimal(30))
                terms_score = min(Decimal(str(row["payment_days"] or 0)) / Decimal(45), Decimal(1))
                days_old = (
                    (as_of - row["last_purchase_date"]).days if row["last_purchase_date"] else 365
                )
                recency = max(Decimal(0), Decimal(1) - Decimal(days_old) / Decimal(365))
                score = (
                    Decimal(30) * cost_score
                    + Decimal(25) * min(fill, Decimal(1))
                    + Decimal(20) * min(on_time, Decimal(1))
                    + Decimal(15) * lead_score
                    + Decimal(5) * terms_score
                    + Decimal(5) * recency
                )
                if row["is_preferred"]:
                    score += Decimal(20)
                row["supplier_score"] = min(score, Decimal(100))
                row["supplier_confidence"] = (
                    "alta"
                    if row["completed_orders"] >= 3
                    else "media"
                    if row["purchase_line_count"] >= 3
                    else "baja"
                )
            options.sort(
                key=lambda row: (
                    not row["is_preferred"],
                    -row["supplier_score"],
                    -row["purchase_line_count"],
                )
            )
        return grouped

    def _recommend(
        self,
        *,
        products: dict[int, dict[str, Any]],
        forecasts: dict[int, ForecastResult],
        supplier_options: dict[int, list[dict[str, Any]]],
        weekly_budget: Decimal,
        review_cycle_days: int,
        blocked: bool,
    ) -> list[dict[str, Any]]:
        candidates = []
        opportunities = []
        for key, product in products.items():
            forecast = forecasts[key]
            stock = Decimal(str(product["quantity_on_hand"] or 0))
            transit = Decimal(str(product["quantity_in_transit"] or 0))
            options = supplier_options.get(key, [])
            supplier = options[0] if options else {}
            lead_days = int(
                supplier.get("observed_lead_days")
                or supplier.get("lead_time_days")
                or supplier.get("default_lead_time_days")
                or 7
            )
            horizon = lead_days + review_cycle_days
            expected = forecast.daily_forecast * Decimal(horizon)
            safety = Z_VALUES[forecast.service_level] * Decimal(
                str(math.sqrt(float(max(expected, 0))))
            )
            target = expected + safety
            stock_position = max(stock, Decimal(0)) + transit
            base = max(target - stock_position, Decimal(0))
            minimum = Decimal(str(supplier.get("minimum_order_quantity") or 0))
            pack = Decimal(str(supplier.get("pack_size") or 1))
            quantity = max(base, minimum) if base > 0 else Decimal(0)
            if quantity > 0:
                quantity = (quantity / pack).to_integral_value(rounding=ROUND_CEILING) * pack
            unit_cost = Decimal(
                str(supplier.get("last_unit_cost") or product.get("unit_cost") or 0)
            )
            value = quantity * unit_cost
            coverage = stock / forecast.daily_forecast if forecast.daily_forecast > 0 else None
            unit_margin = Decimal(str(product.get("margin") or 0)) / max(
                Decimal(str(product.get("gross_units") or 0)), Decimal(1)
            )
            if unit_margin <= 0:
                unit_margin = (
                    Decimal(str(product.get("revenue") or 0))
                    / max(Decimal(str(product.get("gross_units") or 0)), Decimal(1))
                    * Decimal("0.15")
                )
            urgency = (
                Decimal(1000)
                if stock <= 0 and forecast.daily_forecast > 0
                else Decimal(100) / max(coverage or Decimal("0.1"), Decimal("0.1"))
            )
            priority_score = urgency + forecast.daily_forecast * unit_margin / max(
                unit_cost, Decimal(1)
            )
            if stock < 0:
                decision = "reconcile"
            elif forecast.gross_units <= 0 and stock > 0:
                decision = "no_reorder"
            elif quantity <= 0:
                decision = "covered"
            elif unit_cost <= 0:
                decision = "review_cost"
            else:
                decision = "candidate"
            line = {
                "product_key": key,
                "product_alegra_id": product["alegra_id"],
                "name": product["name"],
                "reference": product.get("reference"),
                "family": product.get("family_name") or "SIN FAMILIA",
                "abc_class": forecast.abc_class,
                "xyz_class": forecast.xyz_class,
                "demand_pattern": forecast.demand_pattern,
                "forecast_model": forecast.model_name,
                "forecast_wape": forecast.wape,
                "forecast_bias": forecast.bias,
                "service_level": forecast.service_level,
                "quantity_on_hand": stock,
                "quantity_in_transit": transit,
                "stock_position": stock_position,
                "returned_units_365d": product.get("returned_units") or 0,
                "last_purchase_date": product.get("last_purchase_date"),
                "last_purchase_quantity": product.get("last_purchase_quantity"),
                "daily_forecast": forecast.daily_forecast,
                "forecast_horizon_days": horizon,
                "target_stock": target,
                "coverage_days": coverage,
                "base_quantity": base,
                "recommended_quantity": quantity,
                "unit_cost": unit_cost,
                "estimated_value": value,
                "supplier_key": supplier.get("supplier_key"),
                "supplier": supplier.get("supplier"),
                "supplier_score": supplier.get("supplier_score"),
                "supplier_confidence": supplier.get("supplier_confidence", "baja"),
                "supplier_options": options[:3],
                "minimum_order_quantity": minimum,
                "pack_size": pack,
                "minimum_order_amount": supplier.get("minimum_order_amount"),
                "free_shipping_threshold": supplier.get("free_shipping_threshold"),
                "priority_score": priority_score,
                "priority": "critical"
                if stock <= 0 and forecast.daily_forecast > 0
                else "high"
                if coverage is not None and coverage < Decimal(horizon)
                else "normal",
                "decision": decision,
                "confidence": min(
                    forecast.confidence,
                    supplier.get("supplier_confidence", "baja"),
                    key={"baja": 0, "media": 1, "alta": 2}.get,
                ),
                "explanation": {
                    "gross_demand_not_net_of_returns": True,
                    "expected_horizon_demand": str(expected),
                    "safety_stock": str(safety),
                    "lead_time_days": lead_days,
                    "review_cycle_days": review_cycle_days,
                    "inventory_exception": "negative_stock" if stock < 0 else None,
                },
            }
            (candidates if decision == "candidate" else opportunities).append(line)
        candidates.sort(key=lambda row: (-row["priority_score"], row["estimated_value"]))
        spent = Decimal(0)
        for line in candidates:
            if blocked:
                line["decision"] = "blocked_data"
            elif line["supplier_key"] is None:
                line["decision"] = "review_supplier"
            elif spent + line["estimated_value"] <= weekly_budget:
                line["decision"] = "buy_now" if line["priority"] == "critical" else "buy_weekly"
                spent += line["estimated_value"]
            else:
                line["decision"] = "deferred_budget"
        combined = candidates + opportunities
        combined.sort(
            key=lambda row: (
                {
                    "buy_now": 0,
                    "buy_weekly": 1,
                    "reconcile": 2,
                    "review_supplier": 3,
                    "review_cost": 4,
                    "deferred_budget": 5,
                    "blocked_data": 6,
                    "covered": 7,
                    "no_reorder": 8,
                }.get(row["decision"], 9),
                -row["priority_score"],
            )
        )
        return combined[:1000]

    def _supplier_orders(self, lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
        groups: dict[int, dict[str, Any]] = {}
        for line in lines:
            if line["decision"] not in {"buy_now", "buy_weekly"} or line["supplier_key"] is None:
                continue
            supplier_key = int(line["supplier_key"])
            group = groups.setdefault(
                supplier_key,
                {
                    "supplier_key": supplier_key,
                    "supplier": line["supplier"],
                    "lines": 0,
                    "units": Decimal(0),
                    "estimated_value": Decimal(0),
                    "critical_lines": 0,
                    "minimum_order_amount": line.get("minimum_order_amount"),
                    "free_shipping_threshold": line.get("free_shipping_threshold"),
                    "products": [],
                },
            )
            group["lines"] += 1
            group["units"] += line["recommended_quantity"]
            group["estimated_value"] += line["estimated_value"]
            group["critical_lines"] += int(line["priority"] == "critical")
            group["products"].append(line)
        for group in groups.values():
            minimum = Decimal(str(group.get("minimum_order_amount") or 0))
            value = group["estimated_value"]
            if group["critical_lines"]:
                group["decision"] = "buy_now"
            elif minimum and value < minimum:
                group["decision"] = "accumulate_minimum"
            else:
                group["decision"] = "ready_for_approval"
            group["amount_to_minimum"] = max(minimum - value, Decimal(0))
        return sorted(
            groups.values(), key=lambda row: (-row["critical_lines"], -row["estimated_value"])
        )

    def _summary(self, lines: list[dict[str, Any]], weekly_budget: Decimal) -> dict[str, Any]:
        selected = [line for line in lines if line["decision"] in {"buy_now", "buy_weekly"}]
        return {
            "recommended_products": len(selected),
            "critical_products": sum(line["priority"] == "critical" for line in selected),
            "reconcile_products": sum(line["decision"] == "reconcile" for line in lines),
            "deferred_by_budget": sum(line["decision"] == "deferred_budget" for line in lines),
            "dead_or_no_demand": sum(line["decision"] == "no_reorder" for line in lines),
            "recommended_value": sum((line["estimated_value"] for line in selected), Decimal(0)),
            "weekly_budget": weekly_budget,
            "remaining_budget": max(
                weekly_budget - sum((line["estimated_value"] for line in selected), Decimal(0)),
                Decimal(0),
            ),
        }

    def _persist_plan(self, result: dict[str, Any], forecasts: dict[int, ForecastResult]) -> str:
        plan_id = uuid.uuid4()
        quality = result["data_quality"]
        self._session.execute(
            text("""INSERT INTO purchase_plan_runs
              (id,tenant_id,as_of_date,currency_code,weekly_budget,review_cycle_days,status,
               snapshot_run_id,data_quality,recommended_value)
              VALUES (:id,:tenant_id,:as_of,:currency,:budget,:cycle,:status,:snapshot,
                      CAST(:quality AS jsonb),
                      :recommended)"""),
            {
                "id": plan_id,
                "tenant_id": self._tenant_id,
                "as_of": result["as_of_date"],
                "currency": result["currency_code"],
                "budget": result["weekly_budget"],
                "cycle": result["review_cycle_days"],
                "status": result["status"],
                "snapshot": quality.get("snapshot_run_id"),
                "quality": json.dumps(_jsonable(quality)),
                "recommended": result["summary"]["recommended_value"],
            },
        )
        forecast_values = []
        line_values = []
        for product_key, forecast in forecasts.items():
            forecast_values.append(
                {
                    "tenant_id": self._tenant_id,
                    "product_key": product_key,
                    "as_of": result["as_of_date"],
                    "abc": forecast.abc_class,
                    "xyz": forecast.xyz_class,
                    "pattern": forecast.demand_pattern,
                    "model": forecast.model_name,
                    "daily": forecast.daily_forecast,
                    "wape": forecast.wape,
                    "bias": forecast.bias,
                    "service": forecast.service_level,
                    "confidence": forecast.confidence,
                    "details": json.dumps(
                        {"sale_days": forecast.sale_days, "gross_units": str(forecast.gross_units)}
                    ),
                }
            )
        for line in result["lines"]:
            line_values.append(
                {
                    "id": uuid.uuid4(),
                    "plan_id": plan_id,
                    "tenant_id": self._tenant_id,
                    "product_key": line["product_key"],
                    "supplier_key": line["supplier_key"],
                    "priority": line["priority"],
                    "decision": line["decision"],
                    "stock": line["quantity_on_hand"],
                    "transit": line["quantity_in_transit"],
                    "daily": line["daily_forecast"],
                    "horizon": line["forecast_horizon_days"],
                    "base": line["base_quantity"],
                    "quantity": line["recommended_quantity"],
                    "cost": line["unit_cost"],
                    "value": line["estimated_value"],
                    "score": line["supplier_score"],
                    "confidence": line["confidence"],
                    "explanation": json.dumps(_jsonable(line["explanation"])),
                }
            )
        if forecast_values:
            self._session.execute(
                text("""INSERT INTO replenishment_forecasts
              (tenant_id,product_key,as_of_date,abc_class,xyz_class,demand_pattern,model_name,
               daily_forecast,wape,bias,service_level,confidence,details)
              VALUES (:tenant_id,:product_key,:as_of,:abc,:xyz,:pattern,:model,:daily,:wape,
                      :bias,:service,:confidence,CAST(:details AS jsonb))
              ON CONFLICT (tenant_id,product_key,as_of_date) DO UPDATE SET
                abc_class=EXCLUDED.abc_class,xyz_class=EXCLUDED.xyz_class,
                demand_pattern=EXCLUDED.demand_pattern,model_name=EXCLUDED.model_name,
                daily_forecast=EXCLUDED.daily_forecast,wape=EXCLUDED.wape,bias=EXCLUDED.bias,
                service_level=EXCLUDED.service_level,confidence=EXCLUDED.confidence,
                details=EXCLUDED.details,generated_at=now()"""),
                forecast_values,
            )
        if line_values:
            self._session.execute(
                text("""INSERT INTO purchase_plan_lines
              (id,plan_id,tenant_id,product_key,supplier_key,priority,decision,quantity_on_hand,
               quantity_in_transit,daily_forecast,forecast_horizon_days,base_quantity,
               recommended_quantity,unit_cost,estimated_value,supplier_score,confidence,explanation)
              VALUES (:id,:plan_id,:tenant_id,:product_key,:supplier_key,:priority,:decision,:stock,
                      :transit,:daily,:horizon,:base,:quantity,:cost,:value,:score,:confidence,
                      CAST(:explanation AS jsonb))"""),
                line_values,
            )
        self._session.commit()
        return str(plan_id)

    def get_plan(self, plan_id: uuid.UUID) -> dict[str, Any]:
        run = self._one(
            "SELECT * FROM purchase_plan_runs WHERE id=:plan_id AND tenant_id=:tenant_id",
            {"plan_id": plan_id},
        )
        if run is None:
            raise LookupError("Purchase plan not found")
        lines = self._rows(
            """SELECT l.*,p.alegra_id,p.name,p.reference,p.family_name,c.name supplier
            FROM purchase_plan_lines l JOIN dim_product p ON p.key=l.product_key
            LEFT JOIN dim_contact c ON c.key=l.supplier_key
            WHERE l.plan_id=:plan_id AND l.tenant_id=:tenant_id
            ORDER BY CASE l.decision WHEN 'buy_now' THEN 1 WHEN 'buy_weekly' THEN 2 ELSE 3 END,
                     l.estimated_value DESC""",
            {"plan_id": plan_id},
        )
        orders = self._rows(
            "SELECT * FROM purchase_plan_orders WHERE plan_id=:plan_id AND tenant_id=:tenant_id ORDER BY estimated_value DESC",
            {"plan_id": plan_id},
        )
        return {**run, "lines": lines, "orders": orders}

    def update_line(
        self,
        *,
        plan_id: uuid.UUID,
        line_id: uuid.UUID,
        decision: str,
        approved_quantity: Decimal,
        note: str | None,
    ) -> dict[str, Any]:
        if decision not in {"approved", "discarded", "snoozed"}:
            raise ValueError("Invalid line decision")
        row = self._one(
            """UPDATE purchase_plan_lines SET decision=:decision,
            approved_quantity=:quantity,note=:note
            WHERE id=:line_id AND plan_id=:plan_id AND tenant_id=:tenant_id RETURNING *""",
            {
                "line_id": line_id,
                "plan_id": plan_id,
                "decision": decision,
                "quantity": approved_quantity,
                "note": note,
            },
        )
        if row is None:
            raise LookupError("Purchase plan line not found")
        self._session.commit()
        return row

    def approve_plan(self, plan_id: uuid.UUID) -> dict[str, Any]:
        run = self._one(
            "SELECT * FROM purchase_plan_runs WHERE id=:plan_id AND tenant_id=:tenant_id FOR UPDATE",
            {"plan_id": plan_id},
        )
        if run is None:
            raise LookupError("Purchase plan not found")
        if run["status"] == "blocked_data":
            raise ValueError("The plan is blocked because its source data is stale")
        if run["as_of_date"] != business_today():
            raise ValueError("Only a plan for the current business date can be approved")
        self._session.execute(
            text("""UPDATE purchase_plan_lines
          SET decision='approved',approved_quantity=recommended_quantity
          WHERE plan_id=:plan_id AND tenant_id=:tenant_id
            AND decision IN ('buy_now','buy_weekly') AND approved_quantity=0"""),
            {"plan_id": plan_id, "tenant_id": self._tenant_id},
        )
        total = self._session.execute(
            text(
                "SELECT COALESCE(sum(approved_quantity*unit_cost),0) FROM purchase_plan_lines WHERE plan_id=:plan_id AND tenant_id=:tenant_id AND decision='approved'"
            ),
            {"plan_id": plan_id, "tenant_id": self._tenant_id},
        ).scalar_one()
        if Decimal(str(total)) > Decimal(str(run["weekly_budget"])):
            self._session.rollback()
            raise ValueError("Approved value exceeds the weekly budget")
        self._session.execute(
            text(
                "UPDATE purchase_plan_runs SET status='approved',approved_value=:total,approved_at=now() WHERE id=:plan_id AND tenant_id=:tenant_id"
            ),
            {"plan_id": plan_id, "tenant_id": self._tenant_id, "total": total},
        )
        self._session.commit()
        return self.get_plan(plan_id)

    async def submit_to_alegra(self, *, plan_id: uuid.UUID, alegra: AlegraClient) -> dict[str, Any]:
        plan = self.get_plan(plan_id)
        if plan["status"] not in {"approved", "submitted"}:
            raise ValueError("The plan must be approved before submission")
        groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for line in plan["lines"]:
            if (
                line["decision"] == "approved"
                and line["supplier_key"] is not None
                and Decimal(str(line["approved_quantity"])) > 0
            ):
                groups[int(line["supplier_key"])].append(line)
        warehouse = self._one(
            "SELECT alegra_id FROM dim_warehouse WHERE tenant_id=:tenant_id AND is_deleted=false ORDER BY key LIMIT 1"
        )
        results = []
        for supplier_key, lines in groups.items():
            supplier = self._one(
                "SELECT alegra_id,name FROM dim_contact WHERE tenant_id=:tenant_id AND key=:supplier_key",
                {"supplier_key": supplier_key},
            )
            if supplier is None:
                continue
            today = business_today()
            request = {
                "date": today.isoformat(),
                "deliveryDate": (today + timedelta(days=7)).isoformat(),
                "provider": supplier["alegra_id"],
                "warehouse": warehouse["alegra_id"] if warehouse else None,
                "observations": f"Plan de abastecimiento {plan_id}",
                "purchases": {
                    "items": [
                        {
                            "item": line["alegra_id"],
                            "quantity": str(line["approved_quantity"]),
                            "price": str(line["unit_cost"]),
                        }
                        for line in lines
                    ]
                },
            }
            if request["warehouse"] is None:
                request.pop("warehouse")
            request_hash = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
            existing = self._one(
                "SELECT * FROM purchase_plan_orders WHERE tenant_id=:tenant_id AND request_hash=:request_hash",
                {"request_hash": request_hash},
            )
            if existing and existing.get("alegra_order_id"):
                results.append(existing)
                continue
            response = await alegra.create_purchase_order(request)
            order_payload = (
                response.get("purchaseOrder")
                if isinstance(response.get("purchaseOrder"), dict)
                else response
            )
            order_id = str(order_payload.get("id")) if order_payload.get("id") is not None else None
            value = sum(
                (
                    Decimal(str(line["approved_quantity"])) * Decimal(str(line["unit_cost"]))
                    for line in lines
                ),
                Decimal(0),
            )
            row = self._one(
                """INSERT INTO purchase_plan_orders
              (id,plan_id,tenant_id,supplier_key,status,estimated_value,request_hash,
               alegra_order_id,response_payload,submitted_at)
              VALUES (:id,:plan_id,:tenant_id,:supplier_key,'submitted',:value,:hash,:alegra_id,
                      CAST(:response AS jsonb),now()) ON CONFLICT (tenant_id,request_hash) DO UPDATE SET
              status='submitted',alegra_order_id=EXCLUDED.alegra_order_id,
              response_payload=EXCLUDED.response_payload,submitted_at=now() RETURNING *""",
                {
                    "id": uuid.uuid4(),
                    "plan_id": plan_id,
                    "supplier_key": supplier_key,
                    "value": value,
                    "hash": request_hash,
                    "alegra_id": order_id,
                    "response": json.dumps(response),
                },
            )
            results.append(row)
        self._session.execute(
            text(
                "UPDATE purchase_plan_runs SET status='submitted' WHERE id=:plan_id AND tenant_id=:tenant_id"
            ),
            {"plan_id": plan_id, "tenant_id": self._tenant_id},
        )
        self._session.commit()
        return {"plan_id": plan_id, "orders": results}

    def supplier_performance(self, supplier_key: int) -> dict[str, Any]:
        supplier = self._one(
            "SELECT key,name,alegra_id FROM dim_contact WHERE tenant_id=:tenant_id AND key=:supplier_key",
            {"supplier_key": supplier_key},
        )
        if supplier is None:
            raise LookupError("Supplier not found")
        metrics = (
            self._one(
                """SELECT count(DISTINCT po.alegra_id) orders,
          avg(pb.issue_date-po.order_date) FILTER (WHERE pb.issue_date IS NOT NULL) lead_days,
          avg(CASE WHEN pb.issue_date<=po.delivery_date THEN 1.0 ELSE 0.0 END)
            FILTER (WHERE pb.issue_date IS NOT NULL AND po.delivery_date IS NOT NULL) on_time_rate,
          sum(COALESCE(pbl.quantity,0))/NULLIF(sum(COALESCE(pol.quantity,0)),0) fill_rate
          FROM purchase_orders po JOIN purchase_order_lines pol ON pol.tenant_id=po.tenant_id AND pol.document_alegra_id=po.alegra_id
          LEFT JOIN purchase_bills pb ON pb.tenant_id=po.tenant_id AND pb.purchase_order_alegra_id=po.alegra_id AND pb.is_deleted=false
          LEFT JOIN purchase_bill_lines pbl ON pbl.tenant_id=pb.tenant_id AND pbl.document_alegra_id=pb.alegra_id AND pbl.item_alegra_id=pol.item_alegra_id
          WHERE po.tenant_id=:tenant_id AND po.provider_alegra_id=:supplier_alegra_id AND po.is_deleted=false""",
                {"supplier_alegra_id": supplier["alegra_id"]},
            )
            or {}
        )
        return {"supplier": supplier, "metrics": metrics}

    def _rows(self, statement: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        values = {"tenant_id": self._tenant_id, **(params or {})}
        return [dict(row) for row in self._session.execute(text(statement), values).mappings()]

    def _one(self, statement: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        rows = self._rows(statement, params)
        return rows[0] if rows else None


def _croston(series: list[Decimal], *, sba: bool) -> Decimal:
    alpha = Decimal("0.20")
    demand = Decimal(0)
    interval = Decimal(1)
    gap = 0
    initialized = False
    for value in series:
        gap += 1
        if value <= 0:
            continue
        if not initialized:
            demand, interval, initialized = value, Decimal(gap), True
        else:
            demand = alpha * value + (Decimal(1) - alpha) * demand
            interval = alpha * Decimal(gap) + (Decimal(1) - alpha) * interval
        gap = 0
    if not initialized or interval <= 0:
        return Decimal(0)
    forecast = demand / interval
    return forecast * (Decimal(1) - alpha / Decimal(2)) if sba else forecast


def _tsb(series: list[Decimal]) -> Decimal:
    alpha = Decimal("0.20")
    probability = Decimal(1) if series and series[0] > 0 else Decimal(0)
    demand = series[0] if series and series[0] > 0 else Decimal(0)
    for value in series[1:]:
        occurrence = Decimal(1) if value > 0 else Decimal(0)
        probability = alpha * occurrence + (Decimal(1) - alpha) * probability
        if value > 0:
            demand = alpha * value + (Decimal(1) - alpha) * demand
    return probability * demand


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, (Decimal, date, datetime, uuid.UUID)):
        return str(value)
    return value
