import { useState } from "react";
import { Filters, query } from "../api";
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

const states: Record<string, string> = {
  received: "Recibido",
  diagnosing: "En diagnóstico",
  awaiting_approval: "Esperando aprobación",
  approved: "Aprobado",
  in_progress: "En reparación",
  ready: "Listo",
  delivered: "Entregado",
  cancelled: "Cancelado",
};
const next: Record<string, string[]> = {
  received: ["diagnosing", "cancelled"],
  diagnosing: ["awaiting_approval", "approved", "cancelled"],
  awaiting_approval: ["approved", "cancelled"],
  approved: ["in_progress", "cancelled"],
  in_progress: ["ready", "cancelled"],
  ready: ["delivered", "in_progress", "cancelled"],
  delivered: [],
  cancelled: [],
};

export default function Repairs({ filters }: { filters: Filters }) {
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState("");
  const [id, setId] = useState(() => crypto.randomUUID());
  const resource = useResource<Row>(
    `/operations/repairs${query(filters, { q, offset })}`,
  );
  const mutation = useMutation();
  const data = resource.data || {};
  return (
    <>
      <h2>Servicio técnico y garantías</h2>
      <p className="muted">
        Cola por fecha de recepción; resultados por fecha de entrega. Cobros y
        costos son propios del taller, no se añaden de nuevo a las ventas de
        Alegra.
      </p>
      <State {...resource} />
      <Notice message={mutation.message} />
      <Table
        title="Cola actual (sin límite de fecha)"
        items={data.queue ? [row(data.queue)] : []}
        columns={[
          ["open_jobs", "Abiertos"],
          ["awaiting_approval", "Esperando aprobación"],
          ["overdue", "Atrasados"],
          ["warranty_returns", "Garantías registradas"],
        ]}
      />
      <Table
        title="Resultados de trabajos entregados en el período"
        items={rows(data.metrics)}
        columns={[
          ["currency_code", "Moneda"],
          ["delivered_jobs", "Entregados"],
          ["average_turnaround_days", "Días promedio"],
          [
            "recorded_revenue",
            "Cobros registrados",
            (r) => money(num(r.recorded_revenue), str(r.currency_code)),
          ],
          [
            "recorded_margin",
            "Margen con costo completo",
            (r) => money(num(r.recorded_margin), str(r.currency_code)),
          ],
          ["missing_cost_or_revenue", "Datos incompletos"],
        ]}
      />
      <details className="advanced-settings">
        <summary>Recibir equipo / registrar garantía</summary>
        <form
          className="operations-form"
          onSubmit={(e) => {
            const f = fields(e);
            void mutation.run(
              () =>
                save("/operations/repairs", {
                  ...f,
                  id,
                  currency_code: filters.currency || "COP",
                  customer_phone: optional(f.customer_phone),
                  serial_number: optional(f.serial_number),
                  promised_on: optional(f.promised_on),
                  warranty_parent_id: optional(f.warranty_parent_id),
                  warranty_days: Number(f.warranty_days),
                }),
              () => {
                setId(crypto.randomUUID());
                resource.refresh();
              },
            );
          }}
        >
          <Input name="customer_name" label="Cliente" required />
          <Input name="customer_phone" label="Teléfono" />
          <Input name="device_description" label="Equipo / modelo" required />
          <Input name="serial_number" label="Serial" />
          <Input name="reported_issue" label="Falla reportada" required />
          <Input name="received_on" label="Recibido el" type="date" required />
          <Input name="promised_on" label="Entrega prometida" type="date" />
          <Input
            name="warranty_days"
            label="Días de garantía"
            type="number"
            min={0}
            value="30"
            required
          />
          <Input
            name="warranty_parent_id"
            label="ID del trabajo original (solo garantía)"
          />
          <button disabled={mutation.busy}>Recibir equipo</button>
        </form>
      </details>
      <label>
        Buscar cliente, equipo o serial
        <input
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setOffset(0);
          }}
        />
      </label>
      <Table
        title="Trabajos recibidos en el período"
        items={rows(data.items)}
        columns={[
          [
            "device_description",
            "Equipo",
            (r) => (
              <button onClick={() => setSelected(str(r.id))}>
                {str(r.device_description)}
              </button>
            ),
          ],
          ["customer_name", "Cliente"],
          ["serial_number", "Serial"],
          ["status", "Estado", (r) => states[str(r.status)]],
          ["promised_on", "Prometido"],
          ["warranty_until", "Garantía hasta"],
        ]}
      />
      <Pager
        offset={offset}
        total={Number(data.total || 0)}
        change={setOffset}
      />
      {selected && (
        <RepairDetail
          key={selected}
          id={selected}
          refreshJobs={resource.refresh}
        />
      )}
    </>
  );
}

