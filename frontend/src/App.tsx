import {
  FormEvent,
  Suspense,
  lazy,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { api, Filters, Option, query } from "./api";
import { money, number } from "./format";

type Tab =
  | "today"
  | "product-workspace"
  | "receiving"
  | "treasury"
  | "repairs"
  | "system"
  | "overview"
  | "sales"
  | "purchases"
  | "suppliers"
  | "payments"
  | "customers"
  | "products"
  | "kpis"
  | "purchase-recommendations"
  | "inventory"
  | "alerts"
  | "assistant";
const Workspace = lazy(() => import("./operations/Workspace"));
const Chart = lazy(() => import("./Chart"));
const operationTabs = new Set<Tab>([
  "today",
  "product-workspace",
  "receiving",
  "treasury",
  "repairs",
  "system",
]);
type Row = Record<string, string | number | null>;
type SupplierOption = {
  supplier_key?: number | null;
  supplier?: string | null;
  is_modal?: boolean;
  is_preferred?: boolean;
  supplier_score?: number | null;
  policy_source?: string | null;
  minimum_order_quantity?: number | null;
  pack_size?: number | null;
  lead_time_days?: number | null;
  max_wait_days?: number | null;
  minimum_order_amount?: number | null;
  shipping_cost?: number | null;
  free_shipping_threshold?: number | null;
  confidence_pct?: number | null;
  unit_share_pct?: number | null;
  purchase_lines?: number | null;
  average_unit_cost?: number | null;
  median_unit_cost?: number | null;
  minimum_unit_cost?: number | null;
  maximum_unit_cost?: number | null;
  last_unit_cost?: number | null;
  last_purchase_date?: string | null;
  observed_lead_days?: number | null;
  default_lead_time_days?: number | null;
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
  weekly_budget: number;
  review_cycle_days: number;
  target_coverage_days?: number;
  lead_time_days?: number;
  safety_days?: number;
  limit?: number;
};

const tabs: Array<[Tab, string]> = [
  ["today", "Hoy"],
  ["overview", "Resumen"],
  ["sales", "Ventas"],
  ["purchases", "Compras"],
  ["suppliers", "Proveedores"],
  ["payments", "Pagos"],
  ["customers", "Clientes"],
  ["product-workspace", "Productos"],
  ["products", "Rotación"],
  ["kpis", "Indicadores"],
  ["purchase-recommendations", "Reponer"],
  ["inventory", "Inventario"],
  ["alerts", "Alertas"],
  ["assistant", "Asistente IA"],
  ["receiving", "Pedidos"],
  ["treasury", "Caja"],
  ["repairs", "Servicio técnico"],
  ["system", "Estado"],
];

function todayIso(): string {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/Bogota",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const values = Object.fromEntries(
    parts.map((part) => [part.type, part.value]),
  );
  return `${values.year}-${values.month}-${values.day}`;
}

function shiftDateIso(value: string, days: number): string {
  const [year, month, day] = value.split("-").map(Number);
  const shifted = new Date(Date.UTC(year, month - 1, day));
  shifted.setUTCDate(shifted.getUTCDate() + days);
  return shifted.toISOString().slice(0, 10);
}

function initialFilters(): Filters {
  const to = todayIso();
  const params = new URLSearchParams(location.search);
  const result: Filters = {
    from_date: shiftDateIso(to, -29),
    to_date: to,
    metric_scope: "commercial",
  };
  for (const key of [
    "from_date",
    "to_date",
    "currency",
    "product_key",
    "seller_key",
    "warehouse_key",
    "document_status",
    "family",
    "provider_key",
    "metric_scope",
  ] as Array<keyof Filters>) {
    const value = params.get(key);
    if (value) Object.assign(result, { [key]: value });
  }
  return result;
}

function initialReplenishmentParams(): ReplenishmentParams {
  const params = new URLSearchParams(location.search);
  const budget = Number(params.get("weekly_budget") || 15000000);
  const cycle = Number(params.get("review_cycle_days") || 7);
  return {
    weekly_budget: Number.isFinite(budget) && budget >= 0 ? budget : 15000000,
    review_cycle_days:
      Number.isInteger(cycle) && cycle >= 1 && cycle <= 31 ? cycle : 7,
  };
}

export default function App() {
  const [authenticated, setAuthenticated] = useState<boolean | null>(null);
  const [filters, setFilters] = useState<Filters>(initialFilters);
  const [filterData, setFilterData] = useState<FilterData | null>(null);
  const [tab, setTab] = useState<Tab>(() => {
    const value = new URLSearchParams(location.search).get("tab");
    return tabs.some(([key]) => key === value) ? (value as Tab) : "today";
  });
  const [savedViews, setSavedViews] = useState<
    Array<{
      name: string;
      tab: Tab;
      filters: Filters;
      replenishment?: ReplenishmentParams;
    }>
  >(() => {
    try {
      const value = JSON.parse(
        localStorage.getItem("retail-saved-views") || "[]",
      );
      return Array.isArray(value)
        ? value
            .filter(
              (v) =>
                v &&
                typeof v.name === "string" &&
                tabs.some(([key]) => key === v.tab) &&
                v.filters &&
                typeof v.filters.from_date === "string" &&
                typeof v.filters.to_date === "string",
            )
            .slice(0, 20)
        : [];
    } catch {
      return [];
    }
  });
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const [status, setStatus] = useState<Row | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [replenishmentParams, setReplenishmentParams] =
    useState<ReplenishmentParams>(initialReplenishmentParams);

  useEffect(() => {
    const params = new URLSearchParams(query(filters).slice(1));
    params.set("tab", tab);
    params.set("weekly_budget", String(replenishmentParams.weekly_budget));
    params.set(
      "review_cycle_days",
      String(replenishmentParams.review_cycle_days),
    );
    history.replaceState(null, "", `?${params}`);
  }, [tab, filters, replenishmentParams]);
  useEffect(() => {
    const change = () => {
      setFilters(initialFilters());
      setReplenishmentParams(initialReplenishmentParams());
      const value = new URLSearchParams(location.search).get("tab");
      setTab(tabs.some(([key]) => key === value) ? (value as Tab) : "today");
    };
    window.addEventListener("popstate", change);
    return () => window.removeEventListener("popstate", change);
  }, []);
  const navigate = (next: string, product?: string) => {
    if (tabs.some(([key]) => key === next)) setTab(next as Tab);
    if (product)
      setFilters((current) => ({ ...current, product_key: product }));
  };

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
    if (tab === "assistant" || operationTabs.has(tab)) {
      setData(null);
      setLoading(false);
      setError(null);
      return;
    }
    setLoading(true);
    setError(null);
    const path =
      tab === "purchase-recommendations"
        ? `/procurement/preview${query(filters, { ...replenishmentParams, as_of_date: filters.to_date })}`
        : `/analytics/${tab}${query(filters)}`;
    const controller = new AbortController();
    api<Record<string, unknown>>(path, { signal: controller.signal })
      .then((value) => {
        if (!controller.signal.aborted) setData(value);
      })
      .catch((requestError: Error) => {
        if (!controller.signal.aborted) setError(requestError.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [authenticated, filters, tab, replenishmentParams]);

  if (authenticated === null)
    return <main className="splash">Cargando Retail Intelligence…</main>;
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
          onClick={() =>
            api<void>("/dashboard/session", { method: "DELETE" }).then(() =>
              setAuthenticated(false),
            )
          }
        >
          Cerrar sesión
        </button>
      </header>
      {!["today", "receiving", "system"].includes(tab) && (
        <FiltersBar
          filters={filters}
          setFilters={setFilters}
          options={filterData}
          tab={tab}
        />
      )}
      <div className="saved-views">
        <button
          onClick={() => {
            const name = prompt("Nombre de la vista guardada:");
            if (!name?.trim()) return;
            const next = [
              ...savedViews.filter((v) => v.name !== name.trim()),
              {
                name: name.trim(),
                tab,
                filters,
                replenishment: replenishmentParams,
              },
            ].slice(-20);
            setSavedViews(next);
            localStorage.setItem("retail-saved-views", JSON.stringify(next));
          }}
        >
          Guardar vista
        </button>
        {savedViews.map((view) => (
          <span key={view.name}>
            <button
              onClick={() => {
                setTab(view.tab);
                setFilters(view.filters);
                if (
                  view.replenishment &&
                  Number.isFinite(view.replenishment.weekly_budget) &&
                  view.replenishment.weekly_budget >= 0 &&
                  view.replenishment.review_cycle_days >= 1 &&
                  view.replenishment.review_cycle_days <= 31
                )
                  setReplenishmentParams(view.replenishment);
              }}
            >
              {view.name}
            </button>
            <button
              aria-label={`Eliminar vista ${view.name}`}
              onClick={() => {
                const next = savedViews.filter((v) => v.name !== view.name);
                setSavedViews(next);
                localStorage.setItem(
                  "retail-saved-views",
                  JSON.stringify(next),
                );
              }}
            >
              ×
            </button>
          </span>
        ))}
      </div>
      {status && <RefreshNotice status={status} />}
      <nav className="tabs" aria-label="Áreas del tablero">
        {tabs.map(([value, label]) => (
          <button
            aria-current={tab === value ? "page" : undefined}
            className={tab === value ? "active" : ""}
            key={value}
            onClick={() => setTab(value)}
          >
            {label}
          </button>
        ))}
      </nav>
      {error && <div className="error">{error}</div>}
      <Suspense fallback={<p>Cargando módulo…</p>}>
        {operationTabs.has(tab) ? (
          <Workspace tab={tab} filters={filters} navigate={navigate} />
        ) : loading ? (
          <div className="loading">Actualizando indicadores…</div>
        ) : (
          <DashboardTab
            tab={tab}
            data={data}
            filters={filters}
            replenishmentParams={replenishmentParams}
            setReplenishmentParams={setReplenishmentParams}
          />
        )}
      </Suspense>
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
      await api<void>("/dashboard/session", {
        method: "POST",
        body: JSON.stringify({ password }),
      });
      onLogin();
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "No fue posible ingresar.",
      );
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
          <input
            autoFocus
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
          />
        </label>
        {error && (
          <div role="alert" className="error">
            {error}
          </div>
        )}
        <button className="primary-button" disabled={loading}>
          {loading ? "Ingresando…" : "Ingresar"}
        </button>
      </form>
    </main>
  );
}

function FiltersBar({
  filters,
  setFilters,
  options,
  tab,
}: {
  filters: Filters;
  setFilters: (value: Filters) => void;
  options: FilterData | null;
  tab: Tab;
}) {
  const update = (field: keyof Filters, value: string) =>
    setFilters({ ...filters, [field]: value || undefined });
  const quickRange = (days: number) => {
    const to = todayIso();
    setFilters({
      ...filters,
      from_date: shiftDateIso(to, -(days - 1)),
      to_date: to,
    });
  };
  const historyFrom = options?.date_range.min_date;
  const historyTo = options?.date_range.max_date;
  const useFullHistory = () => {
    if (historyFrom && historyTo)
      setFilters({ ...filters, from_date: historyFrom, to_date: historyTo });
  };
  return (
    <section className="filters">
      {tab !== "treasury" && (
        <>
          <div className="quick-ranges" aria-label="Rangos rápidos">
            {[30, 90, 365].map((days) => (
              <button key={days} onClick={() => quickRange(days)}>
                Últimos {days} días
              </button>
            ))}
            <button
              onClick={useFullHistory}
              disabled={!historyFrom || !historyTo}
            >
              Toda la historia
            </button>
          </div>
          <label>
            Desde
            <input
              type="date"
              min={historyFrom}
              max={historyTo || todayIso()}
              value={filters.from_date}
              onChange={(event) => update("from_date", event.target.value)}
            />
          </label>
          <label>
            Hasta
            <input
              type="date"
              min={historyFrom}
              max={historyTo || todayIso()}
              value={filters.to_date}
              onChange={(event) => update("to_date", event.target.value)}
            />
          </label>
        </>
      )}
      <Select
        label="Moneda"
        value={filters.currency}
        options={options?.currencies}
        onChange={(value) => update("currency", value)}
      />
      {!["treasury", "repairs"].includes(tab) && (
        <>
          <label>
            Modo de cifras
            <select
              value={filters.metric_scope || "commercial"}
              onChange={(event) =>
                setFilters({
                  ...filters,
                  metric_scope: event.target.value as Filters["metric_scope"],
                })
              }
            >
              <option value="commercial">Comercial · open / closed</option>
              <option value="audit">Auditoría · todos los estados</option>
            </select>
          </label>
          <Select
            label="Producto"
            value={filters.product_key}
            options={options?.products}
            onChange={(value) => update("product_key", value)}
          />
          <Select
            label="Vendedor"
            value={filters.seller_key}
            options={options?.sellers}
            onChange={(value) => update("seller_key", value)}
          />
          <Select
            label="Bodega"
            value={filters.warehouse_key}
            options={options?.warehouses}
            onChange={(value) => update("warehouse_key", value)}
          />
          <Select
            label="Familia"
            value={filters.family}
            options={options?.families}
            onChange={(value) => update("family", value)}
          />
          <Select
            label="Proveedor"
            value={filters.provider_key}
            options={options?.suppliers}
            onChange={(value) => update("provider_key", value)}
          />
          <Select
            label="Estado"
            value={filters.document_status}
            options={options?.document_statuses}
            onChange={(value) => update("document_status", value)}
          />
        </>
      )}
      {tab === "purchase-recommendations" && (
        <p className="muted filter-scope">
          Reponer usa Hasta como corte y hasta 365 días de demanda. Desde,
          vendedor, bodega y estado no limitan el modelo.
          Producto/familia/proveedor limitan la vista, no la asignación global
          del presupuesto.
        </p>
      )}
      {tab === "inventory" && (
        <p className="muted filter-scope">
          El rango filtra movimientos; existencias = última captura.
        </p>
      )}
      {!["treasury", "repairs"].includes(tab) && (
        <details className="filter-scope">
          <summary>Cómo interpretar las cifras</summary>
          <p>
            — significa desconocido, no cero. Ticket = ventas netas / facturas;
            notas crédito no cuentan como tickets. Margen solo de líneas con
            costo. Cobertura por valor = valor absoluto vendido con costo /
            valor absoluto vendido. Comercial excluye borradores, anulados y
            estados desconocidos; Auditoría conserva todos los estados no
            eliminados.
          </p>
        </details>
      )}
    </section>
  );
}

function Select({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value?: string;
  options?: Option[];
  onChange: (value: string) => void;
}) {
  return (
    <label>
      {label}
      <select
        value={value || ""}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">Todos</option>
        {options?.map((option) => (
          <option key={String(option.value)} value={option.value}>
            {option.label || option.value}
            {option.reference ? ` · ${option.reference}` : ""}
          </option>
        ))}
      </select>
    </label>
  );
}

