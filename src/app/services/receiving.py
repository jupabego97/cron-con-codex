# ruff: noqa: E501
"""Track supplier confirmations and physical receipts without changing ERP stock."""

from collections import defaultdict
from decimal import Decimal
from uuid import UUID

from app.core.business_time import business_today
from app.domain.batch_repository import persist_resource_batch
from app.services.operations_repository import OperationsRepository


class ReceivingService(OperationsRepository):
    def orders(self, *, query: str = "", offset: int = 0) -> dict:
        rows = self.rows(
            """SELECT po.alegra_id,po.document_number,po.provider_name,
          po.order_date,po.delivery_date,po.currency_code,po.total,
          COALESCE(t.stage,'pending_confirmation') stage,t.expected_on,
          count(*) OVER() total_rows
          FROM purchase_orders po LEFT JOIN purchase_order_tracking t
            ON t.tenant_id=po.tenant_id AND t.order_alegra_id=po.alegra_id
          WHERE po.tenant_id=:tenant_id AND po.is_deleted=false
            AND po.status<>'void'
            AND (COALESCE(po.provider_name,'') ILIKE :q OR COALESCE(po.document_number,po.alegra_id) ILIKE :q)
          ORDER BY CASE WHEN COALESCE(t.stage,'pending_confirmation') IN ('closed','cancelled') THEN 1 ELSE 0 END,
            po.order_date DESC,po.alegra_id DESC LIMIT 50 OFFSET :offset""",
            {"q": f"%{query}%", "offset": offset},
        )
        uncertain = self.rows("""SELECT o.id,o.plan_id,o.status,c.name supplier,o.estimated_value
          FROM purchase_plan_orders o JOIN dim_contact c ON c.tenant_id=o.tenant_id AND c.key=o.supplier_key
          WHERE o.tenant_id=:tenant_id AND o.status IN ('submitting','unknown')
            AND o.alegra_order_id IS NULL ORDER BY o.id LIMIT 50""")
        return {
            "items": rows,
            "uncertain": uncertain,
            "total": rows[0]["total_rows"] if rows else 0,
            "offset": offset,
        }

    def detail(self, order_id: str, *, lock: bool = False) -> dict:
        order = self.one(
            "SELECT * FROM purchase_orders WHERE tenant_id=:tenant_id AND alegra_id=:id AND is_deleted=false"
            + (" FOR UPDATE" if lock else ""),
            {"id": order_id},
        )
        if not order:
            raise LookupError("Orden de compra no encontrada")
        # Operational responses contain typed fields, never Alegra payloads.
        order.pop("payload", None)
        order["tracking"] = self.one(
            "SELECT * FROM purchase_order_tracking WHERE tenant_id=:tenant_id AND order_alegra_id=:id",
            {"id": order_id},
        )
        order["lines"] = self.rows(
            """SELECT l.line_number,l.item_alegra_id,l.item_name,l.quantity,
          l.unit_price,COALESCE(sum(r.accepted_quantity),0) accepted_quantity,
          COALESCE(sum(r.rejected_quantity),0) rejected_quantity,max(r.received_on) last_received_on
          FROM purchase_order_lines l LEFT JOIN purchase_receipts r
            ON r.tenant_id=l.tenant_id AND r.order_alegra_id=l.document_alegra_id AND r.line_number=l.line_number
            AND r.item_alegra_id IS NOT DISTINCT FROM l.item_alegra_id
          WHERE l.tenant_id=:tenant_id AND l.document_alegra_id=:id
          GROUP BY l.line_number,l.item_alegra_id,l.item_name,l.quantity,l.unit_price ORDER BY l.line_number""",
            {"id": order_id},
        )
        order["receipts"] = self.rows(
            "SELECT * FROM purchase_receipts WHERE tenant_id=:tenant_id AND order_alegra_id=:id ORDER BY received_on DESC,created_at DESC",
            {"id": order_id},
        )
        current_refs = {(line["line_number"], line["item_alegra_id"]) for line in order["lines"]}
        order["unmatched_receipts"] = sum((r["line_number"], r["item_alegra_id"]) not in current_refs for r in order["receipts"])
        order["receipt_status"] = (
            "complete"
            if order["lines"]
            and all(line["accepted_quantity"] >= (line["quantity"] or 0) for line in order["lines"])
            else "partial"
            if any(line["accepted_quantity"] > 0 for line in order["lines"])
            else "pending"
        )
        return order

    def track(self, order_id: str, data: dict) -> dict:
        order = self.detail(order_id, lock=True)
        if (
            data["stage"] in {"closed", "cancelled"}
            and order["receipt_status"] != "complete"
            and not data.get("notes")
        ):
            raise ValueError("Indica por qué se cancela o cierra la cantidad pendiente")
        row = self.one(
            """INSERT INTO purchase_order_tracking
          (tenant_id,order_alegra_id,stage,expected_on,notes,confirmed_at)
          VALUES (:tenant_id,:id,:stage,:expected_on,:notes,
            CASE WHEN :stage IN ('confirmed','in_transit') THEN now() END)
          ON CONFLICT(tenant_id,order_alegra_id) DO UPDATE SET stage=excluded.stage,
            expected_on=excluded.expected_on,notes=excluded.notes,
            confirmed_at=COALESCE(purchase_order_tracking.confirmed_at,excluded.confirmed_at),
            updated_at=now() RETURNING *""",
            {"id": order_id, **data},
        )
        self.audit(
            "supplier_tracking",
            "purchase_order",
            order_id,
            {"before": order["tracking"], "after": row},
        )
        self.session.commit()
        return row

    def receive(self, order_id: str, data: dict) -> dict:
        order = self.detail(order_id, lock=True)
        existing = self.one(
            "SELECT * FROM purchase_receipts WHERE tenant_id=:tenant_id AND id=:id",
            {"id": data["id"]},
        )
        if existing:
            if (
                any(
                    existing.get(key) != data.get(key)
                    for key in (
                        "line_number",
                        "received_on",
                        "accepted_quantity",
                        "rejected_quantity",
                        "notes",
                    )
                )
                or existing["order_alegra_id"] != order_id
            ):
                raise ValueError("El identificador de recepción ya pertenece a otro registro")
            return existing
        if order.get("tracking") and order["tracking"]["stage"] in {"closed", "cancelled"}:
            raise ValueError("La orden está cerrada o cancelada")
        line = next(
            (line for line in order["lines"] if line["line_number"] == data["line_number"]), None
        )
        if line is None:
            raise LookupError("Línea de orden no encontrada")
        if data["received_on"] > business_today() or (
            order["order_date"] and data["received_on"] < order["order_date"]
        ):
            raise ValueError("La recepción debe ocurrir entre la fecha del pedido y hoy")
        if data["accepted_quantity"] + line["accepted_quantity"] > (line["quantity"] or 0):
            raise ValueError("La cantidad aceptada excede la cantidad pedida pendiente")
        if data["accepted_quantity"] + data["rejected_quantity"] <= 0:
            raise ValueError("Registra alguna unidad recibida o rechazada")
        row = self.one(
            """INSERT INTO purchase_receipts
          (id,tenant_id,order_alegra_id,line_number,item_alegra_id,received_on,accepted_quantity,rejected_quantity,notes)
          VALUES (:id,:tenant_id,:order,:line_number,:item,:received_on,:accepted_quantity,:rejected_quantity,:notes) RETURNING *""",
            {**data, "order": order_id, "item": line["item_alegra_id"]},
        )
        self.audit("physical_receipt", "purchase_order", order_id, row)
        self.session.commit()
        return row

    def resolve(self, order_row_id: UUID, payload: dict) -> dict:
        row = self.one(
            "SELECT * FROM purchase_plan_orders WHERE tenant_id=:tenant_id AND id=:id FOR UPDATE",
            {"id": order_row_id},
        )
        if not row:
            raise LookupError("Envío no encontrado")
        remote = payload.get("purchaseOrder") or payload
        remote_id = str(remote.get("id") or "")
        if row.get("alegra_order_id"):
            if row["alegra_order_id"] != remote_id:
                raise ValueError("El envío ya está vinculado a otra orden")
            return row
        if row["status"] not in {"submitting", "unknown"}:
            raise ValueError("Solo se pueden resolver envíos de resultado incierto")
        supplier = self.one(
            "SELECT alegra_id FROM dim_contact WHERE tenant_id=:tenant_id AND key=:key",
            {"key": row["supplier_key"]},
        )
        provider = remote.get("provider")
        provider_id = str(provider.get("id")) if isinstance(provider, dict) else str(provider)
        if (
            not supplier
            or provider_id != supplier["alegra_id"]
            or str(row["plan_id"]) not in str(remote.get("observations", ""))
        ):
            raise ValueError(
                "La orden de Alegra no coincide con el proveedor y la referencia del plan"
            )
        expected = self.rows(
            """SELECT p.alegra_id,sum(l.approved_quantity) quantity
          FROM purchase_plan_lines l JOIN dim_product p ON p.tenant_id=l.tenant_id AND p.key=l.product_key
          WHERE l.tenant_id=:tenant_id AND l.plan_id=:plan AND l.supplier_key=:supplier AND l.decision='approved'
          GROUP BY p.alegra_id""",
            {"plan": row["plan_id"], "supplier": row["supplier_key"]},
        )
        quantities = defaultdict(Decimal)
        for item in (remote.get("purchases") or {}).get("items", []):
            ref = item.get("item")
            key = str(ref.get("id")) if isinstance(ref, dict) else str(ref)
            quantities[key] += Decimal(str(item.get("quantity") or 0))
        if not remote_id or dict(quantities) != {
            str(item["alegra_id"]): item["quantity"] for item in expected
        }:
            raise ValueError("Las referencias y cantidades de Alegra no coinciden con el plan")
        if self.one(
            "SELECT id FROM purchase_plan_orders WHERE tenant_id=:tenant_id AND alegra_order_id=:remote AND id<>:id",
            {"remote": remote_id, "id": order_row_id},
        ):
            raise ValueError("Esta orden ya está vinculada a otro envío")
        persist_resource_batch(
            self.session, tenant_id=self.tenant_id, resource="purchase_order", payloads=[remote]
        )
        linked = self.one(
            """UPDATE purchase_plan_orders SET status='submitted',alegra_order_id=:remote,
          submitted_at=now() WHERE tenant_id=:tenant_id AND id=:id RETURNING id,plan_id,status,alegra_order_id""",
            {"remote": remote_id, "id": order_row_id},
        )
        self.audit("resolve_remote_order", "purchase_plan_order", order_row_id, linked)
        self.rows("""UPDATE purchase_plan_runs SET status='submitted'
          WHERE tenant_id=:tenant_id AND id=:plan
            AND NOT EXISTS(SELECT 1 FROM purchase_plan_orders
              WHERE tenant_id=:tenant_id AND plan_id=:plan AND status<>'submitted')
          RETURNING id""", {"plan": row["plan_id"]})
        self.session.commit()
        return linked
