# Fase 6 - Copiloto analitico multi-proveedor

El asistente vive en el mismo servicio FastAPI y puede usar OpenAI o Gemini como modelo de lenguaje. El navegador nunca entrega credenciales al modelo ni consulta PostgreSQL directamente.

## Flujo

Dashboard React -> /api/v1/ai/chat -> RetailAIAgent -> herramientas tipadas
                                                    -> AnalyticsQueryService
                                                    -> data mart tenant-scoped

La primera version es estrictamente de solo lectura. Las herramientas disponibles cubren inventario, reposicion, ventas, ventas por dia y hora, diagnostico de margen, compras/proveedores, pagos, clientes, KPIs, calidad y estado de datos. El modelo no recibe SQL libre y no puede modificar Alegra, el inventario ni las politicas de compra.

## Agente analitico profundo

El agente usa un enrutador deterministico antes de llamar al modelo. El enrutador
identifica intenciones como `ventas_por_dia_y_hora`, `diagnostico_de_margen`,
`reposicion_y_proveedores` o `salud_de_inventario` y entrega una ruta sugerida.
El modelo puede ajustarla, pero no debe repetir una herramienta con los mismos
argumentos.

Las herramientas especializadas son deliberadamente pequenas:

- `get_sales_by_weekday_hour`: compara los siete dias y las franjas 10:00-19:00.
- `get_margin_diagnostics`: compara el periodo actual contra el equivalente anterior y descompone por familia y producto.
- `get_data_quality`: informa faltantes de producto, hora, costo, proveedor, stock negativo y frescura del mart.

Los calculos permanecen en `AnalyticsQueryService`; el modelo interpreta y
explica. Cada respuesta incluye la ruta analitica y las herramientas consultadas.
Si el modelo intenta repetir una consulta identica, el resultado se reutiliza y
la siguiente ronda se fuerza a respuesta sin herramientas para evitar ciclos.

## Calidad y seguridad

- AI_PROVIDER selecciona openai o gemini.
- OPENAI_API_KEY/OPENAI_MODEL se usan con OpenAI.
- GEMINI_API_KEY/GEMINI_MODEL se usan con Gemini; el modelo por defecto es gemini-3.6-flash.
- Todas las consultas se ejecutan con el tenant de la sesion del dashboard.
- El stock negativo se trata como excepcion y requiere reconciliacion antes de comprar.
- Las compras anteriores a 2025 no se usan para reconstruir existencias.
- Las conversaciones y llamadas a herramientas se auditan en ai_conversations, ai_messages y ai_tool_calls.
- El agente conserva la evidencia por tenant y rango de fechas; no expone payloads crudos de Alegra ni SQL al navegador.
- Las herramientas respetan el rango seleccionado en el dashboard, incluida la
  opción **Toda la historia**. Las respuestas siguen limitadas a agregados y a
  los principales registros para evitar payloads descontrolados; no se limita
  artificialmente la cantidad de días del análisis histórico.

## Despliegue

Las migraciones 20260810_14 y 20260811_15 se aplican mediante el pre-deploy existente (python -m app.cli migrate). En Railway configura el proveedor y su clave en el servicio servicio de API y redeploya. No es necesario crear un nuevo servicio para esta fase.
