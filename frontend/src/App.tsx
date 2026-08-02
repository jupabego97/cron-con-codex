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

type Tab = "overview" | "sales" | "purchases" | "suppliers" | "payments" | "customers" | "products" | "kpis" | "purchase-recommendations" | "inventory" | "alerts";
type Row = Record<string, string | number | null>;
type SupplierOption = {
  supplier_key?: number | null;
  supplier?: string | null;
  is_modal?: boolean;
  confidence_pct?: number | null;
  unit_share_pct?: number | null;
  purchase_lines?: number | null;
  average_unit_cost?: number | null;
  median_unit_cost?: number | null;
  minimum_unit_cost?: number | null;
  maximum_unit_cost?: number | null;
  last_unit_cost?: number | null;
  last_purchase_date?: string | null;
};
type RecommendationRow = Row & { supplier_options?: SupplierOption[] };
type FilterData = {
  date_range: { min_date?: string; max_date?: string };
  currencies: Option[];
  products: Option[];
  sellers: Option[];
  warehouses: Option[];
  families: Option[];
  suppliers: Option[];
  document_statuses: Option[];
};
type Overview = { current: Row[]; previous: Row[]; series: Row[] };
type ReplenishmentParams = {
  target_coverage_days: number;
  lead_time_days: number;
  safety_days: number;
};

const tabs: Array<[Tab, string]> = [
  ["overview", "Resumen"],
  ["sales", "Ventas"],
  ["purchases", "Compras"],
  ["suppliers", "Proveedores"],
  ["payments", "Pagos"],
  ["customers", "Clientes"],
  ["products", "Productos"],
  ["kpis", "Indicadores"],
  ["purchase-recommendations", "Reponer"],
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
  const [replenishmentParams, setReplenishmentParams] = useState<ReplenishmentParams>({
    target_coverage_days: 30,
    lead_time_days: 7,
    safety_days: 7,
  });

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
    api<Record<string, unknown>>(`/analytics/${tab}${query(filters, tab === "purchase-recommendations" ? replenishmentParams : {})}`)
      .then(setData)
      .catch((requestError: Error) => setError(requestError.message))
      .finally(() => setLoading(false));
  }, [authenticated, filters, tab, replenishmentParams]);

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
      {loading ? <div className="loading">Actualizando indicadores…</div> : <DashboardTab tab={tab} data={data} filters={filters} replenishmentParams={replenishmentParams} setReplenishmentParams={setReplenishmentParams} />}
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
      <Select label="Familia" value={filters.family} options={options?.families} onChange={(value) => update("family", value)} />
      <Select label="Proveedor" value={filters.provider_key} options={options?.suppliers} onChange={(value) => update("provider_key", value)} />
      <Select label="Estado" value={filters.document_status} options={options?.document_statuses} onChange={(value) => update("document_status", value)} />
    </section>
  );
}

function Select({ label, value, options, onChange }: { label: string; value?: string; options?: Option[]; onChange: (value: string) => void }) {
  return <label>{label}<select value={value || ""} onChange={(event) => onChange(event.target.value)}><option value="">Todos</option>{options?.map((option) => <option key={String(option.value)} value={option.value}>{option.label || option.value}{option.reference ? ` · ${option.reference}` : ""}</option>)}</select></label>;
}

function RefreshNotice({ status }: { status: Row }) {
  if (status.status === "never_run") return <div className="warning">El mart aún no tiene ejecuciones exitosas.</div>;
  const costStatus = status.cost_status ? " · Costos: " + String(status.cost_status) : "";
  const className = status.is_stale || status.cost_is_stale ? "warning" : "refresh-status";
  return <div className={className}>Mart: {status.is_stale ? "actualización pendiente" : "actualizado"} · {String(status.finished_at || status.started_at || "")}{costStatus}</div>;
}

