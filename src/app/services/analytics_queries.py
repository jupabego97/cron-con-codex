# ruff: noqa: E501
"""Read-only query service for the tenant-scoped analytics data mart."""

from dataclasses import dataclass, replace
from datetime import date, timedelta
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

    @classmethod
    def default(cls) -> "AnalyticsFilters":
        today = date.today()
        return cls(from_date=today - timedelta(days=29), to_date=today)

    def previous_period(self) -> "AnalyticsFilters":
        days = (self.to_date - self.from_date).days + 1
        previous_to = self.from_date - timedelta(days=1)
        return replace(self, from_date=previous_to - timedelta(days=days - 1), to_date=previous_to)


class AnalyticsQueryService:
    def __init__(self, *, session: Session, tenant_id: UUID) -> None:
        self._session = session
        self._tenant_id = tenant_id

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
            "series": self._sales_series(filters),
            "by_product": self._sales_breakdown(filters, "product"),
            "by_seller": self._sales_breakdown(filters, "seller"),
            "by_warehouse": self._sales_breakdown(filters, "warehouse"),
            "by_customer": self._sales_breakdown(filters, "customer"),
            "by_status": self._sales_breakdown(filters, "status"),
        }

    def purchases(self, filters: AnalyticsFilters) -> dict[str, Any]:
        where, params = self._fact_where(filters, alias="f", allow_seller=False, allow_status=True)
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

    def payments(self, filters: AnalyticsFilters) -> dict[str, Any]:
        where, params = self._fact_where(
            filters,
            alias="f",
            allow_seller=False,
            allow_status=False,
            allow_product=False,
            allow_warehouse=False,
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
            "stock_coverage": self._stock_coverage(),
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
                   (finished_at IS NULL OR finished_at < now() - interval '2 hours') AS is_stale
            FROM mart_refresh_runs
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
                     NULLIF(count(DISTINCT (f.document_type, f.document_alegra_id)), 0), 0) AS average_ticket
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
                   COALESCE(sum(f.net_sales_amount), 0) AS amount
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
                   COALESCE(sum(f.quantity), 0) AS quantity
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
        ):
            if value is not None and allowed:
                parameter = column
                clauses.append(f"{alias}.{column} = :{parameter}")
                params[parameter] = value
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
