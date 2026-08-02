# ruff: noqa: E501
"""Idempotent PostgreSQL projection from operational tables into the data mart.

This service deliberately has no Alegra client: raw/current/operational tables
are the contract between ingestion and analytics. A refresh replaces a single
tenant's facts in one transaction, so reruns cannot duplicate measurements and
source deletions are reflected immediately.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.models import MartRefreshRun


@dataclass(frozen=True)
class MartRefreshResult:
    run_id: uuid.UUID
    status: str
    records_written: int


class AnalyticsMartService:
    def __init__(self, *, session: Session, default_currency_code: str = "COP") -> None:
        self._session = session
        self._default_currency_code = default_currency_code.strip().upper() or "COP"

    def refresh(self, *, tenant_id: uuid.UUID) -> MartRefreshResult:
        """Rebuild one tenant's facts from its typed operational projections."""
        run = MartRefreshRun(tenant_id=tenant_id)
        self._session.add(run)
        self._session.commit()

        try:
            self._session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(CAST(:tenant_id AS text)))"),
                {"tenant_id": str(tenant_id)},
            )
            params = {
                "tenant_id": tenant_id,
                "default_currency_code": self._default_currency_code,
            }
            written = sum(
                max(int(self._session.execute(statement, params).rowcount or 0), 0)
                for statement in _DIMENSIONS
            )
            for statement in _DELETE_FACTS:
                self._session.execute(statement, params)
            written += sum(
                max(int(self._session.execute(statement, params).rowcount or 0), 0)
                for statement in _FACTS
            )
            run.status = "succeeded"
            run.records_written = written
            run.finished_at = datetime.now(UTC)
            self._session.commit()
            return MartRefreshResult(run_id=run.id, status=run.status, records_written=written)
        except Exception as error:
            self._session.rollback()
            run.status = "failed"
            run.error_message = str(error)[:4000]
            run.finished_at = datetime.now(UTC)
            self._session.add(run)
            self._session.commit()
            raise


