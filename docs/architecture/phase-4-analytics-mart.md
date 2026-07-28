# Phase 4 - Data mart analítico

## Propósito y límite

El data mart es una capa PostgreSQL separada, derivada exclusivamente de las
proyecciones operativas. No consulta la API de Alegra, no procesa webhooks y no
reemplaza las tablas de ingestión. Por tanto, conserva una separación clara:

1. `raw_alegra_documents` y `alegra_entities`: trazabilidad y estado canónico.
2. Tablas operativas tipadas: contrato de datos para el negocio.
3. `dim_*` y `fact_*`: consultas de dashboard, reglas y futura IA.

Las dimensiones usan claves sustitutas. `dim_date` usa la clave de calendario
`YYYYMMDD`; `dim_tenant`, producto, contacto, vendedor y bodega usan claves
`BIGINT` generadas por PostgreSQL. Las dimensiones son SCD tipo 1 inicialmente:
reflejan el estado actual. No se debe utilizar `current_cost` para reconstruir
márgenes históricos; el costo de línea permanece nulo hasta capturar una fuente
histórica confiable.

## Hechos y su granularidad

| Tabla | Una fila representa |
| --- | --- |
| `fact_sales_line` | una línea de factura de venta o nota crédito |
| `fact_purchase_line` | una línea de factura de compra |
| `fact_payment` | un pago Alegra |
| `fact_inventory_movement` | un ajuste, o una mitad de una transferencia |

Las notas crédito entran a `fact_sales_line` con cantidad e importe negativos.
Cada transferencia produce dos hechos: `transfer_out` negativo en la bodega de
origen y `transfer_in` positivo en la bodega destino. Los ajustes conservan el
signo recibido de Alegra. Los dashboards deben filtrar `is_deleted = false` y
aplicar los estados de documento que defina cada indicador.

Los campos de descuento e impuesto comienzan en cero porque las tablas
operativas actuales no exponen esos importes por línea de forma estable. Son
campos explícitos para ampliar cuando se normalice esa parte del payload; no se
inventan importes a partir de totales de cabecera.

## Refresco idempotente

Después de que haya datos operativos, ejecute:

```powershell
python -m app.cli migrate
python -m app.cli refresh-mart <tenant-uuid>
```

El comando adquiere un bloqueo transaccional por tenant, actualiza dimensiones,
elimina únicamente los hechos de ese tenant y los recalcula en la misma
transacción. Repetirlo no duplica datos y una anulación/eliminación de la capa
operativa se refleja en el mart. Cada ejecución queda auditada en
`mart_refresh_runs`.

Para Railway, cree un servicio **Cron** independiente (sin dominio) con:

```text
python -m app.cli refresh-mart <tenant-uuid>
```

Como punto de partida, prográmelo a `25 * * * *` (cada hora, UTC). Debe llevar
solo `DATABASE_URL`; no necesita `ALEGRA_API_BASIC_TOKEN`. Ejecútelo después del
cron de reconciliación, no en paralelo con un backfill manual del mismo tenant.

## Consulta de dashboard de ventas mensual

Los dashboards deben leer exclusivamente el mart. Ejemplo de base, con filtros
opcionales en producto, vendedor y bodega:

```sql
SELECT d.year, d.month, SUM(f.net_sales_amount) AS venta_neta
FROM fact_sales_line f
JOIN dim_date d ON d.date_key = f.date_key
WHERE f.tenant_id = :tenant_id
  AND f.is_deleted = false
  AND d.calendar_date >= :from_date
  AND d.calendar_date < :to_date
GROUP BY d.year, d.month
ORDER BY d.year, d.month;
```

No se eliminan dimensiones cuando una entidad fuente se elimina: se marca
`is_deleted`. Esto mantiene claves estables para auditoría y permite añadir SCD2
para productos y contactos sin rediseñar los hechos.
