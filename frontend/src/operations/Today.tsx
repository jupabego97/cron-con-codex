import { Table, Row, State, row, rows, str, useResource } from "./Shared";

export default function Today({
  navigate,
}: {
  navigate: (tab: string, product?: string) => void;
}) {
  const resource = useResource<Row>("/operations/today");
  const data = resource.data || {};
  return (
    <>
      <h2>Hoy: qué necesita atención</h2>
      <p className="muted">
        Una cola de acciones; abre la ficha o el módulo correspondiente para
        decidir.
      </p>
      <State {...resource} />
      {Array.isArray(row(data.data).warnings) &&
        (row(data.data).warnings as string[]).map((value) => (
          <p className="warning" key={value}>
            {value}
          </p>
        ))}
      <div className="operation-actions">
        <button onClick={() => navigate("purchase-recommendations")}>
          Ver proveedores para reponer
        </button>
        <button onClick={() => navigate("receiving")}>
          Seguimiento de pedidos
        </button>
        <button onClick={() => navigate("treasury")}>Caja de 4 semanas</button>
      </div>
      <Table
        title="Productos para revisar"
        items={rows(data.products)}
        columns={[
          [
            "name",
            "Producto",
            (r) => (
              <button
                className="text-button"
                onClick={() => navigate("product-workspace", str(r.key))}
              >
                {str(r.name)}
              </button>
            ),
          ],
          ["quantity", "Stock"],
          ["units", "Unidades / 90 días"],
          [
            "action",
            "Acción",
            (r) =>
              r.action === "stockout"
                ? "Agotado con demanda"
                : "Revisar stock sin venta reciente",
          ],
        ]}
      />
      <Table
        title="Pedidos vencidos pendientes de recepción"
        items={rows(data.overdue_orders)}
        columns={[
          ["provider_name", "Proveedor"],
          ["document_number", "Pedido"],
          ["expected_on", "Esperado"],
        ]}
      />
      <Table
        title="Reparaciones atrasadas"
        items={rows(data.overdue_repairs)}
        columns={[
          ["customer_name", "Cliente"],
          ["device_description", "Equipo"],
          ["promised_on", "Prometido"],
          ["status", "Estado"],
        ]}
      />
      <p className="muted">{str(data.scope)}</p>
    </>
  );
}
