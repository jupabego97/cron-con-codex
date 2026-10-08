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
| `fact_inventory_snapshot` | la existencia de un producto en una bodega en una captura puntual |

Las notas crédito entran a `fact_sales_line` con cantidad e importe negativos.
Cada transferencia produce dos hechos: `transfer_out` negativo en la bodega de
origen y `transfer_in` positivo en la bodega destino. Los ajustes conservan el
signo recibido de Alegra. Los dashboards deben filtrar `is_deleted = false` y
aplicar los estados de documento que defina cada indicador.

Las existencias no se reconstruyen sumando ajustes y transferencias: Alegra es
la fuente del saldo actual. El comando `snapshot-inventory` consulta los
productos inventariables por cada bodega y guarda una captura inmutable en
`inventory_snapshots`; el mart la proyecta a `fact_inventory_snapshot`. Los
movimientos siguen siendo una herramienta de auditoria, no una medida de stock.

Los campos de descuento e impuesto comienzan en cero porque las tablas
operativas actuales no exponen esos importes por línea de forma estable. Son
campos explícitos para ampliar cuando se normalice esa parte del payload; no se
inventan importes a partir de totales de cabecera.

## Refresco idempotente

Después de que haya datos operativos, ejecute:

```powershell
python -m app.cli migrate
python -m app.cli snapshot-inventory <tenant-uuid>
python -m app.cli refresh-mart <tenant-uuid>
```

El comando adquiere un bloqueo transaccional por tenant y actualiza únicamente
dimensiones y hechos que cambian. Para documentos compara la proyección tipada
completa, no solamente `updated_at` o `source_hash`: las reparaciones de líneas,
anulaciones, cambios de moneda, dimensiones tardías y líneas removidas también
se reflejan. Conserva las claves de hechos sin cambios y sus costos enriquecidos.

Las capturas inmutables usan `mart_inventory_snapshot_runs`, un registro de
proyección que se confirma en la misma transacción que sus hechos. Solo se leen
capturas exitosas pendientes. La primera carga compara el histórico existente
fila por fila, incluidos valores nulos, multiplicidad y redondeo monetario; adopta
las capturas que coinciden sin borrar o reinsertar sus filas. Las capturas con
producto/bodega todavía desconocidos se revisan cuando aparecen nuevas claves
dimensionales de producto/bodega, no por simples cambios de nombre, precio o stock.

Si una ejecución falla, hechos y registro de proyección se revierten juntos.
Los reportes siguen viendo el mart previamente confirmado. Cada ejecución se
audita en `mart_refresh_runs`; `records_written=0` es un resultado correcto si
no cambiaron datos, no una señal de fallo. Las estadísticas por proveedor solo
se recalculan cuando cambian las compras o durante mantenimiento explícito.

`refresh-mart --full` verifica también todas las capturas históricas completadas
y repara exclusivamente las que difieren. No debe incluirse en el cron habitual.
El cálculo FIFO existente sigue ejecutándose después del mart para mantener su
semántica histórica; esta optimización no cambia el método de costeo.

Para Railway crea un Cron independiente (sin dominio) para la captura:

```text
python -m app.cli snapshot-inventory <tenant-uuid>
```

Como punto de partida, programa este Cron a `5 */4 * * *` (cada cuatro horas,
UTC). Requiere `DATABASE_URL` y `ALEGRA_API_BASIC_TOKEN`. Programa el Cron de
`refresh-mart` para `25 */4 * * *`, despues de la captura; ese segundo Cron
solo requiere `DATABASE_URL`. Si mantienes el mart cada hora, tambien es
correcto: hara refrescos sin una nueva captura entre medias.

La captura no ejecuta el mart ni FIFO: su único responsable programado es el cron
`refresh-mart`. `refresh-inventory-analytics` conserva la combinación explícita
para operaciones manuales. Los horarios no garantizan dependencias estrictas:
si la captura o reconciliación tarda más, el siguiente refresco recoge los datos
confirmados que quedaron pendientes; no se marcan completados antes de confirmar.

## Red privada y despliegue seguro en Railway

API, worker, reconciliación, captura y mart usan
`DATABASE_URL=${{Postgres.DATABASE_URL}}` mediante referencia al PostgreSQL
principal. Las conexiones locales siguen usando `Postgres.DATABASE_PUBLIC_URL`.
No se reduce RAM, CPU, frecuencia de captura ni retención histórica.

Orden de despliegue: pruebas en PostgreSQL aislado; comprobar conexión privada
desde un contenedor; migración aditiva `20261008_21`; ensayo transaccional del mart
con rollback; desplegar código y referencias; comprobar readiness y reejecuciones.
La migración no transforma ni borra filas existentes. Un rollback del código
debe conservar la migración `20261008_21` y la compatibilidad de readiness, aunque
deje su tabla sin usar: no hay que quitarla ni eliminar datos históricos. No
redesplegar un artefacto antiguo con migraciones que desconozcan esa revisión.
Si se requiere revertir la conectividad, usar temporalmente la referencia
`Postgres.DATABASE_PUBLIC_URL` y redesplegar el servicio afectado.

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
