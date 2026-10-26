import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { patch, post } from "../api/client";
import type { Area, TimeEvent, UserPublic } from "../api/types";
import { Badge, ErrorBox, Modal, Spinner, Tabs } from "../components/ui";
import { ROBOT_KIND_LABEL } from "../office/robot";
import { useBrand } from "../store/brand";
import { formatInZone } from "../store/timeclock";
import { toast } from "../lib/toast";
import { formatDateTime, ROLE_LABEL } from "../lib/format";
import { useApi } from "../lib/useApi";
import { useSession } from "../store/session";
import { AGENT_STATUS, JOB_STATUS, label } from "../lib/labels";

interface LogRow {
  ts: string;
  level: string;
  logger: string;
  msg: string;
  exc?: string;
}
interface JobsResp {
  counts: Record<string, number>;
  jobs: {
    id: number;
    kind: string;
    status: string;
    attempts: number;
    max_attempts: number;
    last_error: string;
    created_at: string;
    finished_at: string | null;
  }[];
}

export default function AdminPage() {
  const me = useSession((s) => s.me)!;
  const [tab, setTab] = useState<"users" | "time" | "system" | "jobs" | "logs" | "archived">("users");
  if (me.user.role !== "admin") return <ErrorBox error="Requiere rol de administrador" />;
  return (
    <>
      <div className="page-head">
        <div className="grow">
          <h1>Administración</h1>
          <p>Usuarios, control horario, diagnóstico del sistema, cola de trabajos y registros.</p>
        </div>
      </div>
      <Tabs
        tabs={[
          { key: "users", label: "Usuarios" },
          { key: "time", label: "Control horario" },
          { key: "system", label: "Sistema" },
          { key: "jobs", label: "Trabajos" },
          { key: "logs", label: "Logs" },
          { key: "archived", label: "Áreas archivadas" },
        ]}
        value={tab}
        onChange={setTab}
      />
      {tab === "users" && <Users />}
      {tab === "time" && <TimeAdmin />}
      {tab === "system" && <SystemInfo />}
      {tab === "jobs" && <Jobs />}
      {tab === "logs" && <Logs />}
      {tab === "archived" && <Archived />}
    </>
  );
}

