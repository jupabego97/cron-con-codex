# ruff: noqa: E501
"""Read-only query service for the tenant-scoped analytics data mart."""

from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class AnalyticsFilters:
    from_date: date
    to_date: date
    currency: str | None = None
    product_key: int | None = None
    seller_key: int | None = None
    warehouse_key: int | None = None
    document_status: str | None = None
    family: str | None = None
    provider_key: int | None = None

    @classmethod
    def default(cls) -> "AnalyticsFilters":
        today = date.today()
        return cls(from_date=today - timedelta(days=29), to_date=today)

    def previous_period(self) -> "AnalyticsFilters":
        days = (self.to_date - self.from_date).days + 1
        previous_to = self.from_date - timedelta(days=1)
        return replace(self, from_date=previous_to - timedelta(days=days - 1), to_date=previous_to)


class AnalyticsQueryService:
    def __init__(
        self,
        *,
        session: Session,
        tenant_id: UUID,
        monthly_sales_target_cop: Decimal | None = None,
    ) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._monthly_sales_target_cop = monthly_sales_target_cop

    def filters(self) -> dict[str, Any]:
        return {
            "date_range": self._one(
                """
                SELECT min(d.calendar_date) AS min_date, max(d.calendar_date) AS max_date
                FROM dim_date d
                JOIN fact_sales_line f ON f.date_key = d.date_key
                WHERE f.tenant_id = :tenant_id AND f.is_deleted = false
                """
            ),
            "currencies": self._rows(
                """
                SELECT DISTINCT currency_code AS value FROM (
                  SELECT currency_code FROM fact_sales_line WHERE tenant_id = :tenant_id AND is_deleted = false
                  UNION SELECT currency_code FROM fact_purchase_line WHERE tenant_id = :tenant_id AND is_deleted = false
                  UNION SELECT currency_code FROM fact_payment WHERE tenant_id = :tenant_id AND is_deleted = false
                ) currencies WHERE currency_code IS NOT NULL ORDER BY value
                """
            ),
            "products": self._rows(
                """
                SELECT key AS value, name AS label, reference
                FROM dim_product WHERE tenant_id = :tenant_id AND is_deleted = false
                ORDER BY name LIMIT 500
                """
            ),
            "sellers": self._dimension_options("dim_seller"),
            "warehouses": self._dimension_options("dim_warehouse"),
            "families": self._rows(
                """
                SELECT DISTINCT family_name AS value, family_name AS label
                FROM dim_product
                WHERE tenant_id = :tenant_id AND is_deleted = false
                  AND family_name IS NOT NULL
                ORDER BY label
                """
            ),
            "suppliers": self._rows(
                """
                SELECT DISTINCT c.key AS value, c.name AS label
                FROM fact_purchase_line f
                JOIN dim_contact c ON c.key = f.provider_key
                WHERE f.tenant_id = :tenant_id AND f.is_deleted = false
                  AND c.is_deleted = false
                ORDER BY label
                """
            ),
            "document_statuses": self._rows(
                """
                SELECT DISTINCT document_status AS value FROM fact_sales_line
                WHERE tenant_id = :tenant_id AND is_deleted = false AND document_status IS NOT NULL
                ORDER BY value
                """
            ),
        }

    def overview(self, filters: AnalyticsFilters) -> dict[str, Any]:
        current = self._sales_metrics(filters)
        previous = self._sales_metrics(filters.previous_period())
        return {
            "current": current,
            "previous": previous,
            "series": self._sales_series(filters),
        }

    def sales(self, filters: AnalyticsFilters) -> dict[str, Any]:
        return {
            "summary": self._sales_metrics(filters),
            "series": self._sales_series(filters),
            "by_product": self._sales_breakdown(filters, "product"),
            "by_seller": self._sales_breakdown(filters, "seller"),
            "by_warehouse": self._sales_breakdown(filters, "warehouse"),
            "by_customer": self._sales_breakdown(filters, "customer"),
            "by_status": self._sales_breakdown(filters, "status"),
            "by_family_detail": self._sales_detail(
                filters,
                joins="LEFT JOIN dim_product p ON p.key = f.product_key",
                dimension_select="COALESCE(p.family_name, 'SIN FAMILIA') AS family",
                group_by="COALESCE(p.family_name, 'SIN FAMILIA')",
            ),
            "product_detail": self._sales_detail(
                filters,
                joins="LEFT JOIN dim_product p ON p.key = f.product_key",
                dimension_select="COALESCE(p.name, 'Sin producto') AS product, "
                "p.reference, COALESCE(p.family_name, 'SIN FAMILIA') AS family",
                group_by="p.name, p.reference, COALESCE(p.family_name, 'SIN FAMILIA')",
            ),
            "seller_detail": self._sales_detail(
                filters,
                joins="LEFT JOIN dim_seller s ON s.key = f.seller_key",
                dimension_select="COALESCE(s.name, 'Sin vendedor') AS seller",
                group_by="COALESCE(s.name, 'Sin vendedor')",
            ),
            "customer_detail": self._sales_detail(
                filters,
                joins="LEFT JOIN dim_contact c ON c.key = f.contact_key",
                dimension_select="COALESCE(c.name, 'Sin cliente') AS customer",
                group_by="COALESCE(c.name, 'Sin cliente')",
            ),
            "status_detail": self._sales_detail(
                filters,
                joins="",
                dimension_select="COALESCE(f.document_status, 'Sin estado') AS status",
                group_by="COALESCE(f.document_status, 'Sin estado')",
            ),
        }

    def _sales_detail(
        self,
        filters: AnalyticsFilters,
        *,
        joins: str,
        dimension_select: str,
        group_by: str,
    ) -> list[dict[str, Any]]:
        where, params = self._fact_where(filters, alias="f", allow_seller=True, allow_status=True)
        currency = "COALESCE(f.currency_code, 'COP')"
        return self._rows(
            f"""
            WITH aggregated AS (
              SELECT {dimension_select}, {currency} AS currency_code,
                     COALESCE(sum(f.net_sales_amount), 0) AS net_sales,
                     COALESCE(sum(f.quantity), 0) AS units,
                     count(DISTINCT (f.document_type, f.document_alegra_id)) AS documents,
                     COALESCE(sum(f.net_sales_amount) FILTER (WHERE f.document_type = 'invoice'), 0) AS invoice_sales,
                     abs(COALESCE(sum(f.net_sales_amount) FILTER (WHERE f.document_type = 'credit_note'), 0)) AS credit_note_amount,
                     COALESCE(sum(f.cogs_amount) FILTER (WHERE f.cost_status IN ('costed', 'estimated')), 0) AS cogs,
                     COALESCE(sum(f.margin_amount) FILTER (WHERE f.cost_status IN ('costed', 'estimated')), 0) AS gross_margin,
                     COALESCE(sum(f.net_sales_amount) / NULLIF(sum(f.quantity), 0), 0) AS average_unit_sale,
                     COALESCE(sum(f.net_sales_amount) FILTER (WHERE f.cost_status IN ('costed', 'estimated')), 0) AS costed_sales,
                     count(*) AS total_lines,
                     count(*) FILTER (WHERE f.cost_status IN ('costed', 'estimated')) AS costed_lines,
                     count(*) FILTER (WHERE f.cost_status = 'unavailable') AS unavailable_cost_lines,
                     max(d.calendar_date) AS last_sale_date
              FROM fact_sales_line f
              JOIN dim_date d ON d.date_key = f.date_key
              {joins}
              WHERE {where}
              GROUP BY {group_by}, {currency}
            )
            SELECT aggregated.*,
                   COALESCE(gross_margin / NULLIF(costed_sales, 0) * 100, 0) AS gross_margin_pct,
                   COALESCE(costed_lines::numeric / NULLIF(total_lines, 0) * 100, 0) AS cost_coverage_pct,
                   COALESCE(net_sales / NULLIF(sum(net_sales) OVER (PARTITION BY currency_code), 0) * 100, 0) AS share_pct
            FROM aggregated
            ORDER BY net_sales DESC
            LIMIT 200
            """,
            params,
        )

    def purchases(self, filters: AnalyticsFilters) -> dict[str, Any]:
        where, params = self._fact_where(
            filters, alias="f", allow_seller=False, allow_status=True, allow_provider=True
        )
        return {
            "summary": self._rows(
                f"""
                SELECT f.currency_code, COALESCE(sum(f.purchase_amount), 0) AS purchase_amount,
                       COALESCE(sum(f.quantity), 0) AS quantity,
                       count(DISTINCT f.document_alegra_id) AS documents
                FROM fact_purchase_line f JOIN dim_date d ON d.date_key = f.date_key
                WHERE {where} GROUP BY f.currency_code ORDER BY f.currency_code
                """,
                params,
            ),
            "series": self._rows(
                f"""
                SELECT date_trunc('month', d.calendar_date)::date AS period, f.currency_code,
                       COALESCE(sum(f.purchase_amount), 0) AS amount
                FROM fact_purchase_line f JOIN dim_date d ON d.date_key = f.date_key
                WHERE {where} GROUP BY period, f.currency_code ORDER BY period, f.currency_code
                """,
                params,
            ),
            "by_supplier": self._rows(
                f"""
                SELECT COALESCE(c.name, 'Sin proveedor') AS label, f.currency_code,
                       COALESCE(sum(f.purchase_amount), 0) AS amount
                FROM fact_purchase_line f
                JOIN dim_date d ON d.date_key = f.date_key
                LEFT JOIN dim_contact c ON c.key = f.provider_key
                WHERE {where} GROUP BY label, f.currency_code ORDER BY amount DESC LIMIT 15
                """,
                params,
            ),
            "by_product": self._rows(
                f"""
                SELECT COALESCE(p.name, 'Sin producto') AS label, f.currency_code,
                       COALESCE(sum(f.purchase_amount), 0) AS amount
                FROM fact_purchase_line f
                JOIN dim_date d ON d.date_key = f.date_key
                LEFT JOIN dim_product p ON p.key = f.product_key
                WHERE {where} GROUP BY label, f.currency_code ORDER BY amount DESC LIMIT 15
                """,
                params,
            ),
        }

    def suppliers(self, filters: AnalyticsFilters) -> dict[str, Any]:
        """Supplier scorecard over real purchase-bill providers."""
        where, params = self._fact_where(
            filters,
            alias="f",
            allow_seller=False,
            allow_status=True,
            allow_provider=True,
        )
        currency = "COALESCE(f.currency_code, 'COP')"
        return {
            "summary": self._rows(
                f"""
                SELECT {currency} AS currency_code,
                       COALESCE(sum(f.purchase_amount), 0) AS purchase_amount,
                       COALESCE(sum(f.quantity), 0) AS units,
                       count(DISTINCT f.document_alegra_id) AS documents,
                       count(DISTINCT f.provider_key) AS suppliers,
                       COALESCE(sum(f.purchase_amount) / NULLIF(count(DISTINCT f.document_alegra_id), 0), 0) AS average_purchase_ticket,
                       COALESCE(sum(f.purchase_amount) / NULLIF(sum(f.quantity), 0), 0) AS average_unit_cost
                FROM fact_purchase_line f JOIN dim_date d ON d.date_key = f.date_key
                WHERE {where}
                GROUP BY {currency}
                ORDER BY currency_code
                """,
                params,
            ),
            "series": self._rows(
                f"""
                SELECT date_trunc('month', d.calendar_date)::date AS period,
                       {currency} AS currency_code,
                       COALESCE(sum(f.purchase_amount), 0) AS amount,
                       COALESCE(sum(f.quantity), 0) AS units
                FROM fact_purchase_line f JOIN dim_date d ON d.date_key = f.date_key
                WHERE {where}
                GROUP BY period, {currency}
                ORDER BY period, currency_code
                """,
                params,
            ),
            "by_supplier": self._rows(
                f"""
                WITH filtered AS (
                  SELECT f.*, {currency} AS report_currency
                  FROM fact_purchase_line f JOIN dim_date d ON d.date_key = f.date_key
                  WHERE {where}
                ), totals AS (
                  SELECT report_currency, sum(purchase_amount) AS total_amount
                  FROM filtered GROUP BY report_currency
                )
                SELECT c.key AS supplier_key, COALESCE(c.name, 'Sin proveedor') AS supplier,
                       x.report_currency AS currency_code,
                       COALESCE(sum(x.purchase_amount), 0) AS purchase_amount,
                       COALESCE(sum(x.quantity), 0) AS units,
                       count(DISTINCT x.document_alegra_id) AS documents,
                       count(DISTINCT x.product_key) AS skus,
                       COALESCE(sum(x.purchase_amount) / NULLIF(count(DISTINCT x.document_alegra_id), 0), 0) AS average_purchase_ticket,
                       COALESCE(sum(x.purchase_amount) / NULLIF(sum(x.quantity), 0), 0) AS average_unit_cost,
                       COALESCE(sum(x.purchase_amount) / NULLIF(max(t.total_amount), 0) * 100, 0) AS share_pct,
                       min(d.calendar_date) AS first_purchase_date,
                       max(d.calendar_date) AS last_purchase_date
                FROM filtered x
                LEFT JOIN dim_contact c ON c.key = x.provider_key
                JOIN dim_date d ON d.date_key = x.date_key
                JOIN totals t ON t.report_currency = x.report_currency
                GROUP BY c.key, c.name, x.report_currency
                ORDER BY purchase_amount DESC
                LIMIT 100
                """,
                params,
            ),
            "by_family": self._rows(
                f"""
                SELECT COALESCE(p.family_name, 'SIN FAMILIA') AS family,
                       {currency} AS currency_code,
                       COALESCE(sum(f.purchase_amount), 0) AS purchase_amount,
                       COALESCE(sum(f.quantity), 0) AS units,
                       count(DISTINCT f.provider_key) AS suppliers,
                       count(DISTINCT f.product_key) AS skus
                FROM fact_purchase_line f
                JOIN dim_date d ON d.date_key = f.date_key
                LEFT JOIN dim_product p ON p.key = f.product_key
                WHERE {where}
                GROUP BY family, {currency}
                ORDER BY purchase_amount DESC
                LIMIT 100
                """,
                params,
            ),
            "by_product_supplier": self._rows(
                f"""
                SELECT COALESCE(p.name, 'Sin producto') AS product,
                       COALESCE(p.family_name, 'SIN FAMILIA') AS family,
                       COALESCE(c.name, 'Sin proveedor') AS supplier,
                       {currency} AS currency_code,
                       COALESCE(sum(f.purchase_amount), 0) AS purchase_amount,
                       COALESCE(sum(f.quantity), 0) AS units,
                       COALESCE(sum(f.purchase_amount) / NULLIF(sum(f.quantity), 0), 0) AS average_unit_cost,
                       count(DISTINCT f.document_alegra_id) AS documents,
                       max(d.calendar_date) AS last_purchase_date
                FROM fact_purchase_line f
                JOIN dim_date d ON d.date_key = f.date_key
                LEFT JOIN dim_product p ON p.key = f.product_key
                LEFT JOIN dim_contact c ON c.key = f.provider_key
                WHERE {where}
                GROUP BY product, family, supplier, {currency}
                ORDER BY purchase_amount DESC
                LIMIT 200
                """,
                params,
            ),
            "price_variations": self._rows(
                f"""
                SELECT COALESCE(p.name, 'Sin producto') AS product,
                       COALESCE(p.family_name, 'SIN FAMILIA') AS family,
                       COALESCE(c.name, 'Sin proveedor') AS supplier,
                       {currency} AS currency_code,
                       min(f.unit_cost) AS minimum_cost,
                       max(f.unit_cost) AS maximum_cost,
                       avg(f.unit_cost) AS average_unit_cost,
                       COALESCE((max(f.unit_cost) - min(f.unit_cost)) /
                         NULLIF(avg(f.unit_cost), 0) * 100, 0) AS cost_range_pct,
                       count(*) AS purchase_lines
                FROM fact_purchase_line f
                JOIN dim_date d ON d.date_key = f.date_key
                LEFT JOIN dim_product p ON p.key = f.product_key
                LEFT JOIN dim_contact c ON c.key = f.provider_key
                WHERE {where} AND f.unit_cost IS NOT NULL AND f.unit_cost > 0
                GROUP BY product, family, supplier, {currency}
                HAVING count(*) >= 2
                ORDER BY cost_range_pct DESC
                LIMIT 100
                """,
                params,
            ),
        }

    def payments(self, filters: AnalyticsFilters) -> dict[str, Any]:
        where, params = self._fact_where(
            filters,
            alias="f",
            allow_seller=False,
            allow_status=False,
            allow_product=False,
            allow_warehouse=False,
            allow_family=False,
        )
        return {
            "summary": self._rows(
                f"""
                SELECT f.currency_code, COALESCE(sum(f.amount), 0) AS amount, count(*) AS payments
                FROM fact_payment f JOIN dim_date d ON d.date_key = f.date_key
                WHERE {where} GROUP BY f.currency_code ORDER BY f.currency_code
                """,
                params,
            ),
            "series": self._rows(
                f"""
                SELECT date_trunc('month', d.calendar_date)::date AS period, f.currency_code,
                       COALESCE(sum(f.amount), 0) AS amount
                FROM fact_payment f JOIN dim_date d ON d.date_key = f.date_key
                WHERE {where} GROUP BY period, f.currency_code ORDER BY period, f.currency_code
                """,
                params,
            ),
            "by_type": self._rows(
                f"""
                SELECT COALESCE(f.payment_type, 'Sin tipo') AS label, f.currency_code,
                       COALESCE(sum(f.amount), 0) AS amount
                FROM fact_payment f JOIN dim_date d ON d.date_key = f.date_key
                WHERE {where} GROUP BY label, f.currency_code ORDER BY amount DESC
                """,
                params,
            ),
            "by_contact": self._rows(
                f"""
                SELECT COALESCE(c.name, 'Sin contacto') AS label, f.currency_code,
                       COALESCE(sum(f.amount), 0) AS amount
                FROM fact_payment f
                JOIN dim_date d ON d.date_key = f.date_key
                LEFT JOIN dim_contact c ON c.key = f.contact_key
                WHERE {where} GROUP BY label, f.currency_code ORDER BY amount DESC LIMIT 15
                """,
                params,
            ),
        }

    def inventory(self, filters: AnalyticsFilters) -> dict[str, Any]:
        where, params = self._fact_where(
            filters,
            alias="f",
            allow_seller=False,
            allow_status=False,
            allow_currency=False,
        )
        return {
            "snapshot": self._inventory_snapshot(filters),
            "summary": self._rows(
                f"""
                SELECT f.movement_direction AS label, COALESCE(sum(f.quantity_delta), 0) AS quantity
                FROM fact_inventory_movement f JOIN dim_date d ON d.date_key = f.date_key
                WHERE {where} GROUP BY f.movement_direction ORDER BY f.movement_direction
                """,
                params,
            ),
            "by_product": self._rows(
                f"""
                SELECT COALESCE(p.name, 'Sin producto') AS label,
                       COALESCE(sum(f.quantity_delta), 0) AS quantity
                FROM fact_inventory_movement f
                JOIN dim_date d ON d.date_key = f.date_key
                LEFT JOIN dim_product p ON p.key = f.product_key
                WHERE {where} GROUP BY label ORDER BY abs(sum(f.quantity_delta)) DESC LIMIT 20
                """,
                params,
            ),
            "by_warehouse": self._rows(
                f"""
                SELECT COALESCE(w.name, 'Sin bodega') AS label,
                       COALESCE(sum(f.quantity_delta), 0) AS quantity
                FROM fact_inventory_movement f
                JOIN dim_date d ON d.date_key = f.date_key
                LEFT JOIN dim_warehouse w ON w.key = f.warehouse_key
                WHERE {where} GROUP BY label ORDER BY abs(sum(f.quantity_delta)) DESC
                """,
                params,
            ),
            "recent": self._rows(
                f"""
                SELECT d.calendar_date AS date, f.document_type, f.document_number,
                       f.movement_direction, COALESCE(p.name, 'Sin producto') AS product,
                       COALESCE(w.name, 'Sin bodega') AS warehouse, f.quantity_delta
                FROM fact_inventory_movement f
                JOIN dim_date d ON d.date_key = f.date_key
                LEFT JOIN dim_product p ON p.key = f.product_key
                LEFT JOIN dim_warehouse w ON w.key = f.warehouse_key
                WHERE {where} ORDER BY d.calendar_date DESC, f.key DESC LIMIT 100
                """,
                params,
            ),
        }

    def customers(self, filters: AnalyticsFilters) -> dict[str, Any]:
        where, params = self._fact_where(filters, alias="f", allow_seller=True, allow_status=True)
        return {
            "summary": self._rows(f"""SELECT f.currency_code, count(DISTINCT f.contact_key) AS customers,
                count(DISTINCT (f.document_type, f.document_alegra_id)) AS documents,
                COALESCE(sum(f.net_sales_amount),0) AS amount
                FROM fact_sales_line f JOIN dim_date d ON d.date_key=f.date_key
                WHERE {where} GROUP BY f.currency_code ORDER BY f.currency_code""", params),
            "by_customer": self._rows(f"""SELECT COALESCE(c.name,'Sin cliente') AS label, f.currency_code,
                COALESCE(sum(f.net_sales_amount),0) AS amount, count(DISTINCT f.document_alegra_id) AS documents
                FROM fact_sales_line f JOIN dim_date d ON d.date_key=f.date_key LEFT JOIN dim_contact c ON c.key=f.contact_key
                WHERE {where} GROUP BY label,f.currency_code ORDER BY amount DESC LIMIT 20""", params),
            "recent_customers": self._rows(f"""SELECT COALESCE(c.name,'Sin cliente') AS label, max(d.calendar_date) AS last_purchase,
                COALESCE(sum(f.net_sales_amount),0) AS amount
                FROM fact_sales_line f JOIN dim_date d ON d.date_key=f.date_key LEFT JOIN dim_contact c ON c.key=f.contact_key
                WHERE {where} GROUP BY label ORDER BY last_purchase DESC LIMIT 20""", params),
        }

    def products(self, filters: AnalyticsFilters) -> dict[str, Any]:
        where, params = self._fact_where(filters, alias="f", allow_seller=True, allow_status=True)
        return {
            "summary": self._rows(f"""SELECT f.currency_code, count(DISTINCT f.product_key) AS products,
                COALESCE(sum(f.quantity),0) AS units, COALESCE(sum(f.net_sales_amount),0) AS amount
                FROM fact_sales_line f JOIN dim_date d ON d.date_key=f.date_key WHERE {where}
                GROUP BY f.currency_code ORDER BY f.currency_code""", params),
            "best_sellers": self._rows(f"""SELECT COALESCE(p.name,'Sin producto') AS label, f.currency_code,
                COALESCE(sum(f.net_sales_amount),0) AS amount, COALESCE(sum(f.quantity),0) AS quantity
                FROM fact_sales_line f JOIN dim_date d ON d.date_key=f.date_key LEFT JOIN dim_product p ON p.key=f.product_key
                WHERE {where} GROUP BY label,f.currency_code ORDER BY amount DESC LIMIT 25""", params),
            "by_type": self._rows(f"""SELECT COALESCE(p.item_type,'Sin tipo') AS label, f.currency_code,
                COALESCE(sum(f.net_sales_amount),0) AS amount, COALESCE(sum(f.quantity),0) AS quantity
                FROM fact_sales_line f JOIN dim_date d ON d.date_key=f.date_key LEFT JOIN dim_product p ON p.key=f.product_key
                WHERE {where} GROUP BY label,f.currency_code ORDER BY amount DESC""", params),
            "stock_coverage": self._stock_coverage(),
        }

    def kpis(self, filters: AnalyticsFilters) -> dict[str, Any]:
        """Actionable retail KPIs calculated exclusively from the analytics mart.

        The inventory measures use the most recent Alegra snapshot. Coverage is
        based on the net unit demand inside the selected dashboard period; it is
        a replenishment signal, not an accounting inventory-turn calculation.
        """
        return {
            "sales": self._sales_kpis(filters),
            "customers": self._customer_kpis(filters),
            "purchases": self._purchase_kpis(filters),
            **self._inventory_kpis(filters),
        }

    def purchase_recommendations(
        self,
        filters: AnalyticsFilters,
        *,
        target_coverage_days: int = 30,
        lead_time_days: int = 7,
        safety_days: int = 7,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Return a review queue for replenishment, never an automatic order.

        Demand is net units sold during the selected period. The recommendation
        fills stock up to ``target_coverage_days`` and marks urgency using the
        lead time plus safety days. Items with no observed demand are excluded
        so the queue does not turn dead stock into a purchase suggestion.
        """
        run = self._one(
            "SELECT id, finished_at FROM inventory_snapshot_runs "
            "WHERE tenant_id=:tenant_id AND status='succeeded' "
            "ORDER BY finished_at DESC LIMIT 1"
        )
        if run is None:
            return {"snapshot_at": None, "parameters": {}, "items": []}

        demand_where, params = self._fact_where(
            filters,
            alias="s",
            allow_seller=True,
            allow_status=True,
            allow_warehouse=False,
        )
        stock_clauses = [
            "f.tenant_id = :tenant_id",
            "f.snapshot_run_id = :snapshot_run_id",
            "f.product_key IS NOT NULL",
        ]
        if filters.product_key is not None:
            stock_clauses.append("f.product_key = :product_key")
        if filters.warehouse_key is not None:
            stock_clauses.append("f.warehouse_key = :warehouse_key")
        params.update(
            {
                "snapshot_run_id": run["id"],
                "target_coverage_days": target_coverage_days,
                "reorder_point_days": lead_time_days + safety_days,
                "recommendation_currency": filters.currency or "COP",
                "limit": limit,
            }
        )
        stock_where = " AND ".join(stock_clauses)
        statement = f"""
            WITH stock AS (
              SELECT f.product_key, sum(f.quantity_on_hand) AS quantity_on_hand
              FROM fact_inventory_snapshot f
              WHERE {stock_where}
              GROUP BY f.product_key
            ), demand AS (
              SELECT s.product_key, sum(s.quantity) AS units_sold,
                     sum(s.quantity) / NULLIF(:to_date - :from_date + 1, 0) AS daily_velocity
              FROM fact_sales_line s JOIN dim_date d ON d.date_key = s.date_key
              WHERE {demand_where} AND s.product_key IS NOT NULL
              GROUP BY s.product_key
            ), purchase_cost AS (
              SELECT p.product_key,
                     sum(p.purchase_amount) / NULLIF(sum(p.quantity), 0) AS average_unit_cost
              FROM fact_purchase_line p JOIN dim_date d ON d.date_key = p.date_key
              WHERE p.tenant_id = :tenant_id AND p.is_deleted = false
                AND d.calendar_date >= :from_date AND d.calendar_date <= :to_date
                AND p.currency_code = :recommendation_currency
              GROUP BY p.product_key
            ), supplier_spend AS (
              SELECT p.product_key, p.provider_key, sum(p.purchase_amount) AS amount,
                     row_number() OVER (PARTITION BY p.product_key ORDER BY sum(p.purchase_amount) DESC) AS supplier_rank
              FROM fact_purchase_line p JOIN dim_date d ON d.date_key = p.date_key
              WHERE p.tenant_id = :tenant_id AND p.is_deleted = false
                AND d.calendar_date >= :from_date AND d.calendar_date <= :to_date
                AND p.currency_code = :recommendation_currency AND p.provider_key IS NOT NULL
              GROUP BY p.product_key, p.provider_key
            ), candidates AS (
              SELECT p.key AS product_key, p.name, p.reference,
                     COALESCE(stock.quantity_on_hand, 0) AS quantity_on_hand,
                     GREATEST(COALESCE(demand.units_sold, 0), 0) AS units_sold,
                     GREATEST(COALESCE(demand.daily_velocity, 0), 0) AS daily_velocity,
                     CASE WHEN COALESCE(demand.daily_velocity, 0) > 0
                       THEN stock.quantity_on_hand / demand.daily_velocity END AS coverage_days,
                     COALESCE(purchase_cost.average_unit_cost, p.current_cost, 0) AS unit_cost,
                     COALESCE(supplier_spend.provider_key, 0) AS provider_key,
                     CEIL(GREATEST(COALESCE(demand.daily_velocity, 0) * :target_coverage_days
                       - COALESCE(stock.quantity_on_hand, 0), 0)) AS recommended_quantity
              FROM dim_product p
              LEFT JOIN stock ON stock.product_key = p.key
              JOIN demand ON demand.product_key = p.key
              LEFT JOIN purchase_cost ON purchase_cost.product_key = p.key
              LEFT JOIN supplier_spend
                ON supplier_spend.product_key = p.key AND supplier_spend.supplier_rank = 1
              WHERE p.tenant_id = :tenant_id AND p.is_deleted = false
                AND (stock.product_key IS NOT NULL OR p.inventory_enabled IS TRUE)
            )
            SELECT candidates.name AS product, candidates.reference,
                   candidates.quantity_on_hand, candidates.units_sold, candidates.daily_velocity,
                   round(candidates.coverage_days, 1) AS coverage_days,
                   candidates.recommended_quantity, candidates.unit_cost,
                   candidates.recommended_quantity * candidates.unit_cost AS estimated_purchase_value,
                   :recommendation_currency AS currency_code,
                   supplier.name AS preferred_supplier,
                   CASE
                     WHEN candidates.quantity_on_hand <= 0 THEN 'critical'
                     WHEN candidates.coverage_days < :reorder_point_days THEN 'high'
                     ELSE 'medium'
                   END AS priority,
                   CASE
                     WHEN candidates.quantity_on_hand <= 0 THEN 'Agotado con demanda reciente'
                     WHEN candidates.coverage_days < :reorder_point_days THEN 'La cobertura no alcanza el plazo de compra y seguridad'
                     ELSE 'La cobertura está por debajo del objetivo'
                   END AS reason
            FROM candidates
            LEFT JOIN dim_contact supplier ON supplier.key = candidates.provider_key
            WHERE candidates.recommended_quantity > 0
            ORDER BY CASE
                       WHEN candidates.quantity_on_hand <= 0 THEN 1
                       WHEN candidates.coverage_days < :reorder_point_days THEN 2
                       ELSE 3
                     END,
                     (candidates.recommended_quantity * candidates.unit_cost) DESC NULLS LAST
            LIMIT :limit
        """
        params["target_coverage_days"] = target_coverage_days
        return {
            "snapshot_at": run["finished_at"],
            "parameters": {
                "target_coverage_days": target_coverage_days,
                "lead_time_days": lead_time_days,
                "safety_days": safety_days,
                "demand_from": filters.from_date,
                "demand_to": filters.to_date,
            },
            "items": self._rows(statement, params),
        }

    def alerts(self) -> dict[str, Any]:
        run = self._one("""SELECT id FROM inventory_snapshot_runs WHERE tenant_id=:tenant_id AND status='succeeded'
            ORDER BY finished_at DESC LIMIT 1""")
        if run is None:
            return {"summary": [], "stockouts": [], "negative_stock": [], "slow_stock": []}
        params = {"snapshot_run_id": run["id"]}
        return {
            "summary": self._rows("""SELECT 'Agotados con venta reciente' AS label, count(*) AS count FROM (
                SELECT f.product_key FROM fact_inventory_snapshot f WHERE f.tenant_id=:tenant_id AND f.snapshot_run_id=:snapshot_run_id
                GROUP BY f.product_key HAVING sum(f.quantity_on_hand)<=0) stock
                JOIN fact_sales_line s ON s.tenant_id=:tenant_id AND s.product_key=stock.product_key
                JOIN dim_date d ON d.date_key=s.date_key WHERE s.is_deleted=false AND d.calendar_date>=current_date-interval '90 days'""", params),
            "stockouts": self._rows("""SELECT COALESCE(p.name,'Sin producto') AS label, COALESCE(sum(f.quantity_on_hand),0) AS quantity
                FROM fact_inventory_snapshot f LEFT JOIN dim_product p ON p.key=f.product_key
                WHERE f.tenant_id=:tenant_id AND f.snapshot_run_id=:snapshot_run_id GROUP BY label HAVING sum(f.quantity_on_hand)<=0
                ORDER BY quantity LIMIT 50""", params),
            "negative_stock": self._rows("""SELECT COALESCE(p.name,'Sin producto') AS label, COALESCE(sum(f.quantity_on_hand),0) AS quantity
                FROM fact_inventory_snapshot f LEFT JOIN dim_product p ON p.key=f.product_key
                WHERE f.tenant_id=:tenant_id AND f.snapshot_run_id=:snapshot_run_id GROUP BY label HAVING sum(f.quantity_on_hand)<0 ORDER BY quantity LIMIT 50""", params),
            "slow_stock": self._stock_without_sales(params),
        }

    def _stock_coverage(self) -> list[dict[str, Any]]:
        run = self._one("SELECT id FROM inventory_snapshot_runs WHERE tenant_id=:tenant_id AND status='succeeded' ORDER BY finished_at DESC LIMIT 1")
        if run is None:
            return []
        return self._rows("""WITH stock AS (SELECT product_key,sum(quantity_on_hand) quantity FROM fact_inventory_snapshot WHERE tenant_id=:tenant_id AND snapshot_run_id=:run GROUP BY product_key),
          sales AS (SELECT product_key,sum(quantity) quantity FROM fact_sales_line f JOIN dim_date d ON d.date_key=f.date_key WHERE f.tenant_id=:tenant_id AND f.is_deleted=false AND d.calendar_date>=current_date-interval '30 days' GROUP BY product_key)
          SELECT COALESCE(p.name,'Sin producto') label,stock.quantity,round(stock.quantity/nullif(sales.quantity/30,0),1) AS coverage_days
          FROM stock JOIN sales ON sales.product_key=stock.product_key LEFT JOIN dim_product p ON p.key=stock.product_key WHERE stock.quantity>0 ORDER BY coverage_days ASC NULLS LAST LIMIT 30""", {"run":run["id"]})

    def _sales_kpis(self, filters: AnalyticsFilters) -> list[dict[str, Any]]:
        where, params = self._fact_where(filters, alias="f", allow_seller=True, allow_status=True)
        params["monthly_target_cop"] = self._monthly_sales_target_cop
        return self._rows(
            f"""
            SELECT f.currency_code,
                   COALESCE(sum(f.net_sales_amount), 0) AS net_sales,
                   COALESCE(sum(f.quantity), 0) AS units,
                   count(DISTINCT (f.document_type, f.document_alegra_id)) AS documents,
                   COALESCE(sum(f.quantity) / NULLIF(count(DISTINCT (f.document_type, f.document_alegra_id)), 0), 0) AS units_per_transaction,
                   COALESCE(sum(f.net_sales_amount) /
                     NULLIF(count(DISTINCT (f.document_type, f.document_alegra_id)), 0), 0) AS average_ticket,
                   COALESCE(sum(f.net_sales_amount) / NULLIF(sum(f.quantity), 0), 0) AS average_unit_sale,
                   COALESCE(sum(f.cogs_amount) FILTER (WHERE f.cost_status IN ('costed', 'estimated')), 0) AS cogs,
                   COALESCE(sum(f.margin_amount) FILTER (WHERE f.cost_status IN ('costed', 'estimated')), 0) AS gross_margin,
                   COALESCE(sum(f.margin_amount) FILTER (WHERE f.cost_status IN ('costed', 'estimated')) /
                     NULLIF(sum(f.net_sales_amount) FILTER (WHERE f.cost_status IN ('costed', 'estimated')), 0) * 100, 0) AS gross_margin_pct,
                   COALESCE(count(*) FILTER (WHERE f.cost_status IN ('costed', 'estimated'))::numeric /
                     NULLIF(count(*), 0) * 100, 0) AS cost_coverage_pct,
                   count(*) FILTER (WHERE f.cost_status = 'partial') AS partial_cost_lines,
                   count(*) FILTER (WHERE f.cost_status = 'unavailable') AS unavailable_cost_lines,
                   COALESCE(sum(f.net_sales_amount) FILTER (WHERE f.document_type = 'invoice'), 0) AS invoice_sales,
                   abs(COALESCE(sum(f.net_sales_amount) FILTER (WHERE f.document_type = 'credit_note'), 0)) AS credit_note_amount,
                   COALESCE(abs(sum(f.net_sales_amount) FILTER (WHERE f.document_type = 'credit_note')) /
                     NULLIF(sum(f.net_sales_amount) FILTER (WHERE f.document_type = 'invoice'), 0) * 100, 0) AS credit_note_rate,
                   CASE WHEN f.currency_code = 'COP' THEN CAST(:monthly_target_cop AS numeric) END AS monthly_sales_target,
                   COALESCE(sum(f.net_sales_amount) /
                     NULLIF(:to_date - :from_date + 1, 0), 0) AS average_daily_sales,
                   CASE WHEN f.currency_code = 'COP' AND :monthly_target_cop IS NOT NULL THEN
                     CAST(:monthly_target_cop AS numeric) / NULLIF(EXTRACT(DAY FROM
                       (date_trunc('month', CAST(:to_date AS date)) + interval '1 month - 1 day')), 0)
                   END AS target_daily_sales,
                   CASE WHEN f.currency_code = 'COP' AND :monthly_target_cop IS NOT NULL THEN
                     COALESCE(sum(f.net_sales_amount) /
                       NULLIF(:to_date - :from_date + 1, 0), 0) /
                     NULLIF(CAST(:monthly_target_cop AS numeric) / NULLIF(EXTRACT(DAY FROM
                       (date_trunc('month', CAST(:to_date AS date)) + interval '1 month - 1 day')), 0), 0) * 100
                   END AS sales_pace_vs_target_pct
            FROM fact_sales_line f JOIN dim_date d ON d.date_key = f.date_key
            WHERE {where}
            GROUP BY f.currency_code ORDER BY f.currency_code
            """,
            params,
        )

    def _customer_kpis(self, filters: AnalyticsFilters) -> list[dict[str, Any]]:
        where, params = self._fact_where(filters, alias="f", allow_seller=True, allow_status=True)
        return self._rows(
            f"""
            WITH filtered_documents AS (
              SELECT f.currency_code, f.contact_key, f.document_type, f.document_alegra_id,
                     min(d.calendar_date) AS document_date, sum(f.net_sales_amount) AS amount
              FROM fact_sales_line f JOIN dim_date d ON d.date_key = f.date_key
              WHERE {where} AND f.contact_key IS NOT NULL
              GROUP BY f.currency_code, f.contact_key, f.document_type, f.document_alegra_id
            ), customer_period AS (
              SELECT currency_code, contact_key, count(*) AS documents, sum(amount) AS amount
              FROM filtered_documents
              GROUP BY currency_code, contact_key
            ), first_invoice AS (
              SELECT f.currency_code, f.contact_key, min(d.calendar_date) AS first_purchase_date
              FROM fact_sales_line f JOIN dim_date d ON d.date_key = f.date_key
              WHERE f.tenant_id = :tenant_id AND f.is_deleted = false
                AND f.document_type = 'invoice' AND f.contact_key IS NOT NULL
              GROUP BY f.currency_code, f.contact_key
            ), ranked AS (
              SELECT cp.*, fi.first_purchase_date,
                     row_number() OVER (PARTITION BY cp.currency_code ORDER BY cp.amount DESC) AS customer_rank
              FROM customer_period cp
              LEFT JOIN first_invoice fi ON fi.currency_code = cp.currency_code AND fi.contact_key = cp.contact_key
            )
            SELECT currency_code, count(*) AS active_customers,
                   count(*) FILTER (WHERE documents >= 2) AS repeat_customers,
                   COALESCE(count(*) FILTER (WHERE documents >= 2)::numeric / NULLIF(count(*), 0) * 100, 0) AS repeat_customer_rate,
                   count(*) FILTER (WHERE first_purchase_date BETWEEN :from_date AND :to_date) AS new_customers,
                   COALESCE(sum(amount) FILTER (WHERE customer_rank <= 5) / NULLIF(sum(amount), 0) * 100, 0) AS top_5_customer_concentration
            FROM ranked
            GROUP BY currency_code ORDER BY currency_code
            """,
            params,
        )

    def _purchase_kpis(self, filters: AnalyticsFilters) -> list[dict[str, Any]]:
        where, params = self._fact_where(filters, alias="f", allow_seller=False, allow_status=True)
        return self._rows(
            f"""
            WITH filtered AS (
              SELECT f.currency_code, f.provider_key, f.document_alegra_id, f.quantity, f.purchase_amount
              FROM fact_purchase_line f JOIN dim_date d ON d.date_key = f.date_key
              WHERE {where}
            ), summary AS (
              SELECT currency_code, sum(purchase_amount) AS purchase_amount, sum(quantity) AS units,
                     count(DISTINCT document_alegra_id) AS documents, count(DISTINCT provider_key) AS suppliers
              FROM filtered GROUP BY currency_code
            ), supplier_spend AS (
              SELECT currency_code, provider_key, sum(purchase_amount) AS amount
              FROM filtered WHERE provider_key IS NOT NULL GROUP BY currency_code, provider_key
            ), ranked_suppliers AS (
              SELECT *, row_number() OVER (PARTITION BY currency_code ORDER BY amount DESC) AS supplier_rank
              FROM supplier_spend
            )
            SELECT s.currency_code, s.purchase_amount, s.units, s.documents, s.suppliers,
                   COALESCE(s.purchase_amount / NULLIF(s.documents, 0), 0) AS average_purchase_ticket,
                   COALESCE(s.purchase_amount / NULLIF(s.units, 0), 0) AS average_unit_cost,
                   COALESCE(sum(rs.amount) FILTER (WHERE rs.supplier_rank <= 5) /
                     NULLIF(s.purchase_amount, 0) * 100, 0) AS top_5_supplier_concentration
            FROM summary s LEFT JOIN ranked_suppliers rs ON rs.currency_code = s.currency_code
            GROUP BY s.currency_code, s.purchase_amount, s.units, s.documents, s.suppliers
            ORDER BY s.currency_code
            """,
            params,
        )

    def _inventory_kpis(self, filters: AnalyticsFilters) -> dict[str, list[dict[str, Any]]]:
        run = self._one(
            "SELECT id FROM inventory_snapshot_runs WHERE tenant_id=:tenant_id AND status='succeeded' ORDER BY finished_at DESC LIMIT 1"
        )
        empty = {"inventory": [], "low_coverage": [], "excess_coverage": [], "slow_inventory": []}
        if run is None:
            return empty
        stock_clauses = ["f.tenant_id = :tenant_id", "f.snapshot_run_id = :snapshot_run_id", "f.product_key IS NOT NULL"]
        params: dict[str, Any] = {"snapshot_run_id": run["id"], "from_date": filters.from_date, "to_date": filters.to_date}
        if filters.product_key is not None:
            stock_clauses.append("f.product_key = :product_key")
            params["product_key"] = filters.product_key
        if filters.warehouse_key is not None:
            stock_clauses.append("f.warehouse_key = :warehouse_key")
            params["warehouse_key"] = filters.warehouse_key
        stock_where = " AND ".join(stock_clauses)
        demand_where, demand_params = self._fact_where(filters, alias="s", allow_seller=True, allow_status=True, allow_warehouse=False)
        params.update(demand_params)
        base = f"""
            WITH stock AS (
              SELECT f.product_key, sum(f.quantity_on_hand) AS quantity_on_hand,
                     sum(f.inventory_value) AS inventory_value
              FROM fact_inventory_snapshot f WHERE {stock_where}
              GROUP BY f.product_key
            ), demand AS (
              SELECT s.product_key, sum(s.quantity) AS units_sold
              FROM fact_sales_line s JOIN dim_date d ON d.date_key = s.date_key
              WHERE {demand_where} AND s.product_key IS NOT NULL
              GROUP BY s.product_key
            ), coverage AS (
              SELECT stock.product_key, stock.quantity_on_hand, stock.inventory_value,
                     COALESCE(demand.units_sold, 0) AS period_units_sold,
                     CASE WHEN COALESCE(demand.units_sold, 0) > 0
                       THEN round(stock.quantity_on_hand /
                         (demand.units_sold / (:to_date - :from_date + 1)), 1) END AS coverage_days
              FROM stock LEFT JOIN demand ON demand.product_key = stock.product_key
            )
        """
        return {
            "inventory": self._rows(
                base
                + """
                SELECT count(*) AS products, COALESCE(sum(quantity_on_hand), 0) AS units,
                       COALESCE(sum(inventory_value), 0) AS inventory_value,
                       count(*) FILTER (WHERE quantity_on_hand <= 0) AS unavailable_products,
                       count(*) FILTER (WHERE quantity_on_hand < 0) AS negative_products,
                       count(*) FILTER (WHERE coverage_days > 0 AND coverage_days < 14) AS low_coverage_products,
                       count(*) FILTER (WHERE coverage_days >= 120) AS excess_coverage_products,
                       count(*) FILTER (WHERE quantity_on_hand > 0 AND period_units_sold <= 0) AS no_demand_products,
                       COALESCE(sum(inventory_value) FILTER (WHERE quantity_on_hand > 0 AND period_units_sold <= 0), 0) AS no_demand_value
                FROM coverage
                """,
                params,
            ),
            "low_coverage": self._rows(
                base
                + """
                SELECT COALESCE(p.name, 'Sin producto') AS label, coverage.quantity_on_hand,
                       coverage.period_units_sold, coverage.coverage_days, coverage.inventory_value
                FROM coverage LEFT JOIN dim_product p ON p.key = coverage.product_key
                WHERE coverage.coverage_days > 0 AND coverage.coverage_days < 14
                ORDER BY coverage.coverage_days ASC, coverage.period_units_sold DESC LIMIT 30
                """,
                params,
            ),
            "excess_coverage": self._rows(
                base
                + """
                SELECT COALESCE(p.name, 'Sin producto') AS label, coverage.quantity_on_hand,
                       coverage.period_units_sold, coverage.coverage_days, coverage.inventory_value
                FROM coverage LEFT JOIN dim_product p ON p.key = coverage.product_key
                WHERE coverage.coverage_days >= 120
                ORDER BY coverage.inventory_value DESC NULLS LAST LIMIT 30
                """,
                params,
            ),
            "slow_inventory": self._rows(
                base
                + """
                SELECT COALESCE(p.name, 'Sin producto') AS label, coverage.quantity_on_hand,
                       coverage.period_units_sold, coverage.inventory_value
                FROM coverage LEFT JOIN dim_product p ON p.key = coverage.product_key
                WHERE coverage.quantity_on_hand > 0 AND coverage.period_units_sold <= 0
                ORDER BY coverage.inventory_value DESC NULLS LAST LIMIT 30
                """,
                params,
            ),
        }

    def _stock_without_sales(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        return self._rows("""SELECT COALESCE(p.name,'Sin producto') AS label,sum(f.quantity_on_hand) AS quantity
          FROM fact_inventory_snapshot f LEFT JOIN dim_product p ON p.key=f.product_key WHERE f.tenant_id=:tenant_id AND f.snapshot_run_id=:snapshot_run_id
          AND NOT EXISTS (SELECT 1 FROM fact_sales_line s JOIN dim_date d ON d.date_key=s.date_key WHERE s.tenant_id=f.tenant_id AND s.product_key=f.product_key AND s.is_deleted=false AND d.calendar_date>=current_date-interval '90 days')
          GROUP BY label HAVING sum(f.quantity_on_hand)>0 ORDER BY quantity DESC LIMIT 30""", params)

    def _inventory_snapshot(self, filters: AnalyticsFilters) -> dict[str, Any]:
        run = self._one(
            """
            SELECT id, finished_at FROM inventory_snapshot_runs
            WHERE tenant_id = :tenant_id AND status = 'succeeded'
            ORDER BY finished_at DESC LIMIT 1
            """
        )
        if run is None:
            return {"captured_at": None, "summary": [], "by_product": [], "by_warehouse": [], "items": []}
        clauses = ["f.tenant_id = :tenant_id", "f.snapshot_run_id = :snapshot_run_id"]
        params: dict[str, Any] = {"snapshot_run_id": run["id"]}
        if filters.product_key is not None:
            clauses.append("f.product_key = :product_key")
            params["product_key"] = filters.product_key
        if filters.warehouse_key is not None:
            clauses.append("f.warehouse_key = :warehouse_key")
            params["warehouse_key"] = filters.warehouse_key
        where = " AND ".join(clauses)
        return {
            "captured_at": run["finished_at"],
            "summary": self._rows(
                f"""
                SELECT count(*) AS products, COALESCE(sum(f.quantity_on_hand), 0) AS units,
                       COALESCE(sum(f.inventory_value), 0) AS inventory_value
                FROM fact_inventory_snapshot f WHERE {where}
                HAVING count(*) > 0
                """,
                params,
            ),
            "by_product": self._rows(
                f"""
                SELECT COALESCE(p.name, 'Sin producto') AS label, COALESCE(sum(f.quantity_on_hand), 0) AS quantity,
                       COALESCE(sum(f.inventory_value), 0) AS inventory_value
                FROM fact_inventory_snapshot f
                LEFT JOIN dim_product p ON p.key = f.product_key
                WHERE {where} GROUP BY label ORDER BY quantity DESC LIMIT 50
                """,
                params,
            ),
            "by_warehouse": self._rows(
                f"""
                SELECT COALESCE(w.name, 'Sin bodega') AS label, COALESCE(sum(f.quantity_on_hand), 0) AS quantity,
                       COALESCE(sum(f.inventory_value), 0) AS inventory_value
                FROM fact_inventory_snapshot f
                LEFT JOIN dim_warehouse w ON w.key = f.warehouse_key
                WHERE {where} GROUP BY label ORDER BY quantity DESC
                """,
                params,
            ),
            "items": self._rows(
                f"""
                SELECT COALESCE(p.name, 'Sin producto') AS product, COALESCE(w.name, 'Sin bodega') AS warehouse,
                       f.quantity_on_hand, f.unit_cost, f.inventory_value
                FROM fact_inventory_snapshot f
                LEFT JOIN dim_product p ON p.key = f.product_key
                LEFT JOIN dim_warehouse w ON w.key = f.warehouse_key
                WHERE {where} ORDER BY f.quantity_on_hand ASC, product LIMIT 100
                """,
                params,
            ),
        }

    def refresh_status(self) -> dict[str, Any]:
        return self._one(
            """
            SELECT id, status, started_at, finished_at, records_written, error_message,
                   (finished_at IS NULL OR finished_at < now() - interval '2 hours') AS is_stale,
                   cost.id AS cost_run_id, cost.status AS cost_status,
                   cost.finished_at AS cost_finished_at,
                   COALESCE(cost.finished_at IS NULL OR cost.finished_at < now() - interval '2 hours', true)
                     AS cost_is_stale
            FROM mart_refresh_runs
            LEFT JOIN LATERAL (
              SELECT id, status, finished_at
              FROM sales_cost_allocation_runs
              WHERE tenant_id = mart_refresh_runs.tenant_id
              ORDER BY started_at DESC LIMIT 1
            ) cost ON true
            WHERE tenant_id = :tenant_id AND status = 'succeeded'
            ORDER BY started_at DESC LIMIT 1
            """
        ) or {"status": "never_run", "is_stale": True}

    def _sales_metrics(self, filters: AnalyticsFilters) -> list[dict[str, Any]]:
        where, params = self._fact_where(filters, alias="f", allow_seller=True, allow_status=True)
        return self._rows(
            f"""
            SELECT f.currency_code, COALESCE(sum(f.net_sales_amount), 0) AS net_sales,
                   COALESCE(sum(f.quantity), 0) AS units,
                   count(DISTINCT (f.document_type, f.document_alegra_id)) AS documents,
                   COALESCE(sum(f.net_sales_amount) /
                     NULLIF(count(DISTINCT (f.document_type, f.document_alegra_id)), 0), 0) AS average_ticket,
                   COALESCE(sum(f.cogs_amount) FILTER (WHERE f.cost_status IN ('costed', 'estimated')), 0) AS cogs,
                   COALESCE(sum(f.margin_amount) FILTER (WHERE f.cost_status IN ('costed', 'estimated')), 0) AS gross_margin,
                   COALESCE(sum(f.margin_amount) FILTER (WHERE f.cost_status IN ('costed', 'estimated')) /
                     NULLIF(sum(f.net_sales_amount) FILTER (WHERE f.cost_status IN ('costed', 'estimated')), 0) * 100, 0) AS gross_margin_pct,
                   count(*) FILTER (WHERE f.cost_status IN ('costed', 'estimated')) AS costed_lines,
                   count(*) FILTER (WHERE f.cost_status = 'partial') AS partial_cost_lines,
                   count(*) FILTER (WHERE f.cost_status = 'unavailable') AS unavailable_cost_lines,
                   COALESCE(count(*) FILTER (WHERE f.cost_status IN ('costed', 'estimated'))::numeric /
                     NULLIF(count(*), 0) * 100, 0) AS cost_coverage_pct
            FROM fact_sales_line f JOIN dim_date d ON d.date_key = f.date_key
            WHERE {where} GROUP BY f.currency_code ORDER BY f.currency_code
            """,
            params,
        )

    def _sales_series(self, filters: AnalyticsFilters) -> list[dict[str, Any]]:
        where, params = self._fact_where(filters, alias="f", allow_seller=True, allow_status=True)
        granularity = "month" if (filters.to_date - filters.from_date).days > 92 else "day"
        period = "date_trunc('month', d.calendar_date)::date" if granularity == "month" else "d.calendar_date"
        return self._rows(
            f"""
            SELECT {period} AS period, f.currency_code,
                   COALESCE(sum(f.net_sales_amount), 0) AS amount,
                   COALESCE(sum(f.cogs_amount) FILTER (WHERE f.cost_status IN ('costed', 'estimated')), 0) AS cogs,
                   COALESCE(sum(f.margin_amount) FILTER (WHERE f.cost_status IN ('costed', 'estimated')), 0) AS gross_margin
            FROM fact_sales_line f JOIN dim_date d ON d.date_key = f.date_key
            WHERE {where} GROUP BY period, f.currency_code ORDER BY period, f.currency_code
            """,
            params,
        )

    def _sales_breakdown(self, filters: AnalyticsFilters, kind: str) -> list[dict[str, Any]]:
        columns = {
            "product": ("LEFT JOIN dim_product dimension ON dimension.key = f.product_key", "dimension.name"),
            "seller": ("LEFT JOIN dim_seller dimension ON dimension.key = f.seller_key", "dimension.name"),
            "warehouse": ("LEFT JOIN dim_warehouse dimension ON dimension.key = f.warehouse_key", "dimension.name"),
            "customer": ("LEFT JOIN dim_contact dimension ON dimension.key = f.contact_key", "dimension.name"),
            "status": ("", "f.document_status"),
        }
        join, label = columns[kind]
        where, params = self._fact_where(filters, alias="f", allow_seller=True, allow_status=True)
        return self._rows(
            f"""
            SELECT COALESCE({label}, 'Sin dato') AS label, f.currency_code,
                   COALESCE(sum(f.net_sales_amount), 0) AS amount,
                   COALESCE(sum(f.quantity), 0) AS quantity,
                   COALESCE(sum(f.cogs_amount) FILTER (WHERE f.cost_status IN ('costed', 'estimated')), 0) AS cogs,
                   COALESCE(sum(f.margin_amount) FILTER (WHERE f.cost_status IN ('costed', 'estimated')), 0) AS gross_margin
            FROM fact_sales_line f JOIN dim_date d ON d.date_key = f.date_key
            {join}
            WHERE {where} GROUP BY label, f.currency_code
            ORDER BY amount DESC LIMIT 15
            """,
            params,
        )

    def _fact_where(
        self,
        filters: AnalyticsFilters,
        *,
        alias: str,
        allow_seller: bool,
        allow_status: bool,
        allow_currency: bool = True,
        allow_product: bool = True,
        allow_warehouse: bool = True,
        allow_provider: bool = False,
        allow_family: bool = True,
    ) -> tuple[str, dict[str, Any]]:
        clauses = [
            f"{alias}.tenant_id = :tenant_id",
            f"{alias}.is_deleted = false",
            "d.calendar_date >= :from_date",
            "d.calendar_date <= :to_date",
        ]
        params: dict[str, Any] = {
            "tenant_id": self._tenant_id,
            "from_date": filters.from_date,
            "to_date": filters.to_date,
        }
        for column, value, allowed in (
            ("currency_code", filters.currency, allow_currency),
            ("product_key", filters.product_key, allow_product),
            ("seller_key", filters.seller_key, allow_seller),
            ("warehouse_key", filters.warehouse_key, allow_warehouse),
            ("document_status", filters.document_status, allow_status),
            ("provider_key", filters.provider_key, allow_provider),
        ):
            if value is not None and allowed:
                parameter = column
                clauses.append(f"{alias}.{column} = :{parameter}")
                params[parameter] = value
        if filters.family is not None and allow_family:
            clauses.append(
                f"EXISTS (SELECT 1 FROM dim_product family_product "
                f"WHERE family_product.key = {alias}.product_key "
                "AND family_product.tenant_id = :tenant_id "
                "AND COALESCE(family_product.family_name, 'SIN FAMILIA') = :family)"
            )
            params["family"] = filters.family
        return " AND ".join(clauses), params

    def _dimension_options(self, table: str) -> list[dict[str, Any]]:
        return self._rows(
            f"""
            SELECT key AS value, name AS label FROM {table}
            WHERE tenant_id = :tenant_id AND is_deleted = false ORDER BY name LIMIT 500
            """
        )

    def _rows(self, statement: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        values = {"tenant_id": self._tenant_id} | (params or {})
        return [dict(row) for row in self._session.execute(text(statement), values).mappings()]

    def _one(self, statement: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        rows = self._rows(statement, params)
        return rows[0] if rows else None
