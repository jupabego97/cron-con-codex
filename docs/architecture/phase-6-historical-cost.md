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

`inventory_cost_movements` sera la entrada de movimientos de costo. La carga
inicial usa `cost_method=moving_average` y `confidence=certified`. Las capas
abiertas permiten implementar posteriormente consumo FIFO o costo promedio
ponderado cuando existan compras, ventas y transferencias históricas con
calidad suficiente. El dashboard no debe sumar esta capa como inventario
actual: el saldo actual sigue viniendo de la captura de Alegra.

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
