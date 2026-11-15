# Perfil de costo para Reponer

Este cambio conserva algoritmos, ventanas de demanda, sincronizaciones,
capturas, historial y controles de calidad. No elimina menús ni datos.

## Worker finito

En Railway, el servicio WORKER puede usar:

```text
python -m app.cli worker --once --max-events 500 --max-seconds 240
```

Schedule UTC: `*/5 * * * *`. Restart policy: `NEVER`; sin healthcheck HTTP.
Cada lote termina al no encontrar eventos elegibles o alcanzar un límite.
Los eventos futuros, reintentos y leases siguen en PostgreSQL. No se cancela
un evento en curso al alcanzar el tiempo: el límite es suave. Si un lote dura
más de cinco minutos, Railway omite el siguiente disparo mientras siga activo.
`attempted` cuenta intentos, no exclusivamente eventos completados.

La demora del procesamiento puede llegar a cinco minutos, más reintentos.
La publicación al mart conserva su frecuencia independiente: esto no promete
que todo el dashboard se actualice cada cinco minutos. Mantener alertas por
cola atrasada, eventos fallidos y lotes que alcanzan límites repetidamente.

`python -m app.cli worker` conserva el modo permanente para reversión. Para
volver a él, quitar el cron, restaurar el comando original y la política
`ON_FAILURE` con diez reintentos, y redesplegar solo WORKER.

## Pronósticos reutilizables

El API conserva como máximo dos resultados de hasta 10.000 productos cada uno,
con caducidad de una hora y exclusión mutua para solicitudes simultáneas.
La caché es local al proceso: no requiere Redis, tablas ni otro servicio;
reiniciar el API solamente provoca un nuevo cálculo.

Cada consulta vuelve a leer sus datos actuales y calcula una huella de tenant,
moneda, fecha, catálogo evaluado, ingresos, introducción, ventas diarias y
disponibilidad. Un cambio invalida el resultado. Solo se reutilizan los
pronósticos: stock, costos, proveedores, políticas, tránsito y presupuesto se
evalúan de nuevo. No se reutilizan decisiones de compra ni respuestas completas.
Cambiar filtros/presupuesto no repite backtests cuando la demanda es la misma.

## FIFO condicionado

La migración `20261114_22` añade dos columnas nullable a las ejecuciones de
costo, sin transformar hechos ni saldos certificados. Antes de migrar debe
estar desplegada la compatibilidad de readiness para esa revisión.

Los refrescos usan `only_if_changed=True`: bajo el bloqueo del tenant comparan
fuentes y proyección con una ejecución exitosa anterior. Solo se omite el
trabajo si ambas coinciden; se audita como `skipped_unchanged`, conservando las
asignaciones físicas anteriores. Se invalidan ventas, compras, eliminaciones,
base inicial, corte, bodega predeterminada o daños en costos/asignaciones.
Un fallo nunca se toma como cálculo válido. La primera ejecución tras migrar
recalcula para establecer las huellas.

`python -m app.cli allocate-sales-costs <tenant-uuid>` sigue forzando un cálculo.
Para revertir el código, conservar la migración aditiva y desplegar como mínimo
la versión de compatibilidad `327ce69`; no volver a un API que rechace la 22.

## Verificación

Comparar un cálculo y la siguiente omisión con las mismas fuentes: deben
coincidir costos/márgenes y conservarse IDs, asignaciones y saldos del ledger.
Verificar `/readyz`, login, `/api/v1/procurement/preview`, y una ejecución
programada del worker que termine, además de reintentos y cola pendiente.
El ahorro monetario se mide luego por servicio: no equivale automáticamente a
la diferencia de tiempo de un benchmark y depende del mínimo del plan.