function DashboardTab({ tab, data, filters, replenishmentParams, setReplenishmentParams }: { tab: Tab; data: Record<string, unknown> | null; filters: Filters; replenishmentParams: ReplenishmentParams; setReplenishmentParams: (value: ReplenishmentParams) => void }) {
  if (!data) return <div className="empty">No hay datos para el período seleccionado.</div>;
  if (tab === "overview") return <Overview data={data as unknown as Overview} />;
  if (tab === "sales") return <SalesReports data={data} />;
  if (tab === "purchases") return <DomainView title="Compras" data={data} amountKey="amount" />;
  if (tab === "suppliers") return <SupplierReports data={data} />;
  if (tab === "payments") return <DomainView title="Pagos" data={data} amountKey="amount" />;
  if (tab === "customers") return <DomainView title="Clientes" data={data} amountKey="amount" />;
  if (tab === "products") return <DomainView title="Productos y rotaciÃ³n" data={data} amountKey="amount" />;
  if (tab === "kpis") return <KpiView data={data} />;
  if (tab === "purchase-recommendations") return <PurchaseRecommendations data={data} filters={filters} replenishmentParams={replenishmentParams} setReplenishmentParams={setReplenishmentParams} />;
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
    <CostMetrics rows={current} />
    <Chart title="Costo de ventas" data={data.series || []} dataKey="cogs" moneyValue />
    <Chart title="Margen bruto" data={data.series || []} dataKey="gross_margin" moneyValue />
  </>;
}

function MetricCard({ currency, current, previous }: { currency: string; current: Row; previous?: Row }) {
  const sale = Number(current.net_sales || 0);
  const before = Number(previous?.net_sales || 0);
  const change = before ? ((sale - before) / Math.abs(before)) * 100 : null;
  return <article className="metric-card"><p>{currency} · Venta neta</p><strong>{money(sale, currency)}</strong><small>{change === null ? "Sin período comparable" : `${change >= 0 ? "+" : ""}${change.toFixed(1)}% vs. período anterior`}</small><dl><div><dt>Unidades</dt><dd>{number(current.units)}</dd></div><div><dt>Documentos</dt><dd>{number(current.documents)}</dd></div><div><dt>Ticket promedio</dt><dd>{money(current.average_ticket, currency)}</dd></div></dl></article>;
}

function CostMetrics({ rows }: { rows: Row[] }) {
  return <section className="cards">{rows.map((row) => {
    const currency = String(row.currency_code || "COP");
    return <article className="metric-card compact" key={currency + "-cost"}><p>{currency} · Rentabilidad</p><strong>{money(row.gross_margin, currency)}</strong><small>Margen bruto estimado según cobertura disponible</small><dl><div><dt>COGS</dt><dd>{money(row.cogs, currency)}</dd></div><div><dt>Margen %</dt><dd>{percent(row.gross_margin_pct)}</dd></div><div><dt>Costo cubierto</dt><dd>{percent(row.cost_coverage_pct)}</dd></div><div><dt>Líneas parciales</dt><dd>{number(row.partial_cost_lines)}</dd></div><div><dt>Sin costo</dt><dd>{number(row.unavailable_cost_lines)}</dd></div></dl></article>;
  })}</section>;
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
      return <article className="metric-card" key={currency}><p>{currency} · Unidades por transacción</p><strong>{number(row.units_per_transaction)}</strong><small>{row.sales_pace_vs_target_pct == null ? "Meta mensual no configurada" : `${number(row.sales_pace_vs_target_pct)}% del ritmo meta`}</small><dl><div><dt>Venta neta</dt><dd>{money(row.net_sales, currency)}</dd></div><div><dt>Ticket promedio</dt><dd>{money(row.average_ticket, currency)}</dd></div><div><dt>Precio neto/unidad</dt><dd>{money(row.average_unit_sale, currency)}</dd></div><div><dt>Notas crédito</dt><dd>{percent(row.credit_note_rate)}</dd></div></dl></article>;
    })}</section>
    <CostMetrics rows={sales} />
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

