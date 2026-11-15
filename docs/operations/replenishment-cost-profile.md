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

## Vigencia del inventario y medianoche

Para pedidos de hoy (zona `America/Bogota`) se selecciona la última captura
exitosa que ya esté en el mart, sin exigir que pertenezca al mismo día.
Se rechazan fechas futuras y sigue siendo obligatorio que tenga como máximo
seis horas. El mart y las sincronizaciones mantienen sus controles anteriores.
La pantalla muestra la hora real de la captura en Colombia.

Las consultas históricas siguen exigiendo una captura de la fecha solicitada;
no toman stock actual como si fuera histórico ni habilitan pedidos históricos.

## Respuestas de capturas sin duplicación

La migración `20261115_23` añade `inventory_snapshot_payloads` y una referencia
opcional en `inventory_snapshots`. Antes de aplicarla debe estar activo el API
de compatibilidad `8c0da80` o posterior. El DDL tiene un límite de espera de
bloqueos de cinco segundos; si falla, investigar y reintentar sin forzar bloqueos.

Cada captura nueva conserva todas sus filas, cantidades, costos, fecha y bodega.
Respuestas JSON completas e idénticas comparten una copia SHA-256 dentro del
mismo tenant; cualquier cambio genera otra copia. Inserciones por lotes de 250
respuestas / 500 observaciones evitan sentencias gigantes. Las referencias y
las respuestas se escriben en la misma transacción, con FK compuesta por tenant.

El histórico anterior queda exactamente como estaba. Para leer trazabilidad de
ambos formatos usar `load_snapshot_payload(session, tenant_id=..., snapshot_id=...)`
en `app.services.inventory_snapshot`, o un LEFT JOIN por tenant/hash y
`coalesce(s.payload,p.payload)`. `payload=NULL` con hash indica una referencia,
no una respuesta perdida. No consultar solo `inventory_snapshots.payload` para
auditar capturas nuevas. Las consultas del mart no cambian: usan columnas numéricas.

Esto reduce crecimiento futuro, no recupera automáticamente los GB existentes.
No ejecutar compactación, retención, `VACUUM FULL` ni borrado histórico como parte
del despliegue. Un rollback de código conserva el esquema 23: los escritores
anteriores pueden seguir insertando JSON completo; el downgrade se niega a
eliminar la estructura si ya existen referencias.

## Ajuste de recursos PostgreSQL

Crear respaldo reciente antes de modificar recursos. Primer límite conservador:
2 GB de RAM, sin bajar CPU ni cambiar parámetros de PostgreSQL al mismo tiempo.
Comprobar el límite efectivo del cgroup, `memory.events` (OOM), `/readyz`,
Reponer, captura y mart. No concluir ahorro por el límite configurado: comparar
uso observado y facturación. No bajar a 1 GB sin pruebas bajo carga representativa.
Si hay errores o presión sostenida, restaurar el límite anterior (24 GB).

### Perfil de memoria acotado

El pool por proceso conserva dos conexiones y admite dos temporales: máximo
cuatro simultáneas, con espera de 30 segundos. Variables opcionales:
`DATABASE_POOL_SIZE`, `DATABASE_MAX_OVERFLOW`, `DATABASE_POOL_TIMEOUT_SECONDS`.
El límite se multiplica por procesos/réplicas; no es global a la plataforma.
Un backfill local de mayor concurrencia puede ampliarlo explícitamente.

Reponer lee ventas diarias y disponibilidad mediante cursor de 500 filas,
cerrándolo al terminar o interrumpir la lectura. Conserva el historial que
requiere el modelo, pero no otra lista de todas las filas de PostgreSQL.
La huella de pronósticos se calcula producto por producto: conserva exactamente
los bytes y el orden anteriores, sin construir otro catálogo completo en JSON.
No cambian ventanas de demanda, modelos, caché ni asignación de presupuesto.

La captura usa un buffer compartido de hasta 250 observaciones para todas las
bodegas. Cada lote se inserta en la misma transacción, sin retener el catálogo
completo en RAM. Un fallo cancela y espera a las otras lecturas antes del
rollback; ni filas parciales ni respuestas compartidas huérfanas se publican.
La cancelación deja la ejecución auditada como fallida. La transacción permanece
abierta durante la lectura: vigilar su duración y no incrementar concurrencia
indiscriminadamente para compensar límites de la API de Alegra.

Reducir PostgreSQL por etapas (2 → 1,5 GB y, solo tras verificar carga, 1 GB).
La caché inactiva se puede recuperar sin borrar datos; no se busca quitar toda
la caché. Probar captura, mart y Reponer concurrentes, comparar resultados y
latencias y revisar `memory.events`. Un aumento de `max` con OOM=0 puede indicar
recuperación de caché; OOM u OOM-kill exigen revertir. Una prueba puntual no
garantiza capacidad para más tenants ni para toda la carga futura.

La disponibilidad diaria filtra `captured_at` por un intervalo semiabierto
desde medianoche de Colombia hasta la medianoche siguiente. Esto permite usar
el índice existente `(tenant_id,captured_at)` en lugar de convertir la fecha de
todas las capturas históricas. Los límites incluyen el inicio y excluyen el fin;
no se modifica el historial ni la definición del día de negocio.

Una base secundaria solo se puede detener tras revisar todas sus bases,
referencias y conexiones y conservar un respaldo y su volumen. Detenerla es
reversible; no eliminar servicio, volumen ni respaldos. Los volúmenes retenidos
pueden seguir teniendo costo de almacenamiento aunque no haya cómputo.
