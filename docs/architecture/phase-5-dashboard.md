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

La recomendacion de proveedor es preliminar mientras no se registren pedidos y
recepciones reales. La siguiente mejora sera capturar fecha prometida, fecha
recibida, faltantes y variacion de costo para medir cumplimiento del proveedor.
