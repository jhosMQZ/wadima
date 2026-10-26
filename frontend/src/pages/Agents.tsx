import { useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ApiError, post } from "../api/client";
import type { Area, OfficeState } from "../api/types";
import { Markdown } from "../components/Markdown";
import { Badge, EmptyState, ErrorBox, Modal, Spinner, Tabs } from "../components/ui";
import { formatDateTime, timeAgo } from "../lib/format";
import { AGENT_STATUS, RUN_STATUS } from "../lib/labels";
import { toast } from "../lib/toast";
import { useApi } from "../lib/useApi";
import { useSession } from "../store/session";
import type { CredentialsResponse } from "./ClaudeConnections";
import type { ProjectRow } from "./Projects";

interface Run {
  id: string;
  user_id: string;
  provider: string;
  model: string;
  prompt: string;
  output: string;
  usage: Record<string, unknown>;
  status: string;
  error: string;
  shared: boolean;
  area_id: string | null;
  project_id: string | null;
  context_refs: {
    n: number;
    type: string;
    title: string;
    suspicious: boolean;
    reference: Record<string, string | number | null> & { link: string };
  }[];
  created_at: string;
  mine?: boolean;
  author?: string;
}

const AGENT_NAMES: Record<string, string> = {
  memory_agent: "Memory Agent",
  doc_pipeline: "Conversor documental",
  drive_sync: "Sincronizador de Drive",
};

export default function AgentsPage() {
  const [params] = useSearchParams();
  const [tab, setTab] = useState<"claude" | "history" | "agents" | "import">(
    params.get("run") ? "history" : "claude",
  );
  return (
    <>
      <div className="page-head">
        <div className="grow">
          <h1>Laboratorio de IA</h1>
          <p>
            Claude con la cuenta de cada socio, contexto de la memoria compartida y estado real de los agentes
            internos. Las opciones de conexión están en <Link to="/ai">Configuración de IA</Link>.
          </p>
        </div>
      </div>
      <Tabs
        tabs={[
          { key: "claude", label: "Preguntar a Claude" },
          { key: "history", label: "Ejecuciones" },
          { key: "agents", label: "Agentes internos" },
          { key: "import", label: "Importar resultado" },
        ]}
        value={tab}
        onChange={setTab}
      />
      {tab === "claude" && <AskClaude />}
      {tab === "history" && <History focus={params.get("run")} />}
      {tab === "agents" && <InternalAgents />}
      {tab === "import" && <ImportResult />}
    </>
  );
}

