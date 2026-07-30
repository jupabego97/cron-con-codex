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

type Tab = "overview" | "sales" | "purchases" | "payments" | "inventory";
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
  ["inventory", "Inventario"],
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

function DomainView({ title, data, amountKey }: { title: string; data: Record<string, unknown>; amountKey: string }) {
  const summary = (data.summary || []) as Row[];
  const series = (data.series || []) as Row[];
  const sections = Object.entries(data).filter(([key]) => !["summary", "series"].includes(key));
  return <><h2>{title}</h2><section className="cards">{summary.map((row) => <article className="metric-card compact" key={String(row.currency_code || row.label)}><p>{String(row.currency_code || row.label || "Total")}</p><strong>{money(row.amount ?? row.purchase_amount, String(row.currency_code || "COP"))}</strong><small>{number(row.documents ?? row.payments ?? row.quantity)} registros</small></article>)}</section><Chart title={`${title} en el tiempo`} data={series} dataKey={amountKey} moneyValue />{sections.map(([key, value]) => Array.isArray(value) ? <Chart key={key} title={labelFor(key)} data={value as Row[]} dataKey={amountKey} moneyValue /> : null)}</>;
}

function Inventory({ data }: { data: Record<string, unknown> }) {
  const snapshot = (data.snapshot || {}) as Record<string, unknown>;
  const stockSummary = (snapshot.summary || []) as Row[];
  const stockItems = (snapshot.items || []) as Row[];
  const summary = (data.summary || []) as Row[];
  const recent = (data.recent || []) as Row[];
  return <><h2>Inventario</h2><p className="muted">Existencias actuales por producto y bodega. Última captura: {String(snapshot.captured_at || "pendiente")}</p>{stockSummary.length ? <><section className="cards">{stockSummary.map((row) => <article className="metric-card" key="stock"><p>Existencias actuales</p><strong>{number(row.units)} unidades</strong><small>{number(row.products)} referencias · valor a costo: {money(row.inventory_value)}</small></article>)}</section><Chart title="Existencias por producto" data={(snapshot.by_product || []) as Row[]} dataKey="quantity" /><Chart title="Existencias por bodega" data={(snapshot.by_warehouse || []) as Row[]} dataKey="quantity" /><section className="table-card"><h3>Stock actual</h3><table><thead><tr><th>Producto</th><th>Bodega</th><th>Unidades</th><th>Costo unitario</th><th>Valor</th></tr></thead><tbody>{stockItems.map((row, index) => <tr key={`${row.product}-${row.warehouse}-${index}`}><td>{String(row.product)}</td><td>{String(row.warehouse)}</td><td>{number(row.quantity_on_hand)}</td><td>{money(row.unit_cost)}</td><td>{money(row.inventory_value)}</td></tr>)}</tbody></table></section></> : <div className="warning">Aún no existe un snapshot de inventario. Ejecuta la captura de inventario y luego refresca el mart.</div>}<h3 className="section-title">Movimientos de inventario</h3><p className="muted">Ajustes manuales y transferencias; no equivalen al stock disponible.</p><section className="cards">{summary.map((row) => <article className="metric-card compact" key={String(row.label)}><p>{labelFor(String(row.label))}</p><strong>{number(row.quantity)}</strong><small>unidades netas</small></article>)}</section><Chart title="Movimientos por producto" data={(data.by_product || []) as Row[]} dataKey="quantity" /><Chart title="Movimientos por bodega" data={(data.by_warehouse || []) as Row[]} dataKey="quantity" /><section className="table-card"><h3>Últimos movimientos</h3><table><thead><tr><th>Fecha</th><th>Producto</th><th>Bodega</th><th>Tipo</th><th>Cantidad</th></tr></thead><tbody>{recent.map((row, index) => <tr key={`${row.document_number}-${index}`}><td>{String(row.date || "")}</td><td>{String(row.product)}</td><td>{String(row.warehouse)}</td><td>{labelFor(String(row.movement_direction))}</td><td>{number(row.quantity_delta)}</td></tr>)}</tbody></table></section></>;
}

function Chart({ title, data, dataKey, moneyValue = false }: { title: string; data: Row[]; dataKey: string; moneyValue?: boolean }) {
  const rows = useMemo(() => data.map((row) => ({ ...row, label: String(row.label || row.period || "") })), [data]);
  if (!rows.length) return <section className="chart-card"><h3>{title}</h3><p className="muted">Sin datos para estos filtros.</p></section>;
  const trend = "period" in rows[0];
  return <section className="chart-card"><h3>{title}</h3><div className="chart">{trend ? <ResponsiveContainer><LineChart data={rows}><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="label" /><YAxis /><Tooltip formatter={(value) => moneyValue ? money(value as number) : number(value as number)} /><Line type="monotone" dataKey={dataKey} stroke="#76e4d0" strokeWidth={2} /></LineChart></ResponsiveContainer> : <ResponsiveContainer><BarChart data={rows}><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="label" hide={rows.length > 8} /><YAxis /><Tooltip formatter={(value) => moneyValue ? money(value as number) : number(value as number)} /><Bar dataKey={dataKey} fill="#7c83fd" radius={[4, 4, 0, 0]} /></BarChart></ResponsiveContainer>}</div></section>;
}

function labelFor(value: string): string {
  return ({ by_product: "Por producto", by_supplier: "Por proveedor", by_type: "Por tipo", by_contact: "Por contacto", by_seller: "Por vendedor", by_warehouse: "Por bodega", by_customer: "Por cliente", by_status: "Por estado", adjustment: "Ajustes", transfer_in: "Entradas por transferencia", transfer_out: "Salidas por transferencia" }[value] || value).replaceAll("_", " ");
}