function PurchaseRecommendations({ data, filters, replenishmentParams, setReplenishmentParams }: { data: Record<string, unknown>; filters: Filters; replenishmentParams: ReplenishmentParams; setReplenishmentParams: (value: ReplenishmentParams) => void }) {
  const rows = (data.items || []) as RecommendationRow[];
  const excessItems = (data.excess_items || []) as RecommendationRow[];
  const slowItems = (data.slow_items || []) as RecommendationRow[];
  const parameters = (data.parameters || {}) as Row;
  const [statusFilter, setStatusFilter] = useState("all");
  const [localStatuses, setLocalStatuses] = useState<Record<string, string>>({});
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [savingProduct, setSavingProduct] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => {
    const nextStatuses: Record<string, string> = {};
    const nextNotes: Record<string, string> = {};
    rows.forEach((row) => {
      const key = String(row.product_key);
      nextStatuses[key] = String(row.review_status || "pending");
      nextNotes[key] = String(row.review_note || "");
    });
    setLocalStatuses(nextStatuses);
    setNotes(nextNotes);
    setStatusFilter("all");
  }, [data]);

  const currentStatus = (row: RecommendationRow) => localStatuses[String(row.product_key)] || String(row.review_status || "pending");
  const visibleRows = rows.filter((row) => statusFilter === "all" || currentStatus(row) === statusFilter);
  const estimatedValue = rows.reduce((total, row) => total + Number(row.recommended_quantity || 0) * Number(row.unit_cost || 0), 0);
  const counts = rows.reduce<Record<string, number>>((result, row) => {
    const priority = String(row.priority || "medium");
    result[priority] = (result[priority] || 0) + 1;
    return result;
  }, {});
  const noSupplier = rows.filter((row) => !row.preferred_supplier).length;
  const groups = visibleRows.reduce<Record<string, RecommendationRow[]>>((result, row) => {
    const supplier = String(row.preferred_supplier || "Sin proveedor");
    (result[supplier] ||= []).push(row);
    return result;
  }, {});

  async function saveAction(row: RecommendationRow, status: string, note = notes[String(row.product_key)] || "") {
    const productKey = String(row.product_key);
    setSavingProduct(productKey);
    setActionError(null);
    try {
      const snoozedUntil = status === "snoozed"
        ? new Date(Date.now() + 14 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10)
        : null;
      await api("/analytics/purchase-recommendations/" + productKey, {
        method: "PATCH",
        body: JSON.stringify({ status, note: note || null, snoozed_until: snoozedUntil }),
      });
      setLocalStatuses((current) => ({ ...current, [productKey]: status }));
    } catch (requestError) {
      setActionError(requestError instanceof Error ? requestError.message : "No fue posible guardar el estado.");
    } finally {
      setSavingProduct(null);
    }
  }

  async function exportCsv() {
    setActionError(null);
    try {
      const response = await fetch("/api/v1/analytics/purchase-recommendations/export" + query(filters), { credentials: "same-origin" });
      if (!response.ok) throw new Error("No fue posible exportar las recomendaciones.");
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "reponer.csv";
      link.click();
      URL.revokeObjectURL(url);
    } catch (requestError) {
      setActionError(requestError instanceof Error ? requestError.message : "No fue posible exportar.");
    }
  }

  return <>
    <div className="section-heading"><div><h2>Reponer</h2><p className="muted">Cola de decisión de compra basada en stock, demanda, cobertura y proveedores históricos.</p></div><button className="primary-button compact-button" onClick={exportCsv}>Exportar CSV</button></div>
    <section className="cards">
      <article className="metric-card"><p>Productos sugeridos</p><strong>{number(rows.length)}</strong><small>{number(parameters.target_coverage_days)} días de cobertura objetivo</small></article>
      <article className="metric-card"><p>Críticos / agotados</p><strong>{number(counts.critical || 0)}</strong><small>con demanda reciente</small></article>
      <article className="metric-card"><p>Sin proveedor</p><strong>{number(noSupplier)}</strong><small>requieren validación manual</small></article>
      <article className="metric-card"><p>Compra estimada</p><strong>{money(estimatedValue)}</strong><small>costo histórico disponible</small></article>
    </section>
    <div className="warning">Demanda seleccionada: {String(parameters.demand_from || "")} a {String(parameters.demand_to || "")}. También se muestran velocidades de 7, 30 y 90 días. El stock proviene del último snapshot de Alegra; no se descuentan órdenes pendientes porque aún no están integradas.</div>
    {actionError && <div className="error">{actionError}</div>}
    <section className="filters compact-filters">
      <label>Estado de revisión<select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}><option value="all">Todos</option><option value="pending">Pendientes</option><option value="reviewed">Revisados</option><option value="snoozed">Pospuestos</option><option value="purchased">Comprados</option><option value="discarded">Descartados</option></select></label>
      <label>Cobertura objetivo<input type="number" min="7" max="365" value={replenishmentParams.target_coverage_days} onChange={(event) => setReplenishmentParams({ ...replenishmentParams, target_coverage_days: Number(event.target.value) || 30 })} /></label>
      <label>Plazo compra<input type="number" min="0" max="90" value={replenishmentParams.lead_time_days} onChange={(event) => setReplenishmentParams({ ...replenishmentParams, lead_time_days: Number(event.target.value) || 0 })} /></label>
      <label>Días seguridad<input type="number" min="0" max="90" value={replenishmentParams.safety_days} onChange={(event) => setReplenishmentParams({ ...replenishmentParams, safety_days: Number(event.target.value) || 0 })} /></label>
    </section>
    {!visibleRows.length ? <div className="empty">No hay recomendaciones para este filtro.</div> : <>
      <section className="table-card"><h3>Plan agrupado por proveedor</h3><table><thead><tr><th>Proveedor</th><th>Productos</th><th>Unidades</th><th>Compra estimada</th><th>Críticos</th></tr></thead><tbody>{Object.entries(groups).sort(([, left], [, right]) => right.reduce((sum, row) => sum + Number(row.recommended_quantity || 0) * Number(row.unit_cost || 0), 0) - left.reduce((sum, row) => sum + Number(row.recommended_quantity || 0) * Number(row.unit_cost || 0), 0)).map(([supplier, supplierRows]) => <tr key={supplier}><td>{supplier}</td><td>{number(supplierRows.length)}</td><td>{number(supplierRows.reduce((sum, row) => sum + Number(row.recommended_quantity || 0), 0))}</td><td>{money(supplierRows.reduce((sum, row) => sum + Number(row.recommended_quantity || 0) * Number(row.unit_cost || 0), 0))}</td><td>{number(supplierRows.filter((row) => row.priority === "critical").length)}</td></tr>)}</tbody></table></section>
      <section className="table-card"><h3>Cola de reposición</h3><div className="table-scroll"><table><thead><tr><th>Estado</th><th>Prioridad</th><th>Producto</th><th>Proveedor</th><th>Stock</th><th>Velocidad 7/30/90</th><th>Cobertura</th><th>Comprar</th><th>Costo</th><th>Valor</th><th>Confianza</th><th>Motivo</th><th>Revisión</th></tr></thead><tbody>{visibleRows.map((row, index) => {
        const key = String(row.product_key || index);
        const options = row.supplier_options || [];
        const status = currentStatus(row);
        return <tr key={key}><td><select value={status} disabled={savingProduct === key} onChange={(event) => saveAction(row, event.target.value)}><option value="pending">Pendiente</option><option value="reviewed">Revisado</option><option value="snoozed">Posponer 14 días</option><option value="purchased">Comprado</option><option value="discarded">Descartado</option></select></td><td>{String(row.priority)}</td><td>{String(row.name)}{row.reference ? " · " + String(row.reference) : ""}<small className="table-subtitle">{String(row.family)}</small></td><td>{String(row.preferred_supplier || "Sin proveedor")}<small className="table-subtitle">{String(row.supplier_source || "")}</small>{options.length > 0 && <details><summary>{options.length} opciones</summary><div className="supplier-options">{options.map((option, optionIndex) => <div key={String(option.supplier_key || optionIndex)}>{String(option.supplier || "Sin proveedor")} · {money(option.average_unit_cost)} · {number(option.confidence_pct)}% frecuencia · {String(option.last_purchase_date || "sin fecha")}</div>)}</div></details>}</td><td>{number(row.quantity_on_hand)}</td><td>{number(row.units_7d)} / {number(row.units_30d)} / {number(row.units_90d)}</td><td>{row.coverage_days == null ? "Agotado" : number(row.coverage_days) + " días"}</td><td>{number(row.recommended_quantity)}</td><td>{money(row.unit_cost, String(row.currency_code || "COP"))}</td><td>{money(Number(row.recommended_quantity || 0) * Number(row.unit_cost || 0), String(row.currency_code || "COP"))}</td><td>{number(row.supplier_confidence_pct)}%</td><td>{String(row.reason)}</td><td><input className="inline-note" value={notes[key] || ""} placeholder="Nota" onChange={(event) => setNotes((current) => ({ ...current, [key]: event.target.value }))} onBlur={() => saveAction(row, status)} /></td></tr>;
      })}</tbody></table></div></section>
    </>}
    <ReplenishmentOpportunityTable title="Exceso de cobertura (120 días o más)" rows={excessItems} coverage />
    <ReplenishmentOpportunityTable title="Inventario sin demanda en 90 días" rows={slowItems} />
  </>;
}

