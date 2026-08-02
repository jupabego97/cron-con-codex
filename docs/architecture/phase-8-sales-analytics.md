# Fase 8: reporte comercial de ventas

El endpoint existente `/api/v1/analytics/sales` conserva sus respuestas básicas
y añade agregaciones detalladas calculadas sobre `fact_sales_line`:

- ventas por familia;
- productos más vendidos;
- rendimiento por vendedor;
- clientes principales;
- desglose por estado del documento.

Las métricas financieras se separan en venta facturada, notas crédito, venta neta,
COGS y margen bruto. Las notas crédito se mantienen negativas en la venta neta.
El margen solo se calcula con líneas cuyo costo histórico está disponible, por lo
que el tablero también muestra la cobertura de costos.

El filtro de familia se resuelve contra `dim_product.family_name` y los filtros de
vendedor, estado, producto y bodega permanecen restringidos al tenant configurado.
Las consultas están limitadas a agregados para el tablero; no exponen payloads
crudos ni líneas completas de Alegra.