_DIMENSIONS = (
    text(
        """
        INSERT INTO dim_tenant (tenant_id, slug, name, updated_at)
        SELECT id, slug, name, now() FROM tenants WHERE id = :tenant_id
        ON CONFLICT (tenant_id) DO UPDATE
        SET slug = EXCLUDED.slug, name = EXCLUDED.name, updated_at = now()
        """
    ),
    text(
        """
        INSERT INTO dim_product
            (tenant_id, alegra_id, source_hash, is_deleted, name, reference, item_type,
             status, inventory_enabled, unit, base_price, current_cost, family_name,
             preferred_supplier_name, updated_at)
        SELECT tenant_id, alegra_id, source_hash, is_deleted, name, reference, item_type,
               status, inventory_enabled, unit, base_price, cost,
               COALESCE(NULLIF(family_name, ''), 'SIN FAMILIA'),
               NULLIF(preferred_supplier_name, ''), now()
        FROM catalog_items WHERE tenant_id = :tenant_id
        ON CONFLICT (tenant_id, alegra_id) DO UPDATE SET
          source_hash = EXCLUDED.source_hash, is_deleted = EXCLUDED.is_deleted,
          name = EXCLUDED.name, reference = EXCLUDED.reference, item_type = EXCLUDED.item_type,
          status = EXCLUDED.status, inventory_enabled = EXCLUDED.inventory_enabled,
          unit = EXCLUDED.unit, base_price = EXCLUDED.base_price,
          current_cost = EXCLUDED.current_cost, family_name = EXCLUDED.family_name,
          preferred_supplier_name = EXCLUDED.preferred_supplier_name, updated_at = now()
        """
    ),
    text(
        """
        INSERT INTO dim_contact
            (tenant_id, alegra_id, source_hash, is_deleted, name, identification, email,
             contact_type, status, updated_at)
        SELECT tenant_id, alegra_id, source_hash, is_deleted, name, identification, email,
               contact_type, status, now()
        FROM contacts WHERE tenant_id = :tenant_id
        ON CONFLICT (tenant_id, alegra_id) DO UPDATE SET
          source_hash = EXCLUDED.source_hash, is_deleted = EXCLUDED.is_deleted,
          name = EXCLUDED.name, identification = EXCLUDED.identification, email = EXCLUDED.email,
          contact_type = EXCLUDED.contact_type, status = EXCLUDED.status, updated_at = now()
        """
    ),
    text(
        """
        INSERT INTO dim_seller
            (tenant_id, alegra_id, source_hash, is_deleted, name, email, status, updated_at)
        SELECT tenant_id, alegra_id, source_hash, is_deleted, name, email, status, now()
        FROM sellers WHERE tenant_id = :tenant_id
        ON CONFLICT (tenant_id, alegra_id) DO UPDATE SET
          source_hash = EXCLUDED.source_hash, is_deleted = EXCLUDED.is_deleted,
          name = EXCLUDED.name, email = EXCLUDED.email, status = EXCLUDED.status, updated_at = now()
        """
    ),
    text(
        """
        INSERT INTO dim_warehouse
            (tenant_id, alegra_id, source_hash, is_deleted, name, status, description, updated_at)
        SELECT tenant_id, alegra_id, source_hash, is_deleted, name, status, description, now()
        FROM warehouses WHERE tenant_id = :tenant_id
        ON CONFLICT (tenant_id, alegra_id) DO UPDATE SET
          source_hash = EXCLUDED.source_hash, is_deleted = EXCLUDED.is_deleted,
          name = EXCLUDED.name, status = EXCLUDED.status, description = EXCLUDED.description,
          updated_at = now()
        """
    ),
    text(
        """
        WITH source_dates AS (
          SELECT issue_date AS calendar_date FROM sales_invoices WHERE tenant_id = :tenant_id
          UNION SELECT issue_date FROM purchase_bills WHERE tenant_id = :tenant_id
          UNION SELECT payment_date FROM payments WHERE tenant_id = :tenant_id
          UNION SELECT issue_date FROM credit_notes WHERE tenant_id = :tenant_id
          UNION SELECT adjustment_date FROM inventory_adjustments WHERE tenant_id = :tenant_id
          UNION SELECT transfer_date FROM warehouse_transfers WHERE tenant_id = :tenant_id
          UNION SELECT captured_at::date FROM inventory_snapshots WHERE tenant_id = :tenant_id
        )
        INSERT INTO dim_date
          (date_key, calendar_date, year, quarter, month, day, iso_week, day_of_week, is_weekend)
        SELECT to_char(calendar_date, 'YYYYMMDD')::integer, calendar_date,
               extract(year FROM calendar_date)::smallint,
               extract(quarter FROM calendar_date)::smallint,
               extract(month FROM calendar_date)::smallint,
               extract(day FROM calendar_date)::smallint,
               extract(week FROM calendar_date)::smallint,
               extract(isodow FROM calendar_date)::smallint,
               extract(isodow FROM calendar_date) IN (6, 7)
        FROM source_dates WHERE calendar_date IS NOT NULL
        ON CONFLICT (date_key) DO NOTHING
        """
    ),
)

_DELETE_FACTS = tuple(
    text(f"DELETE FROM {table} WHERE tenant_id = :tenant_id")
    for table in (
        "fact_inventory_movement",
        "fact_inventory_snapshot",
        "fact_payment",
        "fact_purchase_line",
        "fact_sales_line",
    )
)

