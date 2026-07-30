import { FormEvent, useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { api, Filters, Option, query } from "./api";
import { money, number } from "./format";

type Tab = "overview" | "sales" | "purchases" | "payments" | "customers" | "products" | "kpis" | "inventory" | "alerts";
type Row = Record<string, string | number | null>;
type FilterData = {
  date_range: { min_date?: string; max_date?: string };
  currencies: Option[];
  products: Option[];
  sellers: Option[];
  warehouses: Option[];
  document_statuses: Option[];
};
type Overview = { current: Row[]; previous: Row[]; series: Row[] };

const tabs: Array<[Tab, string]> = [
  ["overview", "Resumen"],
  ["sales", "Ventas"],
  ["purchases", "Compras"],
  ["payments", "Pagos"],
  ["customers", "Clientes"],
  ["products", "Productos"],
  ["kpis", "Indicadores"],
  ["inventory", "Inventario"],
  ["alerts", "Alertas"],
];

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

function initialFilters(): Filters {
  const today = new Date();
  const previous = new Date(today);
  previous.setDate(today.getDate() - 29);
  return { from_date: previous.toISOString().slice(0, 10), to_date: todayIso() };
}

export default function App() {
  const [authenticated, setAuthenticated] = useState<boolean | null>(null);
  const [filters, setFilters] = useState<Filters>(initialFilters);
  const [filterData, setFilterData] = useState<FilterData | null>(null);
  const [tab, setTab] = useState<Tab>("overview");
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const [status, setStatus] = useState<Row | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api<{ authenticated: boolean }>("/dashboard/session")
      .then((response) => setAuthenticated(response.authenticated))
      .catch(() => setAuthenticated(false));
  }, []);

  useEffect(() => {
    if (!authenticated) return;
    api<FilterData>("/analytics/filters")
      .then(setFilterData)
      .catch((requestError: Error) => setError(requestError.message));
    api<Row>("/analytics/refresh-status")
      .then(setStatus)
      .catch((requestError: Error) => setError(requestError.message));
  }, [authenticated]);

  useEffect(() => {
    if (!authenticated) return;
    setLoading(true);
    setError(null);
    api<Record<string, unknown>>(`/analytics/${tab}${query(filters)}`)
      .then(setData)
      .catch((requestError: Error) => setError(requestError.message))
      .finally(() => setLoading(false));
  }, [authenticated, filters, tab]);

  if (authenticated === null) return <main className="splash">Cargando Retail Intelligence…</main>;
  if (!authenticated) return <Login onLogin={() => setAuthenticated(true)} />;

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">RETAIL INTELLIGENCE</p>
          <h1>Tablero de negocio</h1>
        </div>
        <button
          className="text-button"
          onClick={() => api<void>("/dashboard/session", { method: "DELETE" }).then(() => setAuthenticated(false))}
        >
          Cerrar sesión
        </button>
      </header>
      <FiltersBar filters={filters} setFilters={setFilters} options={filterData} />
      {status && <RefreshNotice status={status} />}
      <nav className="tabs" aria-label="Áreas del tablero">
        {tabs.map(([value, label]) => (
          <button className={tab === value ? "active" : ""} key={value} onClick={() => setTab(value)}>
            {label}
          </button>
        ))}
      </nav>
      {error && <div className="error">{error}</div>}
      {loading ? <div className="loading">Actualizando indicadores…</div> : <DashboardTab tab={tab} data={data} />}
    </main>
  );
}

function Login({ onLogin }: { onLogin: () => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError(null);
    try {
      await api<void>("/dashboard/session", { method: "POST", body: JSON.stringify({ password }) });
      onLogin();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "No fue posible ingresar.");
    } finally {
      setLoading(false);
    }
  }
  return (
    <main className="login-page">
      <form className="login-card" onSubmit={submit}>
        <p className="eyebrow">RETAIL INTELLIGENCE</p>
        <h1>Acceso al tablero</h1>
        <p>Ingresa la contraseña configurada para la plataforma.</p>
        <label>
          Contraseña
          <input autoFocus type="password" value={password} onChange={(event) => setPassword(event.target.value)} required />
        </label>
        {error && <div className="error">{error}</div>}
        <button className="primary-button" disabled={loading}>{loading ? "Ingresando…" : "Ingresar"}</button>
      </form>
    </main>
  );
}

