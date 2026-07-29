export type Option = { value: string | number; label?: string; reference?: string };
export type Filters = {
  from_date: string;
  to_date: string;
  currency?: string;
  product_key?: string;
  seller_key?: string;
  warehouse_key?: string;
  document_status?: string;
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
    throw new Error(payload.detail || "No fue posible cargar los datos.");
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function query(filters: Filters): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value) params.set(key, value);
  }
  const value = params.toString();
  return value ? `?${value}` : "";
}
