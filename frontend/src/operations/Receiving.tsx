import { useState } from "react";
import { money } from "../format";
import {
  Input,
  Notice,
  Pager,
  Row,
  State,
  Table,
  fields,
  num,
  optional,
  row,
  rows,
  save,
  str,
  useMutation,
  useResource,
} from "./Shared";

const stages = [
  ["pending_confirmation", "Pendiente de confirmar"],
  ["confirmed", "Confirmado"],
  ["in_transit", "En tránsito"],
  ["closed", "Cerrado"],
  ["cancelled", "Cancelado"],
];

export default function Receiving() {
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState("");
  const resource = useResource<Row>(
    `/operations/orders?q=${encodeURIComponent(q)}&offset=${offset}`,
  );
  const mutation = useMutation();
  return (
    <>
      <h2>Pedidos y recepción</h2>
      <p className="muted">
        Control físico complementario. Registra además las compras y existencias
        en Alegra; esta pantalla no mueve stock ni crea facturas.
      </p>
      <label>
        Buscar proveedor o pedido
        <input
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setOffset(0);
          }}
        />
      </label>
      <State {...resource} />
      <Table
        title="Órdenes de Alegra"
        items={rows(resource.data?.items)}
        columns={[
          [
            "document_number",
            "Pedido",
            (r) => (
              <button onClick={() => setSelected(str(r.alegra_id))}>
                {str(r.document_number || r.alegra_id)}
              </button>
            ),
          ],
          ["provider_name", "Proveedor"],
          [
            "stage",
            "Seguimiento",
            (r) => stages.find((s) => s[0] === r.stage)?.[1] || str(r.stage),
          ],
          ["expected_on", "Fecha acordada"],
          [
            "total",
            "Importe",
            (r) => money(num(r.total), str(r.currency_code || "COP")),
          ],
        ]}
      />
      <Pager
        offset={offset}
        total={Number(resource.data?.total || 0)}
        change={setOffset}
      />
      {selected && (
        <OrderDetail
          key={selected}
          id={selected}
          refreshOrders={resource.refresh}
        />
      )}
      {rows(resource.data?.uncertain).length > 0 && (
        <section className="table-card">
          <h3>Envíos con resultado incierto</h3>
          <p>
            Busca la orden existente en Alegra e indica su ID. Se verificarán
            proveedor, referencia del plan y cantidades; nunca se reenvía el
            POST.
          </p>
          {rows(resource.data?.uncertain).map((r) => (
            <form
              key={str(r.id)}
              className="operations-form"
              onSubmit={(e) => {
                const f = fields(e);
                void mutation.run(
                  () =>
                    save(`/operations/orders/resolve/${str(r.id)}`, {
                      alegra_id: f.alegra_id,
                    }),
                  resource.refresh,
                );
              }}
            >
              <span>{str(r.supplier)}</span>
              <Input name="alegra_id" label="ID de orden en Alegra" required />
              <button disabled={mutation.busy}>Verificar y vincular</button>
            </form>
          ))}
          <Notice message={mutation.message} />
        </section>
      )}
    </>
  );
}

function OrderDetail({
  id,
  refreshOrders,
}: {
  id: string;
  refreshOrders: () => void;
}) {
  const resource = useResource<Row>(
    `/operations/orders/${encodeURIComponent(id)}`,
  );
  const mutation = useMutation();
  const [receiptId, setReceiptId] = useState(() => crypto.randomUUID());
  const data = resource.data || {};
  const tracking = row(data.tracking);
  const refresh = () => {
    resource.refresh();
    refreshOrders();
  };
  return (
    <section className="table-card">
      <h3>Pedido {str(data.document_number || id)}</h3>
      <State {...resource} />
      <Notice message={mutation.message} />
      {Number(data.unmatched_receipts) > 0 && (
        <p role="alert" className="warning">
          El pedido cambió después de recibir mercancía. Hay recepciones sin
          línea coincidente; revisa Alegra antes de volver a pedir.
        </p>
      )}
      {resource.data && (
        <>
          <p>
            Recepción:{" "}
            {data.receipt_status === "complete"
              ? "Completa"
              : data.receipt_status === "partial"
                ? "Parcial"
                : "Pendiente"}
          </p>
          <form
            className="operations-form"
            onSubmit={(e) => {
              const f = fields(e);
              void mutation.run(
                () =>
                  save(
                    `/operations/orders/${encodeURIComponent(id)}/tracking`,
                    {
                      stage: f.stage,
                      expected_on: optional(f.expected_on),
                      notes: optional(f.notes),
                    },
                    "PUT",
                  ),
                refresh,
              );
            }}
          >
            <label>
              Estado
              <select
                aria-label="Estado"
                name="stage"
                defaultValue={String(tracking.stage || "pending_confirmation")}
              >
                {stages.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <Input
              label="Entrega acordada"
              name="expected_on"
              type="date"
              value={String(tracking.expected_on || "")}
            />
            <Input
              label="Motivo / notas"
              name="notes"
              value={String(tracking.notes || "")}
            />
            <button disabled={mutation.busy}>Guardar seguimiento</button>
          </form>
          <Table
            title="Cantidades por línea"
            items={rows(data.lines)}
            columns={[
              ["line_number", "Línea"],
              ["item_name", "Producto"],
              ["quantity", "Pedidas"],
              ["accepted_quantity", "Aceptadas"],
              ["rejected_quantity", "Rechazadas"],
              ["last_received_on", "Última recepción"],
            ]}
          />
          <form
            className="operations-form"
            onSubmit={(e) => {
              const f = fields(e);
              void mutation.run(
                () =>
                  save(
                    `/operations/orders/${encodeURIComponent(id)}/receipts`,
                    {
                      id: receiptId,
                      line_number: Number(f.line_number),
                      received_on: f.received_on,
                      accepted_quantity: f.accepted_quantity,
                      rejected_quantity: f.rejected_quantity,
                      notes: optional(f.notes),
                    },
                  ),
                () => {
                  setReceiptId(crypto.randomUUID());
                  refresh();
                },
              );
            }}
          >
            <label>
              Línea
              <select aria-label="Línea" name="line_number">
                {rows(data.lines).map((l) => (
                  <option key={str(l.line_number)} value={str(l.line_number)}>
                    {str(l.line_number)} · {str(l.item_name)}
                  </option>
                ))}
              </select>
            </label>
            <Input
              name="received_on"
              label="Fecha física de recepción"
              type="date"
              required
            />
            <Input
              name="accepted_quantity"
              label="Unidades aceptadas"
              type="number"
              min={0}
              step="0.0001"
              value="0"
              required
            />
            <Input
              name="rejected_quantity"
              label="Unidades rechazadas"
              type="number"
              min={0}
              step="0.0001"
              value="0"
              required
            />
            <Input name="notes" label="Notas de recepción" />
            <button disabled={mutation.busy || !rows(data.lines).length}>
              Registrar recepción
            </button>
          </form>
          <Table
            title="Recepciones registradas"
            items={rows(data.receipts)}
            columns={[
              ["received_on", "Fecha"],
              ["line_number", "Línea"],
              ["accepted_quantity", "Aceptadas"],
              ["rejected_quantity", "Rechazadas"],
              ["notes", "Notas"],
            ]}
          />
        </>
      )}
    </section>
  );
}