function FiltersBar({ filters, setFilters, options }: { filters: Filters; setFilters: (value: Filters) => void; options: FilterData | null }) {
  const update = (field: keyof Filters, value: string) => setFilters({ ...filters, [field]: value || undefined });
  const quickRange = (days: number) => {
    const to = new Date();
    const from = new Date(to);
    from.setDate(to.getDate() - (days - 1));
    setFilters({ ...filters, from_date: from.toISOString().slice(0, 10), to_date: to.toISOString().slice(0, 10) });
  };
  return (
    <section className="filters">
      <div className="quick-ranges">
        {[30, 90, 365].map((days) => <button key={days} onClick={() => quickRange(days)}>Últimos {days} días</button>)}
      </div>
      <label>Desde<input type="date" value={filters.from_date} onChange={(event) => update("from_date", event.target.value)} /></label>
      <label>Hasta<input type="date" value={filters.to_date} onChange={(event) => update("to_date", event.target.value)} /></label>
      <Select label="Moneda" value={filters.currency} options={options?.currencies} onChange={(value) => update("currency", value)} />
      <Select label="Producto" value={filters.product_key} options={options?.products} onChange={(value) => update("product_key", value)} />
      <Select label="Vendedor" value={filters.seller_key} options={options?.sellers} onChange={(value) => update("seller_key", value)} />
      <Select label="Bodega" value={filters.warehouse_key} options={options?.warehouses} onChange={(value) => update("warehouse_key", value)} />
      <Select label="Estado" value={filters.document_status} options={options?.document_statuses} onChange={(value) => update("document_status", value)} />
    </section>
  );
}

function Select({ label, value, options, onChange }: { label: string; value?: string; options?: Option[]; onChange: (value: string) => void }) {
  return <label>{label}<select value={value || ""} onChange={(event) => onChange(event.target.value)}><option value="">Todos</option>{options?.map((option) => <option key={String(option.value)} value={option.value}>{option.label || option.value}{option.reference ? ` · ${option.reference}` : ""}</option>)}</select></label>;
}

function RefreshNotice({ status }: { status: Row }) {
  if (status.status === "never_run") return <div className="warning">El mart aún no tiene ejecuciones exitosas.</div>;
  return <div className={status.is_stale ? "warning" : "refresh-status"}>Mart: {status.is_stale ? "actualización pendiente" : "actualizado"} · {String(status.finished_at || status.started_at || "")}</div>;
}

function DashboardTab({ tab, data }: { tab: Tab; data: Record<string, unknown> | null }) {
  if (!data) return <div className="empty">No hay datos para el período seleccionado.</div>;
  if (tab === "overview") return <Overview data={data as unknown as Overview} />;
  if (tab === "sales") return <DomainView title="Ventas" data={data} amountKey="amount" />;
  if (tab === "purchases") return <DomainView title="Compras" data={data} amountKey="amount" />;
  if (tab === "payments") return <DomainView title="Pagos" data={data} amountKey="amount" />;
  if (tab === "customers") return <DomainView title="Clientes" data={data} amountKey="amount" />;
  if (tab === "products") return <DomainView title="Productos y rotaciÃ³n" data={data} amountKey="amount" />;
  if (tab === "kpis") return <KpiView data={data} />;
  if (tab === "alerts") return <Alerts data={data} />;
  return <Inventory data={data} />;
}

function Overview({ data }: { data: Overview }) {
  const current = data.current || [];
  const previous = data.previous || [];
  return <>
    <h2>Resumen comercial</h2>
    <p className="muted">La venta neta incorpora las notas crédito como valores negativos.</p>
    <section className="cards">{current.map((row) => {
      const comparison = previous.find((item) => item.currency_code === row.currency_code);
      return <MetricCard key={String(row.currency_code)} currency={String(row.currency_code || "COP")} current={row} previous={comparison} />;
    })}</section>
    <Chart title="Venta neta en el tiempo" data={data.series || []} dataKey="amount" moneyValue />
  </>;
}

