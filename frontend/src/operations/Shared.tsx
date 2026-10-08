import {
  FormEvent,
  ReactNode,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { api } from "../api";
import { number } from "../format";

export type Row = Record<string, unknown>;
export const rows = (value: unknown): Row[] =>
  Array.isArray(value) ? (value as Row[]) : [];
export const row = (value: unknown): Row =>
  value && typeof value === "object" ? (value as Row) : {};
export const str = (value: unknown): string =>
  value === null || value === undefined ? "—" : String(value);
export const num = (value: unknown): number | null =>
  value === null || value === undefined ? null : Number(value);

export function useResource<T>(path: string) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const previousPath = useRef(path);
  const refresh = useCallback(() => setRevision((value) => value + 1), []);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    if (previousPath.current !== path) setData(null);
    previousPath.current = path;
    api<T>(path, { signal: controller.signal })
      .then((value) => {
        if (!controller.signal.aborted) setData(value);
      })
      .catch((e: Error) => {
        if (!controller.signal.aborted) setError(e.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [path, revision]);
  return { data, error, loading, refresh };
}

export function useMutation() {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  async function run(work: () => Promise<unknown>, success?: () => void) {
    if (busy) return;
    setBusy(true);
    setMessage("");
    try {
      await work();
      setMessage("Guardado correctamente.");
      success?.();
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "No se pudo guardar.");
    } finally {
      setBusy(false);
    }
  }
  return { busy, message, run };
}

export function State({ loading, error }: { loading: boolean; error: string }) {
  return error ? (
    <p role="alert" className="error">
      {error}
    </p>
  ) : loading ? (
    <p role="status">Cargando…</p>
  ) : null;
}

export function Table({
  title,
  items,
  columns,
}: {
  title: string;
  items: Row[];
  columns: Array<[string, string, ((r: Row) => ReactNode)?]>;
}) {
  return (
    <section className="table-card">
      <h3>{title}</h3>
      {!items.length ? (
        <p className="muted">Sin registros para este criterio.</p>
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                {columns.map(([key, label]) => (
                  <th key={key}>{label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {items.map((r, i) => (
                <tr key={str(r.id || r.key || r.alegra_id || i)}>
                  {columns.map(([key, , render]) => (
                    <td key={key}>{render ? render(r) : str(r[key])}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export function Pager({
  offset,
  total,
  change,
}: {
  offset: number;
  total: number;
  change: (offset: number) => void;
}) {
  return (
    <div className="operation-actions">
      <button
        disabled={offset === 0}
        onClick={() => change(Math.max(0, offset - 50))}
      >
        Anterior
      </button>
      <span>
        {number(total)} registros · página {offset / 50 + 1}
      </span>
      <button
        disabled={offset + 50 >= total}
        onClick={() => change(offset + 50)}
      >
        Siguiente
      </button>
    </div>
  );
}

export function fields(
  event: FormEvent<HTMLFormElement>,
): Record<string, string> {
  event.preventDefault();
  return Object.fromEntries(
    new FormData(event.currentTarget).entries(),
  ) as Record<string, string>;
}

export const save = (path: string, data: Row, method = "POST") =>
  api<Row>(path, { method, body: JSON.stringify(data) });
export const optional = (value: string): string | null => value.trim() || null;

export function Input({
  label,
  name,
  type = "text",
  required = false,
  value,
  min,
  step,
}: {
  label: string;
  name: string;
  type?: string;
  required?: boolean;
  value?: string | number;
  min?: number;
  step?: string;
}) {
  return (
    <label>
      {label}
      <input
        name={name}
        type={type}
        required={required}
        defaultValue={value}
        min={min}
        step={step}
      />
    </label>
  );
}

export function Notice({ message }: { message: string }) {
  return message ? (
    <p role="status" className="warning">
      {message}
    </p>
  ) : null;
}
