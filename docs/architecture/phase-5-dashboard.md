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

El inventario muestra movimiento neto, ajustes y transferencias, no una
valorización contable. Margen histórico, impuestos y descuentos por línea se
mantienen fuera de los KPIs hasta que su fuente sea confiable.
