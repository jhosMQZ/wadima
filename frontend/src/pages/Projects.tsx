import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { post } from "../api/client";
import type { Area } from "../api/types";
import { EmptyState, ErrorBox, Modal, Spinner } from "../components/ui";
import { timeAgo } from "../lib/format";
import { PROJECT_STATUS } from "../lib/labels";
import { useApi } from "../lib/useApi";
import { useSession } from "../store/session";

export interface ProjectRow {
  id: string;
  area_id: string;
  name: string;
  description: string;
  status: string;
  updated_at: string;
  version: number;
  last_summary: string;
  last_summary_at: string | null;
  task_counts?: Record<string, number>;
}

export default function ProjectsPage() {
  const me = useSession((s) => s.me)!;
  const [areaFilter, setAreaFilter] = useState<string>(me.active_area?.id ?? "");
  const areas = useApi<{ areas: Area[] }>("/api/areas");
  const { data, error, loading, reload } = useApi<ProjectRow[]>(
    `/api/projects${areaFilter ? `?area_id=${areaFilter}` : ""}`,
    { refreshOn: ["project.updated", "task.changed", "activity.new"] },
  );
  const [creating, setCreating] = useState(false);
  const areaName = (id: string) => areas.data?.areas.find((a) => a.id === id)?.name ?? "";
  return (
    <>
      <div className="page-head">
        <div className="grow">
          <h1>Administración de proyectos</h1>
          <p>Proyectos, tareas, especificaciones, decisiones y resúmenes de actividad.</p>
        </div>
        <select
          value={areaFilter}
          onChange={(e) => setAreaFilter(e.target.value)}
          style={{ width: 220 }}
          aria-label="Filtrar por área"
        >
          <option value="">Todas mis áreas</option>
          {areas.data?.areas.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
        </select>
        <button className="primary" onClick={() => setCreating(true)}>
          Nuevo proyecto
        </button>
      </div>
      <ErrorBox error={error} onRetry={() => reload()} />
      {loading && <Spinner />}
      {data && data.length === 0 && (
        <EmptyState title="Sin proyectos">
          Cree el primer proyecto del área para empezar a organizar tareas.
        </EmptyState>
      )}
      <div className="grid cols-3">
        {data?.map((p) => {
          const c = p.task_counts ?? {};
          const total = Object.values(c).reduce((a, b) => a + b, 0);
          return (
            <Link to={`/projects/${p.id}`} key={p.id} className="card-link">
              <div className="panel">
                <div className="row">
                  <strong style={{ flex: 1 }}>{p.name}</strong>
                  <span className="badge info">{PROJECT_STATUS[p.status]}</span>
                </div>
                <div className="small muted">
                  {areaName(p.area_id)} · actualizado {timeAgo(p.updated_at)}
                </div>
                <p className="small" style={{ margin: "6px 0" }}>
                  {p.description.slice(0, 140) || <span className="faint">Sin descripción</span>}
                </p>
                <div className="small muted">
                  {total} tareas · {c.in_progress ?? 0} en curso · {c.blocked ?? 0} bloqueadas · {c.done ?? 0}{" "}
                  hechas
                </div>
              </div>
            </Link>
          );
        })}
      </div>
      {creating && (
        <CreateProject
          areas={areas.data?.areas.filter((a) => a.my_role !== "viewer") ?? []}
          defaultArea={areaFilter || me.active_area?.id || ""}
          onClose={() => setCreating(false)}
        />
      )}
    </>
  );
}

function CreateProject({
  areas,
  defaultArea,
  onClose,
}: {
  areas: Area[];
  defaultArea: string;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const [form, setForm] = useState({ area_id: defaultArea || areas[0]?.id || "", name: "", description: "" });
  const [error, setError] = useState<string | null>(null);
  async function submit(e: FormEvent) {
    e.preventDefault();
    try {
      const p = await post<ProjectRow>("/api/projects", form);
      navigate(`/projects/${p.id}`);
    } catch (err) {
      setError((err as Error).message);
    }
  }
  return (
    <Modal title="Nuevo proyecto" onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        <ErrorBox error={error} />
        <label className="field">
          Área
          <select
            value={form.area_id}
            onChange={(e) => setForm({ ...form, area_id: e.target.value })}
            required
          >
            {areas.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          Nombre
          <input
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            required
            minLength={2}
          />
        </label>
        <label className="field">
          Descripción
          <textarea
            value={form.description}
            onChange={(e) => setForm({ ...form, description: e.target.value })}
          />
        </label>
        <button className="primary">Crear proyecto</button>
      </form>
    </Modal>
  );
}
