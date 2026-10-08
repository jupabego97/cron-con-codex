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
from app.integrations.alegra.client import AlegraAuthenticationError, AlegraClient


def _lead_time(supplier: dict) -> int:
    """An explicit agreement wins; observed means round up; a real zero stays zero."""
    for key in ("lead_time_days", "observed_lead_days", "default_lead_time_days"):
        if supplier.get(key) is not None:
            return max(0, int(Decimal(str(supplier[key])).to_integral_value(rounding=ROUND_CEILING)))
    return 7


def _freight(goods: Decimal, policy: dict) -> Decimal:
    if goods <= 0:
        return Decimal(0)
    threshold = policy.get("free_shipping_threshold")
    if threshold is not None and goods >= Decimal(str(threshold)):
        return Decimal(0)
    return Decimal(str(policy.get("shipping_cost") or 0))

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
    history_days: int = 365
    observed_days: int = 0
    censored_stockout_days: int = 0


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
        decision: str | None = None,
        offset: int = 0,
        limit: int = 1000,
        product_key: int | None = None,
        family: str | None = None,
        supplier_key: int | None = None,
    ) -> dict[str, Any]:
        return self._build(
            as_of=as_of,
            weekly_budget=weekly_budget,
            currency_code=currency_code,
            review_cycle_days=review_cycle_days,
            decision=decision,
            offset=offset,
            limit=limit,
            persist=False,
            product_key=product_key, family=family, supplier_key=supplier_key,
        )

    def create_plan(
        self,
        *,
        as_of: date,
        weekly_budget: Decimal,
        currency_code: str = "COP",
        review_cycle_days: int = 7,
    ) -> dict[str, Any]:
        if as_of != business_today():
            raise ValueError("Purchase plans can only be created for the current business date")
        return self._build(
            as_of=as_of,
            weekly_budget=weekly_budget,
            currency_code=currency_code,
            review_cycle_days=review_cycle_days,
            decision=None,
            offset=0,
            limit=1000,
            persist=True,
        )

    def _build(
        self,
        *,
        as_of: date,
        weekly_budget: Decimal,
        currency_code: str,
        review_cycle_days: int,
        decision: str | None,
        offset: int,
        limit: int,
        persist: bool,
        product_key: int | None = None,
        family: str | None = None,
        supplier_key: int | None = None,
    ) -> dict[str, Any]:
        if weekly_budget < 0:
            raise ValueError("weekly_budget must not be negative")
        if not 1 <= review_cycle_days <= 31:
            raise ValueError("review_cycle_days must be between 1 and 31")
        currency_code = currency_code.strip().upper() or "COP"
        quality = self.data_quality(as_of=as_of)
        products = self._product_inputs(
            as_of=as_of,
            currency_code=currency_code,
            snapshot_run_id=quality.get("snapshot_run_id"),
        )
        forecasts = self._forecast_products(products, as_of=as_of)
        supplier_options = self._supplier_options(as_of=as_of, currency_code=currency_code)
        all_lines = self._recommend(
            products=products,
            forecasts=forecasts,
            supplier_options=supplier_options,
            weekly_budget=weekly_budget,
            review_cycle_days=review_cycle_days,
            blocked=not quality["ready"],
        )
        supplier_orders = self._supplier_orders(all_lines)
        summary = self._summary(all_lines, weekly_budget)
        filtered_lines = (
            [line for line in all_lines if line["decision"] == decision]
            if decision
            else all_lines
        )
        filtered_lines = [line for line in filtered_lines
                          if (product_key is None or line["product_key"] == product_key)
                          and (family is None or line["family"] == family)
                          and (supplier_key is None or line["supplier_key"] == supplier_key)]
        lines = filtered_lines[offset : offset + limit]
        summary["total_products_evaluated"] = len(all_lines)
        summary["lines_returned"] = len(lines)
        result = {
            "plan_id": None,
            "as_of_date": as_of,
            "currency_code": currency_code,
            "weekly_budget": weekly_budget,
            "review_cycle_days": review_cycle_days,
            "status": (
                "blocked_data"
                if not quality["ready"]
                else "draft"
                if quality["is_current_date"]
                else "historical_preview"
            ),
            "data_quality": quality,
            "summary": summary,
            "supplier_orders": supplier_orders if persist else self._supplier_orders(filtered_lines),
            "scope": {"budget": "Presupuesto asignado al catálogo completo; filtros limitan la vista",
                      "as_of_date": as_of, "product_key": product_key, "family": family,
                      "supplier_key": supplier_key,
                      "demand": "365 días hasta la fecha de corte, limitado por introducción registrada"},
            "lines": lines,
            "line_pagination": {
                "decision": decision,
                "offset": offset,
                "limit": limit,
                "total": len(filtered_lines),
                "has_more": offset + len(lines) < len(filtered_lines),
            },
            "service_levels": {
                f"{abc}{xyz}": value for (abc, xyz), value in SERVICE_LEVELS.items()
            },
        }
        if persist:
            result["plan_id"] = self._persist_plan(result, forecasts, all_lines)
        return result

    def data_quality(self, *, as_of: date | None = None) -> dict[str, Any]:
        as_of = as_of or business_today()
        row = (
            self._one(
                """
            WITH materialized AS (
              SELECT r.id, r.finished_at
              FROM inventory_snapshot_runs r
              WHERE r.tenant_id=:tenant_id AND r.status='succeeded'
                AND (r.finished_at AT TIME ZONE 'America/Bogota')::date=:as_of
                AND EXISTS (
                  SELECT 1 FROM fact_inventory_snapshot f
                  WHERE f.tenant_id=r.tenant_id AND f.snapshot_run_id=r.id
                )
              ORDER BY r.finished_at DESC LIMIT 1
            ), states AS (
              SELECT
                max(last_success_at) FILTER (WHERE resource='bill') AS bill_sync_at,
                max(last_success_at) FILTER (WHERE resource='purchase_order') AS po_sync_at,
                max(last_success_at) FILTER (WHERE resource='item') AS item_sync_at,
                max(last_success_at) FILTER (WHERE resource='contact') AS contact_sync_at
              FROM resource_sync_states WHERE tenant_id=:tenant_id
            )
            SELECT materialized.id AS snapshot_run_id,
                   materialized.finished_at AS snapshot_at,
                   states.bill_sync_at, states.po_sync_at,
                   states.item_sync_at, states.contact_sync_at,
                   (SELECT max(finished_at) FROM sync_runs
                    WHERE tenant_id=:tenant_id AND resource='invoice'
                      AND status='succeeded') AS invoice_sync_at,
                   (SELECT max(finished_at) FROM mart_refresh_runs
                    WHERE tenant_id=:tenant_id AND status='succeeded') AS mart_at,
                   (SELECT count(DISTINCT f.document_alegra_id)
                    FROM fact_purchase_line f
                    JOIN dim_date d ON d.date_key=f.date_key
                    LEFT JOIN dim_contact c ON c.tenant_id=f.tenant_id
                      AND c.key=f.provider_key
                    WHERE f.tenant_id=:tenant_id AND f.is_deleted=false
                      AND d.calendar_date BETWEEN :history_from AND :as_of
                      AND (f.provider_key IS NULL OR c.key IS NULL)) AS unmapped_supplier_documents
            FROM states LEFT JOIN materialized ON true
            """,
                {"as_of": as_of, "history_from": as_of - timedelta(days=364)},
            )
            or {}
        )
        now = datetime.now(UTC)
        snapshot_at = row.get("snapshot_at")
        bill_sync_at = row.get("bill_sync_at")
        po_sync_at = row.get("po_sync_at")
        item_sync_at = row.get("item_sync_at")
        contact_sync_at = row.get("contact_sync_at")
        is_current = as_of == business_today()
        warnings = []
        if snapshot_at is None:
            warnings.append(f"No hay captura de inventario para {as_of.isoformat()}")
        elif is_current and now - snapshot_at > timedelta(hours=6):
            warnings.append("Inventario sin una captura materializada en las ultimas 6 horas")
        sync_times = [
            row.get("invoice_sync_at"),
            bill_sync_at,
            po_sync_at,
            item_sync_at,
            contact_sync_at,
        ]
        latest_sync_at = max((stamp for stamp in sync_times if stamp is not None), default=None)
        if is_current:
            for field, label in (
                ("invoice_sync_at", "Facturas de venta"),
                ("bill_sync_at", "Facturas de proveedor"),
                ("po_sync_at", "Ordenes de compra"),
            ):
                stamp = row.get(field)
                if stamp is None or now - stamp > timedelta(hours=24):
                    warnings.append(f"{label} sin sincronizacion exitosa en las ultimas 24 horas")
        for stamp, label in (
            (item_sync_at, "Catalogo de productos"),
            (contact_sync_at, "Catalogo de contactos/proveedores"),
        ):
            if stamp is None:
                warnings.append(f"{label} sin una sincronizacion inicial exitosa")
        mart_at = row.get("mart_at")
        if mart_at is None:
            warnings.append("No hay una ejecucion exitosa del mart")
        elif latest_sync_at is not None and mart_at < latest_sync_at:
            warnings.append("El mart es anterior a la ultima sincronizacion de Alegra")
        elif snapshot_at is not None and mart_at < snapshot_at:
            warnings.append("El mart no contiene la captura de inventario seleccionada")
        unmapped = int(row.get("unmapped_supplier_documents") or 0)
        if unmapped:
            warnings.append(f"Hay {unmapped} facturas de compra sin proveedor asociado")
        return {
            **row,
            "as_of_date": as_of,
            "latest_sync_at": latest_sync_at,
            "is_current_date": is_current,
            "ready": not warnings,
            "warnings": warnings,
            "notes": (
                []
                if is_current
                else [
                    "El tránsito de órdenes abiertas no se reconstruye históricamente y se omite; esta vista no se puede convertir en un pedido."
                ]
            ),
            "checked_at": now,
        }

    def _product_inputs(
        self, *, as_of: date, currency_code: str, snapshot_run_id: uuid.UUID | None
    ) -> dict[int, dict[str, Any]]:
        rows = self._rows(
            """
            WITH latest AS (
              SELECT CAST(:snapshot_run_id AS uuid) AS id
              WHERE :snapshot_run_id IS NOT NULL
            ), stock AS (
              SELECT f.product_key, sum(f.quantity_on_hand) AS quantity_on_hand,
                     max(f.unit_cost) FILTER (WHERE f.unit_cost>0) AS snapshot_cost
              FROM fact_inventory_snapshot f JOIN latest ON latest.id=f.snapshot_run_id
              WHERE f.tenant_id=:tenant_id AND f.product_key IS NOT NULL
              GROUP BY f.product_key
            ), sales AS (
              SELECT f.product_key, d.calendar_date, sum(f.quantity) AS units,
                     sum(f.net_sales_amount) AS revenue,
                     sum(f.margin_amount) AS margin
              FROM fact_sales_line f JOIN dim_date d ON d.date_key=f.date_key
              WHERE f.tenant_id=:tenant_id AND f.is_deleted=false
                AND f.document_type='invoice' AND f.product_key IS NOT NULL
                AND f.document_status IN ('open','closed')
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
                AND f.document_status IN ('open','closed')
                AND d.calendar_date BETWEEN :history_from AND :as_of
                AND f.currency_code=:currency_code
              GROUP BY f.product_key
            ), purchase_documents AS (
              SELECT p.product_key,p.document_alegra_id,d.calendar_date,
                     sum(p.quantity) purchase_quantity,
                     sum(p.unit_cost*p.quantity) FILTER (WHERE p.unit_cost>0)
                       /NULLIF(sum(p.quantity) FILTER (WHERE p.unit_cost>0),0) weighted_unit_cost,
                     max(p.provider_key) supplier_key
              FROM fact_purchase_line p JOIN dim_date d ON d.date_key=p.date_key
              WHERE p.tenant_id=:tenant_id AND p.is_deleted=false
                AND p.document_status IN ('open','closed')
                AND p.product_key IS NOT NULL AND COALESCE(p.quantity,0)>0
                AND d.calendar_date BETWEEN DATE '2025-01-01' AND :as_of
              GROUP BY p.product_key,p.document_alegra_id,d.calendar_date
            ), received AS (
              SELECT DISTINCT ON (pd.product_key) pd.product_key,
                     pd.calendar_date last_purchase_date,
                     pd.purchase_quantity last_purchase_quantity,
                     pd.weighted_unit_cost last_unit_cost,
                     pd.supplier_key last_purchase_supplier_key,
                     pd.document_alegra_id last_purchase_document_id
              FROM purchase_documents pd
              ORDER BY pd.product_key,pd.calendar_date DESC,pd.document_alegra_id DESC
            ), billed_po AS (
              SELECT pb.purchase_order_alegra_id,pbl.item_alegra_id,sum(pbl.quantity) billed
              FROM purchase_bills pb JOIN purchase_bill_lines pbl
                ON pbl.tenant_id=pb.tenant_id AND pbl.document_alegra_id=pb.alegra_id
              WHERE pb.tenant_id=:tenant_id AND pb.is_deleted=false
                AND pb.status IN ('open','closed')
                AND pb.purchase_order_alegra_id IS NOT NULL
                AND pb.issue_date<=:as_of
              GROUP BY pb.purchase_order_alegra_id,pbl.item_alegra_id
            ), ordered_products AS (
              SELECT l.tenant_id,l.document_alegra_id,l.item_alegra_id,sum(l.quantity) ordered,
                sum(COALESCE(r.accepted,0)) accepted
              FROM purchase_order_lines l LEFT JOIN (
                SELECT tenant_id,order_alegra_id,line_number,item_alegra_id,sum(accepted_quantity) accepted
                FROM purchase_receipts WHERE tenant_id=:tenant_id AND received_on<=:as_of
                GROUP BY tenant_id,order_alegra_id,line_number,item_alegra_id
              ) r ON r.tenant_id=l.tenant_id AND r.order_alegra_id=l.document_alegra_id
                AND r.line_number=l.line_number
                AND r.item_alegra_id IS NOT DISTINCT FROM l.item_alegra_id
              WHERE l.tenant_id=:tenant_id GROUP BY l.tenant_id,l.document_alegra_id,l.item_alegra_id
            ), transit AS (
              SELECT dp.key product_key,
                     sum(GREATEST(COALESCE(pol.ordered,0)-GREATEST(COALESCE(bp.billed,0),pol.accepted),0)) in_transit
              FROM purchase_orders po JOIN ordered_products pol
                ON pol.tenant_id=po.tenant_id AND pol.document_alegra_id=po.alegra_id
              JOIN dim_product dp ON dp.tenant_id=po.tenant_id
                AND dp.alegra_id=pol.item_alegra_id
              LEFT JOIN billed_po bp ON bp.purchase_order_alegra_id=po.alegra_id
                AND bp.item_alegra_id=pol.item_alegra_id
              LEFT JOIN purchase_order_tracking tracking ON tracking.tenant_id=po.tenant_id
                AND tracking.order_alegra_id=po.alegra_id
              WHERE po.tenant_id=:tenant_id AND po.is_deleted=false AND po.status='open'
                AND :include_current_open_po=true AND po.order_date<=:as_of
                AND COALESCE(tracking.stage,'pending_confirmation') NOT IN ('closed','cancelled')
              GROUP BY dp.key
            )
            SELECT p.key product_key,p.alegra_id,p.name,p.reference,p.family_name,
                   COALESCE(profile.lifecycle,'active') lifecycle,profile.introduced_on,
                   profile.replacement_product_key,
                   COALESCE(stock.quantity_on_hand,0) quantity_on_hand,
                   COALESCE(transit.in_transit,0) quantity_in_transit,
                   COALESCE(sales_totals.gross_units,0) gross_units,
                   COALESCE(sales_totals.revenue,0) revenue,
                   sales_totals.margin margin,
                   COALESCE(sales_totals.sale_days,0) sale_days,
                   COALESCE(returns.returned_units,0) returned_units,
                   received.last_purchase_date,received.last_purchase_quantity,
                   received.last_purchase_supplier_key,received.last_purchase_document_id,
                   last_supplier.name last_purchase_supplier,
                   COALESCE(received.last_unit_cost,stock.snapshot_cost,p.current_cost,0) unit_cost
            FROM dim_product p
            LEFT JOIN product_business_profiles profile ON profile.tenant_id=p.tenant_id
              AND profile.product_key=p.key
            LEFT JOIN stock ON stock.product_key=p.key
            LEFT JOIN sales_totals ON sales_totals.product_key=p.key
            LEFT JOIN returns ON returns.product_key=p.key
            LEFT JOIN received ON received.product_key=p.key
            LEFT JOIN dim_contact last_supplier ON last_supplier.tenant_id=p.tenant_id
              AND last_supplier.key=received.last_purchase_supplier_key
            LEFT JOIN transit ON transit.product_key=p.key
            WHERE p.tenant_id=:tenant_id AND p.is_deleted=false
              AND (stock.product_key IS NOT NULL OR p.inventory_enabled=true)
            """,
            {
                "as_of": as_of,
                "history_from": as_of - timedelta(days=364),
                "currency_code": currency_code,
                "snapshot_run_id": snapshot_run_id,
                "include_current_open_po": as_of == business_today(),
            },
        )
        products = {int(row["product_key"]): row for row in rows}
        daily_rows = self._rows(
            """
            SELECT f.product_key,d.calendar_date,sum(f.quantity) units
            FROM fact_sales_line f JOIN dim_date d ON d.date_key=f.date_key
            WHERE f.tenant_id=:tenant_id AND f.is_deleted=false
              AND f.document_type='invoice' AND f.product_key IS NOT NULL
              AND f.document_status IN ('open','closed')
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
        availability = self._rows("""SELECT p.key product_key,a.observed_on,a.positive_samples
          FROM product_daily_availability a JOIN dim_product p
            ON p.tenant_id=a.tenant_id AND p.alegra_id=a.item_alegra_id
          WHERE a.tenant_id=:tenant_id AND a.observed_on BETWEEN :first AND :as_of""",
          {"first": as_of-timedelta(days=364), "as_of": as_of})
        for row in availability:
            if int(row["product_key"]) in products:
                products[int(row["product_key"])].setdefault("availability", {})[row["observed_on"]] = row["positive_samples"] > 0
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

        results = {}
        for key, product in products.items():
            first = max(as_of-timedelta(days=364), product.get("introduced_on") or as_of-timedelta(days=364))
            calendar_dates = [first+timedelta(days=offset) for offset in range((as_of-first).days+1)]
            observations = product.get("availability", {})
            observed = sum(day in observations for day in calendar_dates)
            # Only censor documented zero-stock days when observations cover at least half the window.
            reliable = observed >= 14 and observed >= len(calendar_dates)/2
            dates = [day for day in calendar_dates if not reliable or observations.get(day, True)
                     or product.get("daily", {}).get(day, 0)>0]
            if not dates:
                dates = calendar_dates
            product["forecast_history"] = {"history_days": len(calendar_dates),
                "observed_days": observed, "censored_stockout_days": len(calendar_dates)-len(dates),
                "availability_adjusted": reliable,
                "note": "Días no observados no prueban disponibilidad; ventas perdidas no se inventan"}
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
                    history_days=len(calendar_dates), observed_days=observed,
                    censored_stockout_days=len(calendar_dates)-len(dates),
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
                if len(series)>=90 and len(nonzero) >= 20 and selected[1] <= Decimal("0.35")
                else "media"
                if len(series) >= 28 and len(nonzero) >= 4 and selected[1] <= Decimal("0.75")
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
                history_days=len(calendar_dates), observed_days=observed,
                censored_stockout_days=len(calendar_dates)-len(dates),
            )
        return results

    def _backtest(
        self, name: str, series: list[Decimal], dates: list[date]
    ) -> tuple[str, Decimal, Decimal]:
        errors = Decimal(0)
        signed = Decimal(0)
        actual = Decimal(0)
        cutoffs = [len(series)-holdout for holdout in (56, 42, 28, 14) if len(series)-holdout>=14]
        if not cutoffs:
            return name, Decimal(999), Decimal(0)
        for cutoff in cutoffs:
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
            WITH supplier_stats_as_of AS (
              SELECT f.tenant_id,f.product_key,f.provider_key supplier_key,
                     COALESCE(f.currency_code,'COP') currency_code,
                     count(*)::integer purchase_line_count,
                     sum(f.quantity) purchased_units,
                     sum(f.purchase_amount)/NULLIF(sum(f.quantity),0) average_unit_cost,
                     (array_agg(f.unit_cost ORDER BY d.calendar_date DESC,
                                f.document_alegra_id DESC,f.line_number DESC)
                       FILTER (WHERE f.unit_cost>0))[1] last_unit_cost,
                     max(d.calendar_date) last_purchase_date
              FROM fact_purchase_line f JOIN dim_date d ON d.date_key=f.date_key
              WHERE f.tenant_id=:tenant_id AND f.is_deleted=false
                AND f.product_key IS NOT NULL AND f.provider_key IS NOT NULL
                AND COALESCE(f.quantity,0)>0
                AND d.calendar_date BETWEEN DATE '2025-01-01' AND :as_of
                AND COALESCE(f.currency_code,'COP')=:currency_code
              GROUP BY f.tenant_id,f.product_key,f.provider_key,
                       COALESCE(f.currency_code,'COP')
            ), ranked_supplier_stats AS (
              SELECT s.*,
                     sum(s.purchase_line_count) OVER (
                       PARTITION BY s.product_key,s.currency_code) total_purchase_lines,
                     sum(s.purchased_units) OVER (
                       PARTITION BY s.product_key,s.currency_code) total_purchased_units,
                     s.purchase_line_count::numeric / NULLIF(sum(s.purchase_line_count) OVER (
                       PARTITION BY s.product_key,s.currency_code),0) * 100 line_share_pct,
                     s.purchased_units / NULLIF(sum(s.purchased_units) OVER (
                       PARTITION BY s.product_key,s.currency_code),0) * 100 unit_share_pct,
                     row_number() OVER (
                       PARTITION BY s.product_key,s.currency_code
                       ORDER BY s.purchase_line_count DESC,s.purchased_units DESC,
                                s.last_purchase_date DESC,s.supplier_key)::integer frequency_rank
              FROM supplier_stats_as_of s
            ), receipt_lines AS (
              SELECT tenant_id,order_alegra_id,line_number,item_alegra_id,sum(accepted_quantity) accepted,
                max(received_on) received_on FROM purchase_receipts
              WHERE tenant_id=:tenant_id AND received_on<=:as_of
              GROUP BY tenant_id,order_alegra_id,line_number,item_alegra_id
            ), observed_orders AS (
              SELECT dp.key product_key,dc.key supplier_key,po.alegra_id,
                po.order_date,COALESCE(t.expected_on,po.delivery_date) expected_on,
                sum(pol.quantity) ordered,sum(COALESCE(r.accepted,0)) accepted,
                max(r.received_on) last_receipt,bool_and(COALESCE(r.accepted,0)>=pol.quantity) complete
              FROM purchase_orders po JOIN purchase_order_lines pol
                ON pol.tenant_id=po.tenant_id AND pol.document_alegra_id=po.alegra_id
              JOIN dim_product dp ON dp.tenant_id=po.tenant_id AND dp.alegra_id=pol.item_alegra_id
              JOIN dim_contact dc ON dc.tenant_id=po.tenant_id AND dc.alegra_id=po.provider_alegra_id
              LEFT JOIN receipt_lines r ON r.tenant_id=pol.tenant_id
                AND r.order_alegra_id=po.alegra_id AND r.line_number=pol.line_number
                AND r.item_alegra_id IS NOT DISTINCT FROM pol.item_alegra_id
              LEFT JOIN purchase_order_tracking t ON t.tenant_id=po.tenant_id AND t.order_alegra_id=po.alegra_id
              WHERE po.tenant_id=:tenant_id AND po.is_deleted=false AND po.status<>'void'
                AND po.order_date<=:as_of
              GROUP BY dp.key,dc.key,po.alegra_id,po.order_date,t.expected_on,po.delivery_date
            ), po_performance AS (
              SELECT product_key,supplier_key,count(*) FILTER(WHERE complete) completed_orders,
                avg(CASE WHEN last_receipt<=expected_on THEN 1.0 ELSE 0.0 END)
                  FILTER(WHERE complete AND expected_on IS NOT NULL) on_time_rate,
                sum(accepted) FILTER(WHERE last_receipt IS NOT NULL)
                  /NULLIF(sum(ordered) FILTER(WHERE last_receipt IS NOT NULL),0) fill_rate,
                avg(last_receipt-order_date) FILTER(WHERE complete) observed_lead_days
              FROM observed_orders GROUP BY product_key,supplier_key
            ), terms AS (
              SELECT dc.key supplier_key,avg(pb.due_date-pb.issue_date) payment_days
              FROM purchase_bills pb JOIN dim_contact dc
                ON dc.tenant_id=pb.tenant_id AND dc.alegra_id=pb.provider_alegra_id
              WHERE pb.tenant_id=:tenant_id AND pb.is_deleted=false
                AND pb.issue_date IS NOT NULL AND pb.due_date IS NOT NULL
                AND pb.issue_date<=:as_of
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
            FROM ranked_supplier_stats s JOIN dim_contact c
              ON c.tenant_id=s.tenant_id AND c.key=s.supplier_key
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
            {"currency_code": currency_code, "as_of": as_of},
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
                lead = Decimal(_lead_time(row))
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
            lead_days = _lead_time(supplier)
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
            if product.get("margin") is None:
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
            if product.get("lifecycle", "active") != "active":
                decision = "no_reorder"
                quantity = Decimal(0)
                value = Decimal(0)
            elif stock < 0:
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
                "lifecycle": product.get("lifecycle", "active"),
                "replacement_product_key": product.get("replacement_product_key"),
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
                "last_purchase_supplier": product.get("last_purchase_supplier"),
                "last_purchase_document_id": product.get("last_purchase_document_id"),
                "purchase_lot_reference": product.get("last_purchase_quantity"),
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
                "shipping_cost": supplier.get("shipping_cost"),
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
                    "priority_margin_source": "historical" if product.get("margin") is not None else "ranking_proxy_not_real_margin",
                    "forecast_history": product.get("forecast_history", {}),
                    "gross_demand_not_net_of_returns": True,
                    "expected_horizon_demand": str(expected),
                    "safety_stock": str(safety),
                    "lead_time_days": lead_days,
                    "review_cycle_days": review_cycle_days,
                    "last_purchase_date": product.get("last_purchase_date"),
                    "last_purchase_quantity": product.get("last_purchase_quantity"),
                    "last_purchase_supplier": product.get("last_purchase_supplier"),
                    "last_purchase_document_id": product.get("last_purchase_document_id"),
                    "inventory_exception": "negative_stock" if stock < 0 else None,
                },
            }
            (candidates if decision == "candidate" else opportunities).append(line)
        candidates.sort(key=lambda row: (-row["priority_score"], row["estimated_value"]))
        spent = Decimal(0)
        supplier_goods = defaultdict(Decimal)
        for line in candidates:
            previous = supplier_goods[line["supplier_key"]]
            proposed = previous + line["estimated_value"]
            increment = line["estimated_value"] + _freight(proposed, line) - _freight(previous, line)
            if blocked:
                line["decision"] = "blocked_data"
            elif line["supplier_key"] is None:
                line["decision"] = "review_supplier"
            elif spent + increment <= weekly_budget:
                line["decision"] = "buy_now" if line["priority"] == "critical" else "buy_weekly"
                spent += increment
                supplier_goods[line["supplier_key"]] = proposed
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
        return combined

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
                    "shipping_cost": line.get("shipping_cost"),
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
            below_minimum = bool(minimum and value < minimum)
            if below_minimum and group["critical_lines"]:
                group["decision"] = "urgent_below_minimum"
            elif below_minimum:
                group["decision"] = "accumulate_minimum"
            else:
                group["decision"] = "ready_for_approval"
            group["amount_to_minimum"] = max(minimum - value, Decimal(0))
            group["shipping_cost_due"] = _freight(value, group)
            group["estimated_total"] = value + group["shipping_cost_due"]
        return sorted(
            groups.values(), key=lambda row: (-row["critical_lines"], -row["estimated_value"])
        )

    def _summary(self, lines: list[dict[str, Any]], weekly_budget: Decimal) -> dict[str, Any]:
        selected = [line for line in lines if line["decision"] in {"buy_now", "buy_weekly"}]
        goods = sum((line["estimated_value"] for line in selected), Decimal(0))
        freight = sum((order["shipping_cost_due"] for order in self._supplier_orders(selected)), Decimal(0))
        return {
            "recommended_products": len(selected),
            "critical_products": sum(line["priority"] == "critical" for line in selected),
            "reconcile_products": sum(line["decision"] == "reconcile" for line in lines),
            "deferred_by_budget": sum(line["decision"] == "deferred_budget" for line in lines),
            "dead_or_no_demand": sum(line["decision"] == "no_reorder" for line in lines),
            "recommended_value": goods + freight,
            "recommended_goods_value": goods,
            "estimated_freight": freight,
            "weekly_budget": weekly_budget,
            "remaining_budget": max(
                weekly_budget - goods - freight,
                Decimal(0),
            ),
        }

    def _persist_plan(
        self,
        result: dict[str, Any],
        forecasts: dict[int, ForecastResult],
        all_lines: list[dict[str, Any]],
    ) -> str:
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
        for line in all_lines:
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
            FROM purchase_plan_lines l JOIN dim_product p
              ON p.tenant_id=l.tenant_id AND p.key=l.product_key
            LEFT JOIN dim_contact c
              ON c.tenant_id=l.tenant_id AND c.key=l.supplier_key
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
        supplier_key: int | None = None,
    ) -> dict[str, Any]:
        if decision not in {"approved", "discarded", "snoozed"}:
            raise ValueError("Invalid line decision")
        run = self._one(
            "SELECT status,currency_code FROM purchase_plan_runs "
            "WHERE id=:plan_id AND tenant_id=:tenant_id FOR UPDATE",
            {"plan_id": plan_id},
        )
        if run is None:
            raise LookupError("Purchase plan not found")
        if run["status"] != "draft":
            raise ValueError("Only draft plans can be edited")
        line = self._one(
            "SELECT * FROM purchase_plan_lines WHERE id=:line_id AND plan_id=:plan_id "
            "AND tenant_id=:tenant_id",
            {"line_id": line_id, "plan_id": plan_id},
        )
        if line is None:
            raise LookupError("Purchase plan line not found")
        selected_supplier_key = supplier_key or line.get("supplier_key")
        if decision == "approved" and approved_quantity > 0 and selected_supplier_key is None:
            raise ValueError("Choose a supplier before approving a purchase line")
        unit_cost = Decimal(str(line["unit_cost"]))
        if selected_supplier_key is not None and decision != "discarded":
            supplier = self._one(
                """SELECT s.supplier_key,c.name supplier,
                  COALESCE(NULLIF(s.last_unit_cost,0),s.average_unit_cost,0) unit_cost,
                  COALESCE(p.minimum_order_quantity,0) minimum_order_quantity,
                  COALESCE(p.pack_size,1) pack_size
                FROM supplier_product_stats s
                JOIN dim_contact c ON c.tenant_id=s.tenant_id AND c.key=s.supplier_key
                LEFT JOIN supplier_product_policies p ON p.tenant_id=s.tenant_id
                  AND p.product_key=s.product_key AND p.supplier_key=s.supplier_key
                  AND p.currency_code=s.currency_code AND p.active=true
                WHERE s.tenant_id=:tenant_id AND s.product_key=:product_key
                  AND s.supplier_key=:supplier_key AND s.currency_code=:currency""",
                {
                    "product_key": line["product_key"],
                    "supplier_key": selected_supplier_key,
                    "currency": run["currency_code"],
                },
            )
            if supplier is None:
                raise ValueError("The selected supplier has no purchase history for this product")
            unit_cost = Decimal(str(supplier["unit_cost"] or 0))
            minimum = Decimal(str(supplier["minimum_order_quantity"] or 0))
            pack = Decimal(str(supplier["pack_size"] or 1))
            if decision == "approved" and approved_quantity > 0:
                if unit_cost <= 0:
                    raise ValueError("The selected supplier has no usable unit cost")
                if minimum and approved_quantity < minimum:
                    raise ValueError(f"Quantity is below the supplier minimum of {minimum}")
                if pack > 0 and approved_quantity % pack != 0:
                    raise ValueError(f"Quantity must be a multiple of the supplier pack size {pack}")
        row = self._one(
            """UPDATE purchase_plan_lines SET decision=:decision,
            approved_quantity=:quantity,note=:note,supplier_key=:supplier_key,
            unit_cost=:unit_cost,estimated_value=:estimated_value
            WHERE id=:line_id AND plan_id=:plan_id AND tenant_id=:tenant_id RETURNING *""",
            {
                "line_id": line_id,
                "plan_id": plan_id,
                "decision": decision,
                "quantity": approved_quantity,
                "note": note,
                "supplier_key": selected_supplier_key,
                "unit_cost": unit_cost,
                "estimated_value": unit_cost * approved_quantity,
            },
        )
        if row is None:
            raise LookupError("Purchase plan line not found")
        self._session.commit()
        return row

    def approve_plan(
        self, plan_id: uuid.UUID, *, allow_below_minimum: bool = False
    ) -> dict[str, Any]:
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
        if run["status"] == "approved":
            return self.get_plan(plan_id)
        if run["status"] != "draft":
            raise ValueError("Only a draft plan can be approved")
        quality = self.data_quality(as_of=business_today())
        if not quality["ready"] or quality.get("snapshot_run_id") != run["snapshot_run_id"]:
            raise ValueError("Source data changed or became stale; prepare a fresh plan")
        self._session.execute(
            text("""UPDATE purchase_plan_lines
          SET decision='approved',approved_quantity=recommended_quantity
          WHERE plan_id=:plan_id AND tenant_id=:tenant_id
            AND decision IN ('buy_now','buy_weekly') AND approved_quantity=0"""),
            {"plan_id": plan_id, "tenant_id": self._tenant_id},
        )
        approved_lines = self._rows(
            """SELECT l.supplier_key,c.name supplier,
              sum(l.approved_quantity*l.unit_cost) goods_value,
              max(COALESCE(p.minimum_order_amount,0)) minimum_order_amount,
              max(COALESCE(p.shipping_cost,0)) shipping_cost,
              max(p.free_shipping_threshold) free_shipping_threshold
            FROM purchase_plan_lines l
            LEFT JOIN dim_contact c ON c.tenant_id=l.tenant_id AND c.key=l.supplier_key
            LEFT JOIN supplier_replenishment_policies p ON p.tenant_id=l.tenant_id
              AND p.supplier_key=l.supplier_key AND p.currency_code=:currency
              AND p.active=true
            WHERE l.plan_id=:plan_id AND l.tenant_id=:tenant_id
              AND l.decision='approved' AND l.approved_quantity>0
            GROUP BY l.supplier_key,c.name""",
            {"plan_id": plan_id, "currency": run["currency_code"]},
        )
        missing_supplier = self._one(
            """SELECT count(*) AS line_count FROM purchase_plan_lines
            WHERE plan_id=:plan_id AND tenant_id=:tenant_id AND decision='approved'
              AND approved_quantity>0 AND supplier_key IS NULL""",
            {"plan_id": plan_id},
        )
        if int((missing_supplier or {}).get("line_count") or 0) > 0:
            self._session.rollback()
            raise ValueError("Every approved purchase line must have a supplier")
        if not approved_lines:
            self._session.rollback()
            raise ValueError("The plan has no purchase lines to approve")
        below_minimum = []
        freight_total = Decimal(0)
        approval_supplier_audit = []
        goods_total = Decimal(0)
        for supplier in approved_lines:
            goods = Decimal(str(supplier["goods_value"] or 0))
            goods_total += goods
            minimum = Decimal(str(supplier["minimum_order_amount"] or 0))
            free_threshold = supplier["free_shipping_threshold"]
            if minimum and goods < minimum:
                below_minimum.append(
                    f"{supplier['supplier'] or 'Proveedor'}: faltan {minimum - goods}"
                )
            freight_due = _freight(goods, supplier)
            freight_total += freight_due
            approval_supplier_audit.append(
                {
                    "supplier_key": supplier["supplier_key"],
                    "supplier": supplier["supplier"],
                    "goods_value": str(goods),
                    "minimum_order_amount": str(minimum),
                    "below_minimum": bool(minimum and goods < minimum),
                    "freight_estimate": str(freight_due),
                    "free_shipping_threshold": str(free_threshold) if free_threshold is not None else None,
                }
            )
        if below_minimum and not allow_below_minimum:
            self._session.rollback()
            raise ValueError(
                "Some supplier orders are below their minimum. Explicitly confirm the urgent override: "
                + "; ".join(below_minimum)
            )
        total = goods_total + freight_total
        if Decimal(str(total)) > Decimal(str(run["weekly_budget"])):
            self._session.rollback()
            raise ValueError("Approved value, including estimated freight, exceeds the weekly budget")
        self._session.execute(
            text(
                "UPDATE purchase_plan_runs SET status='approved',approved_value=:total,approved_at=now(),approval_audit=CAST(:audit AS jsonb) WHERE id=:plan_id AND tenant_id=:tenant_id"
            ),
            {
                "plan_id": plan_id,
                "tenant_id": self._tenant_id,
                "total": total,
                "audit": json.dumps(
                    {
                        "allow_below_minimum": bool(below_minimum and allow_below_minimum),
                        "below_minimum_suppliers": below_minimum,
                        "goods_value": str(goods_total),
                        "estimated_freight": str(freight_total),
                        "supplier_orders": approval_supplier_audit,
                        "approved_at": datetime.now(UTC).isoformat(),
                    }
                ),
            },
        )
        self._session.commit()
        return self.get_plan(plan_id)

    async def submit_to_alegra(self, *, plan_id: uuid.UUID, alegra: AlegraClient) -> dict[str, Any]:
        plan_run = self._one(
            "SELECT * FROM purchase_plan_runs WHERE id=:plan_id AND tenant_id=:tenant_id FOR UPDATE",
            {"plan_id": plan_id},
        )
        if plan_run is None:
            raise LookupError("Purchase plan not found")
        if plan_run["status"] not in {"approved", "submitted"}:
            raise ValueError("The plan must be approved before submission")
        plan = self.get_plan(plan_id)
        groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for line in plan["lines"]:
            if (
                line["decision"] == "approved"
                and line["supplier_key"] is not None
                and Decimal(str(line["approved_quantity"])) > 0
            ):
                groups[int(line["supplier_key"])].append(line)
        if not groups:
            raise ValueError("The approved plan contains no purchase lines")
        warehouse = self._one(
            "SELECT alegra_id FROM dim_warehouse WHERE tenant_id=:tenant_id AND is_deleted=false ORDER BY key LIMIT 1"
        )
        results = []
        submission_warnings = []
        for supplier_key, lines in sorted(groups.items()):
            existing = self._one(
                "SELECT * FROM purchase_plan_orders WHERE tenant_id=:tenant_id "
                "AND plan_id=:plan_id AND supplier_key=:supplier_key",
                {"plan_id": plan_id, "supplier_key": supplier_key},
            )
            if existing is not None:
                if existing.get("alegra_order_id"):
                    results.append(existing)
                    continue
                if existing["status"] != "rejected":
                    submission_warnings.append(
                        f"Order for supplier {supplier_key} is in {existing['status']} state without "
                        "an Alegra order ID. Check Alegra; automatic resubmission is blocked."
                    )
                    results.append(existing)
                    continue
            supplier = self._one(
                "SELECT alegra_id,name FROM dim_contact WHERE tenant_id=:tenant_id AND key=:supplier_key",
                {"supplier_key": supplier_key},
            )
            if supplier is None:
                raise ValueError(f"Supplier {supplier_key} no longer exists in the tenant")
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
            request_hash = hashlib.sha256(
                f"procurement-submit-v1:{self._tenant_id}:{plan_id}:{supplier_key}".encode()
            ).hexdigest()
            value = sum(
                (
                    Decimal(str(line["approved_quantity"])) * Decimal(str(line["unit_cost"]))
                    for line in lines
                ),
                Decimal(0),
            )
            order_payload_json = json.dumps({"state": "submitting", "request": request})
            if existing is not None:
                pending = self._one(
                    """UPDATE purchase_plan_orders SET status='submitting',estimated_value=:value,
                      request_hash=:hash,response_payload=CAST(:payload AS jsonb)
                      WHERE id=:order_id AND tenant_id=:tenant_id RETURNING *""",
                    {
                        "order_id": existing["id"],
                        "value": value,
                        "hash": request_hash,
                        "payload": order_payload_json,
                    },
                )
            else:
                pending = self._one(
                    """INSERT INTO purchase_plan_orders
                  (id,plan_id,tenant_id,supplier_key,status,estimated_value,request_hash,
                   response_payload)
                  VALUES (:id,:plan_id,:tenant_id,:supplier_key,'submitting',:value,:hash,
                          CAST(:payload AS jsonb)) RETURNING *""",
                    {
                        "id": uuid.uuid4(),
                        "plan_id": plan_id,
                        "supplier_key": supplier_key,
                        "value": value,
                        "hash": request_hash,
                        "payload": order_payload_json,
                    },
                )
            self._session.commit()
            try:
                response = await alegra.create_purchase_order(request)
            except AlegraAuthenticationError as error:
                self._session.execute(
                    text("""UPDATE purchase_plan_orders SET status='rejected',
                      response_payload=CAST(:payload AS jsonb)
                      WHERE id=:order_id AND tenant_id=:tenant_id"""),
                    {
                        "order_id": pending["id"],
                        "payload": json.dumps(
                            {
                                "state": "rejected",
                                "request": request,
                                "error": str(error)[:500],
                            }
                        ),
                    },
                )
                self._session.commit()
                submission_warnings.append(
                    f"Alegra rejected credentials for supplier {supplier_key}; the order was not accepted. "
                    "After fixing the API credential, this rejected request can be safely retried."
                )
                results.append(
                    self._one(
                        "SELECT * FROM purchase_plan_orders WHERE id=:order_id "
                        "AND tenant_id=:tenant_id",
                        {"order_id": pending["id"]},
                    )
                )
                continue
            except Exception as error:
                self._session.execute(
                    text("""UPDATE purchase_plan_orders SET status='unknown',
                      response_payload=CAST(:payload AS jsonb)
                      WHERE id=:order_id AND tenant_id=:tenant_id"""),
                    {
                        "order_id": pending["id"],
                        "payload": json.dumps(
                            {
                                "state": "unknown",
                                "request": request,
                                "error": str(error)[:500],
                            }
                        ),
                    },
                )
                self._session.commit()
                submission_warnings.append(
                    f"Alegra's result is uncertain for supplier {supplier_key}; check Alegra. "
                    "The request will not be resent automatically."
                )
                results.append(
                    self._one(
                        "SELECT * FROM purchase_plan_orders WHERE id=:order_id "
                        "AND tenant_id=:tenant_id",
                        {"order_id": pending["id"]},
                    )
                )
                continue
            order_payload = (
                response.get("purchaseOrder")
                if isinstance(response.get("purchaseOrder"), dict)
                else response
            )
            order_id = str(order_payload.get("id")) if order_payload.get("id") is not None else None
            if order_id is None:
                self._session.execute(
                    text("""UPDATE purchase_plan_orders SET status='unknown',
                      response_payload=CAST(:payload AS jsonb)
                      WHERE id=:order_id AND tenant_id=:tenant_id"""),
                    {
                        "order_id": pending["id"],
                        "payload": json.dumps(
                            {"state": "unknown", "request": request, "response": response}
                        ),
                    },
                )
                self._session.commit()
                submission_warnings.append(
                    f"Alegra returned no order ID for supplier {supplier_key}; check Alegra. "
                    "The request will not be resent automatically."
                )
                results.append(
                    self._one(
                        "SELECT * FROM purchase_plan_orders WHERE id=:order_id "
                        "AND tenant_id=:tenant_id",
                        {"order_id": pending["id"]},
                    )
                )
                continue
            row = self._one(
                """UPDATE purchase_plan_orders SET status='submitted',alegra_order_id=:alegra_id,
                   response_payload=CAST(:response AS jsonb),submitted_at=now()
                   WHERE id=:order_id AND tenant_id=:tenant_id RETURNING *""",
                {
                    "alegra_id": order_id,
                    "response": json.dumps(response),
                    "order_id": pending["id"],
                },
            )
            results.append(row)
            self._session.commit()
        complete = len(results) == len(groups) and all(
            result is not None and result.get("alegra_order_id") for result in results
        )
        if complete:
            self._session.execute(
                text(
                    "UPDATE purchase_plan_runs SET status='submitted' WHERE id=:plan_id AND tenant_id=:tenant_id"
                ),
                {"plan_id": plan_id, "tenant_id": self._tenant_id},
            )
        self._session.commit()
        return {
            "plan_id": plan_id,
            "orders": results,
            "complete": complete,
            "warnings": submission_warnings,
        }

    def supplier_performance(self, supplier_key: int) -> dict[str, Any]:
        supplier = self._one(
            "SELECT key,name,alegra_id FROM dim_contact WHERE tenant_id=:tenant_id AND key=:supplier_key",
            {"supplier_key": supplier_key},
        )
        if supplier is None:
            raise LookupError("Supplier not found")
        metrics = self._one("""WITH receipts AS (
          SELECT tenant_id,order_alegra_id,line_number,item_alegra_id,sum(accepted_quantity) accepted,
            max(received_on) received_on FROM purchase_receipts WHERE tenant_id=:tenant_id
          GROUP BY tenant_id,order_alegra_id,line_number,item_alegra_id), orders AS (
          SELECT po.alegra_id,po.order_date,COALESCE(t.expected_on,po.delivery_date) expected_on,
            sum(l.quantity) ordered,sum(COALESCE(r.accepted,0)) accepted,
            max(r.received_on) last_receipt,bool_and(COALESCE(r.accepted,0)>=l.quantity) complete
          FROM purchase_orders po JOIN purchase_order_lines l
            ON l.tenant_id=po.tenant_id AND l.document_alegra_id=po.alegra_id
          LEFT JOIN receipts r ON r.tenant_id=l.tenant_id AND r.order_alegra_id=po.alegra_id AND r.line_number=l.line_number
            AND r.item_alegra_id IS NOT DISTINCT FROM l.item_alegra_id
          LEFT JOIN purchase_order_tracking t ON t.tenant_id=po.tenant_id AND t.order_alegra_id=po.alegra_id
          WHERE po.tenant_id=:tenant_id AND po.provider_alegra_id=:supplier AND po.is_deleted=false AND po.status<>'void'
          GROUP BY po.alegra_id,po.order_date,t.expected_on,po.delivery_date)
          SELECT count(*) orders,count(*) FILTER(WHERE complete) completed_orders,
            count(*) FILTER(WHERE last_receipt IS NOT NULL) observed_orders,
            avg(last_receipt-order_date) FILTER(WHERE complete) lead_days,
            avg(CASE WHEN last_receipt<=expected_on THEN 1.0 ELSE 0.0 END)
              FILTER(WHERE complete AND expected_on IS NOT NULL) on_time_rate,
            sum(accepted) FILTER(WHERE last_receipt IS NOT NULL)
              /NULLIF(sum(ordered) FILTER(WHERE last_receipt IS NOT NULL),0) fill_rate FROM orders""",
          {"supplier": supplier["alegra_id"]}) or {}
        return {"supplier": supplier, "metrics": metrics,
                "source": "Recepciones físicas registradas; una factura no prueba entrega"}

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
