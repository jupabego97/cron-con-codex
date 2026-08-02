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


## Ventas por proveedor

La vista de Ventas expone dos atribuciones con significados distintos:

- **Proveedor asociado al producto** agrupa toda la venta por el campo personalizado
  PROVEEDOR del catálogo actual de Alegra. Es útil para medir qué marcas o
  proveedores asociados al catálogo generan demanda, pero no reconstruye el
  proveedor histórico de cada unidad.
- **Proveedor real FIFO** usa sales_cost_allocations y
  inventory_cost_movements para relacionar el COGS de cada línea con la factura
  de compra que originó la capa de inventario. Solo muestra ventas costadas y
  deja explícitamente separado Sin proveedor/costo de apertura cuando la capa
  proviene del inventario inicial, de una devolución o no tiene coincidencia con
  una compra.

La primera vista sirve para decisiones comerciales y de portafolio; la segunda
sirve para margen y rentabilidad de abastecimiento. No se deben sumar ni
interpretar como una única dimensión histórica.
