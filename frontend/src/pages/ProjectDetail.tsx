import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError, del, patch, post, put } from "../api/client";
import type { UserPublic } from "../api/types";
import { ActivityFeed, Comments } from "../components/Collab";
import { Markdown } from "../components/Markdown";
import { Badge, EmptyState, ErrorBox, Modal, Spinner, Tabs } from "../components/ui";
import { formatDateTime, timeAgo } from "../lib/format";
import { toast } from "../lib/toast";
import { useApi } from "../lib/useApi";
import { useSession } from "../store/session";
import { NOTE_KIND, PROJECT_STATUS } from "../lib/labels";
import type { ProjectRow } from "./Projects";

interface ProjectDetail extends ProjectRow {
  members: (UserPublic & { project_role: string })[];
  task_counts: Record<string, number>;
  documents: { id: string; title: string; status: string; ext: string; updated_at: string }[];
  agent_runs: {
    id: string;
    model: string;
    status: string;
    created_at: string;
    prompt: string;
    user_id: string;
  }[];
}

interface AgentRow {
  user_id: string;
  owner_name: string;
  name: string;
  assignable: boolean;
  state: string;
}

const AI_RUN_LABEL: Record<string, string> = {
  queued: "en cola",
  claimed: "trabajando",
  done: "entregada",
  error: "no pudo completarla",
  cancelled: "cancelada",
};

function useAgents(): AgentRow[] {
  return useApi<AgentRow[]>("/api/ai/agents", { refreshOn: ["ai.changed"] }).data ?? [];
}

function agentName(agents: AgentRow[], id: string): string {
  return agents.find((a) => a.user_id === id)?.name ?? "Claude";
}

export interface TaskRow {
  id: string;
  project_id: string;
  parent_id: string | null;
  title: string;
  description: string;
  status: string;
  assignee_id: string | null;
  ai_assignee_id: string | null;
  ai_run: { id: string; status: string } | null;
  due_date: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  version: number;
}

interface NoteRow {
  id: string;
  title: string;
  kind: string;
  version: number;
  updated_at: string;
}

interface DecisionRow {
  id: string;
  title: string;
  body: string;
  status: string;
  decided_at: string | null;
  created_at: string;
}

const COLUMNS = [
  { key: "todo", label: "Pendiente" },
  { key: "in_progress", label: "En curso" },
  { key: "blocked", label: "Bloqueada" },
  { key: "done", label: "Hecha" },
];

type Tab =
  "tasks" | "docs" | "decisions" | "files" | "agents" | "activity" | "summary" | "members" | "comments";

