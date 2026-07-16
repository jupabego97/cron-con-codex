# Fase 1 — Núcleo de sincronización de facturas

## Garantías implementadas

- La paginación de Alegra usa `start` exclusivamente como offset, en lotes de máximo 30.
- Cada factura se identifica por `(tenant_id, alegra_id)`; el ID se guarda como texto.
- Las líneas se identifican por `(invoice_id, line_number)`, no por ID de producto.
- El payload original se guarda como versión inmutable con un hash SHA-256.
- Las cantidades y los importes usan `NUMERIC`/`Decimal`, no punto flotante.
- Una nueva versión de una factura actualiza su cabecera y sustituye atómicamente sus líneas.
- Se conservan por separado `total`, `total_paid` y `balance`.
- La eliminación se modela como una marca lógica; no borra la evidencia histórica.

## Ejecución prevista

1. Un worker crea un `sync_run` para un tenant.
2. El cliente consulta la primera página de `/invoices` con `metadata=true`.
3. Usa los offsets `0, 30, 60...`; nunca calcula offsets desde IDs de factura.
4. Cada payload se persiste en la zona raw y se normaliza en la misma transacción.
5. El resultado del run conserva conteos y errores operativos.

## Límites deliberados de esta entrega

- No existe todavía un comando ni endpoint que ejecute el worker con credenciales reales.
- El rate limiter actual es por proceso y limita a 120 solicitudes/minuto. En Fase 2 será compartido entre workers mediante Redis.
- Webhooks, reconciliación incremental y cola de errores entran en la siguiente entrega.