function MetricCard({ currency, current, previous }: { currency: string; current: Row; previous?: Row }) {
  const sale = Number(current.net_sales || 0);
  const before = Number(previous?.net_sales || 0);
  const change = before ? ((sale - before) / Math.abs(before)) * 100 : null;
  return <article className="metric-card"><p>{currency} · Venta neta</p><strong>{money(sale, currency)}</strong><small>{change === null ? "Sin período comparable" : `${change >= 0 ? "+" : ""}${change.toFixed(1)}% vs. período anterior`}</small><dl><div><dt>Unidades</dt><dd>{number(current.units)}</dd></div><div><dt>Documentos</dt><dd>{number(current.documents)}</dd></div><div><dt>Ticket promedio</dt><dd>{money(current.average_ticket, currency)}</dd></div></dl></article>;
}

function percent(value: string | number | null | undefined): string {
  return `${number(value)}%`;
}

function KpiView({ data }: { data: Record<string, unknown> }) {
  const sales = (data.sales || []) as Row[];
  const customers = (data.customers || []) as Row[];
  const purchases = (data.purchases || []) as Row[];
  const inventory = (data.inventory || []) as Row[];
  const lowCoverage = (data.low_coverage || []) as Row[];
  const excessCoverage = (data.excess_coverage || []) as Row[];
  const slowInventory = (data.slow_inventory || []) as Row[];
  return <>
    <h2>Indicadores clave</h2>
    <p className="muted">KPIs calculados desde el mart. La cobertura usa la demanda neta del período seleccionado y el último snapshot de Alegra.</p>
    <h3 className="section-title">Venta y devoluciones</h3>
    <section className="cards">{sales.map((row) => {
      const currency = String(row.currency_code || "COP");
      return <article className="metric-card" key={currency}><p>{currency} · Unidades por transacción</p><strong>{number(row.units_per_transaction)}</strong><small>unidades netas por documento</small><dl><div><dt>Venta neta</dt><dd>{money(row.net_sales, currency)}</dd></div><div><dt>Precio neto/unidad</dt><dd>{money(row.average_unit_sale, currency)}</dd></div><div><dt>Notas crédito</dt><dd>{percent(row.credit_note_rate)}</dd></div></dl></article>;
    })}</section>
    <h3 className="section-title">Clientes</h3>
    <section className="cards">{customers.map((row) => <article className="metric-card" key={String(row.currency_code || "COP")}><p>{String(row.currency_code || "COP")} · Clientes activos</p><strong>{number(row.active_customers)}</strong><small>con compra o nota crédito en el período</small><dl><div><dt>Recurrentes</dt><dd>{percent(row.repeat_customer_rate)}</dd></div><div><dt>Nuevos</dt><dd>{number(row.new_customers)}</dd></div><div><dt>Concentración Top 5</dt><dd>{percent(row.top_5_customer_concentration)}</dd></div></dl></article>)}</section>
    <h3 className="section-title">Compras y proveedores</h3>
    <section className="cards">{purchases.map((row) => {
      const currency = String(row.currency_code || "COP");
      return <article className="metric-card" key={currency}><p>{currency} · Ticket promedio de compra</p><strong>{money(row.average_purchase_ticket, currency)}</strong><small>{number(row.documents)} documentos de compra</small><dl><div><dt>Costo por unidad</dt><dd>{money(row.average_unit_cost, currency)}</dd></div><div><dt>Proveedores</dt><dd>{number(row.suppliers)}</dd></div><div><dt>Concentración Top 5</dt><dd>{percent(row.top_5_supplier_concentration)}</dd></div></dl></article>;
    })}</section>
    <h3 className="section-title">Salud del inventario</h3>
    <section className="cards">{inventory.map((row) => <article className="metric-card" key="inventory-health"><p>Referencias en el último snapshot</p><strong>{number(row.products)}</strong><small>valor a costo reportado: {money(row.inventory_value)}</small><dl><div><dt>Sin disponibilidad</dt><dd>{number(row.unavailable_products)}</dd></div><div><dt>Cobertura &lt; 14 días</dt><dd>{number(row.low_coverage_products)}</dd></div><div><dt>Sin demanda</dt><dd>{money(row.no_demand_value)}</dd></div></dl></article>)}</section>
    <Chart title="Productos con cobertura menor a 14 días" data={lowCoverage} dataKey="coverage_days" />
    <StockPriorityTable title="Reposición prioritaria" rows={lowCoverage} coverage />
    <StockPriorityTable title="Exceso de cobertura (120 días o más)" rows={excessCoverage} coverage />
    <StockPriorityTable title="Inventario sin demanda en el período" rows={slowInventory} />
  </>;
}

