# Phase 3 - Historical multi-resource backfill

## Scope

The extractor covers the verified retail-operational routes:

- `contacts`, `items`, `warehouses`, `sellers`
- `invoices`, `bills`, `payments`, `credit-notes`
- `inventory-adjustments`, `warehouse-transfers`

Each resource keeps immutable versions in `raw_alegra_documents` and its latest
canonical state in `alegra_entities`. Sales invoices also keep the normalized
`sales_invoices` and `sales_invoice_lines` models.

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
  --resource-concurrency 2 `
  --page-concurrency 4 `
  --detail-concurrency 6 `
  --requests-per-minute 110
```

The program uses one shared client and API budget. Resources, pages and details
are downloaded concurrently, while database writes remain transactional and
idempotent. The default budget of 110 leaves headroom below Alegra's 150 RPM
limit.

`--skip-details` stores listing responses only. It is useful for a quick
bootstrap but is not the recommended final historical load.

## Failure recovery

Every resource creates its own `sync_run` and updates `resource_sync_states`. A
failure in one route does not stop the other resource tasks; the command exits
non-zero when any route fails. Repeat only the failed resource, for example:

```powershell
python -m app.cli backfill-all <tenant-uuid> --resources bill
```

Repeating a load does not create duplicates. The current snapshot key is
`(tenant_id, resource, external_id)`, and raw versions are deduplicated by hash.

## Webhooks

The worker now supports invoices, supplier bills, clients and items when their
corresponding Alegra subscriptions are configured. Payments and inventory
movements remain protected by scheduled reconciliation until compatible events
are enabled.

Before a real load, `ALEGRA_API_BASIC_TOKEN` must authenticate successfully
against Alegra's `/users/self` endpoint.
