export function money(value: number | string | null | undefined, currency?: string | null): string {
  const amount = Number(value ?? 0);
  return new Intl.NumberFormat("es-CO", {
    style: "currency",
    currency: currency || "COP",
    maximumFractionDigits: 0,
  }).format(amount);
}

export function number(value: number | string | null | undefined): string {
  return new Intl.NumberFormat("es-CO", { maximumFractionDigits: 2 }).format(Number(value ?? 0));
}
