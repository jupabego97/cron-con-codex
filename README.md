# Retail Intelligence Platform

Plataforma propia de inteligencia, analítica y operación para retail tecnológico y servicio técnico. Alegra es la fuente transaccional; este repositorio construye la capa confiable de integración, datos y decisiones.

## Estado

Fase 2: facturas, webhooks, cola durable y reconciliación listos como base de código; no ejecuta
extracciones ni recibe tráfico de producción hasta configurar la infraestructura y los secretos.

## Desarrollo local

1. Copia `.env.example` a `.env` y reemplaza únicamente los valores locales.
2. Crea un entorno virtual con Python 3.11–3.13.
3. Instala `pip install -e ".[dev]"`.
4. Ejecuta `uvicorn app.main:app --app-dir src --reload`.
5. Consulta `http://localhost:8000/healthz`.

Para infraestructura local, ejecuta `docker compose up --build` después de crear `.env`.

## Calidad

```powershell
ruff check .
pytest
```

## Preparar la base de datos

Con PostgreSQL disponible y `DATABASE_URL` configurada:

```powershell
alembic upgrade head
```

No agregues `ALEGRA_API_BASIC_TOKEN` hasta haber rotado la credencial expuesta en el ETL anterior.

## Documentación

- [Fase 0](docs/architecture/phase-0.md)
- [ADR 0001](docs/decisions/0001-modular-monolith.md)
- [Fase 1: sincronización de facturas](docs/architecture/phase-1-invoice-sync.md)
- [Fase 2: webhooks y reconciliación](docs/architecture/phase-2-webhooks-reconciliation.md)
- [Fase 3: operación local controlada](docs/architecture/phase-3-local-operation.md)