function RefreshNotice({ status }: { status: Row }) {
  if (status.status === "never_run")
    return (
      <div className="warning">El mart aún no tiene ejecuciones exitosas.</div>
    );
  const costStatus = status.cost_status
    ? " · Costos: " + String(status.cost_status)
    : "";
  const className =
    status.is_stale || status.cost_is_stale ? "warning" : "refresh-status";
  return (
    <div className={className}>
      Mart: {status.is_stale ? "actualización pendiente" : "actualizado"} ·{" "}
      {String(status.finished_at || status.started_at || "")}
      {costStatus}
    </div>
  );
}

function DashboardTab({
  tab,
  data,
  filters,
  replenishmentParams,
  setReplenishmentParams,
}: {
  tab: Tab;
  data: Record<string, unknown> | null;
  filters: Filters;
  replenishmentParams: ReplenishmentParams;
  setReplenishmentParams: (value: ReplenishmentParams) => void;
}) {
  if (tab === "assistant")
    return (
      <AIAssistant
        filters={filters}
        replenishmentParams={replenishmentParams}
      />
    );
  if (!data)
    return (
      <div className="empty">No hay datos para el período seleccionado.</div>
    );
  if (tab === "overview")
    return <Overview data={data as unknown as Overview} />;
  if (tab === "sales") return <SalesReports data={data} />;
  if (tab === "purchases")
    return <DomainView title="Compras" data={data} amountKey="amount" />;
  if (tab === "suppliers") return <SupplierReports data={data} />;
  if (tab === "payments")
    return <DomainView title="Pagos" data={data} amountKey="amount" />;
  if (tab === "customers")
    return <DomainView title="Clientes" data={data} amountKey="amount" />;
  if (tab === "products")
    return (
      <DomainView
        title="Productos y rotaciÃ³n"
        data={data}
        amountKey="amount"
      />
    );
  if (tab === "kpis") return <KpiView data={data} />;
  if (tab === "purchase-recommendations")
    return (
      <PurchaseRecommendations
        data={data}
        filters={filters}
        replenishmentParams={replenishmentParams}
        setReplenishmentParams={setReplenishmentParams}
      />
    );
  if (tab === "alerts") return <Alerts data={data} />;
  return <Inventory data={data} />;
}

function Overview({ data }: { data: Overview }) {
  const current = data.current || [];
  const previous = data.previous || [];
  return (
    <>
      <h2>Resumen comercial</h2>
      <p className="muted">
        La venta neta incorpora las notas crédito como valores negativos.
      </p>
      <section className="cards">
        {current.map((row) => {
          const comparison = previous.find(
            (item) => item.currency_code === row.currency_code,
          );
          return (
            <MetricCard
              key={String(row.currency_code)}
              currency={String(row.currency_code || "COP")}
              current={row}
              previous={comparison}
            />
          );
        })}
      </section>
      <Chart
        title="Venta neta en el tiempo"
        data={data.series || []}
        dataKey="amount"
        moneyValue
      />
      <CostMetrics rows={current} />
      <Chart
        title="Costo de ventas"
        data={data.series || []}
        dataKey="cogs"
        moneyValue
      />
      <Chart
        title="Margen bruto"
        data={data.series || []}
        dataKey="gross_margin"
        moneyValue
      />
    </>
  );
}

function MetricCard({
  currency,
  current,
  previous,
}: {
  currency: string;
  current: Row;
  previous?: Row;
}) {
  const sale = Number(current.net_sales || 0);
  const before = Number(previous?.net_sales || 0);
  const change = before ? ((sale - before) / Math.abs(before)) * 100 : null;
  return (
    <article className="metric-card">
      <p>{currency} · Venta neta</p>
      <strong>{money(sale, currency)}</strong>
      <small>
        {change === null
          ? "Sin período comparable"
          : `${change >= 0 ? "+" : ""}${change.toFixed(1)}% vs. período anterior`}
      </small>
      <dl>
        <div>
          <dt>Unidades</dt>
          <dd>{number(current.units)}</dd>
        </div>
        <div>
          <dt>Documentos</dt>
          <dd>{number(current.documents)}</dd>
        </div>
        <div>
          <dt>Ticket promedio</dt>
          <dd>{money(current.average_ticket, currency)}</dd>
        </div>
      </dl>
    </article>
  );
}

function CostMetrics({ rows }: { rows: Row[] }) {
  return (
    <section className="cards">
      {rows.map((row) => {
        const currency = String(row.currency_code || "COP");
        return (
          <article className="metric-card compact" key={currency + "-cost"}>
            <p>{currency} · Rentabilidad</p>
            <strong>{money(row.gross_margin, currency)}</strong>
            <small>
              Margen solo sobre ventas con costo; — significa no disponible
            </small>
            <dl>
              <div>
                <dt>COGS</dt>
                <dd>{money(row.cogs, currency)}</dd>
              </div>
              <div>
                <dt>Margen %</dt>
                <dd>{percent(row.gross_margin_pct)}</dd>
              </div>
              <div>
                <dt>Costo cubierto por valor</dt>
                <dd>{percent(row.cost_coverage_value_pct)}</dd>
              </div>
              <div>
                <dt>Costo cubierto por líneas</dt>
                <dd>{percent(row.cost_coverage_pct)}</dd>
              </div>
              <div>
                <dt>Líneas parciales</dt>
                <dd>{number(row.partial_cost_lines)}</dd>
              </div>
              <div>
                <dt>Sin costo</dt>
                <dd>{number(row.unavailable_cost_lines)}</dd>
              </div>
            </dl>
          </article>
        );
      })}
    </section>
  );
}

function percent(value: string | number | null | undefined): string {
  return value == null ? "—" : `${number(value)}%`;
}

function KpiView({ data }: { data: Record<string, unknown> }) {
  const sales = (data.sales || []) as Row[];
  const customers = (data.customers || []) as Row[];
  const purchases = (data.purchases || []) as Row[];
  const inventory = (data.inventory || []) as Row[];
  const lowCoverage = (data.low_coverage || []) as Row[];
  const excessCoverage = (data.excess_coverage || []) as Row[];
  const slowInventory = (data.slow_inventory || []) as Row[];
  return (
    <>
      <h2>Indicadores clave</h2>
      <p className="muted">
        KPIs calculados desde el mart. La cobertura usa la demanda neta del
        período seleccionado y el último snapshot de Alegra.
      </p>
      <h3 className="section-title">Venta y devoluciones</h3>
      <section className="cards">
        {sales.map((row) => {
          const currency = String(row.currency_code || "COP");
          return (
            <article className="metric-card" key={currency}>
              <p>{currency} · Unidades por transacción</p>
              <strong>{number(row.units_per_transaction)}</strong>
              <small>
                {row.sales_pace_vs_target_pct == null
                  ? "Meta mensual no configurada"
                  : `${number(row.sales_pace_vs_target_pct)}% del ritmo meta`}
              </small>
              <dl>
                <div>
                  <dt>Venta neta</dt>
                  <dd>{money(row.net_sales, currency)}</dd>
                </div>
                <div>
                  <dt>Ticket promedio</dt>
                  <dd>{money(row.average_ticket, currency)}</dd>
                </div>
                <div>
                  <dt>Precio neto/unidad</dt>
                  <dd>{money(row.average_unit_sale, currency)}</dd>
                </div>
                <div>
                  <dt>Notas crédito</dt>
                  <dd>{percent(row.credit_note_rate)}</dd>
                </div>
              </dl>
            </article>
          );
        })}
      </section>
      <CostMetrics rows={sales} />
      <h3 className="section-title">Clientes</h3>
      <section className="cards">
        {customers.map((row) => (
          <article
            className="metric-card"
            key={String(row.currency_code || "COP")}
          >
            <p>{String(row.currency_code || "COP")} · Clientes activos</p>
            <strong>{number(row.active_customers)}</strong>
            <small>con compra o nota crédito en el período</small>
            <dl>
              <div>
                <dt>Recurrentes</dt>
                <dd>{percent(row.repeat_customer_rate)}</dd>
              </div>
              <div>
                <dt>Nuevos</dt>
                <dd>{number(row.new_customers)}</dd>
              </div>
              <div>
                <dt>Concentración Top 5</dt>
                <dd>{percent(row.top_5_customer_concentration)}</dd>
              </div>
            </dl>
          </article>
        ))}
      </section>
      <h3 className="section-title">Compras y proveedores</h3>
      <section className="cards">
        {purchases.map((row) => {
          const currency = String(row.currency_code || "COP");
          return (
            <article className="metric-card" key={currency}>
              <p>{currency} · Ticket promedio de compra</p>
              <strong>{money(row.average_purchase_ticket, currency)}</strong>
              <small>{number(row.documents)} documentos de compra</small>
              <dl>
                <div>
                  <dt>Costo por unidad</dt>
                  <dd>{money(row.average_unit_cost, currency)}</dd>
                </div>
                <div>
                  <dt>Proveedores</dt>
                  <dd>{number(row.suppliers)}</dd>
                </div>
                <div>
                  <dt>Concentración Top 5</dt>
                  <dd>{percent(row.top_5_supplier_concentration)}</dd>
                </div>
              </dl>
            </article>
          );
        })}
      </section>
      <h3 className="section-title">Salud del inventario</h3>
      <section className="cards">
        {inventory.map((row) => (
          <article className="metric-card" key="inventory-health">
            <p>Referencias en el último snapshot</p>
            <strong>{number(row.products)}</strong>
            <small>valor a costo reportado: {money(row.inventory_value)}</small>
            <dl>
              <div>
                <dt>Sin disponibilidad</dt>
                <dd>{number(row.unavailable_products)}</dd>
              </div>
              <div>
                <dt>Cobertura &lt; 14 días</dt>
                <dd>{number(row.low_coverage_products)}</dd>
              </div>
              <div>
                <dt>Sin demanda</dt>
                <dd>{money(row.no_demand_value)}</dd>
              </div>
            </dl>
          </article>
        ))}
      </section>
      <Chart
        title="Productos con cobertura menor a 14 días"
        data={lowCoverage}
        dataKey="coverage_days"
      />
      <StockPriorityTable
        title="Reposición prioritaria"
        rows={lowCoverage}
        coverage
      />
      <StockPriorityTable
        title="Exceso de cobertura (120 días o más)"
        rows={excessCoverage}
        coverage
      />
      <StockPriorityTable
        title="Inventario sin demanda en el período"
        rows={slowInventory}
      />
    </>
  );
}

