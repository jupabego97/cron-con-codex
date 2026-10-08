import { useState } from "react";
import { Filters, query } from "../api";
import { money, percent } from "../format";
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

export default function Products({
  filters,
  selected,
  navigate,
}: {
  filters: Filters;
  selected?: string;
  navigate: (tab: string, key?: string) => void;
}) {
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const catalog = useResource<Row>(
    `/operations/products?q=${encodeURIComponent(search)}&offset=${offset}`,
  );
  return (
    <>
      <h2>Ficha de producto 360°</h2>
      <label>
        Buscar nombre o referencia
        <input
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setOffset(0);
          }}
        />
      </label>
      <State {...catalog} />
      <Table
        title="Catálogo"
        items={rows(catalog.data?.items)}
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
          ["reference", "Referencia"],
          ["family_name", "Familia"],
          ["lifecycle", "Ciclo"],
        ]}
      />
      <Pager
        offset={offset}
        total={Number(catalog.data?.total || 0)}
        change={setOffset}
      />
      {selected && (
        <ProductDetail key={selected} product={selected} filters={filters} />
      )}
    </>
  );
}

function ProductDetail({
  product,
  filters,
}: {
  product: string;
  filters: Filters;
}) {
  const [offset, setOffset] = useState(0);
  const resource = useResource<Row>(
    `/operations/products/${product}${query(filters, { offset })}`,
  );
  const mutation = useMutation();
  const data = resource.data || {};
  const profile = row(data.profile);
  return (
    <section>
      <State {...resource} />
      {resource.data && (
        <>
          <h3>{str(profile.name)}</h3>
          <p className="muted">
            Ventas y compras: {filters.from_date} a {filters.to_date}. Stock:
            última captura, independiente del rango.
          </p>
          <Table
            title="Indicadores"
            items={rows(data.sales)}
            columns={[
              ["currency_code", "Moneda"],
              [
                "net_sales",
                "Venta neta",
                (r) => money(num(r.net_sales), str(r.currency_code)),
              ],
              [
                "cost_coverage_value_pct",
                "Cobertura de costo por valor",
                (r) => percent(num(r.cost_coverage_value_pct)),
              ],
              [
                "average_ticket",
                "Ticket",
                (r) => money(num(r.average_ticket), str(r.currency_code)),
              ],
            ]}
          />
          <Table
            title="Existencias actuales"
            items={rows(row(data.stock).by_product)}
            columns={[
              ["label", "Producto"],
              ["quantity", "Stock"],
              [
                "inventory_value",
                "Valor reportado",
                (r) => money(num(r.inventory_value)),
              ],
            ]}
          />
          <details className="advanced-settings">
            <summary>Ciclo comercial y primera disponibilidad</summary>
            <form
              key={str(profile.updated_at || profile.key)}
              className="operations-form"
              onSubmit={(event) => {
                const f = fields(event);
                void mutation.run(
                  () =>
                    save(
                      `/operations/products/${product}/profile`,
                      {
                        lifecycle: f.lifecycle,
                        introduced_on: optional(f.introduced_on),
                        replacement_product_key: f.replacement_product_key
                          ? Number(f.replacement_product_key)
                          : null,
                        notes: optional(f.notes),
                      },
                      "PUT",
                    ),
                  resource.refresh,
                );
              }}
            >
              <label>
                Ciclo
                <select
                  aria-label="Ciclo"
                  name="lifecycle"
                  defaultValue={str(profile.lifecycle)}
                >
                  <option value="active">Activo</option>
                  <option value="on_request">Solo bajo pedido</option>
                  <option value="replaced">Reemplazado</option>
                  <option value="discontinued">Descontinuado</option>
                </select>
              </label>
              <Input
                name="introduced_on"
                label="Primera disponibilidad certificada"
                type="date"
                value={String(profile.introduced_on || "")}
              />
              <Input
                name="replacement_product_key"
                label="Clave del producto reemplazo (opcional)"
                type="number"
                min={1}
                value={String(profile.replacement_product_key || "")}
              />
              <Input
                name="notes"
                label="Motivo / notas"
                value={String(profile.notes || "")}
              />
              <button disabled={mutation.busy}>Guardar perfil</button>
            </form>
            <Notice message={mutation.message} />
            <p className="muted">
              Reponer excluye automáticamente productos reemplazados,
              descontinuados y bajo pedido. La fecha certificada limita los días
              de entrenamiento.
            </p>
          </details>
          <Table
            title="Documentos de venta"
            items={rows(data.documents)}
            columns={[
              ["calendar_date", "Fecha"],
              ["document_type", "Tipo"],
              ["document_number", "Documento"],
              ["document_status", "Estado"],
              ["quantity", "Unidades"],
              [
                "net_sales",
                "Venta neta",
                (r) => money(num(r.net_sales), str(r.currency_code)),
              ],
            ]}
          />
          <Pager
            offset={offset}
            total={Number(row(data.document_page).total || 0)}
            change={setOffset}
          />
          <Table
            title="Últimas 50 compras del período"
            items={rows(data.purchases)}
            columns={[
              ["calendar_date", "Fecha"],
              ["supplier", "Proveedor"],
              ["quantity", "Cantidad"],
              [
                "unit_cost",
                "Costo unitario",
                (r) => money(num(r.unit_cost), str(r.currency_code)),
              ],
            ]}
          />
          <Table
            title="Disponibilidad observada (máximo 365 días)"
            items={rows(data.availability)}
            columns={[
              ["observed_on", "Día"],
              ["sample_count", "Capturas"],
              ["positive_samples", "Capturas con stock"],
              ["last_quantity", "Última cantidad"],
            ]}
          />
        </>
      )}
    </section>
  );
}
