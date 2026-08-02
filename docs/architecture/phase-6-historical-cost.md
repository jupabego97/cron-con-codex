# Fase 6: costo historico de inventario

## Objetivo

Establecer una base de costo auditable para reconstruir margen desde el
01-01-2026, usando el reporte de valor de inventario certificado por la
empresa. Alegra continua siendo la fuente transaccional; el reporte es una
fuente de apertura fechada y congelada, no un reemplazo de Alegra.

## Politica aplicada al reporte

- Se conserva cada fila original en `inventory_cost_opening_balances`.
- Se exige una sola bodega activa, o se puede indicar su `alegra_id`.
- El producto se asocia por referencia exacta cuando existe; si no, por
  nombre normalizado. Las coincidencias ambiguas no se cargan como costo.
- Las cantidades positivas con costo y producto identificado crean un
  movimiento `opening_balance` y una capa abierta en
  `inventory_cost_layers`.
- Las cantidades cero quedan como `reference_only`; no crean una capa.
- Las cantidades negativas no se interpretan como inventario inicial. Se
  conserva su precio para revisión en `negative_exception`, pero se ignoran
  cantidad y total para el cálculo de costo.
- El valor calculado es `cantidad positiva * costo promedio`. El total del
  Excel se conserva solo como evidencia y no gobierna el cálculo.

## Ejecucion segura

Desde el entorno local que tenga acceso a la base de datos:

```powershell
python -m app.cli migrate
python -m app.cli import-opening-inventory <tenant-uuid> `
  "D:\ruta\reporte.xlsx" --cutoff-date 2026-01-01 --dry-run
```

El resultado debe revisarse antes de escribir. En particular:

- `layers` debe aproximarse a las filas positivas que correspondan a
  productos activos;
- `unmatched` debe ser cero o estar explicado;
- `exceptions` incluye filas cero, negativas y no identificadas, por lo que
  no tiene que ser cero.

Después se ejecuta el mismo comando sin `--dry-run`:

```powershell
python -m app.cli import-opening-inventory <tenant-uuid> `
  "D:\ruta\reporte.xlsx" --cutoff-date 2026-01-01
```

El importador guarda el hash SHA-256 del archivo. Repetir exactamente la
misma carga devuelve el resultado existente; otra carga exitosa para el mismo
tenant y fecha de corte se rechaza para impedir dos saldos iniciales.

## Modelo y uso posterior

`inventory_cost_movements` es la entrada del ledger de costo. La carga
inicial conserva `confidence=certified`; las compras posteriores se agregan
como capas `source`. Desde el corte, `allocate-sales-costs` consume las capas
por FIFO, actualiza `fact_sales_line` y guarda el detalle de asignación en
`sales_cost_allocations`. Las notas crédito reciben un costo estimado a partir
del costo FIFO disponible y quedan identificadas como `estimated`.

El dashboard usa `cogs_amount`, `unit_cost`, `margin_amount`, `cost_status`,
`cost_confidence` y `cost_method`. Una línea puede quedar `partial` o
`unavailable` si la cantidad vendida supera las capas conocidas; el sistema no
inventa ese costo. El saldo actual del inventario sigue viniendo de la captura
de Alegra, no de este ledger histórico.

## Control posterior

```sql
SELECT status, records_read, records_written, exception_count, started_at, finished_at
FROM inventory_cost_import_runs
WHERE tenant_id = '<tenant-uuid>'
ORDER BY started_at DESC;

SELECT classification, count(*)
FROM inventory_cost_opening_balances
WHERE tenant_id = '<tenant-uuid>' AND cutoff_date = DATE '2026-01-01'
GROUP BY classification
ORDER BY classification;

SELECT count(*) AS layers, sum(original_quantity * unit_cost) AS opening_value
FROM inventory_cost_layers
WHERE tenant_id = '<tenant-uuid>' AND opened_on = DATE '2026-01-01';
```

No se debe editar manualmente una capa certificada. Si el archivo fue
incorrecto, se debe corregir la fuente y realizar una nueva carga con una
fecha de corte diferente o ejecutar una migracion controlada, dejando la
trazabilidad del cambio.

## Asignación de costo a ventas

Después de aplicar la migración y de tener el mart actualizado, el cálculo se
puede ejecutar explícitamente:

```powershell
python -m app.cli allocate-sales-costs <tenant-uuid> --cutoff-date 2026-01-01
```

`refresh-mart` ya ejecuta esta asignación automáticamente después de reconstruir
los hechos. Por eso el Cron existente de `refresh-mart` sigue siendo suficiente;
no se necesita un tercer Cron para costos. La ejecución se registra en
`sales_cost_allocation_runs`. Para controlar cobertura y excepciones:

```sql
SELECT status, lines_read, lines_costed, lines_partial, lines_unavailable,
       sales_units, costed_units, cogs_amount, started_at, finished_at
FROM sales_cost_allocation_runs
WHERE tenant_id = '<tenant-uuid>'
ORDER BY started_at DESC;

SELECT cost_status, count(*), sum(net_sales_amount), sum(cogs_amount),
       sum(margin_amount)
FROM fact_sales_line
WHERE tenant_id = '<tenant-uuid>'
  AND is_deleted = false
GROUP BY cost_status
ORDER BY cost_status;
```
