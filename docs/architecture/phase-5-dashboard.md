# Phase 5 - Dashboard analítico

El dashboard se compila desde `frontend/` y se sirve en la misma URL del API.
El navegador solo consume `/api/v1/analytics/*`; no se conecta a PostgreSQL ni
a Alegra.

## Configuración en Railway

En el servicio **API** agrega estas variables, sin comillas:

```text
DASHBOARD_PASSWORD=<contraseña-larga-y-única>
DASHBOARD_TENANT_ID=<UUID-real-del-tenant>
DASHBOARD_MONTHLY_SALES_TARGET_COP=<meta-mensual-en-COP-opcional>
```

Conserva `APP_SECRET_KEY` configurada. El cookie de sesión es `HttpOnly`, usa
`SameSite=Lax` y se marca `Secure` cuando `APP_ENV=production`.

No agregues estas variables a los servicios Cron: no las necesitan.

Si configuras `DASHBOARD_MONTHLY_SALES_TARGET_COP`, el KPI de ventas mostrarÃ¡ el
ritmo diario observado frente al ritmo diario requerido para la meta del mes. No se
asume una meta automÃ¡tica a partir de las ventas histÃ³ricas.

Al terminar el deploy, abre la URL pública del servicio API. La raíz mostrará
el formulario de acceso; las rutas de salud y webhooks se mantienen intactas.

## Uso y límites de datos

El rango inicial es los últimos 30 días y los filtros se aplican al hecho que
corresponda. El botón **Toda la historia** toma el mínimo y máximo disponibles
en ventas, compras, pagos, movimientos y snapshots del tenant; también puede
usarse antes de una pregunta histórica al asistente IA. Las fechas se calculan
con el calendario `America/Bogota`, no con UTC del navegador o del servidor.
Ventas, compras y pagos se agrupan por moneda: no se suman monedas distintas.
Los documentos no eliminados se incluyen sin excluir estados; el selector de
estado permite validar los resultados contra Alegra.

El menú **Alertas** representa el estado actual del snapshot y sus ventanas
operativas propias; no debe interpretarse como una serie histórica. El menú
**Inventario** combina existencias actuales del último snapshot con movimientos
del rango seleccionado.

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

## RecomendaciÃ³n de compra

La pestaÃ±a **Reponer** produce una cola de revisiÃ³n a partir del Ãºltimo snapshot,
la demanda neta del rango seleccionado y el costo unitario disponible. Por defecto
busca completar 30 dÃ­as de cobertura, considera 7 dÃ­as de plazo y 7 dÃ­as de
seguridad, y clasifica cada referencia como crÃ­tica, alta o media. Solo recomienda
productos con demanda observada; no crea Ã³rdenes automÃ¡ticas.


## Reposición accionable

La pestaña **Reponer** consume el snapshot actual y la demanda del mart para
calcular velocidad seleccionada y contexto de 7, 30 y 90 días. La cantidad
sugerida completa la cobertura objetivo y no descuenta órdenes pendientes hasta
que exista una fuente confiable de compras abiertas.

La tabla supplier_product_stats conserva por producto, proveedor y moneda:

- frecuencia y unidades compradas;
- costo promedio, mediano, mínimo, máximo y último;
- última compra;
- participación de líneas y unidades;
- ranking modal y ranking por costo.

El proveedor principal se sugiere por historial modal y se muestran hasta tres
alternativas. La aplicación distingue proveedor histórico, proveedor actual del
catálogo y ausencia de historial. No afirma disponibilidad, plazo o condiciones
comerciales que todavía no estén integradas.

La tabla replenishment_item_actions permite marcar cada recomendación como
pendiente, revisada, pospuesta, comprada o descartada, guardar una nota y
conservar ese estado entre refrescos del mart. El endpoint de exportación
genera un CSV agrupable por proveedor para preparar la compra manualmente; no
crea órdenes automáticamente en Alegra.

## Pedidos completos por proveedor

La reposicion ahora tambien agrupa las lineas en canastas de compra por proveedor.
Cada canasta clasifica la accion como:

- buy_now: existe una linea critica o el pedido alcanza el minimo.
- complete_order: se alcanza el umbral de envio gratis.
- accumulate: hay proveedor identificado, pero conviene acumular.
- review: falta proveedor o faltan politicas comerciales.

Las tablas supplier_replenishment_policies y supplier_product_policies permiten
configurar minimo de pedido, flete, envio gratis, plazo, dias maximos de espera,
MOQ y multiplo de empaque. El dashboard permite editar las politicas del proveedor
y marcar un proveedor alternativo como preferido para un producto.

## Motor de abastecimiento verificable

La versión actual conserva el flujo anterior como datos históricos, pero la
decisión operativa se calcula con `purchase_orders`, `purchase_order_lines`,
`replenishment_forecasts`, `purchase_plan_runs`, `purchase_plan_lines` y
`purchase_plan_orders`.

- La demanda usa ventas brutas; las notas crédito se presentan como devoluciones
  y no reducen silenciosamente la señal de demanda.
- Los productos continuos comparan media de 30 días, EWMA y patrón semanal; los
  intermitentes comparan Croston-SBA, TSB y media de 90 días.
- La selección usa validaciones temporales, WAPE y sesgo, clasificación ABC/XYZ
  y niveles de servicio distintos por segmento.
- La posición de inventario suma existencias no negativas y órdenes abiertas no
  recibidas. Un stock negativo se pone en cuarentena para conciliación.
- La cantidad objetivo considera plazo del proveedor, ciclo de revisión, stock de
  seguridad, MOQ y múltiplo de empaque.
- El proveedor se puntúa por costo, cumplimiento, entrega a tiempo, plazo,
  condiciones de pago y vigencia de la relación.
- El presupuesto se asigna por producto completo en orden de urgencia y valor;
  lo que no cabe queda visible como diferido, nunca parcialmente oculto.

Antes de calcular una compra, el servicio exige inventario materializado reciente,
facturas de proveedor y órdenes reconciliadas y un mart posterior al snapshot. La
creación en Alegra solo está disponible para planes guardados y aprobados, requiere
confirmación adicional y es idempotente.

En Railway se recomiendan estas ejecuciones:

```text
Cada hora: python -m app.cli reconcile-procurement <tenant-uuid> --lookback-days 45
Cada 4 horas: python -m app.cli refresh-inventory-analytics <tenant-uuid>
Una vez: python -m app.cli backfill-all <tenant-uuid> --resources purchase_order
Una vez o al cambiar el dominio/secreto: python -m app.cli configure-webhooks <tenant-slug> https://<dominio-api>
```

Alegra solo admite webhooks para facturas, compras, contactos e ítems. Las órdenes
de compra se mantienen actuales mediante la reconciliación programada.