function AskClaude() {
  const me = useSession((s) => s.me)!;
  const creds = useApi<CredentialsResponse>("/api/ai/credentials", { refreshOn: ["ai.changed"] });
  const areas = useApi<{ areas: Area[] }>("/api/areas");
  const [form, setForm] = useState({
    prompt: "",
    area_id: me.active_area?.id ?? "",
    project_id: "",
    use_memory: true,
    effort: "medium",
    model: "",
  });
  const projects = useApi<ProjectRow[]>(form.area_id ? `/api/projects?area_id=${form.area_id}` : null);
  const [run, setRun] = useState<(Run & { contradictions?: { detail: string }[] }) | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const mine = creds.data?.mine;
  const ready = mine?.status === "verified";

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setRun(null);
    try {
      const res = await post<Run>("/api/ai/runs", {
        prompt: form.prompt,
        area_id: form.area_id || null,
        project_id: form.project_id || null,
        use_memory: form.use_memory,
        effort: form.effort,
        model: form.model || null,
      });
      setRun(res);
    } catch (err) {
      const detail =
        err instanceof ApiError ? (err.detail as { message?: string; retry_after?: number }) : null;
      setError(
        detail?.message
          ? `${detail.message}${detail.retry_after ? ` (reintente en ${detail.retry_after}s)` : ""}`
          : (err as Error).message,
      );
    } finally {
      setBusy(false);
    }
  }

  if (!creds.data) return <Spinner />;
  if (!ready) {
    return (
      <EmptyState title="Conecte su propia cuenta de Claude">
        Para preguntar a Claude desde aquí necesita una API key de Anthropic verificada. Con una suscripción
        Pro/Max puede encargar tareas a su propio Claude Code mediante MCP.{" "}
        <Link to="/ai">Ir a Configuración de IA</Link>
      </EmptyState>
    );
  }
  return (
    <div className="grid cols-2">
      <form className="panel stack" onSubmit={submit}>
        <label className="field">
          Pregunta o tarea
          <textarea
            value={form.prompt}
            onChange={(e) => setForm({ ...form, prompt: e.target.value })}
            required
            minLength={2}
            style={{ minHeight: 140 }}
            placeholder="Por ejemplo: resume las decisiones aprobadas sobre el proyecto Atlas"
          />
        </label>
        <div className="row">
          <label className="field" style={{ flex: 1 }}>
            Área (contexto)
            <select
              value={form.area_id}
              onChange={(e) => setForm({ ...form, area_id: e.target.value, project_id: "" })}
            >
              <option value="">Todas mis áreas</option>
              {areas.data?.areas.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </select>
          </label>
          <label className="field" style={{ flex: 1 }}>
            Proyecto
            <select
              value={form.project_id}
              onChange={(e) => setForm({ ...form, project_id: e.target.value })}
            >
              <option value="">—</option>
              {projects.data?.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="row">
          <label className="field" style={{ flex: 1 }}>
            Modelo
            <select value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })}>
              <option value="">Por defecto ({mine!.default_model})</option>
              {mine!.models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.display_name}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            Esfuerzo
            <select value={form.effort} onChange={(e) => setForm({ ...form, effort: e.target.value })}>
              {["low", "medium", "high", "xhigh", "max"].map((x) => (
                <option key={x} value={x}>
                  {x}
                </option>
              ))}
            </select>
          </label>
        </div>
        <label className="inline">
          <input
            type="checkbox"
            checked={form.use_memory}
            onChange={(e) => setForm({ ...form, use_memory: e.target.checked })}
          />
          Usar la memoria compartida como contexto (solo áreas a las que tengo acceso)
        </label>
        <button className="primary" disabled={busy}>
          {busy ? "Consultando a Claude…" : "Enviar con mi cuenta"}
        </button>
        <p className="small faint">
          El resultado es privado hasta que usted lo comparta. Los fragmentos recuperados se envían como datos
          no confiables y Claude no recibe herramientas ni permisos.
        </p>
      </form>
      <div className="panel">
        <ErrorBox error={error} />
        {busy && <Spinner label="Esperando respuesta" />}
        {!run && !busy && !error && <p className="muted">La respuesta aparecerá aquí.</p>}
        {run && <RunView run={run} onShared={(r) => setRun({ ...run, ...r })} />}
      </div>
    </div>
  );
}

