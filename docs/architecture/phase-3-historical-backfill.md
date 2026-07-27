# Phase 3 - Historical multi-resource backfill

## Scope

The extractor covers the verified retail-operational routes:

- `contacts`, `items`, `warehouses`, `sellers`
- `invoices`, `bills`, `payments`, `credit-notes`
- `inventory-adjustments`, `warehouse-transfers`

Each resource keeps immutable versions in `raw_alegra_documents` and its latest
canonical state in `alegra_entities`. These are ingestion and audit layers, not
the final analytics model. The same batch also writes typed projections:

- `contacts`, `catalog_items`, `warehouses`, `sellers`
- `sales_invoices`, `sales_invoice_lines`, `purchase_bills`, `purchase_bill_lines`
- `payments`, `credit_notes`, `credit_note_lines`
- `inventory_adjustments`, `inventory_adjustment_lines`
- `warehouse_transfers`, `warehouse_transfer_lines`

## Local execution

With PostgreSQL available, apply migrations and start with a small master-data
group:

```powershell
python -m app.cli migrate
python -m app.cli backfill-all <tenant-uuid> --resources contact,item,warehouse,seller
```

Then run the complete historical load:

```powershell
python -m app.cli backfill-all <tenant-uuid> `
  --resource-concurrency 4 `
  --page-concurrency 6 `
  --detail-concurrency 8 `
  --write-batch-size 200 `
  --requests-per-minute 130
```

The program uses one shared client and API budget. It reads masters (`contacts`,
`items`, `warehouses`, `sellers`, `payments`) directly from their paged listing
responses, avoiding one detail request per record. Documents with line items are
hydrated with details. Database writes are transactional, idempotent PostgreSQL
upserts in batches of 200 records.

The default budget of 130 leaves a margin below Alegra's 150 RPM limit. Use 110
when another live integration shares the same Alegra credential. Do not start a
second backfill process with the same credential.

`--skip-details` skips document hydration and therefore line-item projections.
It is useful only for a fast preliminary inventory of headers, not for the final
historical load.

## Failure recovery

Every resource creates its own `sync_run` and updates `resource_sync_states`. A
failure in one route does not stop the other resource tasks; the command exits
non-zero when any route fails. Repeat only the failed resource, for example:

```powershell
python -m app.cli backfill-all <tenant-uuid> --resources bill
```

Repeating a load does not create duplicates. The current snapshot key is
`(tenant_id, resource, external_id)`, and raw versions are deduplicated by hash.
The typed tables use `(tenant_id, alegra_id)` keys. Document line tables are
replaced atomically for each hydrated document batch.

## Webhooks

The worker now supports invoices, supplier bills, clients and items when their
corresponding Alegra subscriptions are configured. Payments and inventory
movements remain protected by scheduled reconciliation until compatible events
are enabled.

Before a real load, `ALEGRA_API_BASIC_TOKEN` must authenticate successfully
against Alegra's `/users/self` endpoint.