function ReplenishmentOpportunityTable({ title, rows, coverage = false }: { title: string; rows: RecommendationRow[]; coverage?: boolean }) {
  if (!rows.length) return <section className="table-card"><h3>{title}</h3><p className="muted">Sin productos para estos criterios.</p></section>;
  return <section className="table-card"><h3>{title}</h3><table><thead><tr><th>Producto</th><th>Familia</th><th>Stock</th><th>Venta 90 días</th>{coverage && <th>Cobertura</th>}<th>Valor inventario</th></tr></thead><tbody>{rows.slice(0, 100).map((row, index) => <tr key={String(row.product_key || index)}><td>{String(row.name)}{row.reference ? " · " + String(row.reference) : ""}</td><td>{String(row.family)}</td><td>{number(row.quantity_on_hand)}</td><td>{number(row.units_90d)}</td>{coverage && <td>{number(row.coverage_days)} días</td>}<td>{money(row.inventory_value)}</td></tr>)}</tbody></table></section>;
}

function SalesReports({ data }: { data: Record<string, unknown> }) {
  const summary = (data.summary || []) as Row[];
  const families = (data.by_family_detail || []) as Row[];
  const products = (data.product_detail || []) as Row[];
  const sellers = (data.seller_detail || []) as Row[];
  const customers = (data.customer_detail || []) as Row[];
  const statuses = (data.status_detail || []) as Row[];
  const catalogSuppliers = (data.catalog_supplier_detail || []) as Row[];
  const modalSuppliers = (data.modal_supplier_detail || []) as Row[];
  const costSuppliers = (data.cost_supplier_detail || []) as Row[];
  const familyChart = families.map((row) => ({ ...row, label: row.family }));
  const supplierChart = catalogSuppliers.map((row) => ({ ...row, label: row.supplier }));
  const modalSupplierChart = modalSuppliers.map((row) => ({ ...row, label: row.supplier }));
  return <>
    <h2>Ventas</h2>
    <p className="muted">La venta neta incluye las notas crédito como valores negativos. El margen usa únicamente líneas con costo histórico disponible.</p>
    <section className="cards">{summary.map((row) => {
      const currency = String(row.currency_code || "COP");
      return <article className="metric-card" key={currency}>
        <p>{currency} · Venta neta</p><strong>{money(row.net_sales, currency)}</strong>
        <small>{number(row.documents)} documentos · {number(row.units)} unidades</small>
        <dl><div><dt>Ticket promedio</dt><dd>{money(row.average_ticket, currency)}</dd></div><div><dt>Ventas facturadas</dt><dd>{money(row.invoice_sales, currency)}</dd></div><div><dt>Notas crédito</dt><dd>{money(row.credit_note_amount, currency)}</dd></div><div><dt>Margen bruto</dt><dd>{money(row.gross_margin, currency)}</dd></div><div><dt>Margen %</dt><dd>{percent(row.gross_margin_pct)}</dd></div><div><dt>Costo cubierto</dt><dd>{percent(row.cost_coverage_pct)}</dd></div></dl>
      </article>;
    })}</section>
    <Chart title="Ventas en el tiempo" data={(data.series || []) as Row[]} dataKey="amount" moneyValue />
    <Chart title="Ventas por familia" data={familyChart} dataKey="net_sales" moneyValue />
    <section className="table-card"><h3>Productos más vendidos</h3>{products.length ? <table><thead><tr><th>Producto</th><th>Familia</th><th>Venta neta</th><th>Participación</th><th>Unidades</th><th>Documentos</th><th>Precio promedio</th><th>Margen %</th><th>Última venta</th></tr></thead><tbody>{products.slice(0, 100).map((row, index) => <tr key={`${row.product}-${index}`}><td>{String(row.product)}{row.reference ? ` · ${String(row.reference)}` : ""}</td><td>{String(row.family)}</td><td>{money(row.net_sales, String(row.currency_code || "COP"))}</td><td>{percent(row.share_pct)}</td><td>{number(row.units)}</td><td>{number(row.documents)}</td><td>{money(row.average_unit_sale, String(row.currency_code || "COP"))}</td><td>{percent(row.gross_margin_pct)}</td><td>{String(row.last_sale_date || "")}</td></tr>)}</tbody></table> : <p className="muted">Sin ventas para estos filtros.</p>}</section>
    <section className="table-card"><h3>Ventas por familia</h3>{families.length ? <table><thead><tr><th>Familia</th><th>Venta neta</th><th>Participación</th><th>Unidades</th><th>Documentos</th><th>Productos</th><th>Margen %</th></tr></thead><tbody>{families.map((row, index) => <tr key={`${row.family}-${index}`}><td>{String(row.family)}</td><td>{money(row.net_sales, String(row.currency_code || "COP"))}</td><td>{percent(row.share_pct)}</td><td>{number(row.units)}</td><td>{number(row.documents)}</td><td>{number(row.product_count)}</td><td>{percent(row.gross_margin_pct)}</td></tr>)}</tbody></table> : <p className="muted">Sin familias para estos filtros.</p>}</section>
    <Chart title="Ventas por proveedor asociado al producto" data={supplierChart} dataKey="net_sales" moneyValue />
    <section className="table-card"><h3>Ventas por proveedor asociado al producto</h3><p className="muted">Atribución de catálogo: corresponde al proveedor actual guardado en el producto de Alegra; no significa que cada unidad histórica se haya comprado a ese proveedor.</p>{catalogSuppliers.length ? <table><thead><tr><th>Proveedor</th><th>Venta neta</th><th>Participación</th><th>Unidades</th><th>Documentos</th><th>Productos</th><th>Margen %</th><th>Cobertura costo</th></tr></thead><tbody>{catalogSuppliers.map((row, index) => <tr key={String(row.supplier) + "-" + index}><td>{String(row.supplier)}</td><td>{money(row.net_sales, String(row.currency_code || "COP"))}</td><td>{percent(row.share_pct)}</td><td>{number(row.units)}</td><td>{number(row.documents)}</td><td>{number(row.product_count)}</td><td>{percent(row.gross_margin_pct)}</td><td>{percent(row.cost_coverage_pct)}</td></tr>)}</tbody></table> : <p className="muted">Sin proveedor asociado para estos filtros.</p>}</section>
    <Chart title="Ventas por proveedor modal histórico" data={modalSupplierChart} dataKey="net_sales" moneyValue />
    <section className="table-card"><h3>Ventas por proveedor modal histórico</h3><p className="muted">Cada producto se asigna al proveedor que más aparece en sus compras históricas. La moda se calcula por frecuencia de líneas de compra; los empates se resuelven por unidades, valor comprado y fecha más reciente. La confianza es el peso de esa moda dentro de las compras del producto.</p>{modalSuppliers.length ? <table><thead><tr><th>Proveedor modal</th><th>Venta neta</th><th>Participación</th><th>Unidades</th><th>Documentos</th><th>Productos</th><th>Confianza modal</th><th>Margen %</th></tr></thead><tbody>{modalSuppliers.map((row, index) => <tr key={String(row.supplier) + "-" + index}><td>{String(row.supplier)}</td><td>{money(row.net_sales, String(row.currency_code || "COP"))}</td><td>{percent(row.share_pct)}</td><td>{number(row.units)}</td><td>{number(row.documents)}</td><td>{number(row.product_count)}</td><td>{percent(row.supplier_confidence_pct)}</td><td>{percent(row.gross_margin_pct)}</td></tr>)}</tbody></table> : <p className="muted">Sin proveedor modal histórico para estos filtros.</p>}</section>
    <section className="table-card"><h3>Margen por proveedor real (FIFO)</h3><p className="muted">Atribución FIFO: relaciona el costo de cada venta con las capas de compra reales. Las existencias de apertura, devoluciones sin compra trazable y costos sin coincidencia aparecen como “Sin proveedor/costo de apertura”.</p>{costSuppliers.length ? <table><thead><tr><th>Proveedor</th><th>Ventas atribuidas</th><th>COGS</th><th>Margen</th><th>Margen %</th><th>Participación</th><th>Unidades asignadas</th><th>Documentos</th></tr></thead><tbody>{costSuppliers.map((row, index) => <tr key={String(row.supplier) + "-" + index}><td>{String(row.supplier)}</td><td>{money(row.attributed_net_sales, String(row.currency_code || "COP"))}</td><td>{money(row.cogs, String(row.currency_code || "COP"))}</td><td>{money(row.gross_margin, String(row.currency_code || "COP"))}</td><td>{percent(row.gross_margin_pct)}</td><td>{percent(row.share_pct)}</td><td>{number(row.allocated_units)}</td><td>{number(row.documents)}</td></tr>)}</tbody></table> : <p className="muted">No hay asignaciones FIFO disponibles para estos filtros.</p>}</section>
    <section className="table-card"><h3>Rendimiento por vendedor</h3>{sellers.length ? <table><thead><tr><th>Vendedor</th><th>Venta neta</th><th>Participación</th><th>Unidades</th><th>Documentos</th><th>Margen %</th></tr></thead><tbody>{sellers.map((row, index) => <tr key={`${row.seller}-${index}`}><td>{String(row.seller)}</td><td>{money(row.net_sales, String(row.currency_code || "COP"))}</td><td>{percent(row.share_pct)}</td><td>{number(row.units)}</td><td>{number(row.documents)}</td><td>{percent(row.gross_margin_pct)}</td></tr>)}</tbody></table> : <p className="muted">Sin vendedores para estos filtros.</p>}</section>
    <section className="table-card"><h3>Clientes principales</h3>{customers.length ? <table><thead><tr><th>Cliente</th><th>Venta neta</th><th>Participación</th><th>Unidades</th><th>Documentos</th><th>Última venta</th></tr></thead><tbody>{customers.slice(0, 100).map((row, index) => <tr key={`${row.customer}-${index}`}><td>{String(row.customer)}</td><td>{money(row.net_sales, String(row.currency_code || "COP"))}</td><td>{percent(row.share_pct)}</td><td>{number(row.units)}</td><td>{number(row.documents)}</td><td>{String(row.last_sale_date || "")}</td></tr>)}</tbody></table> : <p className="muted">Sin clientes para estos filtros.</p>}</section>
    <section className="table-card"><h3>Desglose por estado del documento</h3>{statuses.length ? <table><thead><tr><th>Estado</th><th>Venta neta</th><th>Participación</th><th>Unidades</th><th>Documentos</th><th>Margen %</th></tr></thead><tbody>{statuses.map((row, index) => <tr key={`${row.status}-${index}`}><td>{String(row.status)}</td><td>{money(row.net_sales, String(row.currency_code || "COP"))}</td><td>{percent(row.share_pct)}</td><td>{number(row.units)}</td><td>{number(row.documents)}</td><td>{percent(row.gross_margin_pct)}</td></tr>)}</tbody></table> : <p className="muted">Sin estados para estos filtros.</p>}</section>
  </>;
}

