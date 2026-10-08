"""Incremental writes against a complete, typed source projection.

Document comparisons deliberately do not depend on ingestion timestamps/hashes:
line repairs, deletions, currency changes and late dimensions must also propagate.
Immutable inventory runs have a transactional completion ledger instead, so their
historical rows are inspected once, not copied at every refresh.
"""

import re
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import TextClause


@dataclass(frozen=True)
class FactProjection:
    table: str
    columns: tuple[str, ...]
    select_sql: str

    @classmethod
    def from_insert(cls, statement: TextClause) -> "FactProjection":
        # Only trusted, checked-in SQL is accepted. Fail closed if its shape changes.
        match = re.fullmatch(
            r"\s*INSERT INTO (fact_[a-z_]+)\s*\(([^)]+)\)\s*(SELECT\b.*)",
            str(statement),
            re.DOTALL,
        )
        if match is None:
            raise ValueError("Unsupported mart projection SQL")
        columns = tuple(column.strip() for column in match[2].split(","))
        if not all(re.fullmatch(r"[a-z_]+", column) for column in columns):
            raise ValueError("Unsupported mart projection columns")
        return cls(match[1], columns, match[3].strip())

    def stage(self, session: Session, params: dict, *, extra_filter: str = "") -> str:
        name = f"mart_stage_{self.table}"
        session.execute(text(f"DROP TABLE IF EXISTS pg_temp.{name}"))
        session.execute(
            text(
                f"CREATE TEMP TABLE {name} ON COMMIT DROP AS "
                f"SELECT {','.join(self.columns)} FROM {self.table} WITH NO DATA"
            ),
            params,
        )
        # Reuse destination types, including numeric rounding and typed NULLs.
        # Inferred CTAS types would turn a NULL warehouse into text and compare
        # unrounded inventory valuations against the stored numeric(18,2).
        session.execute(text(
            f"INSERT INTO {name} ({','.join(self.columns)}) "
            f"{self.select_sql} {extra_filter}"
        ), params)
        session.execute(text(f"ANALYZE {name}"))
        return name


_DOCUMENT_KEYS = {
    "fact_sales_line": ("tenant_id", "document_type", "document_alegra_id", "line_number"),
    "fact_purchase_line": ("tenant_id", "document_alegra_id", "line_number"),
    "fact_payment": ("tenant_id", "payment_alegra_id"),
    "fact_inventory_movement": (
        "tenant_id", "document_type", "document_alegra_id", "line_number", "movement_direction"
    ),
}


def project_documents(session: Session, statement: TextClause, params: dict) -> int:
    projection = FactProjection.from_insert(statement)
    stage = projection.stage(session, params)
    keys = _DOCUMENT_KEYS[projection.table]
    session.execute(text(f"CREATE UNIQUE INDEX ON {stage} ({','.join(keys)})"))
    equality = " AND ".join(f"s.{key}=f.{key}" for key in keys)
    # Enriched costs are not source measurements. Preserve them, and the identity
    # key, for unchanged rows; the existing FIFO job handles changed cost bases.
    enriched = {"unit_cost", "margin_amount"} if projection.table == "fact_sales_line" else set()
    updated = [column for column in projection.columns if column not in {*keys, *enriched}]
    assignments = ",".join(f"{column}=EXCLUDED.{column}" for column in updated)
    current = ",".join(f"f.{column}" for column in updated)
    proposed = ",".join(f"EXCLUDED.{column}" for column in updated)
    source_columns = ",".join(f"s.{column}" for column in projection.columns)
    existing_join = " AND ".join(f"s.{key}=existing.{key}" for key in keys)
    existing_values = ",".join(f"existing.{column}" for column in updated)
    source_values = ",".join(f"s.{column}" for column in updated)
    deleted = session.execute(
        text(
            f"DELETE FROM {projection.table} f WHERE f.tenant_id=:tenant_id "
            f"AND NOT EXISTS (SELECT 1 FROM {stage} s WHERE {equality})"
        ),
        params,
    ).rowcount
    written = session.execute(
        text(
            f"INSERT INTO {projection.table} AS f ({','.join(projection.columns)}) "
            f"SELECT {source_columns} FROM {stage} s "
            f"LEFT JOIN {projection.table} existing ON {existing_join} "
            f"WHERE existing.key IS NULL OR ROW({existing_values}) "
            f"IS DISTINCT FROM ROW({source_values}) "
            f"ON CONFLICT ({','.join(keys)}) DO UPDATE SET {assignments} "
            f"WHERE ROW({current}) IS DISTINCT FROM ROW({proposed})"
        ),
        params,
    ).rowcount
    return max(deleted or 0, 0) + max(written or 0, 0)


