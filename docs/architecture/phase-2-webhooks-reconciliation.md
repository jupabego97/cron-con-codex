# Fase 2 — Webhooks, cola durable y reconciliación

## Flujo de webhook

1. Alegra envía `new-invoice`, `edit-invoice` o `delete-invoice` a la URL del tenant.
2. El receptor valida el token incluido en la URL y persiste el payload en `inbound_events`.
3. Devuelve `202` sin llamar a la API de Alegra.
4. Un worker reclama el evento mediante `FOR UPDATE SKIP LOCKED`.
5. Para altas y ediciones, consulta la factura canónica en Alegra y realiza el upsert. Para eliminaciones, marca la factura local como eliminada.
6. Un fallo entra en backoff exponencial; tras ocho intentos, queda como `failed` para atención operativa.

El endpoint también devuelve `204` ante el POST vacío de verificación de suscripción. Alegra exige un `2xx` en menos de cinco segundos y elimina la suscripción tras fallos repetidos. [Documentación oficial](https://developer.alegra.com/reference/descripci%C3%B3n-general)

## URL de suscripción

La suscripción se registrará con una URL por tenant:

```text
https://<dominio>/webhooks/alegra/<tenant-slug>?token=<ALEGRA_WEBHOOK_SECRET>
```

El token es una medida compensatoria porque la documentación revisada de Alegra describe una URL de destino, pero no una firma de payload. Debe ser aleatorio, de alta entropía, exclusivo por ambiente y nunca aparecer en logs. La evolución a secretos distintos por tenant se realizará antes de habilitar multiempresa externo.

## Reconciliación

Cada ejecución consulta por fecha los últimos 30 días, recorriendo todas las páginas de cada fecha. Esto repara eventos perdidos y es idempotente. Una revisión histórica más amplia se programa periódicamente para cubrir correcciones antiguas.

