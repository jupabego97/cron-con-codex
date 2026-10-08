import { useState } from "react";
import { money } from "../format";
import {
  Input,
  Notice,
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

export default function Treasury({ currency = "COP" }: { currency?: string }) {
  const resource = useResource<Row>(
    `/operations/treasury?currency=${encodeURIComponent(currency)}`,
  );
  const mutation = useMutation();
  const [entryId, setEntryId] = useState(() => crypto.randomUUID());
  const data = resource.data || {};
  const opening = row(data.opening);
  return (
    <>
      <h2>Caja: próximas 4 semanas · {currency}</h2>
      <p className="muted">
        No es contabilidad ni caja confirmada. Los escenarios se basan en datos
        identificados y en el saldo que certifiques.
      </p>
      <State {...resource} />
      <Notice message={mutation.message} />
      {Array.isArray(data.warnings) &&
        (data.warnings as string[]).map((w) => (
          <p className="warning" key={w}>
            {w}
          </p>
        ))}
      <section className="cards">
        <article className="metric-card">
          <p>Saldo reconstruido a hoy</p>
          <strong>{money(num(data.reconstructed_balance), currency)}</strong>
          <small>
            Saldo certificado al {str(opening.as_of_date)} más flujos
            registrados posteriores.
          </small>
        </article>
        <article className="metric-card">
          <p>Capacidad estimada al final de 4 semanas</p>
          <strong>{money(num(data.available_for_purchases), currency)}</strong>
          <small>
            Escenario conservador. Verifica también los mínimos semanales antes
            de comprometer compras.
          </small>
        </article>
      </section>
      <Table
        title="Escenarios por semana"
        items={rows(data.weeks)}
        columns={[
          ["from_date", "Desde"],
          ["to_date", "Hasta"],
          [
            "payables",
            "Facturas pendientes",
            (r) => money(num(r.payables), currency),
          ],
          [
            "estimated_po_commitments",
            "Pedidos no facturados",
            (r) => money(num(r.estimated_po_commitments), currency),
          ],
          [
            "planned_operating_out",
            "Otros gastos",
            (r) => money(num(r.planned_operating_out), currency),
          ],
          [
            "estimated_receivables",
            "Cobros estimados",
            (r) => money(num(r.estimated_receivables), currency),
          ],
          [
            "conservative_balance",
            "Saldo conservador",
            (r) => money(num(r.conservative_balance), currency),
          ],
          [
            "expected_balance",
            "Saldo esperado",
            (r) => money(num(r.expected_balance), currency),
          ],
        ]}
      />
      <details className="advanced-settings">
        <summary>Certificar saldo de cierre</summary>
        <form
          className="operations-form"
          onSubmit={(e) => {
            const f = fields(e);
            void mutation.run(
              () =>
                save("/operations/treasury/balance", {
                  currency_code: currency,
                  as_of_date: f.as_of_date,
                  amount: f.amount,
                  notes: optional(f.notes),
                }),
              resource.refresh,
            );
          }}
        >
          <Input
            name="as_of_date"
            label="Fecha de cierre"
            type="date"
            required
          />
          <Input
            name="amount"
            label="Saldo real al cierre (incluye todos los movimientos de ese día)"
            type="number"
            step="0.01"
            required
          />
          <Input
            name="notes"
            label="Referencia de conciliación bancaria / caja"
          />
          <button disabled={mutation.busy}>Certificar saldo</button>
        </form>
      </details>
      <details className="advanced-settings">
        <summary>Registrar flujo fuera de Alegra</summary>
        <p>
          Solo gastos o cobros que no están registrados en Alegra, para evitar
          duplicarlos.
        </p>
        <form
          className="operations-form"
          onSubmit={(e) => {
            const f = fields(e);
            void mutation.run(
              () =>
                save("/operations/treasury/entries", {
                  ...f,
                  id: entryId,
                  currency_code: currency,
                }),
              () => {
                setEntryId(crypto.randomUUID());
                resource.refresh();
              },
            );
          }}
        >
          <Input name="entry_date" label="Fecha" type="date" required />
          <Input
            name="amount"
            label="Importe"
            type="number"
            step="0.01"
            min={0.01}
            required
          />
          <label>
            Dirección
            <select name="direction">
              <option value="out">Salida</option>
              <option value="in">Entrada</option>
            </select>
          </label>
          <label>
            Tipo
            <select name="stage">
              <option value="planned">Planificado</option>
              <option value="posted">Ya ocurrió</option>
            </select>
          </label>
          <Input
            name="category"
            label="Categoría (arriendo, nómina…)"
            required
          />
          <Input name="description" label="Descripción" required />
          <button disabled={mutation.busy}>Guardar flujo</button>
        </form>
      </details>
      <Table
        title="Flujos manuales (últimos 100)"
        items={rows(data.entries)}
        columns={[
          ["entry_date", "Fecha"],
          ["description", "Descripción"],
          ["direction", "Dirección"],
          ["stage", "Estado"],
          ["amount", "Importe", (r) => money(num(r.amount), currency)],
          [
            "action",
            "Acción",
            (r) => (
              <button
                disabled={mutation.busy || r.stage !== "planned"}
                onClick={() =>
                  void mutation.run(
                    () =>
                      save(
                        `/operations/treasury/entries/${str(r.id)}/post`,
                        {},
                      ),
                    resource.refresh,
                  )
                }
              >
                Registrar ocurrido hoy
              </button>
            ),
          ],
        ]}
      />
      <p className="muted">{str(data.assumptions)}</p>
    </>
  );
}