def prepare_snapshot_runs(session: Session, params: dict) -> None:
    session.execute(text("DROP TABLE IF EXISTS pg_temp.mart_pending_snapshot_runs"))
    session.execute(
        text("""
          CREATE TEMP TABLE mart_pending_snapshot_runs ON COMMIT DROP AS
          SELECT r.id AS snapshot_run_id
          FROM inventory_snapshot_runs r
          LEFT JOIN mart_inventory_snapshot_runs done
            ON done.tenant_id=r.tenant_id AND done.snapshot_run_id=r.id
          WHERE r.tenant_id=:tenant_id AND r.status='succeeded'
            AND (done.snapshot_run_id IS NULL OR :full_refresh
                 OR (:dimensions_changed AND done.has_unresolved_dimensions))
        """),
        params,
    )
    session.execute(text(
        "CREATE UNIQUE INDEX ON mart_pending_snapshot_runs(snapshot_run_id)"
    ))


def project_snapshots(session: Session, statement: TextClause, params: dict) -> int:
    pending = session.execute(
        text("SELECT EXISTS(SELECT 1 FROM mart_pending_snapshot_runs)")
    ).scalar()
    if not pending:
        return 0
    projection = FactProjection.from_insert(statement)
    stage = projection.stage(
        session, params,
        extra_filter=(
            "AND snapshot.snapshot_run_id IN "
            "(SELECT snapshot_run_id FROM mart_pending_snapshot_runs)"
        ),
    )
    columns = ",".join(projection.columns)
    session.execute(text("DROP TABLE IF EXISTS pg_temp.mart_changed_snapshot_runs"))
    # EXCEPT ALL compares the complete multiset, including NULL dimensional keys.
    # A bootstrap can adopt existing matching runs WITHOUT deleting their history.
    session.execute(text(f"""
      CREATE TEMP TABLE mart_changed_snapshot_runs ON COMMIT DROP AS
      SELECT DISTINCT snapshot_run_id FROM (
        (SELECT {columns} FROM {stage}
         EXCEPT ALL
         SELECT {columns} FROM fact_inventory_snapshot
         WHERE tenant_id=:tenant_id AND snapshot_run_id IN
           (SELECT snapshot_run_id FROM mart_pending_snapshot_runs))
        UNION ALL
        (SELECT {columns} FROM fact_inventory_snapshot
         WHERE tenant_id=:tenant_id AND snapshot_run_id IN
           (SELECT snapshot_run_id FROM mart_pending_snapshot_runs)
         EXCEPT ALL
         SELECT {columns} FROM {stage})
      ) differences
    """), params)
    deleted = session.execute(text("""
      DELETE FROM fact_inventory_snapshot WHERE tenant_id=:tenant_id
        AND snapshot_run_id IN (SELECT snapshot_run_id FROM mart_changed_snapshot_runs)
    """), params).rowcount
    written = session.execute(text(f"""
      INSERT INTO fact_inventory_snapshot ({columns})
      SELECT {columns} FROM {stage} WHERE snapshot_run_id IN
        (SELECT snapshot_run_id FROM mart_changed_snapshot_runs)
    """), params).rowcount
    session.execute(text(f"""
      INSERT INTO mart_inventory_snapshot_runs
        (tenant_id,snapshot_run_id,records_projected,has_unresolved_dimensions)
      SELECT :tenant_id,p.snapshot_run_id,count(s.snapshot_run_id),
        COALESCE(bool_or(s.product_key IS NULL OR s.warehouse_key IS NULL)
          FILTER(WHERE s.snapshot_run_id IS NOT NULL),false)
      FROM mart_pending_snapshot_runs p LEFT JOIN {stage} s
        ON s.snapshot_run_id=p.snapshot_run_id
      GROUP BY p.snapshot_run_id
      ON CONFLICT(tenant_id,snapshot_run_id) DO UPDATE SET
        records_projected=excluded.records_projected,
        has_unresolved_dimensions=excluded.has_unresolved_dimensions,projected_at=now()
    """), params)
    return max(deleted or 0, 0) + max(written or 0, 0)