function SupplierReports({ data }: { data: Record<string, unknown> }) {
  const summary = (data.summary || []) as Row[];
  const suppliers = (data.by_supplier || []) as Row[];
  const families = (data.by_family || []) as Row[];
  const products = (data.by_product_supplier || []) as Row[];
  const variations = (data.price_variations || []) as Row[];
  const familyChart = families.map((row) => ({ ...row, label: row.family }));
  return <>
    <h2>Proveedores</h2>
    <p className="muted">El proveedor se toma de la factura de compra. La familia se toma del campo FAMILIA del producto.</p>
    <section className="cards">{summary.map((row) => {
      const currency = String(row.currency_code || "COP");
      return <article className="metric-card" key={currency}>
        <p>{currency} · Compra acumulada</p><strong>{money(row.purchase_amount, currency)}</strong>
        <small>{number(row.suppliers)} proveedores · {number(row.documents)} documentos</small>
        <dl><div><dt>Unidades</dt><dd>{number(row.units)}</dd></div><div><dt>Ticket promedio</dt><dd>{money(row.average_purchase_ticket, currency)}</dd></div><div><dt>Costo unitario</dt><dd>{money(row.average_unit_cost, currency)}</dd></div></dl>
      </article>;
    })}</section>
    <Chart title="Compras en el tiempo" data={(data.series || []) as Row[]} dataKey="amount" moneyValue />
    <Chart title="Compra por familia" data={familyChart} dataKey="purchase_amount" moneyValue />
    <section className="table-card"><h3>Ranking de proveedores</h3>{suppliers.length ? <table><thead><tr><th>Proveedor</th><th>Compra</th><th>Participación</th><th>Unidades</th><th>Documentos</th><th>SKUs</th><th>Última compra</th></tr></thead><tbody>{suppliers.map((row, index) => <tr key={`${row.supplier}-${index}`}><td>{String(row.supplier)}</td><td>{money(row.purchase_amount, String(row.currency_code || "COP"))}</td><td>{percent(row.share_pct)}</td><td>{number(row.units)}</td><td>{number(row.documents)}</td><td>{number(row.skus)}</td><td>{String(row.last_purchase_date || "")}</td></tr>)}</tbody></table> : <p className="muted">Sin compras para estos filtros.</p>}</section>
    <section className="table-card"><h3>Compras por familia</h3>{families.length ? <table><thead><tr><th>Familia</th><th>Compra</th><th>Unidades</th><th>Proveedores</th><th>SKUs</th></tr></thead><tbody>{families.map((row, index) => <tr key={`${row.family}-${index}`}><td>{String(row.family)}</td><td>{money(row.purchase_amount, String(row.currency_code || "COP"))}</td><td>{number(row.units)}</td><td>{number(row.suppliers)}</td><td>{number(row.skus)}</td></tr>)}</tbody></table> : <p className="muted">Sin familias para estos filtros.</p>}</section>
    <section className="table-card"><h3>Matriz producto–proveedor</h3>{products.length ? <table><thead><tr><th>Producto</th><th>Familia</th><th>Proveedor</th><th>Compra</th><th>Unidades</th><th>Costo promedio</th><th>Última compra</th></tr></thead><tbody>{products.slice(0, 100).map((row, index) => <tr key={`${row.product}-${row.supplier}-${index}`}><td>{String(row.product)}</td><td>{String(row.family)}</td><td>{String(row.supplier)}</td><td>{money(row.purchase_amount, String(row.currency_code || "COP"))}</td><td>{number(row.units)}</td><td>{money(row.average_unit_cost, String(row.currency_code || "COP"))}</td><td>{String(row.last_purchase_date || "")}</td></tr>)}</tbody></table> : <p className="muted">Sin detalle producto–proveedor.</p>}</section>
    <section className="table-card"><h3>Variación de costos</h3><p className="muted">Productos con al menos dos compras y diferencia entre costo mínimo y máximo.</p>{variations.length ? <table><thead><tr><th>Producto</th><th>Familia</th><th>Proveedor</th><th>Costo mínimo</th><th>Costo máximo</th><th>Variación</th><th>Compras</th></tr></thead><tbody>{variations.map((row, index) => <tr key={`${row.product}-${row.supplier}-${index}`}><td>{String(row.product)}</td><td>{String(row.family)}</td><td>{String(row.supplier)}</td><td>{money(row.minimum_cost, String(row.currency_code || "COP"))}</td><td>{money(row.maximum_cost, String(row.currency_code || "COP"))}</td><td>{percent(row.cost_range_pct)}</td><td>{number(row.purchase_lines)}</td></tr>)}</tbody></table> : <p className="muted">No hay variaciones suficientes para estos filtros.</p>}</section>
  </>;
}

