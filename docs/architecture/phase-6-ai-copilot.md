# Fase 6 - Copiloto analitico con OpenAI

El asistente vive en el mismo servicio FastAPI y usa OpenAI como modelo de lenguaje. El navegador nunca entrega credenciales al modelo ni consulta PostgreSQL directamente.

## Flujo

Dashboard React -> /api/v1/ai/chat -> RetailAIAgent -> herramientas tipadas
                                                    -> AnalyticsQueryService
                                                    -> data mart tenant-scoped

La primera version es estrictamente de solo lectura. Las herramientas disponibles cubren inventario, reposicion, ventas, compras/proveedores, pagos, clientes, KPIs y estado de datos. El modelo no recibe SQL libre y no puede modificar Alegra, el inventario ni las politicas de compra.

## Calidad y seguridad

- OPENAI_API_KEY se configura solamente como variable de entorno del servicio API.
- OPENAI_MODEL es opcional y por defecto es gpt-4.1-mini.
- Todas las consultas se ejecutan con el tenant de la sesion del dashboard.
- El stock negativo se trata como excepcion y requiere reconciliacion antes de comprar.
- Las compras anteriores a 2025 no se usan para reconstruir existencias.
- Las conversaciones y llamadas a herramientas se auditan en ai_conversations, ai_messages y ai_tool_calls.
- El rango maximo por herramienta es de 731 dias y las respuestas se limitan para evitar consultas descontroladas.

## Despliegue

La migracion 20260810_14 se aplica mediante el pre-deploy existente (python -m app.cli migrate). En Railway agrega OPENAI_API_KEY al servicio servicio de API y redeploya. No es necesario crear un nuevo servicio para esta fase.
