export type Option = {
  value: string | number;
  label?: string;
  reference?: string;
};
export type Filters = {
  from_date: string;
  to_date: string;
  currency?: string;
  product_key?: string;
  seller_key?: string;
  warehouse_key?: string;
  document_status?: string;
  family?: string;
  provider_key?: string;
  metric_scope?: "audit" | "commercial";
};

const apiBase = "/api/v1";

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBase}${path}`, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    const detail =
      typeof payload.detail === "string"
        ? payload.detail
        : Array.isArray(payload.detail)
          ? payload.detail
              .map(
                (item: { loc?: string[]; msg?: string }) =>
                  `${item.loc?.join(".")}: ${item.msg}`,
              )
              .join(" · ")
          : "No fue posible cargar los datos.";
    throw new Error(detail);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function query(
  filters: Filters,
  extras: Record<string, string | number | undefined> = {},
): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries({ ...filters, ...extras })) {
    if (value !== undefined && value !== null && value !== "")
      params.set(key, String(value));
  }
  const value = params.toString();
  return value ? `?${value}` : "";
}
