"""Exact source/output checks before skipping a FIFO rebuild under the tenant lock."""

import hashlib

from sqlalchemy import text


def _fingerprint(session, statements, params):
    parts = [session.execute(text(statement), params).scalar_one() for statement in statements]
    return hashlib.sha256(("fifo-source-order-v1|" + "|".join(parts)).encode()).hexdigest()


def source_fingerprint(session, *, tenant_id, cutoff_date, warehouse_key):
    statements = [
        """SELECT md5(COALESCE(jsonb_agg(jsonb_build_array(
            d.calendar_date,f.document_type,f.document_alegra_id,f.document_number,
            f.line_number,f.product_key,f.warehouse_key,f.quantity,f.net_sales_amount)
            ORDER BY f.document_type,f.document_alegra_id,f.line_number)::text,'[]'))
           FROM fact_sales_line f JOIN dim_date d ON d.date_key=f.date_key
           WHERE f.tenant_id=:tenant AND f.is_deleted=false
             AND d.calendar_date>=:cutoff AND f.quantity<>0""",
        """SELECT md5(COALESCE(jsonb_agg(jsonb_build_array(
            d.calendar_date,p.product_key,p.document_alegra_id,p.document_number,
            p.line_number,p.quantity,p.unit_cost,p.currency_code)
            ORDER BY p.document_alegra_id,p.line_number)::text,'[]'))
           FROM fact_purchase_line p JOIN dim_date d ON d.date_key=p.date_key
           WHERE p.tenant_id=:tenant AND p.is_deleted=false AND p.product_key IS NOT NULL
             AND p.quantity>0 AND p.unit_cost IS NOT NULL AND d.calendar_date>=:cutoff""",
        """SELECT md5(COALESCE(jsonb_agg(jsonb_build_array(
            l.id,m.id,l.product_key,l.warehouse_key,l.opened_on,l.original_quantity,
            l.unit_cost,m.source_type,m.source_id,m.source_line_number)
            ORDER BY l.id)::text,'[]'))
           FROM inventory_cost_layers l JOIN inventory_cost_movements m ON m.id=l.movement_id
           WHERE l.tenant_id=:tenant
             AND m.source_type NOT IN ('purchase_bill','sales_credit_note')""",
        "SELECT md5(CAST(:cutoff AS text)||'|'||CAST(:warehouse AS text))",
    ]
    return _fingerprint(
        session,
        statements,
        {
            "tenant": tenant_id,
            "cutoff": cutoff_date,
            "warehouse": warehouse_key,
        },
    )


def projection_fingerprint(session, *, tenant_id):
    statements = [
        """SELECT md5(COALESCE(jsonb_agg(jsonb_build_array(
            document_type,document_alegra_id,line_number,unit_cost,cogs_amount,
            margin_amount,cost_status,cost_confidence,cost_method)
            ORDER BY document_type,document_alegra_id,line_number)::text,'[]'))
           FROM fact_sales_line WHERE tenant_id=:tenant""",
        """SELECT md5(COALESCE(jsonb_agg(
            (to_jsonb(a)-ARRAY['id','run_id','created_at',
                             'source_movement_id','source_layer_id']) ||
            jsonb_build_object('source_type',m.source_type,'source_id',m.source_id,
                               'source_line_number',m.source_line_number)
            ORDER BY a.document_type,a.document_alegra_id,
                     a.line_number,a.allocation_sequence)::text,'[]'))
           FROM sales_cost_allocations a
           LEFT JOIN inventory_cost_movements m ON m.id=a.source_movement_id
           WHERE a.tenant_id=:tenant""",
        """SELECT md5(COALESCE(jsonb_agg(
            (to_jsonb(l)-ARRAY['id','movement_id']) || (to_jsonb(m)-ARRAY['id','created_at'])
            ORDER BY m.source_type,m.source_id,m.source_line_number,l.warehouse_key)::text,'[]'))
           FROM inventory_cost_layers l JOIN inventory_cost_movements m ON m.id=l.movement_id
           WHERE l.tenant_id=:tenant""",
    ]
    return _fingerprint(session, statements, {"tenant": tenant_id})
