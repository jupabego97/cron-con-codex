# Reponer diario en Railway

El servicio Reponer Diario usa `Dockerfile.reponer` y un volumen propio montado
en `/data`. El informe y la bandeja de eventos se guardan en SQLite en ese
volumen. El servicio necesita `DASHBOARD_PASSWORD`, `ALEGRA_WEBHOOK_SECRET`,
`DAILY_REPORT_PUBLISH_TOKEN` y `DAILY_REPORT_TENANT_SLUG`.
La página pide usuario `reponer` y contraseña del tablero. Todas las lecturas
del informe usan autenticación y `Cache-Control: no-store`.

El cron usa la imagen principal, las credenciales existentes de PostgreSQL y
Alegra, `DASHBOARD_TENANT_ID`, `DAILY_REPORT_URL` y el token de publicación
compartido con el lector. El comando es:

```sh
python -m app.cli publish-daily-replenishment <tenant-uuid>
```

Horario `15 7,10 * * *` UTC (02:15 y 05:15 Colombia). La segunda ejecución
consulta primero el estado del informe y termina antes de conectarse a
PostgreSQL si ya se publicó un corte de hoy. `--force` permite un refresco
manual durante una incidencia y `--weekly-budget` configura su presupuesto.

Cada webhook recibe confirmación tras guardar el evento. El cron transfiere
sus lotes, confirma la transacción en PostgreSQL y luego los retira del volumen.
Una respuesta perdida se puede reintentar con el deduplicador existente.
Un webhook fallido, un origen desactualizado o una captura vacía detienen la
publicación. El lector conserva la última edición y avisa cuando supera
26 horas. Revisar los logs del cron, el estado de las suscripciones en Alegra
y la cola antes de reintentar.

## Activación y reversión

1. Desplegar el lector con volumen y verificar acceso privado, persistencia y
   publicación de un primer corte. Probar una entrega antes del cambio de URL.
2. Actualizar las suscripciones existentes por ID. Verificar las doce rutas y
   sus estados. Mantener ambos puntos de recepción disponibles durante el
   cambio para que las entregas en tránsito se puedan guardar.
3. Ejecutar el cron nocturno, confirmar el corte y pausar los crons anteriores.
   Activar Serverless para el API operativo y PostgreSQL. Medir el tiempo de
   actividad de PostgreSQL sin consultarlo durante la ventana de reposo.

Para revertir, restaurar los crons anteriores y la disponibilidad del API
operativo y PostgreSQL. Restaurar las URLs originales de las suscripciones por
ID. Ejecutar una última importación del buzón antes de retirar el lector. El
volumen del lector conserva eventos pendientes y el informe publicado.
