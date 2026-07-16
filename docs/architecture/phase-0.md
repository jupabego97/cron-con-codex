# Fase 0 — Fundaciones y contención

## Alcance completado en este repositorio

- Repositorio independiente de `cron-job`; no comparte código ni secretos.
- Servicio FastAPI mínimo con comprobaciones de vida y disponibilidad.
- Configuración tipada por variables de entorno.
- Un `.env.example` sin credenciales reales y reglas para que `.env` no se versiona.
- Entorno local reproducible con PostgreSQL y Redis.
- Pruebas, lint y flujo de CI.

## Decisiones de seguridad

1. Ninguna credencial puede estar en código, archivos CSV, logs, fixtures o documentación.
2. `ALEGRA_API_BASIC_TOKEN` se inyecta solo desde un gestor de secretos o variables protegidas del entorno de despliegue.
3. Producción exige `APP_SECRET_KEY`; Fase 1 exigirá también `DATABASE_URL` y las credenciales necesarias para cada worker.
4. Los logs no deben registrar cabeceras HTTP, payloads completos, tokens, contraseñas ni datos personales sin una política de redacción.

## Acciones manuales obligatorias antes de conectar Alegra

1. Revocar y regenerar el token de Alegra usado por el ETL anterior.
2. Rotar la credencial de PostgreSQL expuesta en el ETL anterior.
3. Guardar los nuevos secretos en el gestor de secretos del proveedor de despliegue.
4. Confirmar que los repositorios no contienen secretos históricos antes de hacer público cualquier repositorio.

## Fuera de alcance hasta Fase 1

- Modelos PostgreSQL y migraciones.
- Cliente de Alegra.
- Webhooks, colas y workers.
- Extracción o escritura de datos de producción.

