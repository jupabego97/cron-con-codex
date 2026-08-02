# Fase 7: familias y reportes de proveedores

## Familia de producto

Alegra entrega la familia como un campo personalizado dentro de `item.customFields`,
con `name` o `label` igual a `FAMILIA`. La ingestión conserva el JSON crudo y
proyecta el valor a `catalog_items.family_name`; el refresco del mart lo copia a
`dim_product.family_name`. Los valores ausentes se presentan en el mart como
`SIN FAMILIA`.

La migración `20260802_09` también backfillea los productos ya almacenados desde
`alegra_entities`, por lo que no requiere volver a llamar la API para clasificarlos.

## Moneda de compras

Cuando Alegra no informa moneda en una factura de compra, el mart usa
`ANALYTICS_DEFAULT_CURRENCY_CODE`, cuyo valor predeterminado es `COP`. Si se
incorpora otra empresa o moneda, se debe configurar explícitamente este valor y
revisar la fuente de moneda antes de mezclar importes.

## Reporte de proveedores

El proveedor histórico se toma de `purchase_bills.provider_alegra_id`, no del
campo personalizado `PROVEEDOR` del producto. El endpoint autenticado
`/api/v1/analytics/suppliers` devuelve:

- resumen y serie temporal de compras;
- ranking de proveedores con participación, unidades, documentos y SKUs;
- compras por familia;
- matriz producto–proveedor;
- variación entre costo mínimo y máximo.

El menú **Proveedores** del dashboard consume únicamente ese endpoint y hereda
los filtros de fecha, moneda, familia, proveedor, producto y bodega disponibles.

## Orden de despliegue

```text
python -m app.cli migrate
python -m app.cli refresh-mart <tenant-uuid>
```

La migración debe aplicarse antes del primer `refresh-mart` posterior al deploy.