function StockPriorityTable({
  title,
  rows,
  coverage = false,
}: {
  title: string;
  rows: Row[];
  coverage?: boolean;
}) {
  if (!rows.length)
    return (
      <section className="table-card">
        <h3>{title}</h3>
        <p className="muted">Sin productos para estos criterios.</p>
      </section>
    );
  return (
    <section className="table-card">
      <h3>{title}</h3>
      <table>
        <thead>
          <tr>
            <th>Producto</th>
            <th>Stock</th>
            <th>Unidades vendidas</th>
            {coverage && <th>Cobertura</th>}
            <th>Valor a costo</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={`${row.label}-${index}`}>
              <td>{String(row.label)}</td>
              <td>{number(row.quantity_on_hand)}</td>
              <td>{number(row.period_units_sold)}</td>
              {coverage && <td>{number(row.coverage_days)} días</td>}
              <td>{money(row.inventory_value)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

type AIChatMessage = { role: "user" | "assistant"; content: string };
type AIChatResponse = {
  conversation_id: string;
  answer: string;
  model: string;
  tools_used?: Array<{
    tool?: string;
    duration_ms?: number;
    pagination?: { total?: number; has_more?: boolean };
  }>;
  context?: Filters & ReplenishmentParams;
  analysis_plan?: { intent?: string };
};

function AIAssistant({
  filters,
  replenishmentParams,
}: {
  filters: Filters;
  replenishmentParams: ReplenishmentParams;
}) {
  const requestGeneration = useRef(0);
  const [messages, setMessages] = useState<AIChatMessage[]>([
    {
      role: "assistant",
      content:
        "Soy tu copiloto analitico. Puedo revisar inventario, reposicion, ventas, compras, proveedores, pagos y KPIs usando los filtros actuales.",
    },
  ]);
  const [input, setInput] = useState("");
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [aiConfig, setAiConfig] = useState<{
    configured: boolean;
    provider?: string;
    model?: string;
    key_name?: string;
  } | null>(null);

  useEffect(() => {
    api<{
      configured: boolean;
      provider?: string;
      model?: string;
      key_name?: string;
    }>("/ai/status")
      .then(setAiConfig)
      .catch(() => setAiConfig({ configured: false }));
  }, []);

  useEffect(() => {
    requestGeneration.current += 1;
    setLoading(false);
    setConversationId(null);
    setError(null);
    setMessages([
      {
        role: "assistant",
        content: `Nuevo contexto analítico: ${filters.from_date} a ${filters.to_date}. Puedo revisar inventario, reposición, ventas, compras, proveedores, pagos y KPIs con este período.`,
      },
    ]);
    return () => {
      requestGeneration.current += 1;
    };
  }, [
    filters.from_date,
    filters.to_date,
    filters.currency,
    filters.product_key,
    filters.seller_key,
    filters.warehouse_key,
    filters.document_status,
    filters.family,
    filters.provider_key,
    filters.metric_scope,
    replenishmentParams.weekly_budget,
    replenishmentParams.review_cycle_days,
  ]);

  async function ask(question: string) {
    const message = question.trim();
    if (!message || loading || aiConfig?.configured === false) return;
    const generation = ++requestGeneration.current;
    setInput("");
    setError(null);
    setMessages((current) => [...current, { role: "user", content: message }]);
    setLoading(true);
    try {
      const response = await api<AIChatResponse>("/ai/chat", {
        method: "POST",
        body: JSON.stringify({
          message,
          conversation_id: conversationId,
          context: {
            ...filters,
            weekly_budget: replenishmentParams.weekly_budget,
            review_cycle_days: replenishmentParams.review_cycle_days,
          },
        }),
      });
      if (generation !== requestGeneration.current) return;
      setConversationId(response.conversation_id);
      const tools = response.tools_used
        ?.map((item) => String(item.tool || ""))
        .filter(Boolean)
        .join(", ");
      const route = response.analysis_plan?.intent;
      const metadata = [
        response.context
          ? `Base: ${response.context.from_date} a ${response.context.to_date}; modo ${response.context.metric_scope}; moneda ${response.context.currency || "separada por moneda"}; presupuesto ${money(response.context.weekly_budget)}; revisión cada ${response.context.review_cycle_days} días.`
          : "",
        route ? `Ruta analítica: ${route}.` : "",
        tools ? `Fuentes consultadas: ${tools}.` : "",
        response.tools_used?.some((item) => item.pagination?.has_more)
          ? "Hay fuentes paginadas: esta respuesta no implica haber revisado todo el catálogo."
          : "",
      ]
        .filter(Boolean)
        .join("\n");
      setMessages((current) => [
        ...current,
        {
          role: "assistant",
          content: response.answer + (metadata ? `\n\n${metadata}` : ""),
        },
      ]);
    } catch (requestError) {
      if (generation !== requestGeneration.current) return;
      setError(
        requestError instanceof Error
          ? requestError.message
          : "No fue posible consultar el asistente.",
      );
    } finally {
      if (generation === requestGeneration.current) setLoading(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void ask(input);
  }

  const suggestions = [
    "Que productos debo comprar primero y por que?",
    "Detecta inconsistencias importantes en el inventario actual.",
    "Resume ventas, compras y margen del periodo seleccionado.",
    "A que proveedores deberia comprarles y que productos?",
  ];

  return (
    <>
      <div className="section-heading">
        <div>
          <h2>Asistente IA</h2>
          <p className="muted">
            Analisis de solo lectura sobre el data mart. El asistente no
            modifica Alegra ni aprueba compras.
          </p>
          <p className="muted">
            Contexto de fechas: {filters.from_date} a {filters.to_date}. Usa{" "}
            <strong>Toda la historia</strong> para preguntas históricas.
          </p>
        </div>
        <span className="ai-readonly-badge">Solo lectura</span>
      </div>
      {aiConfig?.configured === false && (
        <div className="warning">
          El asistente esta desactivado. Configura{" "}
          {aiConfig.key_name || "la clave del proveedor"} en las variables del
          servicio API de Railway.
        </div>
      )}
      {aiConfig?.configured && (
        <p className="muted ai-provider">
          Proveedor: {aiConfig.provider} · Modelo: {aiConfig.model}
        </p>
      )}
      <section className="ai-suggestions">
        {suggestions.map((suggestion) => (
          <button
            key={suggestion}
            type="button"
            disabled={aiConfig?.configured === false}
            onClick={() => void ask(suggestion)}
          >
            {suggestion}
          </button>
        ))}
      </section>
      <section className="ai-chat-card">
        <div className="ai-messages">
          {messages.map((message, index) => (
            <div
              className={`ai-message ${message.role}`}
              key={`${message.role}-${index}`}
            >
              <small>{message.role === "assistant" ? "Asistente" : "Tu"}</small>
              <p>{message.content}</p>
            </div>
          ))}
          {loading && (
            <div className="ai-message assistant">
              <small>Asistente</small>
              <p>Analizando los datos...</p>
            </div>
          )}
        </div>
        {error && <div className="error">{error}</div>}
        <form className="ai-composer" onSubmit={submit}>
          <textarea
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder="Pregunta por inventario, ventas, compras o proveedores..."
            maxLength={4000}
            rows={3}
            disabled={aiConfig?.configured === false}
          />
          <button
            className="primary-button"
            disabled={
              loading || aiConfig?.configured === false || !input.trim()
            }
          >
            {loading ? "Analizando..." : "Consultar"}
          </button>
        </form>
      </section>
    </>
  );
}

function PurchaseRecommendations({
  data,
  filters,
  replenishmentParams,
  setReplenishmentParams,
}: {
  data: Record<string, unknown>;
  filters: Filters;
  replenishmentParams: ReplenishmentParams;
  setReplenishmentParams: (value: ReplenishmentParams) => void;
}) {
  const rows = (data.lines || []) as RecommendationRow[];
  const summary = (data.summary || {}) as Row;
  const quality = (data.data_quality || {}) as Record<string, unknown>;
  const backendSupplierOrders = (data.supplier_orders || []) as Array<
    Record<string, unknown>
  >;
  const warnings = (quality.warnings || []) as string[];
  const qualityNotes = (quality.notes || []) as string[];
  const currentDatePlan = quality.is_current_date !== false;
  const [planId, setPlanId] = useState<string | null>(null);
  const [planStatus, setPlanStatus] = useState(String(data.status || "draft"));
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [selectedSupplier, setSelectedSupplier] = useState<string | null>(null);
  const [supplierChoices, setSupplierChoices] = useState<
    Record<string, string>
  >({});
  const [allowBelowMinimum, setAllowBelowMinimum] = useState(false);
  const [includedProducts, setIncludedProducts] = useState<
    Record<string, boolean>
  >({});
  const [quantities, setQuantities] = useState<Record<string, number>>({});

  const supplierKey = (order: Record<string, unknown>) =>
    `${String(order.supplier_key ?? "none")}:${String(order.supplier || "Sin proveedor")}`;
  const productsFor = (order: Record<string, unknown>) =>
    (order.products || []) as RecommendationRow[];
  const recommendedProducts: RecommendationRow[] = backendSupplierOrders
    .flatMap(productsFor)
    .map((product): RecommendationRow => {
      const productKey = String(product.product_key);
      const chosenKey =
        supplierChoices[productKey] || String(product.supplier_key || "");
      const option = (product.supplier_options || []).find(
        (candidate) => String(candidate.supplier_key) === chosenKey,
      );
      if (!option) return product;
      const switched = chosenKey !== String(product.supplier_key || "");
      const suggested = switched
        ? supplierQuantity(
            product,
            option,
            replenishmentParams.review_cycle_days,
          )
        : null;
      return {
        ...product,
        supplier_key: option.supplier_key,
        supplier: option.supplier,
        unit_cost:
          option.last_unit_cost ||
          option.average_unit_cost ||
          product.unit_cost,
        minimum_order_quantity: option.minimum_order_quantity || 0,
        pack_size: option.pack_size || 1,
        minimum_order_amount: option.minimum_order_amount || 0,
        shipping_cost: option.shipping_cost || 0,
        free_shipping_threshold: option.free_shipping_threshold ?? null,
        recommended_quantity:
          suggested?.quantity ?? product.recommended_quantity,
        base_quantity: suggested?.base ?? product.base_quantity,
        forecast_horizon_days:
          suggested?.horizon ?? product.forecast_horizon_days,
      } as unknown as RecommendationRow;
    });
  const supplierOrders = recommendedProducts.reduce<
    Array<Record<string, unknown>>
  >((groups, product) => {
    const groupKey = `${String(product.supplier_key ?? "none")}:${String(product.supplier || "Sin proveedor")}`;
    let group = groups.find((candidate) => supplierKey(candidate) === groupKey);
    if (!group) {
      group = {
        supplier_key: product.supplier_key,
        supplier: product.supplier,
        minimum_order_amount: product.minimum_order_amount,
        shipping_cost: product.shipping_cost,
        free_shipping_threshold: product.free_shipping_threshold,
        products: [],
      };
      groups.push(group);
    }
    (group.products as RecommendationRow[]).push(product);
    return groups;
  }, []);
  const activeOrder =
    supplierOrders.find((order) => supplierKey(order) === selectedSupplier) ||
    supplierOrders[0];
  const exceptionGroups = [
    {
      key: "reconcile",
      title: "Reconciliar inventario",
      count: Number(summary.reconcile_products || 0),
      rows: rows.filter((row) => String(row.decision) === "reconcile"),
      help: "Estos productos tienen diferencias de inventario y no se comprarán automáticamente.",
    },
    {
      key: "supplier",
      title: "Proveedor o costo pendiente",
      count: rows.filter((row) =>
        ["review_supplier", "review_cost"].includes(String(row.decision)),
      ).length,
      rows: rows.filter((row) =>
        ["review_supplier", "review_cost"].includes(String(row.decision)),
      ),
      help: "Requieren completar proveedor o costo antes de sugerir una orden.",
    },
    {
      key: "budget",
      title: "Diferidos por presupuesto",
      count: Number(summary.deferred_by_budget || 0),
      rows: rows.filter((row) => String(row.decision) === "deferred_budget"),
      help: "Tienen necesidad, pero quedaron para la siguiente ventana de compra.",
    },
  ];

  useEffect(() => {
    const nextIncluded: Record<string, boolean> = {};
    const nextQuantities: Record<string, number> = {};
    const orders = (data.supplier_orders || []) as Array<
      Record<string, unknown>
    >;
    const nextSupplierChoices: Record<string, string> = {};
    orders
      .flatMap((order) => (order.products || []) as RecommendationRow[])
      .forEach((row) => {
        const key = String(row.product_key);
        nextIncluded[key] = true;
        nextQuantities[key] = Number(row.recommended_quantity || 0);
        if (row.supplier_key != null)
          nextSupplierChoices[key] = String(row.supplier_key);
      });
    setPlanId(null);
    setPlanStatus(String(data.status || "draft"));
    setActionMessage(null);
    setIncludedProducts(nextIncluded);
    setQuantities(nextQuantities);
    setSupplierChoices(nextSupplierChoices);
    setAllowBelowMinimum(false);
    setSelectedSupplier(
      orders.length
        ? `${String(orders[0].supplier_key ?? "none")}:${String(orders[0].supplier || "Sin proveedor")}`
        : null,
    );
  }, [data]);

  function markUnsaved() {
    if (planId) {
      setPlanId(null);
      setPlanStatus("draft");
    }
    setActionMessage("Tienes cambios sin guardar.");
  }

  function setIncluded(productKey: string, included: boolean) {
    setIncludedProducts((current) => ({ ...current, [productKey]: included }));
    markUnsaved();
  }

  function setQuantity(productKey: string, quantity: number) {
    setQuantities((current) => ({
      ...current,
      [productKey]: Math.max(0, quantity),
    }));
    setIncludedProducts((current) => ({
      ...current,
      [productKey]: quantity > 0,
    }));
    markUnsaved();
  }

  function setProductSupplier(
    product: RecommendationRow,
    supplierKeyValue: string,
  ) {
    const option = (product.supplier_options || []).find(
      (candidate) => String(candidate.supplier_key) === supplierKeyValue,
    );
    if (!option) return;
    const suggested = supplierQuantity(
      product,
      option,
      replenishmentParams.review_cycle_days,
    );
    setSupplierChoices((current) => ({
      ...current,
      [String(product.product_key)]: supplierKeyValue,
    }));
    setQuantities((current) => ({
      ...current,
      [String(product.product_key)]: suggested.quantity,
    }));
    setIncludedProducts((current) => ({
      ...current,
      [String(product.product_key)]: suggested.quantity > 0,
    }));
    setSelectedSupplier(
      `${supplierKeyValue}:${String(option.supplier || "Sin proveedor")}`,
    );
    markUnsaved();
  }

  async function persistPlan() {
    setSaving(true);
    setActionMessage(null);
    try {
      const created = await api<Record<string, unknown>>("/procurement/plans", {
        method: "POST",
        body: JSON.stringify({
          as_of_date: filters.to_date,
          weekly_budget: replenishmentParams.weekly_budget,
          currency_code: filters.currency || "COP",
          review_cycle_days: replenishmentParams.review_cycle_days,
        }),
      });
      const createdPlanId = String(created.plan_id);
      const savedPlan = await api<Record<string, unknown>>(
        `/procurement/plans/${createdPlanId}`,
      );
      const savedLines = (savedPlan.lines || []) as Array<
        Record<string, unknown>
      >;
      const savedByProduct = new Map(
        savedLines.map((line) => [String(line.product_key), line]),
      );
      const adjustments = recommendedProducts.flatMap((product) => {
        const productKey = String(product.product_key);
        const savedLine = savedByProduct.get(productKey);
        if (!savedLine) return [];
        const included =
          includedProducts[productKey] !== false &&
          Number(quantities[productKey] || 0) > 0;
        const quantity = Number(
          quantities[productKey] ?? product.recommended_quantity ?? 0,
        );
        const originalQuantity = Number(product.recommended_quantity || 0);
        if (!included) {
          return [
            api(
              `/procurement/plans/${createdPlanId}/lines/${String(savedLine.id)}`,
              {
                method: "PATCH",
                body: JSON.stringify({
                  decision: "discarded",
                  approved_quantity: 0,
                  note: "Excluido manualmente desde el tablero",
                }),
              },
            ),
          ];
        }
        const supplierChanged =
          Number(product.supplier_key) !== Number(savedLine.supplier_key);
        if (quantity !== originalQuantity || supplierChanged) {
          return [
            api(
              `/procurement/plans/${createdPlanId}/lines/${String(savedLine.id)}`,
              {
                method: "PATCH",
                body: JSON.stringify({
                  decision: "approved",
                  approved_quantity: quantity,
                  supplier_key: Number(product.supplier_key),
                  note: supplierChanged
                    ? "Proveedor seleccionado desde el tablero"
                    : "Cantidad ajustada desde el tablero",
                }),
              },
            ),
          ];
        }
        return [];
      });
      await Promise.all(adjustments);
      setPlanId(createdPlanId);
      setPlanStatus(String(created.status));
      setActionMessage(
        created.status === "blocked_data"
          ? "El plan quedó guardado, pero no puede aprobarse hasta actualizar sus fuentes."
          : "Plan guardado con tus productos y cantidades. Ya puedes aprobarlo.",
      );
    } catch (requestError) {
      setActionMessage(
        requestError instanceof Error
          ? requestError.message
          : "No fue posible guardar el plan.",
      );
    } finally {
      setSaving(false);
    }
  }

  async function approvePlan() {
    if (!planId) return;
    setSaving(true);
    try {
      const approved = await api<Record<string, unknown>>(
        `/procurement/plans/${planId}/approve`,
        {
          method: "POST",
          body: JSON.stringify({ allow_below_minimum: allowBelowMinimum }),
        },
      );
      setPlanStatus(String(approved.status));
      setActionMessage(
        "Plan aprobado. Revisa una última vez antes de enviarlo a Alegra.",
      );
    } catch (requestError) {
      setActionMessage(
        requestError instanceof Error
          ? requestError.message
          : "No fue posible aprobar el plan.",
      );
    } finally {
      setSaving(false);
    }
  }

  async function submitPlan() {
    if (
      !planId ||
      !window.confirm("¿Crear las órdenes de compra aprobadas en Alegra?")
    )
      return;
    setSaving(true);
    try {
      const submitted = await api<Record<string, unknown>>(
        `/procurement/plans/${planId}/submit-to-alegra`,
        {
          method: "POST",
          body: JSON.stringify({ confirm: true }),
        },
      );
      const orders = (submitted.orders || []) as Array<Record<string, unknown>>;
      const warnings = (submitted.warnings || []) as string[];
      const complete = Boolean(submitted.complete ?? true);
      setPlanStatus(complete ? "submitted" : "approved");
      setActionMessage(
        complete
          ? `Se confirmaron ${orders.length} órdenes en Alegra.`
          : `Se confirmaron ${orders.filter((order) => order.alegra_order_id).length} órdenes. ${warnings.join(" ")} No reenvíes manualmente una orden sin verificarla en Alegra.`,
      );
    } catch (requestError) {
      setActionMessage(
        requestError instanceof Error
          ? requestError.message
          : "No fue posible enviar las órdenes.",
      );
    } finally {
      setSaving(false);
    }
  }

  const orderStats = (order: Record<string, unknown>) => {
    const selected = productsFor(order).filter(
      (product) =>
        includedProducts[String(product.product_key)] !== false &&
        Number(quantities[String(product.product_key)] || 0) > 0,
    );
    const value = selected.reduce(
      (total, product) =>
        total +
        Number(quantities[String(product.product_key)] || 0) *
          Number(product.unit_cost || 0),
      0,
    );
    const minimum = Number(order.minimum_order_amount || 0);
    const threshold = Number(order.free_shipping_threshold || 0);
    const shipping =
      value > 0 && (order.free_shipping_threshold == null || value < threshold)
        ? Number(order.shipping_cost || 0)
        : 0;
    return {
      products: selected.length,
      units: selected.reduce(
        (total, product) =>
          total + Number(quantities[String(product.product_key)] || 0),
        0,
      ),
      value,
      belowMinimum: minimum > 0 && value < minimum,
      amountToMinimum: Math.max(minimum - value, 0),
      shipping,
      estimatedTotal: value + shipping,
      critical: selected.some((product) => product.priority === "critical"),
    };
  };

  const hasBelowMinimum = supplierOrders.some(
    (order) => orderStats(order).belowMinimum,
  );
  const needsMinimumOverride =
    hasBelowMinimum ||
    Boolean(actionMessage?.toLowerCase().includes("minimum"));
  const currentRecommendedValue = supplierOrders.reduce(
    (total, order) => total + orderStats(order).estimatedTotal,
    0,
  );
  const currentRecommendedProducts = supplierOrders.reduce(
    (total, order) => total + orderStats(order).products,
    0,
  );
  const currentCriticalProducts = supplierOrders.reduce(
    (total, order) =>
      total +
      productsFor(order).filter(
        (product) =>
          product.priority === "critical" &&
          includedProducts[String(product.product_key)] !== false &&
          Number(quantities[String(product.product_key)] || 0) > 0,
      ).length,
    0,
  );

  const decisionLabel = (decision: unknown) =>
    ({
      buy_now: "Hacer pedido",
      ready_for_approval: "Listo para revisar",
      accumulate_minimum: "Conviene acumular",
      urgent_below_minimum: "Urgente · bajo mínimo",
    })[String(decision)] || "Revisar pedido";

  return (
    <>
      <div className="section-heading">
        <div>
          <h2>Reponer</h2>
          <p className="muted">
            Abre un proveedor, revisa qué comprar y ajusta las cantidades antes
            de guardar el pedido.
          </p>
        </div>
        <div className="purchase-actions">
          <button
            className="primary-button compact-button"
            disabled={
              saving ||
              !quality.ready ||
              !currentDatePlan ||
              !supplierOrders.length
            }
            onClick={persistPlan}
          >
            1. Guardar pedido
          </button>
          <button
            className="text-button action-button"
            disabled={
              saving ||
              !currentDatePlan ||
              !planId ||
              planStatus !== "draft" ||
              (needsMinimumOverride && !allowBelowMinimum)
            }
            onClick={approvePlan}
          >
            2. Aprobar
          </button>
          <button
            className="text-button action-button danger-action"
            disabled={saving || !planId || planStatus !== "approved"}
            onClick={submitPlan}
          >
            3. Enviar a Alegra
          </button>
        </div>
      </div>
      <details className="advanced-settings">
        <summary>Configuración avanzada</summary>
        <section className="filters compact-filters">
          <label>
            Presupuesto semanal
            <input
              type="number"
              min="0"
              step="100000"
              value={replenishmentParams.weekly_budget}
              onChange={(event) => {
                setReplenishmentParams({
                  ...replenishmentParams,
                  weekly_budget: Number(event.target.value) || 0,
                });
                markUnsaved();
              }}
            />
          </label>
          <label>
            Ciclo de revisión
            <input
              type="number"
              min="1"
              max="31"
              value={replenishmentParams.review_cycle_days}
              onChange={(event) => {
                setReplenishmentParams({
                  ...replenishmentParams,
                  review_cycle_days: Number(event.target.value) || 7,
                });
                markUnsaved();
              }}
            />
          </label>
          <label>
            Fecha del plan
            <input value={filters.to_date} readOnly />
          </label>
        </section>
      </details>
      {!quality.ready && (
        <div className="error">
          <strong>Plan bloqueado por calidad de datos.</strong>
          {warnings.map((warning) => (
            <span className="quality-warning" key={warning}>
              {warning}
            </span>
          ))}
        </div>
      )}
      {quality.ready && (
        <div className="refresh-status">
          Datos actualizados y listos para preparar pedidos.
        </div>
      )}
      {qualityNotes.map((note) => (
        <div className="warning" key={note}>
          {note}
        </div>
      ))}
      {actionMessage && (
        <div className="warning" aria-live="polite">
          {actionMessage}
        </div>
      )}
      {needsMinimumOverride && (
        <div className="minimum-approval-warning">
          <strong>
            Se requiere autorización para continuar bajo mínimos de proveedor.
          </strong>
          <p>
            Si un cambio reciente de política hizo aparecer esta alerta al
            aprobar, revisa de nuevo cada pedido y el flete antes de autorizar.
          </p>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={allowBelowMinimum}
              onChange={(event) => setAllowBelowMinimum(event.target.checked)}
            />{" "}
            Confirmo continuar bajo los mínimos indicados
          </label>
        </div>
      )}
      <section className="cards replenishment-summary">
        <article className="metric-card">
          <p>Compra recomendada</p>
          <strong>{money(currentRecommendedValue)}</strong>
          <small>
            incluye flete estimado · presupuesto{" "}
            {money(replenishmentParams.weekly_budget)}
          </small>
        </article>
        <article className="metric-card">
          <p>Proveedores con pedido</p>
          <strong>{number(supplierOrders.length)}</strong>
          <small>abre cada uno para ver sus productos</small>
        </article>
        <article className="metric-card">
          <p>Productos por comprar</p>
          <strong>{number(currentRecommendedProducts)}</strong>
          <small>
            {number(currentCriticalProducts)} requieren atención prioritaria
          </small>
        </article>
      </section>
      <section className="supplier-orders-section">
        <div className="subsection-heading">
          <div>
            <h3>Proveedores a los que debes comprar</h3>
            <p className="muted">
              Selecciona un proveedor para revisar su pedido.
            </p>
          </div>
        </div>
        {!supplierOrders.length ? (
          <div className="empty">
            No hay pedidos sugeridos con los datos y el presupuesto actuales.
          </div>
        ) : (
          <div className="supplier-workspace">
            <div className="supplier-order-list">
              {supplierOrders.map((order) => {
                const key = supplierKey(order);
                const stats = orderStats(order);
                const currentDecision = stats.belowMinimum
                  ? stats.critical
                    ? "urgent_below_minimum"
                    : "accumulate_minimum"
                  : "ready_for_approval";
                return (
                  <button
                    type="button"
                    className={`supplier-order-card ${key === supplierKey(activeOrder) ? "active" : ""}`}
                    key={key}
                    onClick={() => setSelectedSupplier(key)}
                  >
                    <span className="supplier-order-top">
                      <strong>
                        {String(order.supplier || "Sin proveedor")}
                      </strong>
                      <span
                        className={`order-decision decision-${currentDecision}`}
                      >
                        {decisionLabel(currentDecision)}
                      </span>
                    </span>
                    <span className="supplier-order-value">
                      {money(stats.value)}
                    </span>
                    <span className="supplier-order-metrics">
                      <span>{number(stats.products)} productos</span>
                      <span>{number(stats.units)} unidades</span>
                      <span>{number(Number(stats.critical))} críticos</span>
                    </span>
                    {stats.belowMinimum && (
                      <span className="minimum-warning">
                        Faltan {money(stats.amountToMinimum)} para el mínimo.
                      </span>
                    )}
                    {stats.shipping > 0 && (
                      <span className="minimum-warning">
                        Flete estimado: {money(stats.shipping)} · total:{" "}
                        {money(stats.estimatedTotal)}
                      </span>
                    )}
                    <span className="view-products">
                      {key === supplierKey(activeOrder)
                        ? "Viendo productos"
                        : "Ver productos"}{" "}
                      →
                    </span>
                  </button>
                );
              })}
            </div>
            {activeOrder && (
              <div className="table-card supplier-products-panel">
                <div className="subsection-heading">
                  <div>
                    <h3>
                      Pedido a {String(activeOrder.supplier || "Sin proveedor")}
                    </h3>
                    <p className="muted">
                      Desmarca productos, elige proveedor y ajusta la cantidad
                      si hace falta.
                    </p>
                  </div>
                  <strong>
                    {money(orderStats(activeOrder).estimatedTotal)}
                  </strong>
                </div>
                {orderStats(activeOrder).belowMinimum && (
                  <div className="minimum-warning">
                    Este pedido está bajo el mínimo: faltan{" "}
                    {money(orderStats(activeOrder).amountToMinimum)}. El flete
                    estimado se suma al presupuesto.
                  </div>
                )}
                <div className="table-scroll">
                  <table className="replenishment-products">
                    <thead>
                      <tr>
                        <th>Incluir</th>
                        <th>Producto / última compra</th>
                        <th>Disponible</th>
                        <th>Proveedor</th>
                        <th>Cantidad a comprar</th>
                        <th>Total</th>
                        <th>Por qué</th>
                      </tr>
                    </thead>
                    <tbody>
                      {productsFor(activeOrder).map((row, index) => {
                        const key = String(row.product_key || index);
                        const included = includedProducts[key] !== false;
                        const quantity = Number(
                          quantities[key] ?? row.recommended_quantity ?? 0,
                        );
                        const options = row.supplier_options || [];
                        return (
                          <tr
                            className={!included ? "excluded-product" : ""}
                            key={key}
                          >
                            <td>
                              <input
                                className="row-checkbox"
                                type="checkbox"
                                checked={included}
                                onChange={(event) =>
                                  setIncluded(key, event.target.checked)
                                }
                                aria-label={`Incluir ${String(row.name)} en el pedido`}
                              />
                            </td>
                            <td>
                              <strong>{String(row.name)}</strong>
                              <small className="table-subtitle">
                                {String(row.reference || "")} ·{" "}
                                {String(row.family || "SIN FAMILIA")}
                              </small>
                              <small className="table-subtitle">
                                Última compra:{" "}
                                {number(row.last_purchase_quantity)} u ·{" "}
                                {String(row.last_purchase_date || "sin fecha")}
                                {row.last_purchase_supplier
                                  ? ` · ${String(row.last_purchase_supplier)}`
                                  : ""}
                              </small>
                            </td>
                            <td>
                              {number(row.quantity_on_hand)}
                              <small className="table-subtitle">
                                {number(row.quantity_in_transit)} en tránsito
                              </small>
                            </td>
                            <td>
                              {options.length > 1 ? (
                                <select
                                  value={String(row.supplier_key || "")}
                                  onChange={(event) =>
                                    setProductSupplier(row, event.target.value)
                                  }
                                  aria-label={`Proveedor para ${String(row.name)}`}
                                >
                                  {options.map((option) => {
                                    const cost = Number(
                                      option.last_unit_cost ||
                                        option.average_unit_cost ||
                                        0,
                                    );
                                    return (
                                      <option
                                        key={String(option.supplier_key)}
                                        value={String(option.supplier_key)}
                                        disabled={cost <= 0}
                                      >
                                        {String(option.supplier || "Proveedor")}{" "}
                                        · {cost > 0 ? money(cost) : "sin costo"}
                                        {option.is_preferred
                                          ? " · preferido"
                                          : ""}
                                      </option>
                                    );
                                  })}
                                </select>
                              ) : (
                                <span>
                                  {String(row.supplier || "Sin proveedor")}
                                </span>
                              )}
                              <small className="table-subtitle">
                                Empaque: {number(row.pack_size || 1)} · MOQ:{" "}
                                {number(row.minimum_order_quantity || 0)}
                              </small>
                            </td>
                            <td>
                              <input
                                className="quantity-input"
                                type="number"
                                min={Number(row.minimum_order_quantity || 0)}
                                step={Number(row.pack_size || 1)}
                                value={quantity}
                                onChange={(event) =>
                                  setQuantity(
                                    key,
                                    Number(event.target.value) || 0,
                                  )
                                }
                                aria-label={`Cantidad a comprar de ${String(row.name)}`}
                              />
                            </td>
                            <td>
                              <strong>
                                {money(
                                  included
                                    ? quantity * Number(row.unit_cost || 0)
                                    : 0,
                                )}
                              </strong>
                              <small className="table-subtitle">
                                {money(Number(row.unit_cost || 0))} c/u
                              </small>
                            </td>
                            <td>
                              <span className="purchase-reason">
                                {String(row.priority) === "critical"
                                  ? "Stock urgente"
                                  : "Reponer esta semana"}
                              </span>
                              <details>
                                <summary>Ver análisis</summary>
                                <span className="analysis-detail">
                                  Clase {String(row.abc_class)}
                                  {String(row.xyz_class)} · cobertura{" "}
                                  {row.coverage_days == null
                                    ? "n/d"
                                    : `${number(row.coverage_days)} días`}{" "}
                                  · plazo {number(row.forecast_horizon_days)}{" "}
                                  días · pronóstico {number(row.daily_forecast)}
                                  /día · confianza{" "}
                                  {String(row.confidence || "n/d")}
                                </span>
                              </details>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        )}
      </section>
      <section className="exceptions-section">
        <h3>Productos que requieren revisión</h3>
        <p className="muted">
          No forman parte de los pedidos anteriores. Ábrelos solo cuando
          necesites resolver excepciones.
        </p>
        <div className="exception-grid">
          {exceptionGroups.map((group) => (
            <details className="exception-card" key={group.key}>
              <summary>
                <span>
                  <strong>{group.title}</strong>
                  <small>{group.help}</small>
                </span>
                <b>{number(group.count)}</b>
              </summary>
              {group.rows.length > 0 && (
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>Producto</th>
                        <th>Stock</th>
                        <th>Sugerencia</th>
                      </tr>
                    </thead>
                    <tbody>
                      {group.rows.slice(0, 100).map((row, index) => (
                        <tr key={String(row.product_key || index)}>
                          <td>
                            {String(row.name)}
                            <small className="table-subtitle">
                              {String(row.reference || "")}
                            </small>
                          </td>
                          <td>{number(row.quantity_on_hand)}</td>
                          <td>
                            {String(row.decision) === "reconcile"
                              ? "Verificar inventario"
                              : String(row.decision) === "deferred_budget"
                                ? "Comprar en la siguiente ventana"
                                : "Completar proveedor o costo"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </details>
          ))}
        </div>
      </section>
      <NoDemandProducts
        count={Number(summary.dead_or_no_demand || 0)}
        filters={filters}
        replenishmentParams={replenishmentParams}
      />
    </>
  );
}

function supplierQuantity(
  product: RecommendationRow,
  supplier: SupplierOption,
  reviewCycleDays: number,
): { quantity: number; base: number; horizon: number } {
  const lead = Number(
    supplier.lead_time_days ??
      supplier.observed_lead_days ??
      supplier.default_lead_time_days ??
      7,
  );
  const horizon = Math.max(1, Math.ceil(lead + reviewCycleDays));
  const expected = Math.max(0, Number(product.daily_forecast || 0) * horizon);
  const serviceLevel = Number(product.service_level || 0.92).toFixed(3);
  const zValues: Record<string, number> = {
    "0.750": 0.674,
    "0.850": 1.036,
    "0.880": 1.175,
    "0.900": 1.282,
    "0.920": 1.405,
    "0.950": 1.645,
    "0.970": 1.881,
  };
  const target =
    expected + (zValues[serviceLevel] || 1.405) * Math.sqrt(expected);
  const stockPosition =
    Math.max(0, Number(product.quantity_on_hand || 0)) +
    Number(product.quantity_in_transit || 0);
  const base = Math.max(0, target - stockPosition);
  if (base <= 0) return { quantity: 0, base, horizon };
  const minimum = Math.max(0, Number(supplier.minimum_order_quantity || 0));
  const pack = Math.max(0.0001, Number(supplier.pack_size || 1));
  const quantity = Math.ceil((Math.max(base, minimum) - 1e-9) / pack) * pack;
  return { quantity, base, horizon };
}

function NoDemandProducts({
  count,
  filters,
  replenishmentParams,
}: {
  count: number;
  filters: Filters;
  replenishmentParams: ReplenishmentParams;
}) {
  const [items, setItems] = useState<RecommendationRow[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const pageSize = 50;
  const requestGeneration = useRef(0);
  useEffect(() => {
    requestGeneration.current += 1;
    setItems(null);
    setLoading(false);
    setError(null);
    return () => {
      requestGeneration.current += 1;
    };
  }, [filters, replenishmentParams]);

  async function loadProducts() {
    const generation = ++requestGeneration.current;
    setLoading(true);
    setError(null);
    try {
      const params = {
        ...replenishmentParams,
        as_of_date: filters.to_date,
        decision: "no_reorder",
        offset: 0,
        limit: 5000,
      };
      const all: RecommendationRow[] = [];
      while (true) {
        const result = await api<Record<string, unknown>>(
          `/procurement/preview${query(filters, params)}`,
        );
        if (generation !== requestGeneration.current) return;
        const batch = (result.lines || []) as RecommendationRow[];
        all.push(...batch);
        const pagination = result.line_pagination as
          | { has_more?: boolean }
          | undefined;
        if (!pagination?.has_more) break;
        if (!batch.length)
          throw new Error("No se pudo completar la paginación del catálogo.");
        params.offset += batch.length;
      }
      setItems(all);
      setPage(0);
    } catch (requestError) {
      if (generation !== requestGeneration.current) return;
      setError(
        requestError instanceof Error
          ? requestError.message
          : "No fue posible cargar los productos.",
      );
    } finally {
      if (generation === requestGeneration.current) setLoading(false);
    }
  }

  const matching = (items || []).filter((item) =>
    `${String(item.name || "")} ${String(item.reference || "")}`
      .toLocaleLowerCase()
      .includes(search.toLocaleLowerCase()),
  );
  const pageCount = Math.ceil(matching.length / pageSize);
  const visible = matching.slice(page * pageSize, (page + 1) * pageSize);
  return (
    <section className="exception-card no-demand-section">
      <div className="subsection-heading">
        <div>
          <h3>Sin demanda reciente</h3>
          <p className="muted">
            {number(count)} productos con existencias y sin ventas en los
            últimos 365 días de la fecha seleccionada.
          </p>
        </div>
        {items === null && (
          <button
            className="text-button compact-button"
            disabled={loading}
            onClick={loadProducts}
          >
            {loading ? "Cargando…" : "Ver productos"}
          </button>
        )}
      </div>
      {error && (
        <div className="error" role="alert">
          {error}
        </div>
      )}
      {items && (
        <>
          <label className="no-demand-search">
            Buscar producto
            <input
              value={search}
              onChange={(event) => {
                setSearch(event.target.value);
                setPage(0);
              }}
              placeholder="Nombre o referencia"
            />
          </label>
          {!visible.length ? (
            <p className="muted">No hay coincidencias.</p>
          ) : (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Producto</th>
                    <th>Familia</th>
                    <th>Stock actual</th>
                    <th>Última compra</th>
                    <th>Proveedor último</th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((item) => (
                    <tr key={String(item.product_key)}>
                      <td>
                        {String(item.name)}
                        <small className="table-subtitle">
                          {String(item.reference || "")}
                        </small>
                      </td>
                      <td>{String(item.family || "SIN FAMILIA")}</td>
                      <td>{number(item.quantity_on_hand)}</td>
                      <td>
                        {number(item.last_purchase_quantity)} u ·{" "}
                        {String(item.last_purchase_date || "sin dato")}
                      </td>
                      <td>
                        {String(
                          item.last_purchase_supplier ||
                            "Sin proveedor relacionado",
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {pageCount > 1 && (
            <div className="pagination-controls">
              <button
                className="text-button compact-button"
                disabled={page === 0}
                onClick={() => setPage((current) => current - 1)}
              >
                Anterior
              </button>
              <span>
                Página {page + 1} de {pageCount} · {number(matching.length)}{" "}
                productos
              </span>
              <button
                className="text-button compact-button"
                disabled={page + 1 >= pageCount}
                onClick={() => setPage((current) => current + 1)}
              >
                Siguiente
              </button>
            </div>
          )}
        </>
      )}
    </section>
  );
}

function LegacyPurchaseRecommendations({
  data,
  filters,
  replenishmentParams,
  setReplenishmentParams,
}: {
  data: Record<string, unknown>;
  filters: Filters;
  replenishmentParams: ReplenishmentParams;
  setReplenishmentParams: (value: ReplenishmentParams) => void;
}) {
  const rows = (data.items || []) as RecommendationRow[];
  const excessItems = (data.excess_items || []) as RecommendationRow[];
  const slowItems = (data.slow_items || []) as RecommendationRow[];
  const supplierOrders = (data.supplier_orders || []) as Array<
    Record<string, unknown>
  >;
  const supplierPolicies = (data.supplier_policies || []) as Row[];
  const parameters = (data.parameters || {}) as Row;
  const [statusFilter, setStatusFilter] = useState("all");
  const [localStatuses, setLocalStatuses] = useState<Record<string, string>>(
    {},
  );
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [savingProduct, setSavingProduct] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [policyEdits, setPolicyEdits] = useState<
    Record<string, Record<string, string>>
  >({});
  const [policySaving, setPolicySaving] = useState<string | null>(null);

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

  const currentStatus = (row: RecommendationRow) =>
    localStatuses[String(row.product_key)] ||
    String(row.review_status || "pending");
  const visibleRows = rows.filter(
    (row) => statusFilter === "all" || currentStatus(row) === statusFilter,
  );
  const estimatedValue = rows.reduce(
    (total, row) =>
      total +
      Number(row.recommended_quantity || 0) * Number(row.unit_cost || 0),
    0,
  );
  const counts = rows.reduce<Record<string, number>>((result, row) => {
    const priority = String(row.priority || "medium");
    result[priority] = (result[priority] || 0) + 1;
    return result;
  }, {});
  const noSupplier = rows.filter((row) => !row.preferred_supplier).length;
  const negativeStock = rows.filter(
    (row) => Number(row.quantity_on_hand || 0) < 0,
  ).length;
  const lowConfidence = rows.filter(
    (row) => String(row.recommendation_confidence || "") === "baja",
  ).length;
  const groups = visibleRows.reduce<Record<string, RecommendationRow[]>>(
    (result, row) => {
      const supplier = String(row.preferred_supplier || "Sin proveedor");
      (result[supplier] ||= []).push(row);
      return result;
    },
    {},
  );

  async function saveAction(
    row: RecommendationRow,
    status: string,
    note = notes[String(row.product_key)] || "",
  ) {
    const productKey = String(row.product_key);
    setSavingProduct(productKey);
    setActionError(null);
    try {
      const snoozedUntil =
        status === "snoozed"
          ? new Date(Date.now() + 14 * 24 * 60 * 60 * 1000)
              .toISOString()
              .slice(0, 10)
          : null;
      await api("/analytics/purchase-recommendations/" + productKey, {
        method: "PATCH",
        body: JSON.stringify({
          status,
          note: note || null,
          snoozed_until: snoozedUntil,
        }),
      });
      setLocalStatuses((current) => ({ ...current, [productKey]: status }));
    } catch (requestError) {
      setActionError(
        requestError instanceof Error
          ? requestError.message
          : "No fue posible guardar el estado.",
      );
    } finally {
      setSavingProduct(null);
    }
  }

  async function saveSupplierPolicy(row: Row, values: Record<string, string>) {
    const key = String(row.supplier_key);
    setPolicySaving(key);
    setActionError(null);
    const optionalNumber = (value: string) =>
      value.trim() === "" ? null : Number(value);
    try {
      await api(
        "/analytics/purchase-recommendations/policies/suppliers/" +
          row.supplier_key,
        {
          method: "PUT",
          body: JSON.stringify({
            currency_code: String(
              row.currency_code || filters.currency || "COP",
            ),
            minimum_order_amount: optionalNumber(
              values.minimum_order_amount || "",
            ),
            shipping_cost: Number(values.shipping_cost || 0),
            free_shipping_threshold: optionalNumber(
              values.free_shipping_threshold || "",
            ),
            default_lead_time_days: Number(values.default_lead_time_days || 7),
            max_wait_days: Number(values.max_wait_days || 7),
            priority: Number(values.priority || 100),
            active: true,
            notes: values.notes || null,
          }),
        },
      );
      setActionError(
        "Politica del proveedor guardada. Actualiza los datos para recalcular el pedido.",
      );
    } catch (requestError) {
      setActionError(
        requestError instanceof Error
          ? requestError.message
          : "No fue posible guardar la politica.",
      );
    } finally {
      setPolicySaving(null);
    }
  }

  async function exportCsv() {
    setActionError(null);
    try {
      const response = await fetch(
        "/api/v1/analytics/purchase-recommendations/export" + query(filters),
        { credentials: "same-origin" },
      );
      if (!response.ok)
        throw new Error("No fue posible exportar las recomendaciones.");
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "reponer.csv";
      link.click();
      URL.revokeObjectURL(url);
    } catch (requestError) {
      setActionError(
        requestError instanceof Error
          ? requestError.message
          : "No fue posible exportar.",
      );
    }
  }

  return (
    <>
      <div className="section-heading">
        <div>
          <h2>Reponer</h2>
          <p className="muted">
            Cola de decisión de compra basada en stock, demanda, cobertura y
            proveedores históricos.
          </p>
        </div>
        <button className="primary-button compact-button" onClick={exportCsv}>
          Exportar CSV
        </button>
      </div>
      <section className="cards">
        <article className="metric-card">
          <p>Productos sugeridos</p>
          <strong>{number(rows.length)}</strong>
          <small>
            {number(parameters.target_coverage_days)} días de cobertura objetivo
          </small>
        </article>
        <article className="metric-card">
          <p>Críticos / agotados</p>
          <strong>{number(counts.critical || 0)}</strong>
          <small>con demanda reciente</small>
        </article>
        <article className="metric-card">
          <p>Sin proveedor</p>
          <strong>{number(noSupplier)}</strong>
          <small>requieren validación manual</small>
        </article>
        <article className="metric-card">
          <p>Stock a reconciliar</p>
          <strong>{number(negativeStock)}</strong>
          <small>
            {number(lowConfidence)} recomendaciones con confianza baja
          </small>
        </article>
        <article className="metric-card">
          <p>Compra estimada</p>
          <strong>{money(estimatedValue)}</strong>
          <small>costo histórico disponible</small>
        </article>
      </section>
      <div className="warning">
        Demanda seleccionada: {String(parameters.demand_from || "")} a{" "}
        {String(parameters.demand_to || "")}. Tambien se muestran velocidades de
        7, 30 y 90 dias. El stock proviene del ultimo snapshot de Alegra; no se
        descuentan ordenes pendientes porque aun no estan integradas. Las
        cantidades de compra se calculan con historial desde{" "}
        {String(parameters.purchase_history_from || "2025-01-01")}; las compras
        anteriores no se usan como referencia de lote.
      </div>
      {actionError && <div className="error">{actionError}</div>}
      <section className="filters compact-filters">
        <label>
          Estado de revisión
          <select
            value={statusFilter}
            onChange={(event) => setStatusFilter(event.target.value)}
          >
            <option value="all">Todos</option>
            <option value="pending">Pendientes</option>
            <option value="reviewed">Revisados</option>
            <option value="snoozed">Pospuestos</option>
            <option value="purchased">Comprados</option>
            <option value="discarded">Descartados</option>
          </select>
        </label>
        <label>
          Cobertura objetivo
          <input
            type="number"
            min="7"
            max="365"
            value={replenishmentParams.target_coverage_days}
            onChange={(event) =>
              setReplenishmentParams({
                ...replenishmentParams,
                target_coverage_days: Number(event.target.value) || 30,
              })
            }
          />
        </label>
        <label>
          Plazo compra
          <input
            type="number"
            min="0"
            max="90"
            value={replenishmentParams.lead_time_days}
            onChange={(event) =>
              setReplenishmentParams({
                ...replenishmentParams,
                lead_time_days: Number(event.target.value) || 0,
              })
            }
          />
        </label>
        <label>
          Días seguridad
          <input
            type="number"
            min="0"
            max="90"
            value={replenishmentParams.safety_days}
            onChange={(event) =>
              setReplenishmentParams({
                ...replenishmentParams,
                safety_days: Number(event.target.value) || 0,
              })
            }
          />
        </label>
      </section>
      <SupplierOrderPlan orders={supplierOrders} />
      <SupplierPolicyTable
        policies={supplierPolicies}
        edits={policyEdits}
        setEdits={setPolicyEdits}
        onSave={saveSupplierPolicy}
        saving={policySaving}
      />
      {!visibleRows.length ? (
        <div className="empty">No hay recomendaciones para este filtro.</div>
      ) : (
        <>
          <section className="table-card">
            <h3>Plan agrupado por proveedor</h3>
            <table>
              <thead>
                <tr>
                  <th>Proveedor</th>
                  <th>Productos</th>
                  <th>Unidades</th>
                  <th>Compra estimada</th>
                  <th>Críticos</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(groups)
                  .sort(
                    ([, left], [, right]) =>
                      right.reduce(
                        (sum, row) =>
                          sum +
                          Number(row.recommended_quantity || 0) *
                            Number(row.unit_cost || 0),
                        0,
                      ) -
                      left.reduce(
                        (sum, row) =>
                          sum +
                          Number(row.recommended_quantity || 0) *
                            Number(row.unit_cost || 0),
                        0,
                      ),
                  )
                  .map(([supplier, supplierRows]) => (
                    <tr key={supplier}>
                      <td>{supplier}</td>
                      <td>{number(supplierRows.length)}</td>
                      <td>
                        {number(
                          supplierRows.reduce(
                            (sum, row) =>
                              sum + Number(row.recommended_quantity || 0),
                            0,
                          ),
                        )}
                      </td>
                      <td>
                        {money(
                          supplierRows.reduce(
                            (sum, row) =>
                              sum +
                              Number(row.recommended_quantity || 0) *
                                Number(row.unit_cost || 0),
                            0,
                          ),
                        )}
                      </td>
                      <td>
                        {number(
                          supplierRows.filter(
                            (row) => row.priority === "critical",
                          ).length,
                        )}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </section>
          <section className="table-card">
            <h3>Cola de reposición</h3>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Estado</th>
                    <th>Prioridad</th>
                    <th>Producto</th>
                    <th>Proveedor</th>
                    <th>Stock actual</th>
                    <th>Ultima compra</th>
                    <th>Lote ref.</th>
                    <th>Compras / intervalo</th>
                    <th>Velocidad 7/30/90</th>
                    <th>Cobertura</th>
                    <th>Base / comprar</th>
                    <th>Costo</th>
                    <th>Valor</th>
                    <th>Confianza</th>
                    <th>Motivo</th>
                    <th>Revisión</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleRows.map((row, index) => {
                    const key = String(row.product_key || index);
                    const options = row.supplier_options || [];
                    const status = currentStatus(row);
                    return (
                      <tr key={key}>
                        <td>
                          <select
                            value={status}
                            disabled={savingProduct === key}
                            onChange={(event) =>
                              saveAction(row, event.target.value)
                            }
                          >
                            <option value="pending">Pendiente</option>
                            <option value="reviewed">Revisado</option>
                            <option value="snoozed">Posponer 14 días</option>
                            <option value="purchased">Comprado</option>
                            <option value="discarded">Descartado</option>
                          </select>
                        </td>
                        <td>{String(row.priority)}</td>
                        <td>
                          {String(row.name)}
                          {row.reference ? " · " + String(row.reference) : ""}
                          <small className="table-subtitle">
                            {String(row.family)}
                          </small>
                        </td>
                        <td>
                          {String(row.preferred_supplier || "Sin proveedor")}
                          <small className="table-subtitle">
                            {String(row.supplier_source || "")}
                          </small>
                          {options.length > 0 && (
                            <details>
                              <summary>{options.length} opciones</summary>
                              <div className="supplier-options">
                                {options.map((option, optionIndex) => (
                                  <div
                                    className="supplier-option-row"
                                    key={String(
                                      option.supplier_key || optionIndex,
                                    )}
                                  >
                                    <span>
                                      {String(
                                        option.supplier || "Sin proveedor",
                                      )}{" "}
                                      · {money(option.average_unit_cost)} ·{" "}
                                      {number(option.confidence_pct)}%
                                      frecuencia ·{" "}
                                      {String(
                                        option.last_purchase_date ||
                                          "sin fecha",
                                      )}
                                    </span>
                                    {option.supplier_key && (
                                      <ProductPolicyEditor
                                        row={row}
                                        option={option}
                                      />
                                    )}
                                  </div>
                                ))}
                              </div>
                            </details>
                          )}
                        </td>
                        <td>
                          {number(row.quantity_on_hand)}
                          {row.inventory_exception === "stock_negativo" && (
                            <small className="table-subtitle data-warning">
                              Reconciliar
                            </small>
                          )}
                        </td>
                        <td>
                          {number(row.last_purchase_quantity)}
                          <small className="table-subtitle">
                            {String(
                              row.last_purchase_any_date ||
                                row.last_purchase_date ||
                                "sin fecha",
                            )}
                          </small>
                        </td>
                        <td>
                          {number(row.purchase_lot_reference)}
                          <small className="table-subtitle">
                            {String(
                              row.lot_reference_source || "sin referencia",
                            )}
                          </small>
                        </td>
                        <td>
                          {number(row.purchase_events_365d)}
                          <small className="table-subtitle">
                            {row.purchase_cycle_days == null
                              ? "sin intervalo"
                              : number(row.purchase_cycle_days) + " dias"}
                          </small>
                        </td>
                        <td>
                          {number(row.units_7d)} / {number(row.units_30d)} /{" "}
                          {number(row.units_90d)}
                        </td>
                        <td>
                          {row.coverage_days == null
                            ? "Agotado"
                            : number(row.coverage_days) + " dias"}
                        </td>
                        <td>
                          {number(row.base_recommended_quantity)} /{" "}
                          {number(row.recommended_quantity)}
                          <small className="table-subtitle">
                            Lote:{" "}
                            {number(row.suggested_quantity_by_historical_lot)}
                          </small>
                        </td>
                        <td>
                          {money(
                            row.unit_cost,
                            String(row.currency_code || "COP"),
                          )}
                        </td>
                        <td>
                          {money(
                            Number(row.recommended_quantity || 0) *
                              Number(row.unit_cost || 0),
                            String(row.currency_code || "COP"),
                          )}
                        </td>
                        <td>
                          {String(row.recommendation_confidence || "sin dato")}{" "}
                          ({number(row.confidence_score)}%)
                          <small className="table-subtitle">
                            {String(row.data_quality_flag || "")}
                          </small>
                        </td>
                        <td>
                          {String(row.replenishment_warning || row.reason)}
                        </td>
                        <td>
                          <input
                            className="inline-note"
                            value={notes[key] || ""}
                            placeholder="Nota"
                            onChange={(event) =>
                              setNotes((current) => ({
                                ...current,
                                [key]: event.target.value,
                              }))
                            }
                            onBlur={() => saveAction(row, status)}
                          />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
      <ReplenishmentOpportunityTable
        title="Exceso de cobertura (120 días o más)"
        rows={excessItems}
        coverage
      />
      <ReplenishmentOpportunityTable
        title="Inventario sin demanda en 90 días"
        rows={slowItems}
      />
    </>
  );
}

function ProductPolicyEditor({
  row,
  option,
}: {
  row: RecommendationRow;
  option: SupplierOption;
}) {
  const [minimum, setMinimum] = useState(
    String(option.minimum_order_quantity ?? ""),
  );
  const [pack, setPack] = useState(String(option.pack_size || 1));
  const [lead, setLead] = useState(String(option.lead_time_days ?? 7));
  const [wait, setWait] = useState(String(option.max_wait_days ?? 7));
  const [preferred, setPreferred] = useState(Boolean(option.is_preferred));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function save() {
    if (!option.supplier_key) return;
    setSaving(true);
    setError(null);
    try {
      await api(
        "/analytics/purchase-recommendations/policies/products/" +
          row.product_key,
        {
          method: "PUT",
          body: JSON.stringify({
            supplier_key: option.supplier_key,
            currency_code: String(row.currency_code || "COP"),
            minimum_order_quantity:
              minimum.trim() === "" ? null : Number(minimum),
            pack_size: Number(pack) || 1,
            lead_time_days: Number(lead) || 0,
            max_wait_days: Number(wait) || 0,
            is_preferred: preferred,
            active: true,
          }),
        },
      );
      setError("Guardado");
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "Error al guardar",
      );
    } finally {
      setSaving(false);
    }
  }
  return (
    <div className="product-policy-editor">
      <label>
        MOQ
        <input
          className="policy-input small"
          type="number"
          min="0"
          value={minimum}
          onChange={(event) => setMinimum(event.target.value)}
        />
      </label>
      <label>
        Empaque
        <input
          className="policy-input small"
          type="number"
          min="0.01"
          value={pack}
          onChange={(event) => setPack(event.target.value)}
        />
      </label>
      <label>
        Plazo
        <input
          className="policy-input small"
          type="number"
          min="0"
          max="90"
          value={lead}
          onChange={(event) => setLead(event.target.value)}
        />
      </label>
      <label>
        Espera
        <input
          className="policy-input small"
          type="number"
          min="0"
          max="90"
          value={wait}
          onChange={(event) => setWait(event.target.value)}
        />
      </label>
      <label className="checkbox-label">
        <input
          type="checkbox"
          checked={preferred}
          onChange={(event) => setPreferred(event.target.checked)}
        />{" "}
        Preferido
      </label>
      <button
        className="text-button compact-button"
        disabled={saving}
        onClick={save}
      >
        {saving ? "Guardando" : "Guardar"}
      </button>
      {error && <small className="table-subtitle">{error}</small>}
    </div>
  );
}

function SupplierOrderPlan({
  orders,
}: {
  orders: Array<Record<string, unknown>>;
}) {
  const decisionLabels: Record<string, string> = {
    buy_now: "Comprar ahora",
    complete_order: "Completar pedido",
    accumulate: "Acumular",
    review: "Revisar",
  };
  if (!orders.length) {
    return (
      <section className="table-card">
        <h3>Pedidos por proveedor</h3>
        <p className="muted">No hay canastas de compra para estos filtros.</p>
      </section>
    );
  }
  return (
    <section className="table-card">
      <h3>Pedidos por proveedor</h3>
      <p className="muted">
        La decision combina urgencia, minimo de compra, flete, envio gratis y
        politicas configuradas.
      </p>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Decision</th>
              <th>Proveedor</th>
              <th>Lineas</th>
              <th>Unidades</th>
              <th>Valor</th>
              <th>Minimo</th>
              <th>Falta</th>
              <th>Criticos</th>
              <th>Proxima revision</th>
              <th>Politica</th>
              <th>Detalle</th>
            </tr>
          </thead>
          <tbody>
            {orders.map((order, index) => {
              const lines = (order.product_lines || []) as Array<
                Record<string, unknown>
              >;
              const decision = String(order.decision || "review");
              return (
                <tr key={String(order.supplier_key || index)}>
                  <td>
                    <strong className={"decision-" + decision}>
                      {decisionLabels[decision] || decision}
                    </strong>
                    <small className="table-subtitle">
                      {String(order.decision_reason || "")}
                    </small>
                  </td>
                  <td>{String(order.supplier || "Sin proveedor")}</td>
                  <td>{number(Number(order.lines || 0))}</td>
                  <td>{number(Number(order.units || 0))}</td>
                  <td>
                    {money(
                      Number(order.estimated_value || 0),
                      String(order.currency_code || "COP"),
                    )}
                  </td>
                  <td>
                    {Number(order.minimum_order_amount || 0)
                      ? money(
                          Number(order.minimum_order_amount),
                          String(order.currency_code || "COP"),
                        )
                      : "No definido"}
                  </td>
                  <td>
                    {money(
                      Number(order.amount_to_minimum || 0),
                      String(order.currency_code || "COP"),
                    )}
                  </td>
                  <td>{number(Number(order.critical_lines || 0))}</td>
                  <td>{String(order.next_review_date || "")}</td>
                  <td>
                    {order.policy_configured ? "Configurada" : "Pendiente"}
                  </td>
                  <td>
                    <details>
                      <summary>Ver lineas</summary>
                      <div className="supplier-options">
                        {lines.map((line, lineIndex) => (
                          <div key={String(line.product_key || lineIndex)}>
                            {String(line.name)} ?{" "}
                            {number(Number(line.quantity || 0))} ?{" "}
                            {money(
                              Number(line.estimated_value || 0),
                              String(order.currency_code || "COP"),
                            )}
                          </div>
                        ))}
                      </div>
                    </details>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

type PolicyEditValues = Record<string, string>;

function SupplierPolicyTable({
  policies,
  edits,
  setEdits,
  onSave,
  saving,
}: {
  policies: Row[];
  edits: Record<string, PolicyEditValues>;
  setEdits: (
    updater: (
      current: Record<string, PolicyEditValues>,
    ) => Record<string, PolicyEditValues>,
  ) => void;
  onSave: (row: Row, values: PolicyEditValues) => void;
  saving: string | null;
}) {
  if (!policies.length)
    return (
      <section className="table-card">
        <h3>Politicas de proveedores</h3>
        <p className="muted">
          No hay proveedores con compras historicas para configurar.
        </p>
      </section>
    );
  const field = (row: Row, key: string) => {
    const id = String(row.supplier_key);
    return edits[id]?.[key] ?? String(row[key] ?? "");
  };
  const update = (row: Row, key: string, value: string) => {
    const id = String(row.supplier_key);
    setEdits((current) => ({
      ...current,
      [id]: { ...(current[id] || {}), [key]: value },
    }));
  };
  const values = (row: Row): PolicyEditValues => ({
    minimum_order_amount: field(row, "minimum_order_amount"),
    shipping_cost: field(row, "shipping_cost"),
    free_shipping_threshold: field(row, "free_shipping_threshold"),
    default_lead_time_days: field(row, "default_lead_time_days") || "7",
    max_wait_days: field(row, "max_wait_days") || "7",
    priority: field(row, "priority") || "100",
    notes: field(row, "notes"),
  });
  return (
    <section className="table-card">
      <h3>Politicas de proveedores</h3>
      <p className="muted">
        Configura minimo, transporte y plazo. Esto permite decidir cuando
        consolidar una compra completa.
      </p>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Proveedor</th>
              <th>Productos</th>
              <th>Minimo pedido</th>
              <th>Flete</th>
              <th>Envio gratis desde</th>
              <th>Plazo dias</th>
              <th>Esperar dias</th>
              <th>Accion</th>
            </tr>
          </thead>
          <tbody>
            {policies.map((row) => {
              const id = String(row.supplier_key);
              return (
                <tr key={id}>
                  <td>
                    {String(row.supplier || "Sin proveedor")}
                    <small className="table-subtitle">
                      {row.configured ? "Configurada" : "Sin configurar"}
                    </small>
                  </td>
                  <td>{number(Number(row.products || 0))}</td>
                  <td>
                    <input
                      className="policy-input"
                      type="number"
                      min="0"
                      value={field(row, "minimum_order_amount")}
                      onChange={(event) =>
                        update(row, "minimum_order_amount", event.target.value)
                      }
                      placeholder="0"
                    />
                  </td>
                  <td>
                    <input
                      className="policy-input"
                      type="number"
                      min="0"
                      value={field(row, "shipping_cost")}
                      onChange={(event) =>
                        update(row, "shipping_cost", event.target.value)
                      }
                      placeholder="0"
                    />
                  </td>
                  <td>
                    <input
                      className="policy-input"
                      type="number"
                      min="0"
                      value={field(row, "free_shipping_threshold")}
                      onChange={(event) =>
                        update(
                          row,
                          "free_shipping_threshold",
                          event.target.value,
                        )
                      }
                      placeholder="Opcional"
                    />
                  </td>
                  <td>
                    <input
                      className="policy-input small"
                      type="number"
                      min="0"
                      max="90"
                      value={field(row, "default_lead_time_days") || "7"}
                      onChange={(event) =>
                        update(
                          row,
                          "default_lead_time_days",
                          event.target.value,
                        )
                      }
                    />
                  </td>
                  <td>
                    <input
                      className="policy-input small"
                      type="number"
                      min="0"
                      max="90"
                      value={field(row, "max_wait_days") || "7"}
                      onChange={(event) =>
                        update(row, "max_wait_days", event.target.value)
                      }
                    />
                  </td>
                  <td>
                    <button
                      className="primary-button compact-button"
                      disabled={saving === id}
                      onClick={() => onSave(row, values(row))}
                    >
                      {saving === id ? "Guardando" : "Guardar"}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function ReplenishmentOpportunityTable({
  title,
  rows,
  coverage = false,
}: {
  title: string;
  rows: RecommendationRow[];
  coverage?: boolean;
}) {
  if (!rows.length)
    return (
      <section className="table-card">
        <h3>{title}</h3>
        <p className="muted">Sin productos para estos criterios.</p>
      </section>
    );
  return (
    <section className="table-card">
      <h3>{title}</h3>
      <table>
        <thead>
          <tr>
            <th>Producto</th>
            <th>Familia</th>
            <th>Stock</th>
            <th>Venta 90 días</th>
            {coverage && <th>Cobertura</th>}
            <th>Valor inventario</th>
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 100).map((row, index) => (
            <tr key={String(row.product_key || index)}>
              <td>
                {String(row.name)}
                {row.reference ? " · " + String(row.reference) : ""}
              </td>
              <td>{String(row.family)}</td>
              <td>{number(row.quantity_on_hand)}</td>
              <td>{number(row.units_90d)}</td>
              {coverage && <td>{number(row.coverage_days)} días</td>}
              <td>{money(row.inventory_value)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function SalesReports({ data }: { data: Record<string, unknown> }) {
  const summary = (data.summary || []) as Row[];
  const timeCoverage = (data.time_coverage || []) as Row[];
  const weekdays = (data.by_weekday || []) as Row[];
  const weekdayHours = (data.by_weekday_hour || []) as Row[];
  const families = (data.by_family_detail || []) as Row[];
  const products = (data.product_detail || []) as Row[];
  const sellers = (data.seller_detail || []) as Row[];
  const customers = (data.customer_detail || []) as Row[];
  const statuses = (data.status_detail || []) as Row[];
  const catalogSuppliers = (data.catalog_supplier_detail || []) as Row[];
  const modalSuppliers = (data.modal_supplier_detail || []) as Row[];
  const costSuppliers = (data.cost_supplier_detail || []) as Row[];
  const familyChart = families.map((row) => ({ ...row, label: row.family }));
  const supplierChart = catalogSuppliers.map((row) => ({
    ...row,
    label: row.supplier,
  }));
  const modalSupplierChart = modalSuppliers.map((row) => ({
    ...row,
    label: row.supplier,
  }));
  return (
    <>
      <h2>Ventas</h2>
      <p className="muted">
        La venta neta incluye las notas crédito como valores negativos. El
        margen usa únicamente líneas con costo histórico disponible.
      </p>
      <section className="cards">
        {summary.map((row) => {
          const currency = String(row.currency_code || "COP");
          return (
            <article className="metric-card" key={currency}>
              <p>{currency} · Venta neta</p>
              <strong>{money(row.net_sales, currency)}</strong>
              <small>
                {number(row.documents)} documentos · {number(row.units)}{" "}
                unidades
              </small>
              <dl>
                <div>
                  <dt>Ticket promedio</dt>
                  <dd>{money(row.average_ticket, currency)}</dd>
                </div>
                <div>
                  <dt>Ventas facturadas</dt>
                  <dd>{money(row.invoice_sales, currency)}</dd>
                </div>
                <div>
                  <dt>Notas crédito</dt>
                  <dd>{money(row.credit_note_amount, currency)}</dd>
                </div>
                <div>
                  <dt>Margen bruto</dt>
                  <dd>{money(row.gross_margin, currency)}</dd>
                </div>
                <div>
                  <dt>Margen %</dt>
                  <dd>{percent(row.gross_margin_pct)}</dd>
                </div>
                <div>
                  <dt>Costo cubierto</dt>
                  <dd>{percent(row.cost_coverage_pct)}</dd>
                </div>
              </dl>
            </article>
          );
        })}
      </section>
      <Chart
        title="Ventas en el tiempo"
        data={(data.series || []) as Row[]}
        dataKey="amount"
        moneyValue
      />
      <Chart
        title="Ventas por hora comercial (10:00–20:00, America/Bogota)"
        data={(data.by_hour || []) as Row[]}
        dataKey="amount"
        moneyValue
      />
      <Chart
        title="Ventas por día de la semana"
        data={weekdays.map((row) => ({ ...row, label: row.weekday }))}
        dataKey="amount"
        moneyValue
      />
      <section className="table-card">
        <h3>Ventas por día y hora comercial</h3>
        {weekdayHours.length ? (
          <table>
            <thead>
              <tr>
                <th>Día</th>
                <th>Hora</th>
                <th>Venta neta</th>
                <th>Unidades</th>
                <th>Documentos</th>
              </tr>
            </thead>
            <tbody>
              {weekdayHours.map((row, index) => (
                <tr
                  key={`${row.weekday}-${row.period}-${row.currency_code}-${index}`}
                >
                  <td>{String(row.weekday)}</td>
                  <td>{String(row.period)}</td>
                  <td>
                    {money(row.amount, String(row.currency_code || "COP"))}
                  </td>
                  <td>{number(row.units)}</td>
                  <td>{number(row.documents)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="muted">Sin ventas con hora para estos filtros.</p>
        )}
      </section>
      {timeCoverage.map((row) => (
        <p className="muted" key={`time-${String(row.currency_code || "COP")}`}>
          Hora disponible para {number(row.documents_with_time)} de{" "}
          {number(row.documents)} documentos (
          {percent(
            row.documents
              ? (Number(row.documents_with_time || 0) / Number(row.documents)) *
                  100
              : 0,
          )}
          ). Fuera del horario comercial:{" "}
          {number(row.documents_outside_business_hours)} documentos por{" "}
          {money(
            row.amount_outside_business_hours,
            String(row.currency_code || "COP"),
          )}
          . Las notas crédito u otros documentos sin hora se mantienen en sus
          totales diarios.
        </p>
      ))}
      <Chart
        title="Ventas por familia"
        data={familyChart}
        dataKey="net_sales"
        moneyValue
      />
      <section className="table-card">
        <h3>Productos más vendidos</h3>
        {products.length ? (
          <table>
            <thead>
              <tr>
                <th>Producto</th>
                <th>Familia</th>
                <th>Venta neta</th>
                <th>Participación</th>
                <th>Unidades</th>
                <th>Documentos</th>
                <th>Precio promedio</th>
                <th>Margen %</th>
                <th>Última venta</th>
              </tr>
            </thead>
            <tbody>
              {products.slice(0, 100).map((row, index) => (
                <tr key={`${row.product}-${index}`}>
                  <td>
                    {String(row.product)}
                    {row.reference ? ` · ${String(row.reference)}` : ""}
                  </td>
                  <td>{String(row.family)}</td>
                  <td>
                    {money(row.net_sales, String(row.currency_code || "COP"))}
                  </td>
                  <td>{percent(row.share_pct)}</td>
                  <td>{number(row.units)}</td>
                  <td>{number(row.documents)}</td>
                  <td>
                    {money(
                      row.average_unit_sale,
                      String(row.currency_code || "COP"),
                    )}
                  </td>
                  <td>{percent(row.gross_margin_pct)}</td>
                  <td>{String(row.last_sale_date || "")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="muted">Sin ventas para estos filtros.</p>
        )}
      </section>
      <section className="table-card">
        <h3>Ventas por familia</h3>
        {families.length ? (
          <table>
            <thead>
              <tr>
                <th>Familia</th>
                <th>Venta neta</th>
                <th>Participación</th>
                <th>Unidades</th>
                <th>Documentos</th>
                <th>Productos</th>
                <th>Margen %</th>
              </tr>
            </thead>
            <tbody>
              {families.map((row, index) => (
                <tr key={`${row.family}-${index}`}>
                  <td>{String(row.family)}</td>
                  <td>
                    {money(row.net_sales, String(row.currency_code || "COP"))}
                  </td>
                  <td>{percent(row.share_pct)}</td>
                  <td>{number(row.units)}</td>
                  <td>{number(row.documents)}</td>
                  <td>{number(row.product_count)}</td>
                  <td>{percent(row.gross_margin_pct)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="muted">Sin familias para estos filtros.</p>
        )}
      </section>
      <Chart
        title="Ventas por proveedor asociado al producto"
        data={supplierChart}
        dataKey="net_sales"
        moneyValue
      />
      <section className="table-card">
        <h3>Ventas por proveedor asociado al producto</h3>
        <p className="muted">
          Atribución de catálogo: corresponde al proveedor actual guardado en el
          producto de Alegra; no significa que cada unidad histórica se haya
          comprado a ese proveedor.
        </p>
        {catalogSuppliers.length ? (
          <table>
            <thead>
              <tr>
                <th>Proveedor</th>
                <th>Venta neta</th>
                <th>Participación</th>
                <th>Unidades</th>
                <th>Documentos</th>
                <th>Productos</th>
                <th>Margen %</th>
                <th>Cobertura costo</th>
              </tr>
            </thead>
            <tbody>
              {catalogSuppliers.map((row, index) => (
                <tr key={String(row.supplier) + "-" + index}>
                  <td>{String(row.supplier)}</td>
                  <td>
                    {money(row.net_sales, String(row.currency_code || "COP"))}
                  </td>
                  <td>{percent(row.share_pct)}</td>
                  <td>{number(row.units)}</td>
                  <td>{number(row.documents)}</td>
                  <td>{number(row.product_count)}</td>
                  <td>{percent(row.gross_margin_pct)}</td>
                  <td>{percent(row.cost_coverage_pct)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="muted">Sin proveedor asociado para estos filtros.</p>
        )}
      </section>
      <Chart
        title="Ventas por proveedor modal histórico"
        data={modalSupplierChart}
        dataKey="net_sales"
        moneyValue
      />
      <section className="table-card">
        <h3>Ventas por proveedor modal histórico</h3>
        <p className="muted">
          Cada producto se asigna al proveedor que más aparece en sus compras
          históricas. La moda se calcula por frecuencia de líneas de compra; los
          empates se resuelven por unidades, valor comprado y fecha más
          reciente. La confianza es el peso de esa moda dentro de las compras
          del producto.
        </p>
        {modalSuppliers.length ? (
          <table>
            <thead>
              <tr>
                <th>Proveedor modal</th>
                <th>Venta neta</th>
                <th>Participación</th>
                <th>Unidades</th>
                <th>Documentos</th>
                <th>Productos</th>
                <th>Confianza modal</th>
                <th>Margen %</th>
              </tr>
            </thead>
            <tbody>
              {modalSuppliers.map((row, index) => (
                <tr key={String(row.supplier) + "-" + index}>
                  <td>{String(row.supplier)}</td>
                  <td>
                    {money(row.net_sales, String(row.currency_code || "COP"))}
                  </td>
                  <td>{percent(row.share_pct)}</td>
                  <td>{number(row.units)}</td>
                  <td>{number(row.documents)}</td>
                  <td>{number(row.product_count)}</td>
                  <td>{percent(row.supplier_confidence_pct)}</td>
                  <td>{percent(row.gross_margin_pct)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="muted">
            Sin proveedor modal histórico para estos filtros.
          </p>
        )}
      </section>
      <section className="table-card">
        <h3>Margen por proveedor real (FIFO)</h3>
        <p className="muted">
          Atribución FIFO: relaciona el costo de cada venta con las capas de
          compra reales. Las existencias de apertura, devoluciones sin compra
          trazable y costos sin coincidencia aparecen como “Sin proveedor/costo
          de apertura”.
        </p>
        {costSuppliers.length ? (
          <table>
            <thead>
              <tr>
                <th>Proveedor</th>
                <th>Ventas atribuidas</th>
                <th>COGS</th>
                <th>Margen</th>
                <th>Margen %</th>
                <th>Participación</th>
                <th>Unidades asignadas</th>
                <th>Documentos</th>
              </tr>
            </thead>
            <tbody>
              {costSuppliers.map((row, index) => (
                <tr key={String(row.supplier) + "-" + index}>
                  <td>{String(row.supplier)}</td>
                  <td>
                    {money(
                      row.attributed_net_sales,
                      String(row.currency_code || "COP"),
                    )}
                  </td>
                  <td>{money(row.cogs, String(row.currency_code || "COP"))}</td>
                  <td>
                    {money(
                      row.gross_margin,
                      String(row.currency_code || "COP"),
                    )}
                  </td>
                  <td>{percent(row.gross_margin_pct)}</td>
                  <td>{percent(row.share_pct)}</td>
                  <td>{number(row.allocated_units)}</td>
                  <td>{number(row.documents)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="muted">
            No hay asignaciones FIFO disponibles para estos filtros.
          </p>
        )}
      </section>
      <section className="table-card">
        <h3>Rendimiento por vendedor</h3>
        {sellers.length ? (
          <table>
            <thead>
              <tr>
                <th>Vendedor</th>
                <th>Venta neta</th>
                <th>Participación</th>
                <th>Unidades</th>
                <th>Documentos</th>
                <th>Margen %</th>
              </tr>
            </thead>
            <tbody>
              {sellers.map((row, index) => (
                <tr key={`${row.seller}-${index}`}>
                  <td>{String(row.seller)}</td>
                  <td>
                    {money(row.net_sales, String(row.currency_code || "COP"))}
                  </td>
                  <td>{percent(row.share_pct)}</td>
                  <td>{number(row.units)}</td>
                  <td>{number(row.documents)}</td>
                  <td>{percent(row.gross_margin_pct)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="muted">Sin vendedores para estos filtros.</p>
        )}
      </section>
      <section className="table-card">
        <h3>Clientes principales</h3>
        {customers.length ? (
          <table>
            <thead>
              <tr>
                <th>Cliente</th>
                <th>Venta neta</th>
                <th>Participación</th>
                <th>Unidades</th>
                <th>Documentos</th>
                <th>Última venta</th>
              </tr>
            </thead>
            <tbody>
              {customers.slice(0, 100).map((row, index) => (
                <tr key={`${row.customer}-${index}`}>
                  <td>{String(row.customer)}</td>
                  <td>
                    {money(row.net_sales, String(row.currency_code || "COP"))}
                  </td>
                  <td>{percent(row.share_pct)}</td>
                  <td>{number(row.units)}</td>
                  <td>{number(row.documents)}</td>
                  <td>{String(row.last_sale_date || "")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="muted">Sin clientes para estos filtros.</p>
        )}
      </section>
      <section className="table-card">
        <h3>Desglose por estado del documento</h3>
        {statuses.length ? (
          <table>
            <thead>
              <tr>
                <th>Estado</th>
                <th>Venta neta</th>
                <th>Participación</th>
                <th>Unidades</th>
                <th>Documentos</th>
                <th>Margen %</th>
              </tr>
            </thead>
            <tbody>
              {statuses.map((row, index) => (
                <tr key={`${row.status}-${index}`}>
                  <td>{String(row.status)}</td>
                  <td>
                    {money(row.net_sales, String(row.currency_code || "COP"))}
                  </td>
                  <td>{percent(row.share_pct)}</td>
                  <td>{number(row.units)}</td>
                  <td>{number(row.documents)}</td>
                  <td>{percent(row.gross_margin_pct)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="muted">Sin estados para estos filtros.</p>
        )}
      </section>
    </>
  );
}

function SupplierReports({ data }: { data: Record<string, unknown> }) {
  const summary = (data.summary || []) as Row[];
  const suppliers = (data.by_supplier || []) as Row[];
  const families = (data.by_family || []) as Row[];
  const products = (data.by_product_supplier || []) as Row[];
  const variations = (data.price_variations || []) as Row[];
  const familyChart = families.map((row) => ({ ...row, label: row.family }));
  return (
    <>
      <h2>Proveedores</h2>
      <p className="muted">
        El proveedor se toma de la factura de compra. La familia se toma del
        campo FAMILIA del producto.
      </p>
      <section className="cards">
        {summary.map((row) => {
          const currency = String(row.currency_code || "COP");
          return (
            <article className="metric-card" key={currency}>
              <p>{currency} · Compra acumulada</p>
              <strong>{money(row.purchase_amount, currency)}</strong>
              <small>
                {number(row.suppliers)} proveedores · {number(row.documents)}{" "}
                documentos
              </small>
              <dl>
                <div>
                  <dt>Unidades</dt>
                  <dd>{number(row.units)}</dd>
                </div>
                <div>
                  <dt>Ticket promedio</dt>
                  <dd>{money(row.average_purchase_ticket, currency)}</dd>
                </div>
                <div>
                  <dt>Costo unitario</dt>
                  <dd>{money(row.average_unit_cost, currency)}</dd>
                </div>
              </dl>
            </article>
          );
        })}
      </section>
      <Chart
        title="Compras en el tiempo"
        data={(data.series || []) as Row[]}
        dataKey="amount"
        moneyValue
      />
      <Chart
        title="Compra por familia"
        data={familyChart}
        dataKey="purchase_amount"
        moneyValue
      />
      <section className="table-card">
        <h3>Ranking de proveedores</h3>
        {suppliers.length ? (
          <table>
            <thead>
              <tr>
                <th>Proveedor</th>
                <th>Compra</th>
                <th>Participación</th>
                <th>Unidades</th>
                <th>Documentos</th>
                <th>SKUs</th>
                <th>Última compra</th>
              </tr>
            </thead>
            <tbody>
              {suppliers.map((row, index) => (
                <tr key={`${row.supplier}-${index}`}>
                  <td>{String(row.supplier)}</td>
                  <td>
                    {money(
                      row.purchase_amount,
                      String(row.currency_code || "COP"),
                    )}
                  </td>
                  <td>{percent(row.share_pct)}</td>
                  <td>{number(row.units)}</td>
                  <td>{number(row.documents)}</td>
                  <td>{number(row.skus)}</td>
                  <td>{String(row.last_purchase_date || "")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="muted">Sin compras para estos filtros.</p>
        )}
      </section>
      <section className="table-card">
        <h3>Compras por familia</h3>
        {families.length ? (
          <table>
            <thead>
              <tr>
                <th>Familia</th>
                <th>Compra</th>
                <th>Unidades</th>
                <th>Proveedores</th>
                <th>SKUs</th>
              </tr>
            </thead>
            <tbody>
              {families.map((row, index) => (
                <tr key={`${row.family}-${index}`}>
                  <td>{String(row.family)}</td>
                  <td>
                    {money(
                      row.purchase_amount,
                      String(row.currency_code || "COP"),
                    )}
                  </td>
                  <td>{number(row.units)}</td>
                  <td>{number(row.suppliers)}</td>
                  <td>{number(row.skus)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="muted">Sin familias para estos filtros.</p>
        )}
      </section>
      <section className="table-card">
        <h3>Matriz producto–proveedor</h3>
        {products.length ? (
          <table>
            <thead>
              <tr>
                <th>Producto</th>
                <th>Familia</th>
                <th>Proveedor</th>
                <th>Compra</th>
                <th>Unidades</th>
                <th>Costo promedio</th>
                <th>Última compra</th>
              </tr>
            </thead>
            <tbody>
              {products.slice(0, 100).map((row, index) => (
                <tr key={`${row.product}-${row.supplier}-${index}`}>
                  <td>{String(row.product)}</td>
                  <td>{String(row.family)}</td>
                  <td>{String(row.supplier)}</td>
                  <td>
                    {money(
                      row.purchase_amount,
                      String(row.currency_code || "COP"),
                    )}
                  </td>
                  <td>{number(row.units)}</td>
                  <td>
                    {money(
                      row.average_unit_cost,
                      String(row.currency_code || "COP"),
                    )}
                  </td>
                  <td>{String(row.last_purchase_date || "")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="muted">Sin detalle producto–proveedor.</p>
        )}
      </section>
      <section className="table-card">
        <h3>Variación de costos</h3>
        <p className="muted">
          Productos con al menos dos compras y diferencia entre costo mínimo y
          máximo.
        </p>
        {variations.length ? (
          <table>
            <thead>
              <tr>
                <th>Producto</th>
                <th>Familia</th>
                <th>Proveedor</th>
                <th>Costo mínimo</th>
                <th>Costo máximo</th>
                <th>Variación</th>
                <th>Compras</th>
              </tr>
            </thead>
            <tbody>
              {variations.map((row, index) => (
                <tr key={`${row.product}-${row.supplier}-${index}`}>
                  <td>{String(row.product)}</td>
                  <td>{String(row.family)}</td>
                  <td>{String(row.supplier)}</td>
                  <td>
                    {money(
                      row.minimum_cost,
                      String(row.currency_code || "COP"),
                    )}
                  </td>
                  <td>
                    {money(
                      row.maximum_cost,
                      String(row.currency_code || "COP"),
                    )}
                  </td>
                  <td>{percent(row.cost_range_pct)}</td>
                  <td>{number(row.purchase_lines)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="muted">
            No hay variaciones suficientes para estos filtros.
          </p>
        )}
      </section>
    </>
  );
}

function DomainView({
  title,
  data,
  amountKey,
}: {
  title: string;
  data: Record<string, unknown>;
  amountKey: string;
}) {
  const summary = (data.summary || []) as Row[];
  const series = (data.series || []) as Row[];
  const sections = Object.entries(data).filter(
    ([key]) => !["summary", "series"].includes(key),
  );
  return (
    <>
      {title === "Ventas" && <CostMetrics rows={summary} />}
      <h2>{title}</h2>
      <section className="cards">
        {summary.map((row) => (
          <article
            className="metric-card compact"
            key={String(row.currency_code || row.label)}
          >
            <p>{String(row.currency_code || row.label || "Total")}</p>
            <strong>
              {money(
                row.amount ?? row.purchase_amount ?? row.net_sales,
                String(row.currency_code || "COP"),
              )}
            </strong>
            <small>
              {number(row.documents ?? row.payments ?? row.quantity)} registros
            </small>
          </article>
        ))}
      </section>
      <Chart
        title={`${title} en el tiempo`}
        data={series}
        dataKey={amountKey}
        moneyValue
      />
      {sections.map(([key, value]) =>
        Array.isArray(value) ? (
          <Chart
            key={key}
            title={labelFor(key)}
            data={value as Row[]}
            dataKey={key === "stock_coverage" ? "coverage_days" : amountKey}
            moneyValue={key !== "stock_coverage"}
          />
        ) : null,
      )}
    </>
  );
}

function Inventory({ data }: { data: Record<string, unknown> }) {
  const snapshot = (data.snapshot || {}) as Record<string, unknown>;
  const stockSummary = (snapshot.summary || []) as Row[];
  const stockItems = (snapshot.items || []) as Row[];
  const summary = (data.summary || []) as Row[];
  const recent = (data.recent || []) as Row[];
  return (
    <>
      <h2>Inventario</h2>
      <p className="muted">
        Existencias actuales por producto y bodega. Última captura:{" "}
        {String(snapshot.captured_at || "pendiente")}
      </p>
      {stockSummary.length ? (
        <>
          <section className="cards">
            {stockSummary.map((row) => (
              <article className="metric-card" key="stock">
                <p>Existencias actuales</p>
                <strong>{number(row.units)} unidades</strong>
                <small>
                  {number(row.products)} referencias · valor a costo:{" "}
                  {money(row.inventory_value)}
                </small>
              </article>
            ))}
          </section>
          <Chart
            title="Existencias por producto"
            data={(snapshot.by_product || []) as Row[]}
            dataKey="quantity"
          />
          <Chart
            title="Existencias por bodega"
            data={(snapshot.by_warehouse || []) as Row[]}
            dataKey="quantity"
          />
          <section className="table-card">
            <h3>Stock actual</h3>
            <table>
              <thead>
                <tr>
                  <th>Producto</th>
                  <th>Bodega</th>
                  <th>Unidades</th>
                  <th>Costo unitario</th>
                  <th>Valor</th>
                </tr>
              </thead>
              <tbody>
                {stockItems.map((row, index) => (
                  <tr key={`${row.product}-${row.warehouse}-${index}`}>
                    <td>{String(row.product)}</td>
                    <td>{String(row.warehouse)}</td>
                    <td>{number(row.quantity_on_hand)}</td>
                    <td>{money(row.unit_cost)}</td>
                    <td>{money(row.inventory_value)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </>
      ) : (
        <div className="warning">
          Aún no existe un snapshot de inventario. Ejecuta la captura de
          inventario y luego refresca el mart.
        </div>
      )}
      <h3 className="section-title">Movimientos de inventario</h3>
      <p className="muted">
        Ajustes manuales y transferencias; no equivalen al stock disponible.
      </p>
      <section className="cards">
        {summary.map((row) => (
          <article className="metric-card compact" key={String(row.label)}>
            <p>{labelFor(String(row.label))}</p>
            <strong>{number(row.quantity)}</strong>
            <small>unidades netas</small>
          </article>
        ))}
      </section>
      <Chart
        title="Movimientos por producto"
        data={(data.by_product || []) as Row[]}
        dataKey="quantity"
      />
      <Chart
        title="Movimientos por bodega"
        data={(data.by_warehouse || []) as Row[]}
        dataKey="quantity"
      />
      <section className="table-card">
        <h3>Últimos movimientos</h3>
        <table>
          <thead>
            <tr>
              <th>Fecha</th>
              <th>Producto</th>
              <th>Bodega</th>
              <th>Tipo</th>
              <th>Cantidad</th>
            </tr>
          </thead>
          <tbody>
            {recent.map((row, index) => (
              <tr key={`${row.document_number}-${index}`}>
                <td>{String(row.date || "")}</td>
                <td>{String(row.product)}</td>
                <td>{String(row.warehouse)}</td>
                <td>{labelFor(String(row.movement_direction))}</td>
                <td>{number(row.quantity_delta)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}

function Alerts({ data }: { data: Record<string, unknown> }) {
  const summary = (data.summary || []) as Row[];
  return (
    <>
      <h2>Alertas operativas</h2>
      <p className="muted">
        PriorizaciÃ³n basada en el Ãºltimo snapshot y ventas de los Ãºltimos 90
        dÃ­as.
      </p>
      <section className="cards">
        {summary.map((row) => (
          <article className="metric-card" key={String(row.label)}>
            <p>{String(row.label)}</p>
            <strong>{number(row.count)}</strong>
            <small>productos que requieren revisiÃ³n</small>
          </article>
        ))}
      </section>
      <Chart
        title="Agotados con venta reciente"
        data={(data.stockouts || []) as Row[]}
        dataKey="quantity"
      />
      <Chart
        title="Inventario negativo"
        data={(data.negative_stock || []) as Row[]}
        dataKey="quantity"
      />
      <Chart
        title="Stock sin ventas en 90 dÃ­as"
        data={(data.slow_stock || []) as Row[]}
        dataKey="quantity"
      />
    </>
  );
}

function labelFor(value: string): string {
  return (
    {
      by_product: "Por producto",
      best_sellers: "MÃ¡s vendidos",
      stock_coverage: "Cobertura de stock",
      recent_customers: "Clientes recientes",
      by_supplier: "Por proveedor",
      by_family: "Por familia",
      by_type: "Por tipo",
      by_contact: "Por contacto",
      by_seller: "Por vendedor",
      by_warehouse: "Por bodega",
      by_customer: "Por cliente",
      by_status: "Por estado",
      adjustment: "Ajustes",
      transfer_in: "Entradas por transferencia",
      transfer_out: "Salidas por transferencia",
    }[value] || value
  ).replaceAll("_", " ");
}