function StockPriorityTable({ title, rows, coverage = false }: { title: string; rows: Row[]; coverage?: boolean }) {
  if (!rows.length) return <section className="table-card"><h3>{title}</h3><p className="muted">Sin productos para estos criterios.</p></section>;
  return <section className="table-card"><h3>{title}</h3><table><thead><tr><th>Producto</th><th>Stock</th><th>Unidades vendidas</th>{coverage && <th>Cobertura</th>}<th>Valor a costo</th></tr></thead><tbody>{rows.map((row, index) => <tr key={`${row.label}-${index}`}><td>{String(row.label)}</td><td>{number(row.quantity_on_hand)}</td><td>{number(row.period_units_sold)}</td>{coverage && <td>{number(row.coverage_days)} días</td>}<td>{money(row.inventory_value)}</td></tr>)}</tbody></table></section>;
}

function DomainView({ title, data, amountKey }: { title: string; data: Record<string, unknown>; amountKey: string }) {
  const summary = (data.summary || []) as Row[];
  const series = (data.series || []) as Row[];
  const sections = Object.entries(data).filter(([key]) => !["summary", "series"].includes(key));
  return <><h2>{title}</h2><section className="cards">{summary.map((row) => <article className="metric-card compact" key={String(row.currency_code || row.label)}><p>{String(row.currency_code || row.label || "Total")}</p><strong>{money(row.amount ?? row.purchase_amount, String(row.currency_code || "COP"))}</strong><small>{number(row.documents ?? row.payments ?? row.quantity)} registros</small></article>)}</section><Chart title={`${title} en el tiempo`} data={series} dataKey={amountKey} moneyValue />{sections.map(([key, value]) => Array.isArray(value) ? <Chart key={key} title={labelFor(key)} data={value as Row[]} dataKey={key === "stock_coverage" ? "coverage_days" : amountKey} moneyValue={key !== "stock_coverage"} /> : null)}</>;
}

function Inventory({ data }: { data: Record<string, unknown> }) {
  const snapshot = (data.snapshot || {}) as Record<string, unknown>;
  const stockSummary = (snapshot.summary || []) as Row[];
  const stockItems = (snapshot.items || []) as Row[];
  const summary = (data.summary || []) as Row[];
  const recent = (data.recent || []) as Row[];
  return <><h2>Inventario</h2><p className="muted">Existencias actuales por producto y bodega. Última captura: {String(snapshot.captured_at || "pendiente")}</p>{stockSummary.length ? <><section className="cards">{stockSummary.map((row) => <article className="metric-card" key="stock"><p>Existencias actuales</p><strong>{number(row.units)} unidades</strong><small>{number(row.products)} referencias · valor a costo: {money(row.inventory_value)}</small></article>)}</section><Chart title="Existencias por producto" data={(snapshot.by_product || []) as Row[]} dataKey="quantity" /><Chart title="Existencias por bodega" data={(snapshot.by_warehouse || []) as Row[]} dataKey="quantity" /><section className="table-card"><h3>Stock actual</h3><table><thead><tr><th>Producto</th><th>Bodega</th><th>Unidades</th><th>Costo unitario</th><th>Valor</th></tr></thead><tbody>{stockItems.map((row, index) => <tr key={`${row.product}-${row.warehouse}-${index}`}><td>{String(row.product)}</td><td>{String(row.warehouse)}</td><td>{number(row.quantity_on_hand)}</td><td>{money(row.unit_cost)}</td><td>{money(row.inventory_value)}</td></tr>)}</tbody></table></section></> : <div className="warning">Aún no existe un snapshot de inventario. Ejecuta la captura de inventario y luego refresca el mart.</div>}<h3 className="section-title">Movimientos de inventario</h3><p className="muted">Ajustes manuales y transferencias; no equivalen al stock disponible.</p><section className="cards">{summary.map((row) => <article className="metric-card compact" key={String(row.label)}><p>{labelFor(String(row.label))}</p><strong>{number(row.quantity)}</strong><small>unidades netas</small></article>)}</section><Chart title="Movimientos por producto" data={(data.by_product || []) as Row[]} dataKey="quantity" /><Chart title="Movimientos por bodega" data={(data.by_warehouse || []) as Row[]} dataKey="quantity" /><section className="table-card"><h3>Últimos movimientos</h3><table><thead><tr><th>Fecha</th><th>Producto</th><th>Bodega</th><th>Tipo</th><th>Cantidad</th></tr></thead><tbody>{recent.map((row, index) => <tr key={`${row.document_number}-${index}`}><td>{String(row.date || "")}</td><td>{String(row.product)}</td><td>{String(row.warehouse)}</td><td>{labelFor(String(row.movement_direction))}</td><td>{number(row.quantity_delta)}</td></tr>)}</tbody></table></section></>;
}