export default function ProjectDetailPage() {
  const { id } = useParams();
  const {
    data: p,
    error,
    reload,
  } = useApi<ProjectDetail>(`/api/projects/${id}`, {
    refreshOn: ["project.updated", "task.changed"],
    eventFilter: (ev) => !ev.project_id || ev.project_id === id || ev.id === id,
  });
  const [tab, setTab] = useState<Tab>("tasks");
  const [editing, setEditing] = useState(false);
  if (error) return <ErrorBox error={error} onRetry={() => reload()} />;
  if (!p) return <Spinner />;
  return (
    <>
      <div className="page-head">
        <div className="grow">
          <div className="small muted">
            <Link to="/projects">Proyectos</Link> /
          </div>
          <h1>
            {p.name} <span className="badge info">{PROJECT_STATUS[p.status]}</span>
          </h1>
          <p style={{ whiteSpace: "pre-wrap" }}>{p.description || "Sin descripción"}</p>
        </div>
        <button onClick={() => setEditing(true)}>Editar</button>
      </div>
      <Tabs<Tab>
        tabs={[
          { key: "tasks", label: "Tareas" },
          { key: "docs", label: "Documentos" },
          { key: "decisions", label: "Decisiones" },
          { key: "files", label: "Archivos" },
          { key: "agents", label: "Agentes" },
          { key: "activity", label: "Actividad" },
          { key: "summary", label: "Resumen" },
          { key: "members", label: "Responsables" },
          { key: "comments", label: "Conversación" },
        ]}
        value={tab}
        onChange={setTab}
      />
      {tab === "tasks" && <TaskBoard project={p} />}
      {tab === "docs" && <ProjectNotes project={p} />}
      {tab === "decisions" && <Decisions areaId={p.area_id} projectId={p.id} />}
      {tab === "files" && (
        <div className="panel">
          <div className="panel-title">
            <h3>Documentos de referencia</h3>
            <Link className="btn small" to={`/library?project=${p.id}`}>
              Subir a la biblioteca
            </Link>
          </div>
          {!p.documents.length && <p className="muted">No hay archivos asociados.</p>}
          <ul className="list">
            {p.documents.map((d) => (
              <li key={d.id} className="row">
                <Link to={`/library/${d.id}`}>{d.title}</Link>
                <Badge tone="muted">{d.ext}</Badge>
                <Badge tone={d.status === "indexed" ? "ok" : d.status === "error" ? "err" : "info"}>
                  {d.status}
                </Badge>
                <span className="small faint right">{timeAgo(d.updated_at)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {tab === "agents" && (
        <div className="panel">
          <h3>Resultados de agentes compartidos</h3>
          {!p.agent_runs.length && (
            <p className="muted">
              Ningún resultado compartido. Desde <Link to="/agents">Laboratorio de IA</Link> puede ejecutar
              Claude con su propia cuenta y compartir el resultado con el proyecto.
            </p>
          )}
          <ul className="list">
            {p.agent_runs.map((r) => (
              <li key={r.id}>
                <Link to={`/agents?run=${r.id}`}>{r.prompt}</Link>
                <div className="small muted">
                  {r.model} · {r.status} · {formatDateTime(r.created_at)}
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
      {tab === "activity" && (
        <div className="panel">
          <ActivityFeed query={`project_id=${p.id}`} limit={100} />
        </div>
      )}
      {tab === "summary" && <SummaryPanel project={p} reload={() => reload()} />}
      {tab === "members" && <Members project={p} reload={() => reload()} />}
      {tab === "comments" && (
        <div className="panel">
          <Comments targetType="project" targetId={p.id} />
        </div>
      )}
      {editing && <EditProject project={p} onClose={() => setEditing(false)} onSaved={() => reload()} />}
    </>
  );
}

function EditProject({
  project,
  onClose,
  onSaved,
}: {
  project: ProjectDetail;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [form, setForm] = useState({
    name: project.name,
    description: project.description,
    status: project.status,
  });
  const [error, setError] = useState<string | null>(null);
  async function submit(e: FormEvent) {
    e.preventDefault();
    try {
      await patch(`/api/projects/${project.id}`, { ...form, version: project.version });
      onSaved();
      onClose();
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 409
          ? "Otra persona modificó el proyecto; recargue."
          : (err as Error).message,
      );
    }
  }
  return (
    <Modal title="Editar proyecto" onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        <ErrorBox error={error} />
        <label className="field">
          Nombre
          <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
        </label>
        <label className="field">
          Descripción
          <textarea
            value={form.description}
            onChange={(e) => setForm({ ...form, description: e.target.value })}
          />
        </label>
        <label className="field">
          Estado
          <select value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })}>
            {Object.entries(PROJECT_STATUS).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </label>
        <button className="primary">Guardar</button>
      </form>
    </Modal>
  );
}

function TaskBoard({ project }: { project: ProjectDetail }) {
  const { data: tasks, reload } = useApi<TaskRow[]>(`/api/projects/${project.id}/tasks`, {
    refreshOn: ["task.changed"],
    eventFilter: (ev) => ev.project_id === project.id,
  });
  const [title, setTitle] = useState("");
  const [assignee, setAssignee] = useState("");
  const [open, setOpen] = useState<TaskRow | null>(null);
  const agents = useAgents();
  const nameOf = (uid: string | null) => project.members.find((m) => m.id === uid)?.display_name;
  async function create(e: FormEvent) {
    e.preventDefault();
    // El selector mezcla personas («user:») e IA de cada socio («ai:»).
    const [kind, id] = assignee.split(":");
    try {
      await post(`/api/projects/${project.id}/tasks`, {
        title,
        assignee_id: kind === "user" ? id : null,
        ai_assignee_id: kind === "ai" ? id : null,
      });
      setTitle("");
      reload();
    } catch (err) {
      toast((err as Error).message, "err");
    }
  }
  async function move(t: TaskRow, status: string) {
    try {
      await patch(`/api/tasks/${t.id}`, { version: t.version, status });
      reload();
    } catch (err) {
      toast(
        err instanceof ApiError && err.status === 409
          ? "La tarea cambió; se recargó la lista."
          : (err as Error).message,
        "err",
      );
      reload();
    }
  }
  const roots = (tasks ?? []).filter((t) => !t.parent_id);
  return (
    <>
      <form className="panel row" onSubmit={create}>
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Nueva tarea…"
          aria-label="Título de la tarea"
          style={{ flex: 1, minWidth: 220 }}
          required
        />
        <select
          value={assignee}
          onChange={(e) => setAssignee(e.target.value)}
          style={{ width: 220 }}
          aria-label="Asignar a"
        >
          <option value="">Sin asignar</option>
          <optgroup label="Personas">
            {project.members.map((m) => (
              <option key={m.id} value={`user:${m.id}`}>
                {m.display_name}
              </option>
            ))}
          </optgroup>
          <optgroup label="IA (Claude de cada socio)">
            {agents.map((a) => (
              <option key={a.user_id} value={`ai:${a.user_id}`} disabled={!a.assignable}>
                🤖 {a.name}
                {!a.assignable ? " (sin conectar)" : ""}
              </option>
            ))}
          </optgroup>
        </select>
        <button className="primary">Añadir</button>
      </form>
      {!tasks && <Spinner />}
      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))" }}>
        {COLUMNS.map((col) => (
          <div key={col.key} className="panel" style={{ minHeight: 120 }}>
            <h3>
              {col.label}{" "}
              <span className="faint small">({roots.filter((t) => t.status === col.key).length})</span>
            </h3>
            <ul className="list">
              {roots
                .filter((t) => t.status === col.key)
                .map((t) => {
                  const subs = (tasks ?? []).filter((s) => s.parent_id === t.id);
                  return (
                    <li key={t.id}>
                      <button className="link" onClick={() => setOpen(t)} style={{ textAlign: "left" }}>
                        {t.title}
                      </button>
                      {t.ai_assignee_id && (
                        <div className="small ai-badge" data-testid="task-ai">
                          🤖 {agentName(agents, t.ai_assignee_id)} ·{" "}
                          {AI_RUN_LABEL[t.ai_run?.status ?? "queued"]}
                        </div>
                      )}
                      <div className="small muted">
                        {nameOf(t.assignee_id) ??
                          (t.ai_assignee_id ? "sin persona responsable" : "sin responsable")}
                        {subs.length > 0 &&
                          ` · ${subs.filter((s) => s.status === "done").length}/${subs.length} subtareas`}
                      </div>
                      <select
                        value={t.status}
                        onChange={(e) => move(t, e.target.value)}
                        aria-label={`Estado de ${t.title}`}
                        style={{ marginTop: 4, padding: "2px 6px" }}
                      >
                        {COLUMNS.map((c) => (
                          <option key={c.key} value={c.key}>
                            {c.label}
                          </option>
                        ))}
                      </select>
                    </li>
                  );
                })}
            </ul>
          </div>
        ))}
      </div>
      {open && (
        <TaskModal
          task={(tasks ?? []).find((t) => t.id === open.id) ?? open}
          subtasks={(tasks ?? []).filter((s) => s.parent_id === open.id)}
          project={project}
          onClose={() => setOpen(null)}
          reload={() => reload()}
        />
      )}
    </>
  );
}

function TaskModal({
  task,
  subtasks,
  project,
  onClose,
  reload,
}: {
  task: TaskRow;
  subtasks: TaskRow[];
  project: ProjectDetail;
  onClose: () => void;
  reload: () => void;
}) {
  const me = useSession((s) => s.me)!;
  const agents = useAgents();
  const [form, setForm] = useState({
    title: task.title,
    description: task.description,
    assignee_id: task.assignee_id ?? "",
    ai_assignee_id: task.ai_assignee_id ?? "",
  });
  const [sub, setSub] = useState("");
  const [error, setError] = useState<string | null>(null);
  async function save(e: FormEvent) {
    e.preventDefault();
    try {
      await patch(`/api/tasks/${task.id}`, {
        version: task.version,
        title: form.title,
        description: form.description,
        ...(form.assignee_id ? { assignee_id: form.assignee_id } : { clear_assignee: true }),
        ...(form.ai_assignee_id
          ? form.ai_assignee_id !== task.ai_assignee_id
            ? { ai_assignee_id: form.ai_assignee_id }
            : {}
          : { clear_ai_assignee: true }),
      });
      reload();
      toast("Tarea guardada", "ok");
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 409
          ? "Otra persona modificó la tarea. Cierre y vuelva a abrirla."
          : (err as Error).message,
      );
    }
  }
  return (
    <Modal title={task.title} onClose={onClose} wide>
      <div className="grid cols-2">
        <form className="stack" onSubmit={save}>
          <ErrorBox error={error} />
          <label className="field">
            Título
            <input
              value={form.title}
              onChange={(e) => setForm({ ...form, title: e.target.value })}
              required
            />
          </label>
          <label className="field">
            Descripción (Markdown)
            <textarea
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              style={{ minHeight: 140 }}
            />
          </label>
          <label className="field">
            Responsable
            <select
              value={form.assignee_id}
              onChange={(e) => setForm({ ...form, assignee_id: e.target.value })}
            >
              <option value="">Sin responsable</option>
              {project.members.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.display_name}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            IA asignada
            <select
              value={form.ai_assignee_id}
              onChange={(e) => setForm({ ...form, ai_assignee_id: e.target.value })}
              aria-label="IA asignada"
            >
              <option value="">Ninguna</option>
              {agents.map((a) => (
                <option key={a.user_id} value={a.user_id} disabled={!a.assignable}>
                  🤖 {a.name}
                  {!a.assignable ? " (sin conectar)" : ""}
                </option>
              ))}
            </select>
            <span className="small muted">
              {task.ai_assignee_id
                ? `Estado: ${AI_RUN_LABEL[task.ai_run?.status ?? "queued"]}. El resultado aparecerá en los comentarios.`
                : "La tarea entra en la cola del Claude elegido; su dueño la atiende desde Claude Code con su suscripción."}
            </span>
          </label>
          <div className="row">
            <button className="primary">Guardar</button>
            <button
              type="button"
              onClick={async () => {
                await put("/api/me/status", { declared: "working", message: task.title, task_id: task.id });
                await useSession.getState().refresh();
                toast("Declarada como su tarea actual", "ok");
              }}
            >
              Estoy trabajando en esto
            </button>
            <button
              type="button"
              className="danger right"
              onClick={async () => {
                try {
                  await del(`/api/tasks/${task.id}?version=${task.version}`);
                  reload();
                  onClose();
                } catch (err) {
                  setError((err as Error).message);
                }
              }}
            >
              Eliminar
            </button>
          </div>
          <div className="small muted">
            Creada {formatDateTime(task.created_at)}
            {task.started_at && ` · iniciada ${formatDateTime(task.started_at)}`}
            {task.completed_at && ` · terminada ${formatDateTime(task.completed_at)}`}
            {me.status?.task_id === task.id && " · es su tarea actual"}
          </div>
          {task.description && (
            <div className="panel">
              <Markdown source={task.description} />
            </div>
          )}
        </form>
        <div className="stack">
          <h3>Subtareas</h3>
          <ul className="list">
            {subtasks.map((s) => (
              <li key={s.id} className="row">
                <input
                  type="checkbox"
                  checked={s.status === "done"}
                  aria-label={`Completar ${s.title}`}
                  onChange={async (e) => {
                    await patch(`/api/tasks/${s.id}`, {
                      version: s.version,
                      status: e.target.checked ? "done" : "todo",
                    });
                    reload();
                  }}
                />
                <span style={{ textDecoration: s.status === "done" ? "line-through" : undefined }}>
                  {s.title}
                </span>
              </li>
            ))}
          </ul>
          <form
            className="row"
            onSubmit={async (e) => {
              e.preventDefault();
              await post(`/api/projects/${project.id}/tasks`, { title: sub, parent_id: task.id });
              setSub("");
              reload();
            }}
          >
            <input
              value={sub}
              onChange={(e) => setSub(e.target.value)}
              placeholder="Nueva subtarea"
              style={{ flex: 1 }}
              aria-label="Nueva subtarea"
              required
            />
            <button className="small">Añadir</button>
          </form>
          <h3>Comentarios</h3>
          <Comments targetType="task" targetId={task.id} />
        </div>
      </div>
    </Modal>
  );
}

function ProjectNotes({ project }: { project: ProjectDetail }) {
  const navigate = useNavigate();
  const { data } = useApi<NoteRow[]>(`/api/notes?project_id=${project.id}`, { refreshOn: ["note.changed"] });
  const [kind, setKind] = useState("spec");
  const [title, setTitle] = useState("");
  return (
    <div className="panel">
      <form
        className="row"
        onSubmit={async (e) => {
          e.preventDefault();
          const n = await post<{ id: string }>("/api/notes", {
            area_id: project.area_id,
            project_id: project.id,
            title,
            kind,
            content: `# ${title}\n\n`,
          });
          navigate(`/notes/${n.id}`);
        }}
      >
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Título del documento"
          required
          style={{ flex: 1, minWidth: 200 }}
          aria-label="Título del documento"
        />
        <select
          value={kind}
          onChange={(e) => setKind(e.target.value)}
          style={{ width: 180 }}
          aria-label="Tipo"
        >
          {Object.entries(NOTE_KIND).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>
        <button className="primary">Crear documento</button>
      </form>
      {data?.length === 0 && (
        <EmptyState title="Sin documentos">Cree especificaciones, notas, requisitos o planes.</EmptyState>
      )}
      <ul className="list" style={{ marginTop: 10 }}>
        {data?.map((n) => (
          <li key={n.id} className="row">
            <Link to={`/notes/${n.id}`}>{n.title}</Link>
            <Badge tone="muted">{NOTE_KIND[n.kind]}</Badge>
            <span className="small faint right">
              v{n.version} · {timeAgo(n.updated_at)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function Decisions({ areaId, projectId }: { areaId: string; projectId?: string }) {
  const q = projectId ? `project_id=${projectId}` : `area_id=${areaId}`;
  const { data, reload } = useApi<DecisionRow[]>(`/api/decisions?${q}`, { refreshOn: ["decision.changed"] });
  const [form, setForm] = useState({ title: "", body: "" });
  const tone: Record<string, string> = {
    approved: "ok",
    rejected: "err",
    proposed: "info",
    superseded: "muted",
  };
  const label: Record<string, string> = {
    approved: "Aprobada",
    rejected: "Rechazada",
    proposed: "Propuesta",
    superseded: "Sustituida",
  };
  return (
    <div className="grid cols-2">
      <form
        className="panel stack"
        onSubmit={async (e) => {
          e.preventDefault();
          try {
            await post("/api/decisions", { area_id: areaId, project_id: projectId ?? null, ...form });
            setForm({ title: "", body: "" });
            reload();
          } catch (err) {
            toast((err as Error).message, "err");
          }
        }}
      >
        <h3>Proponer decisión</h3>
        <label className="field">
          Título
          <input
            value={form.title}
            onChange={(e) => setForm({ ...form, title: e.target.value })}
            required
            minLength={2}
          />
        </label>
        <label className="field">
          Contexto y justificación
          <textarea value={form.body} onChange={(e) => setForm({ ...form, body: e.target.value })} />
        </label>
        <button className="primary">Proponer</button>
        <p className="small muted">
          Las decisiones aprobadas por un propietario pasan a la memoria operativa como conocimiento
          confirmado.
        </p>
      </form>
      <div className="panel">
        <h3>Registro de decisiones</h3>
        {data?.length === 0 && <p className="muted">Sin decisiones.</p>}
        <ul className="list">
          {data?.map((d) => (
            <li key={d.id}>
              <div className="row">
                <strong style={{ flex: 1 }}>{d.title}</strong>
                <Badge tone={tone[d.status]}>{label[d.status]}</Badge>
              </div>
              {d.body && (
                <div className="small" style={{ whiteSpace: "pre-wrap" }}>
                  {d.body}
                </div>
              )}
              <div className="row small" style={{ marginTop: 4 }}>
                <span className="faint">{formatDateTime(d.decided_at ?? d.created_at)}</span>
                {d.status === "proposed" && (
                  <span className="row right">
                    {(["approved", "rejected"] as const).map((s) => (
                      <button
                        key={s}
                        className={`small ${s === "approved" ? "primary" : "danger"}`}
                        onClick={async () => {
                          try {
                            await post(`/api/decisions/${d.id}/status`, { status: s });
                            reload();
                          } catch (err) {
                            toast((err as Error).message, "err");
                          }
                        }}
                      >
                        {s === "approved" ? "Aprobar" : "Rechazar"}
                      </button>
                    ))}
                  </span>
                )}
                {d.status === "approved" && (
                  <button
                    className="small right"
                    onClick={async () => {
                      await post(`/api/decisions/${d.id}/status`, { status: "superseded" });
                      reload();
                    }}
                  >
                    Marcar sustituida
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function SummaryPanel({ project, reload }: { project: ProjectDetail; reload: () => void }) {
  const [busy, setBusy] = useState(false);
  return (
    <div className="panel">
      <div className="panel-title">
        <h3>Último resumen de actividad</h3>
        <button
          className="primary small"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            try {
              await post(`/api/projects/${project.id}/summary`);
              reload();
            } finally {
              setBusy(false);
            }
          }}
        >
          {busy ? "Generando…" : "Generar resumen"}
        </button>
      </div>
      <p className="small muted">
        Se construye solo con eventos registrados (tareas, documentos, decisiones, agentes, errores) desde el
        resumen anterior.
        {project.last_summary_at && ` Último: ${formatDateTime(project.last_summary_at)}.`}
      </p>
      {project.last_summary ? (
        <Markdown source={project.last_summary} />
      ) : (
        <p className="muted">Todavía no se ha generado.</p>
      )}
    </div>
  );
}

function Members({ project, reload }: { project: ProjectDetail; reload: () => void }) {
  const area = useApi<{ members: UserPublic[] }>(`/api/areas/${project.area_id}`);
  const [adding, setAdding] = useState("");
  const ids = new Set(project.members.map((m) => m.id));
  return (
    <div className="panel">
      <ul className="list">
        {project.members.map((m) => (
          <li key={m.id} className="row">
            <strong>{m.display_name}</strong>
            <Badge tone="muted">{m.project_role === "lead" ? "responsable principal" : "miembro"}</Badge>
            <button
              className="small danger right"
              onClick={async () => {
                await del(`/api/projects/${project.id}/members/${m.id}`);
                reload();
              }}
            >
              Quitar
            </button>
          </li>
        ))}
      </ul>
      <div className="row" style={{ marginTop: 10 }}>
        <select
          value={adding}
          onChange={(e) => setAdding(e.target.value)}
          style={{ width: 240 }}
          aria-label="Añadir responsable"
        >
          <option value="">Añadir miembro del área…</option>
          {area.data?.members
            .filter((m) => !ids.has(m.id))
            .map((m) => (
              <option key={m.id} value={m.id}>
                {m.display_name}
              </option>
            ))}
        </select>
        <button
          disabled={!adding}
          onClick={async () => {
            try {
              await put(`/api/projects/${project.id}/members/${adding}`, { role: "member" });
              setAdding("");
              reload();
            } catch (err) {
              toast((err as Error).message, "err");
            }
          }}
        >
          Añadir
        </button>
      </div>
    </div>
  );
}
