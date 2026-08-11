# Fase 6 — Proveedores del copiloto analitico

El asistente sigue siendo un componente de solo lectura. Las herramientas consultan el mart mediante `AnalyticsQueryService`; ningun proveedor recibe acceso directo a PostgreSQL.

## Proveedores

El servicio API selecciona el proveedor con `AI_PROVIDER`:

- `openai`: usa `OPENAI_API_KEY` y `OPENAI_MODEL` mediante Responses API.
- `gemini`: usa `GEMINI_API_KEY` y `GEMINI_MODEL` mediante Gemini Interactions API.

Para Gemini 3.6 Flash configura:

```text
AI_PROVIDER=gemini
GEMINI_API_KEY=<clave de Google AI Studio>
GEMINI_MODEL=gemini-3.6-flash
```

Las conversaciones Gemini usan el identificador de interaccion de Google para conservar el contexto entre preguntas. La auditoria local mantiene tenant, mensajes, herramientas y resultados. Cambiar de proveedor en una conversacion existente requiere iniciar una conversacion nueva.

## Seguridad

- Las claves viven unicamente en variables de entorno del servicio API.
- La sesion del dashboard y el tenant se validan antes de invocar el agente.
- Las herramientas son tipadas, limitadas y de solo lectura.
- El agente nunca crea compras, modifica Alegra ni altera inventario.
