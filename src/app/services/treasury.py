# ruff: noqa: E501
"""Four-week cash scenarios from a certified balance and explicitly sourced flows."""

from datetime import date, timedelta
from decimal import Decimal

from app.core.business_time import BUSINESS_TIMEZONE, business_today
from app.services.operations_repository import OperationsRepository


def _due(value, fallback: date) -> date:
    try:
        return value if isinstance(value, date) else date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return fallback


class TreasuryService(OperationsRepository):
    def balance(self, data: dict) -> dict:
        if data["as_of_date"] > business_today():
            raise ValueError("Un saldo certificado no puede tener fecha futura")
        row = self.one(
            """INSERT INTO treasury_balances(tenant_id,currency_code,as_of_date,amount,notes)
          VALUES (:tenant_id,:currency_code,:as_of_date,:amount,:notes)
          ON CONFLICT(tenant_id,currency_code,as_of_date) DO UPDATE SET amount=excluded.amount,
            notes=excluded.notes,created_at=now() RETURNING *""",
            data,
        )
        self.audit("certify_closing_balance", "treasury", data["currency_code"], row)
        self.session.commit()
        return row

    def entry(self, data: dict) -> dict:
        if data["stage"] == "posted" and data["entry_date"] > business_today():
            raise ValueError("Un movimiento real no puede registrarse en el futuro")
        existing = self.one(
            "SELECT * FROM treasury_entries WHERE tenant_id=:tenant_id AND id=:id",
            {"id": data["id"]},
        )
        if existing:
            if any(existing[key] != data[key] for key in data):
                raise ValueError("Este identificador pertenece a otro movimiento")
            return existing
        row = self.one(
            """INSERT INTO treasury_entries
          (id,tenant_id,entry_date,currency_code,amount,direction,stage,category,description)
          VALUES (:id,:tenant_id,:entry_date,:currency_code,:amount,:direction,:stage,:category,:description)
          RETURNING *""",
            data,
        )
        self.audit("record_manual_cash_flow", "treasury_entry", data["id"], row)
        self.session.commit()
        return row

    def post_entry(self, entry_id) -> dict:
        old = self.one(
            "SELECT * FROM treasury_entries WHERE tenant_id=:tenant_id AND id=:id FOR UPDATE",
            {"id": entry_id},
        )
        if not old:
            raise LookupError("Movimiento no encontrado")
        if old["stage"] == "posted":
            return old
        row = self.one(
            """UPDATE treasury_entries SET stage='posted',entry_date=:today
          WHERE tenant_id=:tenant_id AND id=:id RETURNING *""",
            {"id": entry_id, "today": business_today()},
        )
        self.audit("post_cash_flow", "treasury_entry", entry_id, {"before": old, "after": row})
        self.session.commit()
        return row

    def projection(self, currency: str = "COP") -> dict:
        today = business_today()
        baseline = self.one(
            """SELECT * FROM treasury_balances WHERE tenant_id=:tenant_id
          AND currency_code=:currency AND as_of_date<=:today ORDER BY as_of_date DESC LIMIT 1""",
            {"currency": currency, "today": today},
        )
        payments = (
            self.one(
                """SELECT
          sum(CASE WHEN payment_type IN ('in','income') THEN amount
                   WHEN payment_type IN ('out','expense','outcome') THEN -amount END) flow,
          count(*) FILTER(WHERE payment_type IS NULL OR payment_type NOT IN ('in','out','income','expense','outcome')) unknown
          FROM payments WHERE tenant_id=:tenant_id AND is_deleted=false
            AND COALESCE(currency_code,'COP')=:currency
            AND payment_date>:opening AND payment_date<=:today""",
                {
                    "currency": currency,
                    "opening": baseline["as_of_date"] if baseline else today,
                    "today": today,
                },
            )
            or {}
        )
        entries = self.rows(
            """SELECT * FROM treasury_entries WHERE tenant_id=:tenant_id
          AND currency_code=:currency ORDER BY entry_date DESC,created_at DESC""",
            {"currency": currency},
        )
        actual = None
        if baseline:
            actual = baseline["amount"] + (payments.get("flow") or Decimal(0))
            actual += sum(
                (
                    row["amount"] * (1 if row["direction"] == "in" else -1)
                    for row in entries
                    if row["stage"] == "posted"
                    and baseline["as_of_date"] < row["entry_date"] <= today
                ),
                Decimal(0),
            )
        bills = self.rows(
            """SELECT alegra_id,document_number,provider_name,due_date,balance,currency_code
          FROM purchase_bills WHERE tenant_id=:tenant_id AND is_deleted=false AND status='open'
            AND COALESCE(currency_code,'COP')=:currency AND (balance>0 OR balance IS NULL)
          ORDER BY due_date NULLS FIRST,alegra_id""",
            {"currency": currency},
        )
        debtors = self.rows(
            """SELECT s.alegra_id,s.client_name,s.balance,
          (SELECT payload->>'dueDate' FROM raw_alegra_documents r WHERE r.tenant_id=s.tenant_id
            AND r.entity_type='invoice' AND r.external_id=s.alegra_id ORDER BY received_at DESC LIMIT 1) due_date
          FROM sales_invoices s WHERE s.tenant_id=:tenant_id AND s.is_deleted=false
            AND s.status='open' AND s.balance>0 AND COALESCE(s.currency_code,'COP')=:currency""",
            {"currency": currency},
        )
        commitments = self.rows(
            """SELECT po.alegra_id,po.provider_name,
          COALESCE(t.expected_on,po.delivery_date) due_date,
          GREATEST(po.total-COALESCE(sum(pb.total),0),0) amount
          FROM purchase_orders po LEFT JOIN purchase_order_tracking t
            ON t.tenant_id=po.tenant_id AND t.order_alegra_id=po.alegra_id
          LEFT JOIN purchase_bills pb ON pb.tenant_id=po.tenant_id
            AND pb.purchase_order_alegra_id=po.alegra_id AND pb.is_deleted=false AND pb.status<>'void'
          WHERE po.tenant_id=:tenant_id AND po.is_deleted=false AND po.status='open'
            AND COALESCE(po.currency_code,'COP')=:currency
            AND COALESCE(t.stage,'pending_confirmation') NOT IN ('closed','cancelled')
          GROUP BY po.alegra_id,po.provider_name,po.total,t.expected_on,po.delivery_date""",
            {"currency": currency},
        )
        conservative, expected = actual, actual
        weeks = []
        for week in range(4):
            start, stop = today + timedelta(days=week * 7), today + timedelta(days=week * 7 + 6)

            def in_week(value, start=start, stop=stop):
                return start <= max(_due(value, today), today) <= stop

            payables = sum(
                (
                    row["balance"]
                    for row in bills
                    if row["balance"] is not None and in_week(row["due_date"])
                ),
                Decimal(0),
            )
            receivables = sum(
                (row["balance"] for row in debtors if in_week(row["due_date"])), Decimal(0)
            )
            po_amount = sum(
                (row["amount"] or Decimal(0) for row in commitments if in_week(row["due_date"])),
                Decimal(0),
            )
            manual_out = sum(
                (
                    row["amount"]
                    for row in entries
                    if row["stage"] == "planned"
                    and row["direction"] == "out"
                    and in_week(row["entry_date"])
                ),
                Decimal(0),
            )
            manual_in = sum(
                (
                    row["amount"]
                    for row in entries
                    if row["stage"] == "planned"
                    and row["direction"] == "in"
                    and in_week(row["entry_date"])
                ),
                Decimal(0),
            )
            outgoing = payables + po_amount + manual_out
            conservative = None if conservative is None else conservative - outgoing
            expected = None if expected is None else expected + receivables + manual_in - outgoing
            weeks.append(
                {
                    "from_date": start,
                    "to_date": stop,
                    "payables": payables,
                    "estimated_po_commitments": po_amount,
                    "planned_operating_out": manual_out,
                    "estimated_receivables": receivables,
                    "planned_in": manual_in,
                    "conservative_balance": conservative,
                    "expected_balance": expected,
                }
            )
        warnings = []
        if baseline is None:
            warnings.append(
                "Registra un saldo total de caja y bancos certificado al cierre de una fecha; no se infiere a partir de ventas."
            )
        if payments.get("unknown"):
            warnings.append(
                "Hay pagos con dirección desconocida: no se incluyen en el saldo reconstruido."
            )
        if any(row["balance"] is None for row in bills):
            warnings.append(
                "Hay facturas de proveedor sin saldo pendiente; sus importes no se inventan."
            )
        if any(row["due_date"] is None for row in bills + debtors + commitments):
            warnings.append(
                "Los documentos sin vencimiento se ubican en la primera semana como escenario prudente."
            )
        sync = self.one(
            "SELECT max(finished_at) last_sync FROM sync_runs WHERE tenant_id=:tenant_id AND resource='payment' AND status='succeeded'"
        )
        if not sync or not sync["last_sync"] or sync["last_sync"].astimezone(BUSINESS_TIMEZONE).date() < today:
            warnings.append(
                "Pagos de Alegra sin sincronización exitosa hoy: el saldo puede estar incompleto."
            )
        return {
            "currency_code": currency,
            "as_of_date": today,
            "opening": baseline,
            "reconstructed_balance": actual,
            "weeks": weeks,
            "warnings": warnings,
            "available_for_purchases": None
            if conservative is None
            else max(conservative, Decimal(0)),
            "bills": bills[:100],
            "entries": entries[:100],
            "commitments": commitments[:100],
            "coverage": {
                "bill_count": len(bills),
                "entry_count": len(entries),
                "commitment_count": len(commitments),
            },
            "assumptions": "Cobros son estimaciones; pedidos no facturados son compromisos estimados. Los movimientos manuales son solo flujos no registrados en Alegra. El saldo certificado incluye todos los movimientos hasta el cierre de su fecha.",
        }
