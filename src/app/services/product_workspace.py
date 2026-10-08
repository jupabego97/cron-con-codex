# ruff: noqa: E501
"""Product identity, commercial lifecycle, and traceable product drill-down."""

from dataclasses import replace

from app.core.business_time import business_today
from app.services.analytics_queries import AnalyticsFilters, AnalyticsQueryService
from app.services.operations_repository import OperationsRepository


class ProductWorkspaceService(OperationsRepository):
    def search(self, *, query: str = "", offset: int = 0, limit: int = 50) -> dict:
        rows = self.rows(
            """SELECT p.key,p.alegra_id,p.name,p.reference,p.family_name,
          COALESCE(b.lifecycle,'active') lifecycle,count(*) OVER() total
          FROM dim_product p LEFT JOIN product_business_profiles b
            ON b.tenant_id=p.tenant_id AND b.product_key=p.key
          WHERE p.tenant_id=:tenant_id AND p.is_deleted=false
            AND (p.name ILIKE :query OR COALESCE(p.reference,'') ILIKE :query)
          ORDER BY p.name,p.key LIMIT :limit OFFSET :offset""",
            {"query": f"%{query}%", "limit": limit, "offset": offset},
        )
        return {
            "items": rows,
            "total": rows[0]["total"] if rows else 0,
            "offset": offset,
            "limit": limit,
        }

    def detail(self, key: int, filters: AnalyticsFilters, *, offset: int = 0) -> dict:
        self.dimension("dim_product", key)
        profile = self.one(
            """SELECT p.key,p.name,p.reference,p.family_name,
          p.base_price,p.current_cost,p.alegra_id,COALESCE(b.lifecycle,'active') lifecycle,
          b.introduced_on,b.replacement_product_key,b.notes
          FROM dim_product p LEFT JOIN product_business_profiles b
            ON b.tenant_id=p.tenant_id AND b.product_key=p.key
          WHERE p.tenant_id=:tenant_id AND p.key=:key""",
            {"key": key},
        )
        analytics = AnalyticsQueryService(session=self.session, tenant_id=self.tenant_id)
        scoped = replace(filters, product_key=key)
        where, params = analytics._fact_where(
            scoped, alias="f", allow_seller=True, allow_status=True
        )
        documents = self.rows(
            f"""SELECT f.document_type,f.document_alegra_id,f.document_number,
          d.calendar_date,f.document_status,f.currency_code,sum(f.quantity) quantity,
          sum(f.net_sales_amount) net_sales,sum(f.margin_amount) margin,
          count(*) FILTER(WHERE f.cost_status IN ('costed','estimated')) costed_lines,
          count(*) line_count,count(*) OVER() total
          FROM fact_sales_line f JOIN dim_date d ON d.date_key=f.date_key
          WHERE {where} GROUP BY f.document_type,f.document_alegra_id,f.document_number,
            d.calendar_date,f.document_status,f.currency_code
          ORDER BY d.calendar_date DESC,f.document_alegra_id DESC LIMIT 50 OFFSET :offset""",
            {**params, "offset": offset},
        )
        purchase_where, purchase_params = analytics._fact_where(scoped, alias="f", allow_seller=False, allow_provider=True, allow_status=True)
        purchases = self.rows(
            f"""SELECT d.calendar_date,f.document_alegra_id,
          c.name supplier,f.currency_code,sum(f.quantity) quantity,
          sum(f.purchase_amount) amount,sum(f.purchase_amount)/NULLIF(sum(f.quantity),0) unit_cost
          FROM fact_purchase_line f JOIN dim_date d ON d.date_key=f.date_key
          LEFT JOIN dim_contact c ON c.tenant_id=f.tenant_id AND c.key=f.provider_key
          WHERE {purchase_where}
          GROUP BY d.calendar_date,f.document_alegra_id,c.name,f.currency_code
          ORDER BY d.calendar_date DESC,f.document_alegra_id DESC LIMIT 50""",
            purchase_params,
        )
        availability = self.rows(
            """SELECT a.observed_on,a.sample_count,a.positive_samples,
          a.last_quantity,a.captured_at FROM product_daily_availability a
          JOIN dim_product p ON p.tenant_id=a.tenant_id AND p.alegra_id=a.item_alegra_id
          WHERE a.tenant_id=:tenant_id AND p.key=:key
            AND a.observed_on BETWEEN :from_date AND :to_date
          ORDER BY a.observed_on DESC LIMIT 365""",
            {"key": key, "from_date": filters.from_date, "to_date": filters.to_date},
        )
        return {
            "profile": profile,
            "sales": analytics._sales_kpis(scoped),
            "stock": analytics.inventory(scoped).get("snapshot"),
            "documents": documents,
            "purchases": purchases,
            "availability": availability,
            "document_page": {
                "offset": offset,
                "limit": 50,
                "total": documents[0]["total"] if documents else 0,
            },
            "scope": {
                "from": filters.from_date,
                "to": filters.to_date,
                "metric_scope": filters.metric_scope,
                "stock": "Última captura, independiente del período de ventas",
            },
        }

    def update_profile(self, key: int, data: dict) -> dict:
        self.dimension("dim_product", key)
        self.dimension("dim_product", data.get("replacement_product_key"))
        if data.get("replacement_product_key") == key:
            raise ValueError("Un producto no puede reemplazarse a sí mismo")
        introduced = data.get("introduced_on")
        if introduced and introduced > business_today():
            raise ValueError("La primera disponibilidad no puede ser futura")
        old = self.one(
            "SELECT * FROM product_business_profiles WHERE tenant_id=:tenant_id AND product_key=:key",
            {"key": key},
        )
        row = self.one(
            """INSERT INTO product_business_profiles
          (tenant_id,product_key,lifecycle,introduced_on,replacement_product_key,notes)
          VALUES (:tenant_id,:key,:lifecycle,:introduced_on,:replacement_product_key,:notes)
          ON CONFLICT(tenant_id,product_key) DO UPDATE SET lifecycle=excluded.lifecycle,
            introduced_on=excluded.introduced_on,replacement_product_key=excluded.replacement_product_key,
            notes=excluded.notes,updated_at=now() RETURNING *""",
            {"key": key, **data},
        )
        self.audit("update_profile", "product", key, {"before": old, "after": row})
        self.session.commit()
        return row
