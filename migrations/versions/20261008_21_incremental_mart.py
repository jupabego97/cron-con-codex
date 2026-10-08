"""Transactional completion ledger for immutable inventory projections.

Revision ID: 20261008_21
Revises: 20261008_20

Additive only: no source/fact rows are changed by this migration. The first
refresh verifies and adopts existing matching history instead of rebuilding it.
"""

from alembic import op

revision = "20261008_21"
down_revision = "20261008_20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
      CREATE TABLE mart_inventory_snapshot_runs (
        tenant_id uuid NOT NULL REFERENCES tenants(id),
        snapshot_run_id uuid NOT NULL REFERENCES inventory_snapshot_runs(id),
        records_projected bigint NOT NULL CHECK(records_projected>=0),
        has_unresolved_dimensions boolean NOT NULL DEFAULT false,
        projected_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(tenant_id,snapshot_run_id));
    """)


def downgrade() -> None:
    op.drop_table("mart_inventory_snapshot_runs")