function Alerts({ data }: { data: Record<string, unknown> }) {
  const summary = (data.summary || []) as Row[];
  return <><h2>Alertas operativas</h2><p className="muted">PriorizaciÃ³n basada en el Ãºltimo snapshot y ventas de los Ãºltimos 90 dÃ­as.</p><section className="cards">{summary.map((row) => <article className="metric-card" key={String(row.label)}><p>{String(row.label)}</p><strong>{number(row.count)}</strong><small>productos que requieren revisiÃ³n</small></article>)}</section><Chart title="Agotados con venta reciente" data={(data.stockouts || []) as Row[]} dataKey="quantity" /><Chart title="Inventario negativo" data={(data.negative_stock || []) as Row[]} dataKey="quantity" /><Chart title="Stock sin ventas en 90 dÃ­as" data={(data.slow_stock || []) as Row[]} dataKey="quantity" /></>;
}

function Chart({ title, data, dataKey, moneyValue = false }: { title: string; data: Row[]; dataKey: string; moneyValue?: boolean }) {
  const rows = useMemo(() => data.map((row) => ({ ...row, label: String(row.label || row.period || "") })), [data]);
  if (!rows.length) return <section className="chart-card"><h3>{title}</h3><p className="muted">Sin datos para estos filtros.</p></section>;
  const trend = "period" in rows[0];
  const axis = { fill: "#c7d4ea", fontSize: 12 };
  const tooltip = {
    contentStyle: { background: "#101a2d", border: "1px solid #536b91", borderRadius: 8, color: "#ffffff" },
    labelStyle: { color: "#dbeafe" },
    itemStyle: { color: "#ffffff" },
    cursor: { fill: "#ffffff10" },
  };
  return <section className="chart-card"><h3>{title}</h3><div className="chart">{trend ? <ResponsiveContainer><LineChart data={rows}><CartesianGrid stroke="#334866" strokeDasharray="3 3" /><XAxis dataKey="label" tick={axis} axisLine={{ stroke: "#536b91" }} tickLine={{ stroke: "#536b91" }} /><YAxis tick={axis} axisLine={{ stroke: "#536b91" }} tickLine={{ stroke: "#536b91" }} /><Tooltip {...tooltip} formatter={(value) => moneyValue ? money(value as number) : number(value as number)} /><Line type="monotone" dataKey={dataKey} stroke="#78f3d3" strokeWidth={2.5} /></LineChart></ResponsiveContainer> : <ResponsiveContainer><BarChart data={rows}><CartesianGrid stroke="#334866" strokeDasharray="3 3" /><XAxis dataKey="label" hide={rows.length > 8} tick={axis} axisLine={{ stroke: "#536b91" }} tickLine={{ stroke: "#536b91" }} /><YAxis tick={axis} axisLine={{ stroke: "#536b91" }} tickLine={{ stroke: "#536b91" }} /><Tooltip {...tooltip} formatter={(value) => moneyValue ? money(value as number) : number(value as number)} /><Bar dataKey={dataKey} fill="#91a7ff" radius={[4, 4, 0, 0]} /></BarChart></ResponsiveContainer>}</div></section>;
}

function labelFor(value: string): string {
  return ({ by_product: "Por producto", best_sellers: "MÃ¡s vendidos", stock_coverage: "Cobertura de stock", recent_customers: "Clientes recientes", by_supplier: "Por proveedor", by_type: "Por tipo", by_contact: "Por contacto", by_seller: "Por vendedor", by_warehouse: "Por bodega", by_customer: "Por cliente", by_status: "Por estado", adjustment: "Ajustes", transfer_in: "Entradas por transferencia", transfer_out: "Salidas por transferencia" }[value] || value).replaceAll("_", " ");
}