function Users() {
  const { data, reload } = useApi<UserPublic[]>("/api/users");
  const [creating, setCreating] = useState(false);
  const [secret, setSecret] = useState<{ user: string; password: string } | null>(null);
  return (
    <div className="panel">
      <div className="panel-title">
        <h3>Usuarios</h3>
        <button className="primary small" onClick={() => setCreating(true)}>
          Nuevo usuario
        </button>
      </div>
      {secret && (
        <div className="alert ok">
          Contraseña temporal de <strong>{secret.user}</strong> (se muestra una vez):{" "}
          <span className="mono">{secret.password}</span>
        </div>
      )}
      <table className="table">
        <thead>
          <tr>
            <th>Usuario</th>
            <th>Rol</th>
            <th>Estado</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {data?.map((u) => (
            <tr key={u.id}>
              <td>
                {u.display_name} <span className="faint small">@{u.username}</span>
              </td>
              <td>
                <select
                  value={u.role}
                  style={{ width: 150 }}
                  aria-label={`Rol de ${u.username}`}
                  onChange={async (e) => {
                    try {
                      await patch(`/api/users/${u.id}`, { role: e.target.value });
                      reload(true);
                    } catch (err) {
                      toast((err as Error).message, "err");
                    }
                  }}
                >
                  <option value="admin">{ROLE_LABEL.admin}</option>
                  <option value="member">{ROLE_LABEL.member}</option>
                </select>
              </td>
              <td>
                {u.is_active ? <Badge tone="ok">activo</Badge> : <Badge tone="muted">desactivado</Badge>}
              </td>
              <td className="row">
                <button
                  className="small"
                  onClick={async () => {
                    try {
                      await patch(`/api/users/${u.id}`, { is_active: !u.is_active });
                      reload(true);
                    } catch (err) {
                      toast((err as Error).message, "err");
                    }
                  }}
                >
                  {u.is_active ? "Desactivar" : "Activar"}
                </button>
                <button
                  className="small"
                  onClick={async () => {
                    const res = await post<{ temporary_password: string }>(
                      `/api/users/${u.id}/reset-password`,
                    );
                    setSecret({ user: u.username, password: res.temporary_password });
                  }}
                >
                  Restablecer contraseña
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {creating && (
        <CreateUser
          onClose={() => setCreating(false)}
          onCreated={(user, password) => {
            setSecret({ user, password });
            setCreating(false);
            reload(true);
          }}
        />
      )}
    </div>
  );
}

function CreateUser({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (u: string, p: string) => void;
}) {
  const [form, setForm] = useState({ username: "", display_name: "", role: "member" });
  const [error, setError] = useState<string | null>(null);
  async function submit(e: FormEvent) {
    e.preventDefault();
    try {
      const res = await post<{ temporary_password: string; user: UserPublic }>("/api/users", form);
      onCreated(res.user.username, res.temporary_password);
    } catch (err) {
      setError((err as Error).message);
    }
  }
  return (
    <Modal title="Nuevo usuario" onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        <ErrorBox error={error} />
        <label className="field">
          Usuario
          <input
            value={form.username}
            onChange={(e) => setForm({ ...form, username: e.target.value })}
            required
          />
        </label>
        <label className="field">
          Nombre visible
          <input
            value={form.display_name}
            onChange={(e) => setForm({ ...form, display_name: e.target.value })}
            required
          />
        </label>
        <label className="field">
          Rol
          <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
            <option value="member">Miembro</option>
            <option value="admin">Administrador</option>
          </select>
        </label>
        <button className="primary">Crear</button>
      </form>
    </Modal>
  );
}

function SystemInfo() {
  const { data, error } = useApi<Record<string, unknown>>("/api/admin/system", { intervalMs: 30000 });
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Spinner />;
  const tools = data.tools as Record<string, boolean>;
  const agents = data.agents as {
    key: string;
    status: string;
    heartbeat_at: string | null;
    current: string;
  }[];
  return (
    <div className="grid cols-2">
      <div className="panel">
        <h3>Servidor</h3>
        <dl className="kv">
          <dt>Python</dt>
          <dd>{String(data.python)}</dd>
          <dt>Plataforma</dt>
          <dd>{String(data.platform)}</dd>
          <dt>PostgreSQL</dt>
          <dd>{String(data.postgres)}</dd>
          <dt>Tamaño BD</dt>
          <dd>{String(data.database_size)}</dd>
          <dt>Datos locales</dt>
          <dd className="mono small">{String(data.data_dir)}</dd>
          <dt>Disco libre</dt>
          <dd>{String(data.disk_free_gb)} GB</dd>
          <dt>Usuarios en línea</dt>
          <dd>{String(data.online_users)}</dd>
        </dl>
      </div>
      <div className="panel">
        <h3>Herramientas detectadas</h3>
        <div className="row">
          {Object.entries(tools).map(([k, v]) => (
            <Badge key={k} tone={v ? "ok" : "muted"}>
              {k}
            </Badge>
          ))}
        </div>
        <h3 style={{ marginTop: 14 }}>Agentes</h3>
        <ul className="list">
          {agents.map((a) => (
            <li key={a.key} className="small">
              <strong>{a.key}</strong> · {label(AGENT_STATUS, a.status)} · latido{" "}
              {formatDateTime(a.heartbeat_at)} {a.current && `· ${a.current}`}
            </li>
          ))}
          {!agents.length && <li className="muted small">Sin agentes registrados.</li>}
        </ul>
      </div>
    </div>
  );
}

function Jobs() {
  const { data, reload } = useApi<JobsResp>("/api/admin/jobs", { intervalMs: 10000 });
  if (!data) return <Spinner />;
  return (
    <div className="panel">
      <div className="row" style={{ marginBottom: 8 }}>
        {Object.entries(data.counts).map(([k, v]) => (
          <Badge key={k} tone={k === "error" || k === "dead" ? "err" : k === "done" ? "ok" : "info"}>
            {k}: {v}
          </Badge>
        ))}
      </div>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>#</th>
              <th>Tipo</th>
              <th>Estado</th>
              <th>Intentos</th>
              <th>Creado</th>
              <th>Error</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {data.jobs.map((j) => (
              <tr key={j.id}>
                <td className="mono small">{j.id}</td>
                <td>{j.kind}</td>
                <td>{label(JOB_STATUS, j.status)}</td>
                <td>
                  {j.attempts}/{j.max_attempts}
                </td>
                <td className="small">{formatDateTime(j.created_at)}</td>
                <td className="small" style={{ maxWidth: 360, overflowWrap: "anywhere" }}>
                  {j.last_error.slice(0, 200)}
                </td>
                <td>
                  {(j.status === "error" || j.status === "dead") && (
                    <button
                      className="small"
                      onClick={async () => {
                        await post(`/api/admin/jobs/${j.id}/retry`);
                        reload(true);
                      }}
                    >
                      Reintentar
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Logs() {
  const [level, setLevel] = useState("");
  const { data } = useApi<LogRow[]>(`/api/admin/logs?limit=300${level ? `&level=${level}` : ""}`, {
    intervalMs: 10000,
  });
  return (
    <div className="panel">
      <div className="row" style={{ marginBottom: 8 }}>
        <select
          value={level}
          onChange={(e) => setLevel(e.target.value)}
          style={{ width: 160 }}
          aria-label="Nivel"
        >
          <option value="">Todos los niveles</option>
          <option value="ERROR">ERROR</option>
          <option value="WARNING">WARNING</option>
          <option value="INFO">INFO</option>
        </select>
      </div>
      <div className="code-box">
        {data?.map((l, i) => (
          <div key={i}>
            [{l.ts}] {l.level} {l.logger}: {l.msg}
            {l.exc ? `\n${l.exc}` : ""}
          </div>
        ))}
        {data?.length === 0 && "Sin registros."}
      </div>
    </div>
  );
}

function Archived() {
  const { data, reload } = useApi<Area[]>("/api/areas-archived");
  if (!data) return <Spinner />;
  return (
    <div className="panel">
      {!data.length && <p className="muted">No hay áreas archivadas.</p>}
      <ul className="list">
        {data.map((a) => (
          <li key={a.id} className="row">
            <strong>{a.name}</strong>
            <span className="small muted">archivada {formatDateTime(a.archived_at)}</span>
            <button
              className="small right"
              onClick={async () => {
                await post(`/api/areas/${a.id}/restore`);
                toast("Área restaurada", "ok");
                reload(true);
              }}
            >
              Restaurar
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Registros horarios recogidos (entradas, salidas, cierres, aprobaciones) con su entrada de auditoría. */
function TimeAdmin() {
  const tz = useBrand((s) => s.org.timezone);
  const events = useApi<TimeEvent[]>("/api/time/events?limit=30", { refreshOn: ["time.recorded"] });
  const incidents = useApi<{ open_too_long: unknown[]; pending_close: unknown[]; needs_review: unknown[] }>(
    "/api/time/incidents",
    { refreshOn: ["time.recorded"] },
  );
  return (
    <div className="stack" data-testid="admin-time">
      <div className="panel row wrap">
        <span>
          Pendientes de revisión: <strong>{incidents.data?.needs_review.length ?? "…"}</strong> · Pendientes
          de cierre: <strong>{incidents.data?.pending_close.length ?? "…"}</strong> · Abiertas demasiado
          tiempo: <strong>{incidents.data?.open_too_long.length ?? "…"}</strong>
        </span>
        <Link className="btn small right" to="/time?tab=review">
          Revisar jornadas
        </Link>
        <Link className="btn small" to="/time?tab=events">
          Historial completo
        </Link>
      </div>
      <div className="panel">
        <h3>Últimos registros horarios</h3>
        <ErrorBox error={events.error} onRetry={() => events.reload()} />
        {!events.data && !events.error && <Spinner />}
        {events.data && (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Hora del servidor</th>
                  <th>Socio</th>
                  <th>Evento</th>
                  <th>Realizado por</th>
                  <th>Origen</th>
                  <th>Auditoría</th>
                </tr>
              </thead>
              <tbody>
                {events.data.map((ev) => (
                  <tr key={ev.id}>
                    <td>{formatInZone(ev.at, tz, { dateStyle: "short", timeStyle: "medium" })}</td>
                    <td>{ev.user_name}</td>
                    <td>{ROBOT_KIND_LABEL[ev.kind] ?? ev.kind}</td>
                    <td>{ev.actor_name}</td>
                    <td>{ev.source}</td>
                    <td>#{ev.audit_id ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="small muted">
          La cadena de la bitácora de auditoría se verifica en Seguridad o con{" "}
          <code>python -m wadima.cli verify-audit</code>.
        </p>
      </div>
    </div>
  );
}
