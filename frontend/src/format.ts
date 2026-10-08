export function money(value: number | string | null | undefined, currency?: string | null): string {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  const amount = Number(value ?? 0);
  return new Intl.NumberFormat("es-CO", {
    style: "currency",
    currency: currency || "COP",
    maximumFractionDigits: 0,
  }).format(amount);
}

export function number(value: number | string | null | undefined): string {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  return new Intl.NumberFormat("es-CO", { maximumFractionDigits: 2 }).format(Number(value ?? 0));
}

export function percent(value: number | string | null | undefined): string {
  return value == null || !Number.isFinite(Number(value)) ? "—" : `${number(value)}%`;
}
