"""Reliable synchronization and tenant-scoped retail operations.

Revision ID: 20261008_20
Revises: 20261006_19
"""

from alembic import op

revision = "20261008_20"
down_revision = "20261006_19"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
      ALTER TABLE sync_runs ADD COLUMN window_from date,
        ADD COLUMN window_to date, ADD COLUMN checkpoint_date date,
        ADD COLUMN heartbeat_at timestamptz, ADD COLUMN lease_token uuid;
      ALTER TABLE inbound_events ADD COLUMN lease_token uuid;
      CREATE INDEX ix_inbound_processing_lease ON inbound_events(locked_at)
        WHERE status='processing';
      CREATE UNIQUE INDEX uq_dim_product_tenant_key ON dim_product(tenant_id,key);
      CREATE UNIQUE INDEX uq_dim_contact_tenant_key ON dim_contact(tenant_id,key);
      CREATE TABLE operational_audit (
        id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenants(id),
        action text NOT NULL, entity_type text NOT NULL, entity_id text NOT NULL,
        details jsonb NOT NULL DEFAULT '{}', actor text NOT NULL DEFAULT 'dashboard',
        created_at timestamptz NOT NULL DEFAULT now());
      CREATE INDEX ix_operational_audit_tenant_time ON operational_audit(tenant_id,created_at);
      CREATE TABLE product_business_profiles (
        tenant_id uuid NOT NULL REFERENCES tenants(id), product_key bigint NOT NULL,
        lifecycle text NOT NULL DEFAULT 'active'
          CHECK(lifecycle IN ('active','replaced','discontinued','on_request')),
        introduced_on date, replacement_product_key bigint, notes text,
        updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(tenant_id,product_key),
        FOREIGN KEY(tenant_id,product_key) REFERENCES dim_product(tenant_id,key),
        FOREIGN KEY(tenant_id,replacement_product_key) REFERENCES dim_product(tenant_id,key),
        CHECK(replacement_product_key IS NULL OR replacement_product_key<>product_key));
      CREATE TABLE product_daily_availability (
        tenant_id uuid NOT NULL REFERENCES tenants(id), item_alegra_id text NOT NULL,
        observed_on date NOT NULL, sample_count integer NOT NULL CHECK(sample_count>0),
        positive_samples integer NOT NULL CHECK(positive_samples>=0),
        last_quantity numeric(18,4) NOT NULL, captured_at timestamptz NOT NULL,
        PRIMARY KEY(tenant_id,item_alegra_id,observed_on),
        CHECK(positive_samples<=sample_count));
      INSERT INTO product_daily_availability
      SELECT tenant_id,item_alegra_id,observed_on,count(*),
        count(*) FILTER(WHERE quantity>0),
        (array_agg(quantity ORDER BY captured_at DESC))[1],max(captured_at)
      FROM (
        SELECT s.tenant_id,s.item_alegra_id,s.snapshot_run_id,
          (max(s.captured_at) AT TIME ZONE 'America/Bogota')::date observed_on,
          max(s.captured_at) captured_at,sum(s.quantity_on_hand) quantity
        FROM inventory_snapshots s JOIN inventory_snapshot_runs r ON r.id=s.snapshot_run_id
          AND r.tenant_id=s.tenant_id AND r.status='succeeded'
        GROUP BY s.tenant_id,s.item_alegra_id,s.snapshot_run_id
      ) daily GROUP BY tenant_id,item_alegra_id,observed_on;
      CREATE TABLE purchase_order_tracking (
        tenant_id uuid NOT NULL REFERENCES tenants(id), order_alegra_id text NOT NULL,
        stage text NOT NULL DEFAULT 'pending_confirmation'
          CHECK(stage IN ('pending_confirmation','confirmed','in_transit','closed','cancelled')),
        expected_on date, confirmed_at timestamptz, notes text,
        updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(tenant_id,order_alegra_id),
        FOREIGN KEY(tenant_id,order_alegra_id) REFERENCES purchase_orders(tenant_id,alegra_id));
      CREATE TABLE purchase_receipts (
        id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenants(id),
        order_alegra_id text NOT NULL, line_number integer NOT NULL,
        item_alegra_id text, received_on date NOT NULL, accepted_quantity numeric(18,4) NOT NULL DEFAULT 0
          CHECK(accepted_quantity>=0),
        rejected_quantity numeric(18,4) NOT NULL DEFAULT 0 CHECK(rejected_quantity>=0),
        notes text, created_at timestamptz NOT NULL DEFAULT now(),
        FOREIGN KEY(tenant_id,order_alegra_id) REFERENCES purchase_orders(tenant_id,alegra_id),
        CHECK(accepted_quantity+rejected_quantity>0));
      CREATE INDEX ix_receipts_tenant_order ON purchase_receipts(tenant_id,order_alegra_id,line_number);
      CREATE TABLE treasury_balances (
        tenant_id uuid NOT NULL REFERENCES tenants(id), currency_code text NOT NULL,
        as_of_date date NOT NULL, amount numeric(18,2) NOT NULL,
        notes text, created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(tenant_id,currency_code,as_of_date));
      CREATE TABLE treasury_entries (
        id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenants(id),
        entry_date date NOT NULL, currency_code text NOT NULL, amount numeric(18,2) NOT NULL,
        direction text NOT NULL CHECK(direction IN ('in','out')),
        stage text NOT NULL CHECK(stage IN ('planned','posted')),
        category text NOT NULL, description text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(), CHECK(amount>0));
      CREATE INDEX ix_treasury_tenant_date ON treasury_entries(tenant_id,entry_date);
      CREATE TABLE repair_jobs (
        id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenants(id),
        customer_name text NOT NULL, customer_phone text, contact_key bigint, product_key bigint,
        device_description text NOT NULL, serial_number text, reported_issue text NOT NULL,
        diagnosis text, status text NOT NULL DEFAULT 'received'
          CHECK(status IN ('received','diagnosing','awaiting_approval','approved','in_progress',
            'ready','delivered','cancelled')),
        quoted_amount numeric(18,2), charged_amount numeric(18,2), labour_cost numeric(18,2),
        costs_complete boolean NOT NULL DEFAULT false,
        currency_code text NOT NULL DEFAULT 'COP', warranty_days integer NOT NULL DEFAULT 30,
        warranty_parent_id uuid, invoice_alegra_id text, received_on date NOT NULL,
        promised_on date, delivered_on date, updated_at timestamptz NOT NULL DEFAULT now(),
        FOREIGN KEY(tenant_id,contact_key) REFERENCES dim_contact(tenant_id,key),
        FOREIGN KEY(tenant_id,product_key) REFERENCES dim_product(tenant_id,key),
        UNIQUE(tenant_id,id),
        FOREIGN KEY(tenant_id,warranty_parent_id) REFERENCES repair_jobs(tenant_id,id),
        CHECK(warranty_days>=0), CHECK(quoted_amount>=0), CHECK(charged_amount>=0),
        CHECK(labour_cost>=0));
      CREATE INDEX ix_repairs_tenant_status ON repair_jobs(tenant_id,status,received_on);
      CREATE INDEX ix_repairs_tenant_serial ON repair_jobs(tenant_id,serial_number);
      CREATE TABLE repair_parts (
        id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenants(id), repair_id uuid NOT NULL,
        product_key bigint, description text NOT NULL, quantity numeric(18,4) NOT NULL,
        unit_cost numeric(18,2) NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        FOREIGN KEY(tenant_id,repair_id) REFERENCES repair_jobs(tenant_id,id),
        FOREIGN KEY(tenant_id,product_key) REFERENCES dim_product(tenant_id,key),
        CHECK(quantity>0), CHECK(unit_cost>=0));
      CREATE TABLE dashboard_login_limits (
        identity text PRIMARY KEY, attempted_at timestamptz NOT NULL,
        attempt_count integer NOT NULL DEFAULT 0, locked_until timestamptz);
      CREATE TABLE dashboard_security_state (
        tenant_id uuid PRIMARY KEY REFERENCES tenants(id),
        session_generation bigint NOT NULL DEFAULT 0);
      CREATE TABLE backup_verifications (
        id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenants(id),
        verified_at timestamptz NOT NULL DEFAULT now(), details jsonb NOT NULL);
    """)


def downgrade() -> None:
    for table in (
        "backup_verifications",
        "dashboard_security_state",
        "dashboard_login_limits",
        "repair_parts",
        "repair_jobs",
        "treasury_entries",
        "treasury_balances",
        "purchase_receipts",
        "purchase_order_tracking",
        "product_daily_availability",
        "product_business_profiles",
        "operational_audit",
    ):
        op.execute(f"DROP TABLE {table}")
    op.execute("DROP INDEX uq_dim_contact_tenant_key; DROP INDEX uq_dim_product_tenant_key")
    op.execute("DROP INDEX ix_inbound_processing_lease")
    op.execute("ALTER TABLE inbound_events DROP COLUMN lease_token")
    op.execute(
        "ALTER TABLE sync_runs DROP COLUMN window_from, DROP COLUMN window_to, "
        "DROP COLUMN checkpoint_date, DROP COLUMN heartbeat_at, DROP COLUMN lease_token"
    )