function RepairDetail({
  id,
  refreshJobs,
}: {
  id: string;
  refreshJobs: () => void;
}) {
  const resource = useResource<Row>(`/operations/repairs/${id}`);
  const mutation = useMutation();
  const [partId, setPartId] = useState(() => crypto.randomUUID());
  const data = resource.data || {};
  const refresh = () => {
    resource.refresh();
    refreshJobs();
  };
  return (
    <section className="table-card">
      <State {...resource} />
      <Notice message={mutation.message} />
      {resource.data && (
        <>
          <h3>
            {str(data.device_description)} · {str(data.serial_number)}
          </h3>
          <p>ID de trabajo: {id}</p>
          <p>Falla: {str(data.reported_issue)}</p>
          <form
            className="operations-form"
            onSubmit={(e) => {
              const f = fields(e);
              void mutation.run(
                () =>
                  save(
                    `/operations/repairs/${id}`,
                    {
                      status: f.status,
                      diagnosis: optional(f.diagnosis),
                      quoted_amount: f.quoted_amount || null,
                      charged_amount: f.charged_amount || null,
                      labour_cost: f.labour_cost || null,
                      costs_complete: f.costs_complete === "on",
                      promised_on: optional(f.promised_on),
                      warranty_days: Number(f.warranty_days),
                      invoice_alegra_id: optional(f.invoice_alegra_id),
                    },
                    "PATCH",
                  ),
                refresh,
              );
            }}
          >
            <label>
              Estado
              <select name="status" defaultValue={str(data.status)}>
                {[str(data.status), ...(next[str(data.status)] || [])].map(
                  (s) => (
                    <option key={s} value={s}>
                      {states[s]}
                    </option>
                  ),
                )}
              </select>
            </label>
            <Input
              name="diagnosis"
              label="Diagnóstico"
              value={String(data.diagnosis || "")}
            />
            <Input
              name="quoted_amount"
              label="Cotización"
              type="number"
              min={0}
              step="0.01"
              value={data.quoted_amount == null ? "" : str(data.quoted_amount)}
            />
            <Input
              name="charged_amount"
              label="Cobro real (vacío = desconocido)"
              type="number"
              min={0}
              step="0.01"
              value={
                data.charged_amount == null ? "" : str(data.charged_amount)
              }
            />
            <Input
              name="labour_cost"
              label="Costo de mano de obra (vacío = desconocido)"
              type="number"
              min={0}
              step="0.01"
              value={data.labour_cost == null ? "" : str(data.labour_cost)}
            />
            <Input
              name="promised_on"
              label="Fecha prometida"
              type="date"
              value={String(data.promised_on || "")}
            />
            <label>
              <input
                type="checkbox"
                name="costs_complete"
                defaultChecked={Boolean(data.costs_complete)}
              />
              Certifico que registré todos los repuestos y la mano de obra
            </label>
            <Input
              name="warranty_days"
              label="Días de garantía"
              type="number"
              min={0}
              value={Number(data.warranty_days || 0)}
            />
            <Input
              name="invoice_alegra_id"
              label="ID factura de Alegra vinculada"
              value={String(data.invoice_alegra_id || "")}
            />
            <button disabled={mutation.busy}>Guardar trabajo</button>
          </form>
          <form
            className="operations-form"
            onSubmit={(e) => {
              const f = fields(e);
              void mutation.run(
                () =>
                  save(`/operations/repairs/${id}/parts`, {
                    ...f,
                    id: partId,
                    product_key: f.product_key ? Number(f.product_key) : null,
                  }),
                () => {
                  setPartId(crypto.randomUUID());
                  refresh();
                },
              );
            }}
          >
            <Input name="description" label="Repuesto / material" required />
            <Input
              name="product_key"
              label="Clave producto (opcional)"
              type="number"
              min={1}
            />
            <Input
              name="quantity"
              label="Cantidad"
              type="number"
              min={0.0001}
              step="0.0001"
              required
            />
            <Input
              name="unit_cost"
              label="Costo unitario real"
              type="number"
              min={0}
              step="0.01"
              required
            />
            <button
              disabled={
                mutation.busy ||
                ["delivered", "cancelled"].includes(str(data.status))
              }
            >
              Registrar costo de repuesto
            </button>
          </form>
          <Table
            title="Repuestos registrados"
            items={rows(data.parts)}
            columns={[
              ["description", "Repuesto"],
              ["quantity", "Cantidad"],
              [
                "unit_cost",
                "Costo unitario",
                (r) => money(num(r.unit_cost), str(data.currency_code)),
              ],
            ]}
          />
          <Table
            title="Auditoría (últimos 100 eventos)"
            items={rows(data.history)}
            columns={[
              ["created_at", "Fecha"],
              ["action", "Acción"],
            ]}
          />
        </>
      )}
    </section>
  );
}
