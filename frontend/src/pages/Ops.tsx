import { Link } from "react-router-dom";
import type { Area, OfficeState } from "../api/types";
import { ActivityFeed } from "../components/Collab";
import { Markdown } from "../components/Markdown";
import { EmptyState, Spinner } from "../components/ui";
import { STATE_LABEL, timeAgo } from "../lib/format";
import { AGENT_STATUS } from "../lib/labels";
import { useApi } from "../lib/useApi";
import { ROOM_NAMES } from "../office/rooms";
import { useWorkArea } from "../lib/areas";
import { useBrand } from "../store/brand";

interface Summary {
  markdown: string;
  counts: Record<string, number>;
  events: number;
}

const COUNT_LABEL: Record<string, string> = {
  changes: "Cambios",
  tasks_created: "Tareas creadas",
  tasks_started: "Iniciadas",
  tasks_done: "Terminadas",
  documents: "Documentos",
  agents: "Resultados de agentes",
  errors: "Errores y bloqueos",
};

export default function OpsPage() {
  const { area, loading } = useWorkArea("ops");
  if (!area)
    return loading ? (
      <Spinner />
    ) : (
      <EmptyState title="Sin áreas accesibles">Use un código de invitación en Áreas.</EmptyState>
    );
  return <Ops area={area} />;
}

function Ops({ area }: { area: Area }) {
  const org = useBrand((s) => s.org);
  const office = useApi<OfficeState>("/api/office/state", {
    refreshOn: ["presence.changed", "agent.changed", "time.recorded"],
  });
  const summary = useApi<Summary>(`/api/areas/${area.id}/summary?days=7`, { refreshOn: ["activity.new"] });
  return (
    <>
      <div className="page-head">
        <div className="grow">
          <h1>Centro de operaciones</h1>
          <p>
            {org.legal_name}: presencia, actividad registrada y estado de los servicios del área «{area.name}
            ».
          </p>
        </div>
      </div>
      <div className="grid cols-3">
        {office.data?.people.map((p) => (
          <div key={p.id} className="panel">
            <div className="row">
              <span className={`dot ${p.state}`} />
              <strong>{p.display_name}</strong>
              <span className="small muted right">{STATE_LABEL[p.state]}</span>
            </div>
            <div className="small muted">
              {p.online && p.room_key ? ROOM_NAMES[p.room_key] : `Última conexión ${timeAgo(p.last_seen_at)}`}
            </div>
            {p.task && (
              <div className="small">
                Tarea: <Link to={`/projects/${p.task.project_id}`}>{p.task.title}</Link>
              </div>
            )}
            <div className="small faint">
              {p.work_summary.in_progress} en curso · {p.work_summary.done_last_7_days} terminadas en 7 días
            </div>
          </div>
        ))}
      </div>
      <div className="grid cols-2">
        <div className="panel">
          <h3>Últimos 7 días en «{area.name}»</h3>
          {!summary.data && <Spinner />}
          {summary.data && (
            <>
              <div className="row" style={{ marginBottom: 8 }}>
                {Object.entries(summary.data.counts)
                  .filter(([, v]) => v > 0)
                  .map(([k, v]) => (
                    <span key={k} className={`badge ${k === "errors" ? "err" : "info"}`}>
                      {COUNT_LABEL[k] ?? k}: {v}
                    </span>
                  ))}
                {summary.data.events === 0 && <span className="muted small">Sin actividad registrada.</span>}
              </div>
              <details>
                <summary className="small">Ver resumen completo</summary>
                <Markdown source={summary.data.markdown} />
              </details>
            </>
          )}
          <h3 style={{ marginTop: 14 }}>Agentes y servicios</h3>
          <ul className="list">
            {office.data?.agents.map((a) => (
              <li key={a.key} className="row small">
                <span
                  className={`badge ${a.status === "error" ? "err" : a.status === "working" ? "info" : "ok"}`}
                >
                  {AGENT_STATUS[a.status] ?? a.status}
                </span>
                <strong>{a.name}</strong>
                <span className="muted">{a.current || a.last_result}</span>
                <span className="faint right">{timeAgo(a.heartbeat_at)}</span>
              </li>
            ))}
            {office.data && office.data.agents.length === 0 && (
              <li className="muted small">Ningún agente ha reportado estado todavía.</li>
            )}
          </ul>
        </div>
        <div className="panel">
          <h3>Actividad reciente</h3>
          <ActivityFeed query={`area_id=${area.id}`} limit={40} />
        </div>
      </div>
    </>
  );
}
