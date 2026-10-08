# ruff: noqa: E501
"""Operational health and a small, actionable daily workspace."""

from datetime import UTC, datetime, timedelta

from app.core.business_time import business_today
from app.services.operations_repository import OperationsRepository


class OperationsHealthService(OperationsRepository):
    def status(self) -> dict:
        syncs = self.rows("""SELECT DISTINCT ON (resource) resource,status,started_at,
          finished_at,checkpoint_date,window_from,window_to,records_read,records_written
          FROM sync_runs WHERE tenant_id=:tenant_id ORDER BY resource,started_at DESC""")
        queue = self.rows("""SELECT status,count(*) count,min(created_at) oldest
          FROM inbound_events WHERE tenant_id=:tenant_id GROUP BY status""")
        snapshot = self.one("""SELECT id,status,started_at,finished_at,records_written
          FROM inventory_snapshot_runs WHERE tenant_id=:tenant_id
          ORDER BY started_at DESC LIMIT 1""")
        mart = self.one("""SELECT id,status,started_at,finished_at FROM mart_refresh_runs
          WHERE tenant_id=:tenant_id ORDER BY started_at DESC LIMIT 1""")
        backup = self.one("""SELECT verified_at,details FROM backup_verifications
          WHERE tenant_id=:tenant_id ORDER BY verified_at DESC LIMIT 1""")
        now = datetime.now(UTC)
        warnings = []
        for name, run in (("Inventario", snapshot), ("Mart", mart)):
            if not run or run["status"] != "succeeded" or not run["finished_at"]:
                warnings.append(f"{name}: sin última ejecución exitosa")
            elif now - run["finished_at"] > timedelta(hours=8):
                warnings.append(f"{name}: ejecución anterior a 8 horas")
        if any(item["status"] == "failed" and item["count"] for item in queue):
            warnings.append("Hay webhooks fallidos que requieren revisión")
        if not backup:
            warnings.append("No hay una restauración de respaldo verificada registrada")
        return {
            "syncs": syncs,
            "queue": queue,
            "snapshot": snapshot,
            "mart": mart,
            "backup": backup,
            "warnings": warnings,
        }

    def events(self, *, offset: int = 0) -> dict:
        rows = self.rows(
            """SELECT id,subject,entity_type,external_id,status,attempt_count,
          created_at,available_at,locked_at,count(*) OVER() total_rows
          FROM inbound_events WHERE tenant_id=:tenant_id AND status IN ('failed','retry_wait','processing')
          ORDER BY created_at DESC LIMIT 50 OFFSET :offset""",
            {"offset": offset},
        )
        return {"items": rows, "total": rows[0]["total_rows"] if rows else 0}

    def retry(self, event_id) -> dict:
        row = self.one(
            """SELECT id,status FROM inbound_events
          WHERE tenant_id=:tenant_id AND id=:id FOR UPDATE""",
            {"id": event_id},
        )
        if not row:
            raise LookupError("Evento no encontrado")
        if row["status"] != "failed":
            raise ValueError("Solo se reintentan manualmente eventos fallidos")
        self.rows(
            """UPDATE inbound_events SET status='retry_wait',attempt_count=0,
          available_at=now(),locked_at=NULL,lease_token=NULL,last_error=NULL
          WHERE tenant_id=:tenant_id AND id=:id RETURNING id""",
            {"id": event_id},
        )
        self.audit("retry_webhook", "inbound_event", event_id, row)
        self.session.commit()
        return {"id": event_id, "status": "retry_wait"}

    def today(self) -> dict:
        today = business_today()
        repairs = self.rows(
            """SELECT id,customer_name,device_description,promised_on,status
          FROM repair_jobs WHERE tenant_id=:tenant_id
          AND status NOT IN ('delivered','cancelled') AND promised_on<:today
          ORDER BY promised_on,id LIMIT 20""",
            {"today": today},
        )
        orders = self.rows(
            """SELECT p.alegra_id,p.document_number,p.provider_name,
          COALESCE(t.expected_on,p.delivery_date) expected_on
          FROM purchase_orders p LEFT JOIN purchase_order_tracking t
            ON t.tenant_id=p.tenant_id AND t.order_alegra_id=p.alegra_id
          WHERE p.tenant_id=:tenant_id AND p.is_deleted=false AND p.status='open'
            AND COALESCE(t.stage,'pending_confirmation') NOT IN ('closed','cancelled')
            AND COALESCE(t.expected_on,p.delivery_date)<:today
            AND EXISTS(SELECT 1 FROM purchase_order_lines l WHERE l.tenant_id=p.tenant_id
              AND l.document_alegra_id=p.alegra_id AND l.quantity>COALESCE((
                SELECT sum(r.accepted_quantity) FROM purchase_receipts r
                WHERE r.tenant_id=l.tenant_id AND r.order_alegra_id=l.document_alegra_id
                  AND r.line_number=l.line_number AND r.item_alegra_id IS NOT DISTINCT FROM l.item_alegra_id),0))
          ORDER BY expected_on LIMIT 20""",
            {"today": today},
        )
        products = self.rows(
            """WITH latest AS (
            SELECT id FROM inventory_snapshot_runs WHERE tenant_id=:tenant_id AND status='succeeded'
            ORDER BY finished_at DESC LIMIT 1
          ), stock AS (SELECT item_alegra_id,sum(quantity_on_hand) quantity
            FROM inventory_snapshots WHERE tenant_id=:tenant_id AND snapshot_run_id=(SELECT id FROM latest)
            GROUP BY item_alegra_id), demand AS (
            SELECT f.product_key,sum(f.quantity) units,max(d.calendar_date) last_sale
            FROM fact_sales_line f JOIN dim_date d ON d.date_key=f.date_key
            WHERE f.tenant_id=:tenant_id AND f.is_deleted=false AND f.document_status IN ('open','closed')
              AND d.calendar_date BETWEEN :first AND :today GROUP BY f.product_key)
          SELECT p.key,p.name,p.family_name,s.quantity,d.units,d.last_sale,
            CASE WHEN s.quantity<=0 AND d.units>0 THEN 'stockout'
              WHEN s.quantity>0 AND COALESCE(d.units,0)<=0 THEN 'no_recent_demand' END action
          FROM stock s JOIN dim_product p ON p.tenant_id=:tenant_id AND p.alegra_id=s.item_alegra_id
          LEFT JOIN demand d ON d.product_key=p.key
          LEFT JOIN product_business_profiles b ON b.tenant_id=p.tenant_id AND b.product_key=p.key
          WHERE p.is_deleted=false AND COALESCE(b.lifecycle,'active')='active'
            AND ((s.quantity<=0 AND d.units>0) OR (s.quantity>0 AND COALESCE(d.units,0)<=0))
          ORDER BY CASE WHEN s.quantity<=0 THEN 0 ELSE 1 END,d.units DESC NULLS LAST LIMIT 40""",
            {"today": today, "first": today - timedelta(days=90)},
        )
        return {
            "as_of_date": today,
            "products": products,
            "overdue_orders": orders,
            "overdue_repairs": repairs,
            "data": self.status(),
            "scope": "Acciones actuales; demanda comercial de 90 días. Sin venta no significa obsoleto: verifica fecha de introducción y disponibilidad.",
        }
