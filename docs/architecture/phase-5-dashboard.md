# Phase 5 - Dashboard analítico

El dashboard se compila desde `frontend/` y se sirve en la misma URL del API.
El navegador solo consume `/api/v1/analytics/*`; no se conecta a PostgreSQL ni
a Alegra.

## Configuración en Railway

En el servicio **API** agrega estas variables, sin comillas:

```text
DASHBOARD_PASSWORD=<contraseña-larga-y-única>
DASHBOARD_TENANT_ID=<UUID-real-del-tenant>
```

Conserva `APP_SECRET_KEY` configurada. El cookie de sesión es `HttpOnly`, usa
`SameSite=Lax` y se marca `Secure` cuando `APP_ENV=production`.

No agregues estas variables a los servicios Cron: no las necesitan.

Al terminar el deploy, abre la URL pública del servicio API. La raíz mostrará
el formulario de acceso; las rutas de salud y webhooks se mantienen intactas.

## Uso y límites de datos

El rango inicial es los últimos 30 días y los filtros se aplican al hecho que
corresponda. Ventas, compras y pagos se agrupan por moneda: no se suman monedas
distintas. Los documentos no eliminados se incluyen sin excluir estados; el
selector de estado permite validar los resultados contra Alegra.

## Existencias actuales

El inventario separa dos conceptos: **Existencias actuales** proviene del ultimo
snapshot de Alegra por producto y bodega; **Movimientos de inventario** solo
muestra ajustes y transferencias para auditoria. Ejecuta primero
`snapshot-inventory` y despues `refresh-mart` para poblar las existencias. El
valor a costo es informativo, segun el costo devuelto por Alegra, y no sustituye
una valorizacion contable.

Los movimientos muestran ajustes y transferencias; no deben usarse para
reconstruir existencias ni para una valorización contable. Margen histórico,
impuestos y descuentos por línea se mantienen fuera de los KPIs hasta que su
fuente sea confiable.

## Indicadores clave

La pestaña **Indicadores** utiliza únicamente hechos y dimensiones del mart. Incluye
unidades por transacción, precio neto por unidad, tasa de notas crédito, clientes
recurrentes y nuevos, concentración de clientes/proveedores y ticket/costo promedio
de compra. Para el inventario muestra referencias sin disponibilidad, cobertura menor
a 14 días, exceso de cobertura (120 días o más) e inventario sin demanda dentro del
período filtrado.

La cobertura es `stock actual / demanda neta diaria del período seleccionado`; sirve
para priorizar reposición, no para valorar inventario. No se muestran rotación
contable, GMROI, margen ni cartera hasta contar con costo histórico por venta,
snapshots de inventario suficientes y saldos de documentos confiables.
