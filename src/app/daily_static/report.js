"use strict";

const element = (id) => document.getElementById(id);
const normalize = (value) =>
  String(value ?? "").normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase();
const dateTime = new Intl.DateTimeFormat("es-CO", {
  timeZone: "America/Bogota",
  dateStyle: "medium",
  timeStyle: "short",
});
const number = new Intl.NumberFormat("es-CO", { maximumFractionDigits: 4 });

function node(tag, text, className) {
  const result = document.createElement(tag);
  if (text != null) result.textContent = text;
  if (className) result.className = className;
  return result;
}

function supplierCard(supplier, products, money) {
  const details = node("details");
  const summary = node("summary");
  const count = products.length;
  const countLabel = count === 1 ? "1 producto" : `${count} productos`;
  summary.append(
    node("strong", supplier.supplier),
    node("span", `${countLabel} · Pedido completo ${money(supplier.estimated_total)}`),
  );
  details.append(summary);
  if (Number(supplier.amount_to_minimum) > 0) {
    details.append(
      node("p", `Faltan ${money(supplier.amount_to_minimum)} para el mínimo del proveedor.`,
        "order-note"),
    );
  }
  const table = node("table");
  const head = table.createTHead().insertRow();
  for (const label of ["Producto", "Stock", "En tránsito", "Comprar", "Costo unit.", "Total"]) {
    const heading = node("th", label);
    heading.scope = "col";
    head.append(heading);
  }
  const rows = table.createTBody();
  for (const product of products) {
    const row = rows.insertRow();
    const name = node("td");
    name.append(node("span", product.name));
    const label = [product.reference, product.family].filter(Boolean).join(" · ");
    if (label) name.append(node("span", label, "reference"));
    row.append(name);
    for (const quantity of [product.quantity_on_hand, product.quantity_in_transit]) {
      row.append(node("td", number.format(Number(quantity)), "number"));
    }
    const suggested = number.format(Number(product.recommended_quantity));
    row.append(node("td", product.priority === "critical" ? `${suggested} · Crítico` : suggested,
      product.priority === "critical" ? "number critical" : "number"));
    row.append(node("td", money(product.unit_cost), "number"),
      node("td", money(product.estimated_value), "number"));
  }
  const scroll = node("div", null, "table-wrap");
  scroll.append(table);
  details.append(scroll);
  return details;
}

function downloadRows(groups, report) {
  // Quote text and neutralize values that spreadsheets interpret as formulas.
  const textCell = (value) => {
    let text = String(value ?? "");
    if (/^\s*[=+@-]/.test(text)) text = "'" + text;
    return '"' + text.replaceAll('"', '""') + '"';
  };
  const numericCell = (value) => Number.isFinite(Number(value))
    ? String(value).replace(".", ",")
    : "";
  const rows = [[
    "Fecha de corte", "Moneda", "Proveedor", "Referencia", "Producto", "Familia",
    "Stock", "En tránsito", "Comprar", "Costo unitario", "Subtotal",
  ].map(textCell).join(";")];
  for (const { supplier, products } of groups) {
    for (const p of products) {
      rows.push([
        report.as_of_date, report.currency_code, supplier.supplier, p.reference, p.name, p.family,
      ].map(textCell).concat([
        p.quantity_on_hand, p.quantity_in_transit, p.recommended_quantity, p.unit_cost,
        p.estimated_value,
      ].map(numericCell)).join(";"));
    }
  }
  const url = URL.createObjectURL(new Blob(["\ufeff", rows.join("\r\n")], {
    type: "text/csv;charset=utf-8",
  }));
  const link = node("a");
  link.href = url;
  link.download = `reponer-${report.as_of_date}.csv`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function loadReport() {
  const response = await fetch("/report.json", { cache: "no-store" });
  if (response.status === 404) {
    element("cutoff").textContent = "El primer informe se mostrará cuando termine la actualización.";
    return;
  }
  if (!response.ok) throw new Error("No se pudo cargar el último informe.");
  const report = await response.json();
  const moneyFormatter = new Intl.NumberFormat("es-CO", {
    style: "currency",
    currency: report.currency_code,
  });
  const money = (value) => moneyFormatter.format(Number(value));
  element("cutoff").textContent = `Inventario del ${dateTime.format(new Date(report.snapshot_at))} (Colombia)`;
  if (Date.now() - Date.parse(report.generated_at) > 26 * 60 * 60 * 1000) {
    element("warning").textContent = "Actualización pendiente. Se muestra el último corte válido.";
    element("warning").hidden = false;
  }
  element("total").textContent = money(report.recommended_value);
  element("budget").textContent = money(report.weekly_budget);
  element("suppliers").textContent = number.format(report.suppliers.length);
  let groups = [];
  function render() {
    const search = normalize(element("search").value.trim());
    const critical = element("critical").checked;
    groups = report.suppliers.map((supplier) => ({
      supplier,
      products: supplier.products.filter((product) =>
        (!critical || product.priority === "critical") &&
        normalize([supplier.supplier, product.name, product.reference, product.family].join(" "))
          .includes(search),
      ),
    })).filter(({ products }) => products.length);
    const visible = groups.reduce((sum, group) => sum + group.products.length, 0);
    element("matches").textContent = visible
      ? `${number.format(visible)} de ${number.format(report.recommended_products)} productos sugeridos`
      : "No hay productos para esta vista.";
    element("orders").replaceChildren(
      ...groups.map(({ supplier, products }) => supplierCard(supplier, products, money)),
    );
    element("download").disabled = visible === 0;
  }
  element("search").addEventListener("input", render);
  element("critical").addEventListener("change", render);
  element("download").addEventListener("click", () => downloadRows(groups, report));
  element("report").hidden = false;
  render();
}

loadReport().catch(() => {
  element("cutoff").textContent = "No se pudo cargar el informe. Recarga la página para reintentar.";
});