_FACTS = (
    text(
        """
        INSERT INTO fact_sales_line
          (tenant_id, tenant_key, date_key, product_key, contact_key, seller_key, warehouse_key,
           document_type, document_alegra_id, document_number, document_status, line_number,
           currency_code, quantity, unit_price, discount_amount, tax_amount, net_sales_amount,
           unit_cost, margin_amount, is_deleted)
        SELECT si.tenant_id, dt.key,
               to_char(si.issue_date, 'YYYYMMDD')::integer, dp.key, dc.key, ds.key, NULL,
               'invoice', si.alegra_id, si.alegra_id, si.status, sil.line_number,
               COALESCE(si.currency_code, :default_currency_code), COALESCE(sil.quantity, 0), sil.unit_price, 0, 0,
               COALESCE(sil.line_total, 0), NULL::numeric(18, 2), NULL::numeric(18, 2),
               si.is_deleted
        FROM sales_invoices si
        JOIN sales_invoice_lines sil ON sil.invoice_id = si.id
        JOIN dim_tenant dt ON dt.tenant_id = si.tenant_id
        LEFT JOIN dim_product dp ON dp.tenant_id = si.tenant_id AND dp.alegra_id = sil.item_alegra_id
        LEFT JOIN dim_contact dc ON dc.tenant_id = si.tenant_id AND dc.alegra_id = si.client_alegra_id
        LEFT JOIN dim_seller ds ON ds.tenant_id = si.tenant_id AND ds.alegra_id = si.seller_alegra_id
        WHERE si.tenant_id = :tenant_id
        UNION ALL
        SELECT cn.tenant_id, dt.key,
               to_char(cn.issue_date, 'YYYYMMDD')::integer, dp.key, dc.key, NULL, dw.key,
               'credit_note', cn.alegra_id, cn.document_number, cn.status, cnl.line_number,
               COALESCE(cn.currency_code, :default_currency_code), -COALESCE(cnl.quantity, 0), cnl.unit_price, 0, 0,
               -COALESCE(cnl.line_total, 0), NULL::numeric(18, 2), NULL::numeric(18, 2),
               cn.is_deleted
        FROM credit_notes cn
        JOIN credit_note_lines cnl
          ON cnl.tenant_id = cn.tenant_id AND cnl.document_alegra_id = cn.alegra_id
        JOIN dim_tenant dt ON dt.tenant_id = cn.tenant_id
        LEFT JOIN dim_product dp ON dp.tenant_id = cn.tenant_id AND dp.alegra_id = cnl.item_alegra_id
        LEFT JOIN dim_contact dc ON dc.tenant_id = cn.tenant_id AND dc.alegra_id = cn.client_alegra_id
        LEFT JOIN dim_warehouse dw ON dw.tenant_id = cn.tenant_id AND dw.alegra_id = cn.warehouse_alegra_id
        WHERE cn.tenant_id = :tenant_id
        """
    ),
    text(
        """
        INSERT INTO fact_purchase_line
          (tenant_id, tenant_key, date_key, product_key, provider_key, warehouse_key,
           document_alegra_id, document_number, document_status, line_number, currency_code,
           quantity, unit_cost, purchase_amount, is_deleted)
        SELECT pb.tenant_id, dt.key, to_char(pb.issue_date, 'YYYYMMDD')::integer,
               dp.key, dc.key, NULL, pb.alegra_id, pb.document_number, pb.status,
               pbl.line_number, COALESCE(pb.currency_code, :default_currency_code), COALESCE(pbl.quantity, 0), pbl.unit_price,
               COALESCE(pbl.line_total, 0), pb.is_deleted
        FROM purchase_bills pb
        JOIN purchase_bill_lines pbl
          ON pbl.tenant_id = pb.tenant_id AND pbl.document_alegra_id = pb.alegra_id
        JOIN dim_tenant dt ON dt.tenant_id = pb.tenant_id
        LEFT JOIN dim_product dp ON dp.tenant_id = pb.tenant_id AND dp.alegra_id = pbl.item_alegra_id
        LEFT JOIN dim_contact dc ON dc.tenant_id = pb.tenant_id AND dc.alegra_id = pb.provider_alegra_id
        WHERE pb.tenant_id = :tenant_id
        """
    ),
    text(
        """
        INSERT INTO fact_payment
          (tenant_id, tenant_key, date_key, contact_key, payment_alegra_id, payment_type,
           document_number, currency_code, amount, is_deleted)
        SELECT p.tenant_id, dt.key, to_char(p.payment_date, 'YYYYMMDD')::integer,
               dc.key, p.alegra_id, p.payment_type, p.document_number, COALESCE(p.currency_code, :default_currency_code),
               COALESCE(p.amount, 0), p.is_deleted
        FROM payments p
        JOIN dim_tenant dt ON dt.tenant_id = p.tenant_id
        LEFT JOIN dim_contact dc ON dc.tenant_id = p.tenant_id AND dc.alegra_id = p.contact_alegra_id
        WHERE p.tenant_id = :tenant_id
        """
    ),
    text(
        """
        INSERT INTO fact_inventory_snapshot
          (tenant_id, tenant_key, date_key, product_key, warehouse_key, snapshot_run_id,
           captured_at, quantity_on_hand, unit_cost, inventory_value)
        SELECT snapshot.tenant_id, tenant.key,
               to_char(snapshot.captured_at::date, 'YYYYMMDD')::integer,
               product.key, warehouse.key, snapshot.snapshot_run_id, snapshot.captured_at,
               snapshot.quantity_on_hand, snapshot.unit_cost,
               snapshot.quantity_on_hand * snapshot.unit_cost
        FROM inventory_snapshots snapshot
        JOIN dim_tenant tenant ON tenant.tenant_id = snapshot.tenant_id
        LEFT JOIN dim_product product
          ON product.tenant_id = snapshot.tenant_id AND product.alegra_id = snapshot.item_alegra_id
        LEFT JOIN dim_warehouse warehouse
          ON warehouse.tenant_id = snapshot.tenant_id AND warehouse.alegra_id = snapshot.warehouse_alegra_id
        WHERE snapshot.tenant_id = :tenant_id
        """
    ),
    text(
        """
        INSERT INTO fact_inventory_movement
          (tenant_id, tenant_key, date_key, product_key, warehouse_key, source_warehouse_key,
           destination_warehouse_key, document_type, document_alegra_id, document_number,
           line_number, movement_direction, quantity_delta, unit_cost, is_deleted)
        SELECT ia.tenant_id, dt.key, to_char(ia.adjustment_date, 'YYYYMMDD')::integer,
               dp.key, dw.key, NULL, NULL, 'inventory_adjustment', ia.alegra_id,
               ia.document_number, ial.line_number, 'adjustment', COALESCE(ial.quantity, 0),
               ial.unit_price, ia.is_deleted
        FROM inventory_adjustments ia
        JOIN inventory_adjustment_lines ial
          ON ial.tenant_id = ia.tenant_id AND ial.document_alegra_id = ia.alegra_id
        JOIN dim_tenant dt ON dt.tenant_id = ia.tenant_id
        LEFT JOIN dim_product dp ON dp.tenant_id = ia.tenant_id AND dp.alegra_id = ial.item_alegra_id
        LEFT JOIN dim_warehouse dw ON dw.tenant_id = ia.tenant_id AND dw.alegra_id = ia.warehouse_alegra_id
        WHERE ia.tenant_id = :tenant_id
        UNION ALL
        SELECT wt.tenant_id, dt.key, to_char(wt.transfer_date, 'YYYYMMDD')::integer,
               dp.key, source_dw.key, source_dw.key, destination_dw.key,
               'warehouse_transfer', wt.alegra_id, wt.document_number, wtl.line_number,
               'transfer_out', -COALESCE(wtl.quantity, 0), wtl.unit_price, wt.is_deleted
        FROM warehouse_transfers wt
        JOIN warehouse_transfer_lines wtl
          ON wtl.tenant_id = wt.tenant_id AND wtl.document_alegra_id = wt.alegra_id
        JOIN dim_tenant dt ON dt.tenant_id = wt.tenant_id
        LEFT JOIN dim_product dp ON dp.tenant_id = wt.tenant_id AND dp.alegra_id = wtl.item_alegra_id
        LEFT JOIN dim_warehouse source_dw
          ON source_dw.tenant_id = wt.tenant_id AND source_dw.alegra_id = wt.source_warehouse_alegra_id
        LEFT JOIN dim_warehouse destination_dw
          ON destination_dw.tenant_id = wt.tenant_id AND destination_dw.alegra_id = wt.destination_warehouse_alegra_id
        WHERE wt.tenant_id = :tenant_id
        UNION ALL
        SELECT wt.tenant_id, dt.key, to_char(wt.transfer_date, 'YYYYMMDD')::integer,
               dp.key, destination_dw.key, source_dw.key, destination_dw.key,
               'warehouse_transfer', wt.alegra_id, wt.document_number, wtl.line_number,
               'transfer_in', COALESCE(wtl.quantity, 0), wtl.unit_price, wt.is_deleted
        FROM warehouse_transfers wt
        JOIN warehouse_transfer_lines wtl
          ON wtl.tenant_id = wt.tenant_id AND wtl.document_alegra_id = wt.alegra_id
        JOIN dim_tenant dt ON dt.tenant_id = wt.tenant_id
        LEFT JOIN dim_product dp ON dp.tenant_id = wt.tenant_id AND dp.alegra_id = wtl.item_alegra_id
        LEFT JOIN dim_warehouse source_dw
          ON source_dw.tenant_id = wt.tenant_id AND source_dw.alegra_id = wt.source_warehouse_alegra_id
        LEFT JOIN dim_warehouse destination_dw
          ON destination_dw.tenant_id = wt.tenant_id AND destination_dw.alegra_id = wt.destination_warehouse_alegra_id
        WHERE wt.tenant_id = :tenant_id
        """
    ),
)
