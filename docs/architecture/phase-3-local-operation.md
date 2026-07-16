# Fase 3 — Operación local controlada

## Comandos

Con el entorno virtual activo y `DATABASE_URL` configurada:

```powershell
python -m app.cli migrate
python -m app.cli create-tenant mi-empresa "Mi Empresa"
python -m app.cli sync-invoices <tenant-uuid> --mode initial
python -m app.cli sync-invoices <tenant-uuid> --mode reconcile --lookback-days 30
python -m app.cli worker
```

El comando de sincronización exige `ALEGRA_API_BASIC_TOKEN`. No existe un valor por defecto y no se imprime el token.

## Flujo local recomendado

1. Crear `.env` desde `.env.example`.
2. Levantar `docker compose up -d postgres redis`.
3. Ejecutar `python -m app.cli migrate`.
4. Crear el tenant.
5. Ejecutar primero un backfill con un tenant de prueba y observar los conteos.
6. Configurar el webhook solo después de validar el backfill.

La aplicación no ejecuta una sincronización automáticamente al arrancar. Toda extracción requiere un comando o worker explícito.

El worker usa la cola PostgreSQL de webhooks; Redis queda reservado para una futura optimización de rate limiting compartido.
