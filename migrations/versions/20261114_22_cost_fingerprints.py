"""Add nullable fingerprints; never rewrite costs, sources or inventory history."""

import sqlalchemy as sa
from alembic import op

revision = "20261114_22"
down_revision = "20261008_21"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sales_cost_allocation_runs", sa.Column("source_fingerprint", sa.String(64)))
    op.add_column("sales_cost_allocation_runs", sa.Column("projection_fingerprint", sa.String(64)))


def downgrade() -> None:
    op.drop_column("sales_cost_allocation_runs", "projection_fingerprint")
    op.drop_column("sales_cost_allocation_runs", "source_fingerprint")