function RunView({
  run,
  onShared,
}: {
  run: Run & { contradictions?: { detail: string }[] };
  onShared?: (r: Run) => void;
}) {
  const [sharing, setSharing] = useState(false);
  const usage = run.usage as Record<string, number | string | boolean | undefined>;
  return (
    <div className="stack">
      <div className="row small">
        <Badge
          tone={
            run.status === "done"
              ? "ok"
              : run.status === "refused"
                ? "warn"
                : run.status === "error"
                  ? "err"
                  : "info"
          }
        >
          {RUN_STATUS[run.status] ?? run.status}
        </Badge>
        <span className="muted">{run.model}</span>
        {run.provider === "manual_import" && <Badge tone="muted">importado manualmente</Badge>}
        {run.provider === "claude_mcp" && <Badge tone="info">vía su suscripción de Claude (MCP)</Badge>}
        {usage.served_by_fallback === true && <Badge tone="info">respondido por modelo de respaldo</Badge>}
        {run.shared && <Badge tone="info">compartido</Badge>}
        <span className="faint right">{formatDateTime(run.created_at)}</span>
      </div>
      {run.error && <div className="alert warn">{run.error}</div>}
      {run.contradictions && run.contradictions.length > 0 && (
        <div className="alert warn">
          Las fuentes contienen contradicciones sin resolver:{" "}
          {run.contradictions.map((c) => c.detail).join("; ")}
        </div>
      )}
      {run.output && <Markdown source={run.output} />}
      {run.provider !== "manual_import" && run.provider !== "claude_mcp" && (
        <div className="small muted">
          Uso medido por Anthropic: {String(usage.input_tokens ?? "—")} tokens de entrada ·{" "}
          {String(usage.output_tokens ?? "—")} de salida
          {usage.context_token_estimate
            ? ` · contexto ~${usage.context_token_estimate} tokens (estimación)`
            : ""}
          {usage.request_id ? ` · ${usage.request_id}` : ""}
        </div>
      )}
      {run.context_refs.length > 0 && (
        <details>
          <summary className="small">Fuentes enviadas como contexto ({run.context_refs.length})</summary>
          <ul className="list">
            {run.context_refs.map((r) => (
              <li key={r.n} className="small">
                [{r.n}] <Link to={r.reference.link}>{r.title}</Link>{" "}
                {r.reference.page_from && <span className="faint">pág. {r.reference.page_from}</span>}{" "}
                {r.suspicious && <Badge tone="warn">sospechoso</Badge>}
              </li>
            ))}
          </ul>
        </details>
      )}
      {onShared && run.mine !== false && run.status === "done" && !run.shared && (
        <button className="small" onClick={() => setSharing(true)}>
          Compartir con un área o proyecto…
        </button>
      )}
      {sharing && (
        <ShareDialog
          run={run}
          onClose={() => setSharing(false)}
          onDone={(r) => {
            setSharing(false);
            onShared?.(r);
          }}
        />
      )}
    </div>
  );
}

