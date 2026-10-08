# ruff: noqa: E501
"""Audited service jobs, component costs, delivery and serial-based warranties."""

from datetime import timedelta

from app.core.business_time import business_today
from app.services.operations_repository import OperationsRepository

TRANSITIONS = {
    "received": {"diagnosing", "cancelled"},
    "diagnosing": {"awaiting_approval", "approved", "cancelled"},
    "awaiting_approval": {"approved", "cancelled"},
    "approved": {"in_progress", "cancelled"},
    "in_progress": {"ready", "cancelled"},
    "ready": {"delivered", "in_progress", "cancelled"},
    "delivered": set(),
    "cancelled": set(),
}


class RepairService(OperationsRepository):
    def list(self, filters, *, query: str = "", offset: int = 0) -> dict:
        items = self.rows(
            """SELECT j.*,COALESCE(p.cost,0) parts_cost,
          CASE WHEN j.status='delivered' AND j.costs_complete AND j.charged_amount IS NOT NULL AND j.labour_cost IS NOT NULL
            THEN j.charged_amount-j.labour_cost-COALESCE(p.cost,0) END recorded_margin,
          CASE WHEN j.delivered_on IS NOT NULL THEN j.delivered_on+j.warranty_days END warranty_until,
          count(*) OVER() total_rows
          FROM repair_jobs j LEFT JOIN (
            SELECT tenant_id,repair_id,sum(quantity*unit_cost) cost FROM repair_parts
            WHERE tenant_id=:tenant_id GROUP BY tenant_id,repair_id
          ) p ON p.tenant_id=j.tenant_id AND p.repair_id=j.id
          WHERE j.tenant_id=:tenant_id AND j.received_on BETWEEN :from_date AND :to_date
            AND (CAST(:currency AS text) IS NULL OR j.currency_code=:currency)
            AND (j.customer_name ILIKE :q OR j.device_description ILIKE :q OR COALESCE(j.serial_number,'') ILIKE :q)
          ORDER BY j.received_on DESC,j.id LIMIT 50 OFFSET :offset""",
            {
                "from_date": filters.from_date,
                "to_date": filters.to_date,
                "q": f"%{query}%",
                "offset": offset,
                "currency": filters.currency,
            },
        )
        queue = self.one(
            """SELECT count(*) FILTER(WHERE status NOT IN ('delivered','cancelled')) open_jobs,
          count(*) FILTER(WHERE status='awaiting_approval') awaiting_approval,
          count(*) FILTER(WHERE status NOT IN ('delivered','cancelled') AND promised_on<:today) overdue,
          count(*) FILTER(WHERE warranty_parent_id IS NOT NULL) warranty_returns
          FROM repair_jobs WHERE tenant_id=:tenant_id""",
            {"today": business_today()},
        )
        metrics = self.rows(
            """SELECT j.currency_code,count(*) delivered_jobs,
          avg(j.delivered_on-j.received_on) average_turnaround_days,
          sum(j.charged_amount) FILTER(WHERE j.charged_amount IS NOT NULL) recorded_revenue,
          sum(j.charged_amount-j.labour_cost-COALESCE(p.cost,0))
            FILTER(WHERE j.costs_complete AND j.charged_amount IS NOT NULL AND j.labour_cost IS NOT NULL) recorded_margin,
          count(*) FILTER(WHERE NOT j.costs_complete OR j.charged_amount IS NULL OR j.labour_cost IS NULL) missing_cost_or_revenue
          FROM repair_jobs j LEFT JOIN (
            SELECT tenant_id,repair_id,sum(quantity*unit_cost) cost FROM repair_parts
            WHERE tenant_id=:tenant_id GROUP BY tenant_id,repair_id
          ) p ON p.tenant_id=j.tenant_id AND p.repair_id=j.id
          WHERE j.tenant_id=:tenant_id AND j.status='delivered'
            AND j.delivered_on BETWEEN :from_date AND :to_date
            AND (CAST(:currency AS text) IS NULL OR j.currency_code=:currency)
            GROUP BY j.currency_code""",
            {"from_date": filters.from_date, "to_date": filters.to_date, "currency": filters.currency},
        )
        return {
            "items": items,
            "queue": queue,
            "metrics": metrics,
            "total": items[0]["total_rows"] if items else 0,
            "scope": "Lista por fecha de recepción; indicadores entregados por fecha de entrega. Pendientes abarcan todos los trabajos abiertos. Márgenes operativos de datos registrados; no se suman de nuevo a ventas de Alegra.",
        }

    def detail(self, job_id, *, lock=False) -> dict:
        job = self.one(
            "SELECT * FROM repair_jobs WHERE tenant_id=:tenant_id AND id=:id"
            + (" FOR UPDATE" if lock else ""),
            {"id": job_id},
        )
        if not job:
            raise LookupError("Trabajo no encontrado")
        job["parts"] = self.rows(
            "SELECT * FROM repair_parts WHERE tenant_id=:tenant_id AND repair_id=:id ORDER BY created_at",
            {"id": job_id},
        )
        job["history"] = self.rows(
            """SELECT action,details,created_at FROM operational_audit
          WHERE tenant_id=:tenant_id AND entity_type='repair' AND entity_id=:id
          ORDER BY created_at DESC LIMIT 100""",
            {"id": str(job_id)},
        )
        return job

    def create(self, data: dict) -> dict:
        existing = self.one(
            "SELECT * FROM repair_jobs WHERE tenant_id=:tenant_id AND id=:id", {"id": data["id"]}
        )
        if existing:
            if any(existing[key] != data[key] for key in data):
                raise ValueError("El identificador pertenece a otro trabajo")
            return existing
        self.dimension("dim_product", data.get("product_key"))
        self.dimension("dim_contact", data.get("contact_key"))
        if data["received_on"] > business_today():
            raise ValueError("La fecha de recepción no puede ser futura")
        parent = data.get("warranty_parent_id")
        if parent:
            original = self.detail(parent)
            if not original["delivered_on"] or not original["delivered_on"] <= data[
                "received_on"
            ] <= original["delivered_on"] + timedelta(days=original["warranty_days"]):
                raise ValueError("La garantía original no está vigente para esta fecha")
            if not original["serial_number"] or original["serial_number"] != data.get(
                "serial_number"
            ):
                raise ValueError("La garantía debe coincidir con el serial original")
        allowed = {
            "id",
            "customer_name",
            "customer_phone",
            "contact_key",
            "product_key",
            "device_description",
            "serial_number",
            "reported_issue",
            "currency_code",
            "warranty_days",
            "warranty_parent_id",
            "received_on",
            "promised_on",
        }
        if not set(data).issubset(allowed):
            raise ValueError("Campos de recepción inválidos")
        columns = ",".join(data)
        values = ",".join(f":{key}" for key in data)
        row = self.one(
            f"INSERT INTO repair_jobs(tenant_id,{columns}) VALUES (:tenant_id,{values}) RETURNING *",
            data,
        )
        self.audit("receive_device", "repair", data["id"], row)
        self.session.commit()
        return row

    def update(self, job_id, data: dict) -> dict:
        old = self.detail(job_id, lock=True)
        allowed = {
            "diagnosis",
            "status",
            "quoted_amount",
            "charged_amount",
            "labour_cost",
            "costs_complete",
            "promised_on",
            "delivered_on",
            "warranty_days",
            "invoice_alegra_id",
        }
        if not data or not set(data).issubset(allowed):
            raise ValueError("Cambios de trabajo inválidos")
        if data.get("costs_complete") and data.get("labour_cost", old["labour_cost"]) is None:
            raise ValueError("Registra la mano de obra antes de certificar los costos")
        status = data.get("status", old["status"])
        if status != old["status"] and status not in TRANSITIONS[old["status"]]:
            raise ValueError("La transición de estado no está permitida")
        if status == "delivered":
            delivered = data.get("delivered_on", old["delivered_on"]) or business_today()
            if not old["received_on"] <= delivered <= business_today():
                raise ValueError("La entrega debe ocurrir entre la recepción y hoy")
            data["delivered_on"] = delivered
        elif data.get("delivered_on") is not None:
            raise ValueError("La fecha de entrega requiere estado Entregado")
        invoice = data.get("invoice_alegra_id")
        if invoice and not self.one(
            "SELECT id FROM sales_invoices WHERE tenant_id=:tenant_id AND alegra_id=:invoice AND is_deleted=false",
            {"invoice": invoice},
        ):
            raise LookupError("Factura no encontrada en esta empresa")
        assignments = ",".join(f"{key}=:{key}" for key in data)
        row = self.one(
            f"UPDATE repair_jobs SET {assignments},updated_at=now() WHERE tenant_id=:tenant_id AND id=:id RETURNING *",
            {**data, "id": job_id},
        )
        self.audit(
            "update_job", "repair", job_id, {"changes": data, "previous_status": old["status"]}
        )
        self.session.commit()
        return row

    def add_part(self, job_id, data: dict) -> dict:
        job = self.detail(job_id, lock=True)
        if job["status"] in {"delivered", "cancelled"}:
            raise ValueError("No se modifican costos de un trabajo cerrado")
        self.dimension("dim_product", data.get("product_key"))
        existing = self.one(
            "SELECT * FROM repair_parts WHERE tenant_id=:tenant_id AND id=:id", {"id": data["id"]}
        )
        if existing:
            if existing["repair_id"] != job_id or any(existing[key] != data[key] for key in data):
                raise ValueError("El identificador de repuesto pertenece a otro registro")
            return existing
        row = self.one(
            """INSERT INTO repair_parts(id,tenant_id,repair_id,product_key,description,quantity,unit_cost)
          VALUES (:id,:tenant_id,:job,:product_key,:description,:quantity,:unit_cost) RETURNING *""",
            {**data, "job": job_id},
        )
        self.audit("register_part_cost", "repair", job_id, row)
        self.one("UPDATE repair_jobs SET costs_complete=false WHERE tenant_id=:tenant_id AND id=:id RETURNING id", {"id": job_id})
        self.session.commit()
        return row