function DomainView({ title, data, amountKey }: { title: string; data: Record<string, unknown>; amountKey: string }) {
  const summary = (data.summary || []) as Row[];
  const series = (data.series || []) as Row[];
  const sections = Object.entries(data).filter(([key]) => !["summary", "series"].includes(key));
  return <>{title === "Ventas" && <CostMetrics rows={summary} />}<h2>{title}</h2><section className="cards">{summary.map((row) => <article className="metric-card compact" key={String(row.currency_code || row.label)}><p>{String(row.currency_code || row.label || "Total")}</p><strong>{money(row.amount ?? row.purchase_amount ?? row.net_sales, String(row.currency_code || "COP"))}</strong><small>{number(row.documents ?? row.payments ?? row.quantity)} registros</small></article>)}</section><Chart title={`${title} en el tiempo`} data={series} dataKey={amountKey} moneyValue />{sections.map(([key, value]) => Array.isArray(value) ? <Chart key={key} title={labelFor(key)} data={value as Row[]} dataKey={key === "stock_coverage" ? "coverage_days" : amountKey} moneyValue={key !== "stock_coverage"} /> : null)}</>;
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
  return ({ by_product: "Por producto", best_sellers: "MÃ¡s vendidos", stock_coverage: "Cobertura de stock", recent_customers: "Clientes recientes", by_supplier: "Por proveedor", by_family: "Por familia", by_type: "Por tipo", by_contact: "Por contacto", by_seller: "Por vendedor", by_warehouse: "Por bodega", by_customer: "Por cliente", by_status: "Por estado", adjustment: "Ajustes", transfer_in: "Entradas por transferencia", transfer_out: "Salidas por transferencia" }[value] || value).replaceAll("_", " ");
}