function ShareDialog({ run, onClose, onDone }: { run: Run; onClose: () => void; onDone: (r: Run) => void }) {
  const me = useSession((s) => s.me)!;
  const areas = useApi<{ areas: Area[] }>("/api/areas");
  const [areaId, setAreaId] = useState(run.area_id ?? me.active_area?.id ?? "");
  const [projectId, setProjectId] = useState(run.project_id ?? "");
  const projects = useApi<ProjectRow[]>(areaId ? `/api/projects?area_id=${areaId}` : null);
  return (
    <Modal title="Compartir resultado" onClose={onClose}>
      <div className="stack">
        <p className="small muted">
          Los miembros del área podrán ver la pregunta y la respuesta. El Memory Agent lo registrará como
          inferencia (no como hecho confirmado).
        </p>
        <select value={areaId} onChange={(e) => setAreaId(e.target.value)} aria-label="Área">
          {areas.data?.areas
            .filter((a) => a.my_role !== "viewer")
            .map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
        </select>
        <select value={projectId} onChange={(e) => setProjectId(e.target.value)} aria-label="Proyecto">
          <option value="">Sin proyecto</option>
          {projects.data?.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
        <button
          className="primary"
          disabled={!areaId}
          onClick={async () => {
            try {
              const r = await post<Run>(`/api/ai/runs/${run.id}/share`, {
                area_id: areaId,
                project_id: projectId || null,
              });
              toast("Resultado compartido", "ok");
              onDone(r);
            } catch (err) {
              toast((err as Error).message, "err");
            }
          }}
        >
          Compartir
        </button>
      </div>
    </Modal>
  );
}

function History({ focus }: { focus: string | null }) {
  const { data } = useApi<Run[]>("/api/ai/runs", { refreshOn: ["activity.new", "ai.changed"] });
  const [open, setOpen] = useState<string | null>(focus);
  const detail = useApi<Run>(open ? `/api/ai/runs/${open}` : null);
  if (!data) return <Spinner />;
  if (!data.length) return <EmptyState title="Sin ejecuciones" />;
  return (
    <div className="grid" style={{ gridTemplateColumns: "minmax(260px, 1fr) 2fr" }}>
      <div className="panel">
        <ul className="list">
          {data.map((r) => (
            <li key={r.id}>
              <button className="link" onClick={() => setOpen(r.id)} style={{ textAlign: "left" }}>
                {r.prompt.slice(0, 90)}
              </button>
              <div className="small faint">
                {r.mine ? "mía" : "compartida"} · {RUN_STATUS[r.status] ?? r.status} · {timeAgo(r.created_at)}
              </div>
            </li>
          ))}
        </ul>
      </div>
      <div className="panel">
        {!open && <p className="muted">Seleccione una ejecución.</p>}
        {open && !detail.data && <Spinner />}
        {detail.data && (
          <>
            <h3>{detail.data.prompt.slice(0, 160)}</h3>
            {!detail.data.mine && <p className="small muted">Compartida por {detail.data.author}</p>}
            <RunView run={detail.data} onShared={() => detail.reload()} />
          </>
        )}
      </div>
    </div>
  );
}

function InternalAgents() {
  const office = useApi<OfficeState>("/api/office/state", { refreshOn: ["agent.changed"] });
  if (!office.data) return <Spinner />;
  return (
    <div className="grid cols-3">
      {office.data.agents.map((a) => (
        <div key={a.key} className="panel">
          <div className="row">
            <strong>{AGENT_NAMES[a.key] ?? a.name}</strong>
            <Badge tone={a.status === "error" ? "err" : a.status === "working" ? "info" : "ok"}>
              {AGENT_STATUS[a.status] ?? a.status}
            </Badge>
          </div>
          <p className="small muted">Latido {timeAgo(a.heartbeat_at)}</p>
          {a.current && <p className="small">Ahora: {a.current}</p>}
          <p className="small">{a.last_result || "Sin resultados registrados."}</p>
        </div>
      ))}
      {office.data.agents.length === 0 && <EmptyState title="Ningún agente ha reportado estado todavía" />}
    </div>
  );
}

function ImportResult() {
  const [form, setForm] = useState({ prompt: "", output: "", source: "claude.ai" });
  const [done, setDone] = useState<Run | null>(null);
  return (
    <div className="grid cols-2">
      <form
        className="panel stack"
        onSubmit={async (e) => {
          e.preventDefault();
          try {
            setDone(await post<Run>("/api/ai/runs/import", form));
            setForm({ prompt: "", output: "", source: form.source });
            toast("Resultado importado", "ok");
          } catch (err) {
            toast((err as Error).message, "err");
          }
        }}
      >
        <p className="small muted">
          Modo compatible para su suscripción personal: copie aquí una conversación o resultado obtenido en la
          interfaz oficial. Se guarda como «importado manualmente» y es privado hasta que lo comparta.
        </p>
        <label className="field">
          Origen
          <select value={form.source} onChange={(e) => setForm({ ...form, source: e.target.value })}>
            <option value="claude.ai">claude.ai</option>
            <option value="claude-code">Claude Code</option>
            <option value="claude-desktop">Claude Desktop</option>
          </select>
        </label>
        <label className="field">
          Pregunta
          <textarea
            value={form.prompt}
            onChange={(e) => setForm({ ...form, prompt: e.target.value })}
            required
          />
        </label>
        <label className="field">
          Resultado
          <textarea
            value={form.output}
            onChange={(e) => setForm({ ...form, output: e.target.value })}
            required
            style={{ minHeight: 200 }}
          />
        </label>
        <button className="primary">Importar</button>
      </form>
      <div className="panel">
        {done ? (
          <RunView run={{ ...done, mine: true }} onShared={(r) => setDone({ ...done, ...r })} />
        ) : (
          <p className="muted">Vista previa del resultado importado.</p>
        )}
      </div>
    </div>
  );
}
