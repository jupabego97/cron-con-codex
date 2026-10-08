import { api } from "../api";
import {
  Notice,
  Row,
  State,
  Table,
  rows,
  str,
  useMutation,
  useResource,
} from "./Shared";

export default function System() {
  const status = useResource<Row>("/operations/status");
  const events = useResource<Row>("/operations/events");
  const mutation = useMutation();
  const data = status.data || {};
  return (
    <>
      <h2>Estado y recuperación</h2>
      <State {...status} />
      <Notice message={mutation.message} />
      {Array.isArray(data.warnings) &&
        (data.warnings as string[]).map((warning) => (
          <p className="warning" key={warning}>
            {warning}
          </p>
        ))}
      <div className="operation-actions">
        <button
          onClick={() => {
            status.refresh();
            events.refresh();
          }}
        >
          Actualizar
        </button>
        <button
          disabled={mutation.busy}
          onClick={() => {
            if (confirm("¿Cerrar todas tus sesiones?"))
              void mutation.run(
                () => api("/dashboard/sessions/revoke", { method: "POST" }),
                () => location.reload(),
              );
          }}
        >
          Cerrar todas las sesiones
        </button>
      </div>
      <Table
        title="Última conciliación por recurso"
        items={rows(data.syncs)}
        columns={[
          ["resource", "Recurso"],
          ["status", "Estado"],
          ["finished_at", "Fin"],
          ["checkpoint_date", "Último día completo"],
          ["records_written", "Escritos"],
        ]}
      />
      <Table
        title="Cola de webhooks"
        items={rows(data.queue)}
        columns={[
          ["status", "Estado"],
          ["count", "Eventos"],
          ["oldest", "Más antiguo"],
        ]}
      />
      <State {...events} />
      <Table
        title="Eventos con atención pendiente"
        items={rows(events.data?.items)}
        columns={[
          ["subject", "Evento"],
          ["external_id", "Documento"],
          ["status", "Estado"],
          ["attempt_count", "Intentos"],
          [
            "retry",
            "Acción",
            (r) => (
              <button
                disabled={mutation.busy || r.status !== "failed"}
                onClick={() =>
                  void mutation.run(
                    () =>
                      api(`/operations/events/${str(r.id)}/retry`, {
                        method: "POST",
                      }),
                    () => {
                      events.refresh();
                      status.refresh();
                    },
                  )
                }
              >
                Reintentar
              </button>
            ),
          ],
        ]}
      />
      <p className="muted">
        Los reintentos no crean documentos en Alegra. Los workers recuperan
        automáticamente las reservas vencidas; esta acción solo aplica a eventos
        fallidos.
      </p>
    </>
  );
}
