import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { del, post } from "../api/client";
import type { Area } from "../api/types";
import { Badge, ConfirmDialog, EmptyState, ErrorBox, Spinner } from "../components/ui";
import { formatDateTime, timeAgo } from "../lib/format";
import { RUN_STATUS } from "../lib/labels";
import { toast } from "../lib/toast";
import { useApi } from "../lib/useApi";
import { ClaudeConnections } from "./ClaudeConnections";
import { OtherApis } from "./OtherApis";

interface McpToken {
  id: string;
  name: string;
  hint: string;
  created_at: string;
  last_used_at: string | null;
  calls: number;
  client: { name?: string; version?: string; user_agent?: string; protocol?: string; at?: string };
}

interface Options {
  api_key: { status: string; verified_at: string | null; key_hint: string | null };
  subscription_mcp: {
    enabled: boolean;
    url: string;
    status: "connected" | "used_before" | "tokens_only" | "not_configured";
    last_used_at: string | null;
    client: McpToken["client"];
    tokens: McpToken[];
  };
  manual_import: { status: string };
  other_apis: { provider: string; label: string; status: string; key_hint: string | null }[];
}

interface RunRow {
  id: string;
  provider: string;
  prompt: string;
  status: string;
  created_at: string;
  mine?: boolean;
}

const MCP_STATUS: Record<Options["subscription_mcp"]["status"], [string, string]> = {
  connected: ["conectado (petición real recibida)", "ok"],
  used_before: ["usado antes, sin actividad reciente", "warn"],
  tokens_only: ["token creado, ningún cliente lo ha usado aún", "warn"],
  not_configured: ["sin configurar", "muted"],
};

const API_STATUS: Record<string, [string, string]> = {
  verified: ["verificada", "ok"],
  unverified: ["sin verificar", "warn"],
  error: ["error", "err"],
  revoked: ["eliminada", "muted"],
  not_configured: ["sin configurar", "muted"],
};

export default function AISettingsPage() {
  const opts = useApi<Options>("/api/ai/options", { refreshOn: ["ai.changed"] });
  return (
    <>
      <div className="page-head">
        <div className="grow">
          <h1>Configuración de IA</h1>
          <p>
            Cada socio conecta su propia cuenta de Claude u otras IA (OpenAI, Gemini, DeepSeek o APIs
            compatibles). WADIMA funciona sin IA y nunca comparte credenciales entre socios.
          </p>
        </div>
      </div>
      <ErrorBox error={opts.error} onRetry={() => opts.reload()} />
      {!opts.data && !opts.error && <Spinner />}
      {opts.data && (
        <>
          <Alternatives opts={opts.data} />
          <SubscriptionPanel opts={opts.data} onChange={() => opts.reload()} />
          <h2>API de Anthropic (clave propia)</h2>
          <ClaudeConnections />
          <h2>Otras APIs de IA (clave propia)</h2>
          <OtherApis />
        </>
      )}
    </>
  );
}

function Alternatives({ opts }: { opts: Options }) {
  const [mcpLabel, mcpTone] = MCP_STATUS[opts.subscription_mcp.status];
  const [apiLabel, apiTone] = API_STATUS[opts.api_key.status] ?? [opts.api_key.status, "info"];
  return (
    <div className="panel" data-testid="ai-options">
      <h3>Opciones disponibles y sus requisitos</h3>
      <div className="table-wrap">
        <table className="table options-table">
          <thead>
            <tr>
              <th>Opción</th>
              <th>Qué necesita</th>
              <th>Qué permite en WADIMA</th>
              <th>Coste</th>
              <th>Mi estado</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>
                <strong>Suscripción Claude Pro/Max</strong> a través de Claude Code (MCP)
              </td>
              <td className="small">
                Suscripción activa; Claude Code instalado en su equipo e iniciado con su cuenta (flujo oficial
                de Anthropic); acceso de red a WADIMA (misma red o VPN); un token personal de WADIMA.
              </td>
              <td className="small">
                Desde Claude Code, Claude consulta la memoria, documentos, proyectos y tareas de sus áreas,
                recoge las tareas que usted le encarga aquí y entrega el resultado. El modelo se elige en
                Claude Code.
              </td>
              <td className="small">Incluido en su plan (con sus límites de uso).</td>
              <td>
                <Badge tone={mcpTone}>{mcpLabel}</Badge>
              </td>
            </tr>
            <tr>
              <td>
                <strong>API de Anthropic</strong> (Claude Console)
              </td>
              <td className="small">API key propia creada en platform.claude.com.</td>
              <td className="small">
                WADIMA envía sus preguntas con contexto de la memoria, mide tokens y puede usarla para el
                Memory Agent si usted lo permite.
              </td>
              <td className="small">Pago por uso, aparte de la suscripción.</td>
              <td>
                <Badge tone={apiTone}>{apiLabel}</Badge>
              </td>
            </tr>
            <tr>
              <td>
                <strong>Otras APIs</strong> (OpenAI, Google Gemini, DeepSeek o compatibles con OpenAI)
              </td>
              <td className="small">
                API key propia del servicio. Las compatibles (OpenRouter, Groq, Mistral…) necesitan además su
                dirección https; Ollama o LM Studio en el equipo de WADIMA, un administrador.
              </td>
              <td className="small">
                Las mismas preguntas con contexto de la memoria que con Claude. El recuento de tokens sin
                generar respuesta solo existe en Anthropic y Gemini.
              </td>
              <td className="small">
                Pago por uso en cada servicio (las suscripciones de chat no incluyen la API).
              </td>
              <td>
                <span className="stack" style={{ gap: 4 }}>
                  {opts.other_apis.map((a) => {
                    const [l, t] = API_STATUS[a.status] ?? [a.status, "info"];
                    return (
                      <span key={a.provider} className="small">
                        {a.label}: <Badge tone={t}>{l}</Badge>
                      </span>
                    );
                  })}
                </span>
              </td>
            </tr>
            <tr>
              <td>
                <strong>Importación manual</strong>
              </td>
              <td className="small">Ninguno.</td>
              <td className="small">
                Pegar en <Link to="/agents">Laboratorio de IA</Link> un resultado obtenido en claude.ai o
                Claude Code.
              </td>
              <td className="small">—</td>
              <td>
                <Badge tone="ok">disponible</Badge>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <h4>Lo que no se ofrece, y por qué</h4>
      <ul className="small">
        <li>
          <strong>Iniciar sesión con claude.ai dentro de WADIMA</strong> o hacer que el servidor use su
          suscripción por usted: las condiciones de Anthropic no permiten que aplicaciones de terceros
          ofrezcan el inicio de sesión de claude.ai ni enruten peticiones con credenciales de planes Free, Pro
          o Max, ni almacenen esos tokens. Tampoco se extraen cookies ni se automatiza la web de claude.ai.
        </li>
        <li>
          <strong>Conectores personalizados de claude.ai</strong> (web o Claude Desktop): Anthropic se conecta
          a ellos desde su nube, así que exigirían publicar WADIMA en internet. Esta instalación es privada;
          use Claude Code, que se conecta desde su propio equipo.
        </li>
        <li>
          Una suscripción Pro <strong>no incluye</strong> acceso a la API: la API se factura aparte.
        </li>
      </ul>
    </div>
  );
}

function SubscriptionPanel({ opts, onChange }: { opts: Options; onChange: () => void }) {
  const mcp = opts.subscription_mcp;
  const [created, setCreated] = useState<{ token: string; url: string } | null>(null);
  const [name, setName] = useState("Claude Code");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [revoke, setRevoke] = useState<McpToken | null>(null);
  const command = created
    ? `claude mcp add --transport http wadima ${created.url} --header "Authorization: Bearer ${created.token}"`
    : "";
  return (
    <div className="grid cols-2">
      <div className="panel" data-testid="mcp-panel">
        <div className="panel-title">
          <h3>Suscripción de Claude vía MCP</h3>
          <Badge tone={MCP_STATUS[mcp.status][1]}>{MCP_STATUS[mcp.status][0]}</Badge>
        </div>
        {!mcp.enabled && (
          <div className="alert warn">El servidor MCP está deshabilitado en esta instalación.</div>
        )}
        <ol className="small">
          <li>
            Instale Claude Code en su equipo e inicie sesión con su cuenta Pro/Max (<code>claude</code> →
            «Claude account»).
          </li>
          <li>Cree aquí un token personal (solo se muestra una vez).</li>
          <li>
            Ejecute en su terminal el comando que aparecerá; después, en Claude Code, use <code>/mcp</code>{" "}
            para comprobar la conexión.
          </li>
          <li>WADIMA marca la conexión como activa solo cuando recibe una petición real de su cliente.</li>
        </ol>
        {mcp.status === "connected" || mcp.status === "used_before" ? (
          <dl className="kv">
            <dt>Cliente</dt>
            <dd>
              {mcp.client.name
                ? `${mcp.client.name} ${mcp.client.version ?? ""}`
                : mcp.client.user_agent || "desconocido"}
            </dd>
            <dt>Última petición</dt>
            <dd>{timeAgo(mcp.last_used_at)}</dd>
          </dl>
        ) : null}
        <form
          className="row wrap"
          onSubmit={async (e: FormEvent) => {
            e.preventDefault();
            setBusy(true);
            setError(null);
            try {
              const res = await post<McpToken & { token: string; url: string }>("/api/ai/mcp-tokens", {
                name,
              });
              setCreated({ token: res.token, url: res.url });
              onChange();
            } catch (err) {
              setError((err as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          <label className="field inline">
            Nombre del dispositivo
            <input value={name} onChange={(e) => setName(e.target.value)} maxLength={80} required />
          </label>
          <button className="primary" disabled={busy || !mcp.enabled}>
            Crear token
          </button>
        </form>
        <ErrorBox error={error} />
        {created && (
          <div className="alert ok stack" data-testid="mcp-token-created">
            <span>Copie el comando ahora: el token no se volverá a mostrar.</span>
            <pre className="mono small copyable">{command}</pre>
            <button
              type="button"
              className="small"
              onClick={() => {
                void navigator.clipboard?.writeText(command).then(
                  () => toast("Comando copiado", "ok"),
                  () => toast("No se pudo copiar; selecciónelo manualmente", "err"),
                );
              }}
            >
              Copiar comando
            </button>
            <span className="small muted">
              Para compartir la configuración sin el token, use un <code>.mcp.json</code> con{" "}
              <code>{'"headers": {"Authorization": "Bearer ${WADIMA_TOKEN}"}'}</code> y defina WADIMA_TOKEN en
              su equipo.
            </span>
          </div>
        )}
        {mcp.tokens.length > 0 && (
          <ul className="list" style={{ marginTop: 8 }}>
            {mcp.tokens.map((t) => (
              <li key={t.id} className="row">
                <span style={{ flex: 1 }}>
                  {t.name} <span className="mono small">{t.hint}</span>
                  <span className="small muted" style={{ display: "block" }}>
                    creado {formatDateTime(t.created_at)} ·{" "}
                    {t.last_used_at
                      ? `${t.calls} petición(es), última ${timeAgo(t.last_used_at)}`
                      : "sin usar"}
                  </span>
                </span>
                <button className="small danger" onClick={() => setRevoke(t)}>
                  Revocar
                </button>
              </li>
            ))}
          </ul>
        )}
        <p className="small muted">
          Dirección del servidor MCP: <code>{mcp.url}</code>. Desde otro equipo debe ser la dirección privada
          o de VPN de WADIMA (no localhost).
        </p>
      </div>
      <TaskQueue />
      {revoke && (
        <ConfirmDialog
          title="Revocar token"
          message={`El cliente «${revoke.name}» dejará de poder consultar WADIMA inmediatamente.`}
          danger
          confirmLabel="Revocar"
          onClose={() => setRevoke(null)}
          onConfirm={async () => {
            await del(`/api/ai/mcp-tokens/${revoke.id}`);
            onChange();
          }}
        />
      )}
    </div>
  );
}

function TaskQueue() {
  const runs = useApi<RunRow[]>("/api/ai/runs", { refreshOn: ["ai.changed"] });
  const areas = useApi<{ areas: Area[] }>("/api/areas");
  const [prompt, setPrompt] = useState("");
  const [areaId, setAreaId] = useState("");
  const [memory, setMemory] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mine = (runs.data ?? []).filter((r) => r.provider === "claude_mcp" && r.mine);
  return (
    <div className="panel" data-testid="mcp-tasks">
      <h3>Tareas para su Claude</h3>
      <p className="small muted" style={{ marginTop: 0 }}>
        Encargue un trabajo; su Claude Code lo recoge con la herramienta <code>list_pending_tasks</code> y
        entrega el resultado con <code>submit_result</code>. El resultado es privado hasta que lo comparta.
      </p>
      <form
        className="stack"
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setError(null);
          try {
            await post("/api/ai/tasks", { prompt, area_id: areaId || null, use_memory: memory });
            setPrompt("");
            toast("Tarea en cola: pídale a Claude Code que atienda sus tareas de WADIMA", "ok");
            runs.reload();
          } catch (err) {
            setError((err as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <label className="field">
          Encargo
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            rows={3}
            required
            minLength={2}
            maxLength={20000}
          />
        </label>
        <div className="row wrap">
          <label className="field inline">
            Área
            <select value={areaId} onChange={(e) => setAreaId(e.target.value)}>
              <option value="">Todas mis áreas</option>
              {areas.data?.areas
                .filter((a) => a.enabled)
                .map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
            </select>
          </label>
          <label className="row small">
            <input type="checkbox" checked={memory} onChange={(e) => setMemory(e.target.checked)} /> Incluir
            contexto de la memoria
          </label>
          <button className="primary" disabled={busy}>
            Encargar
          </button>
        </div>
        <ErrorBox error={error} />
      </form>
      {mine.length === 0 ? (
        <EmptyState title="Sin tareas" />
      ) : (
        <ul className="list" style={{ marginTop: 8 }}>
          {mine.slice(0, 10).map((r) => (
            <li key={r.id} className="row">
              <span style={{ flex: 1 }}>
                {r.prompt.slice(0, 80)}
                <span className="small muted" style={{ display: "block" }}>
                  {RUN_STATUS[r.status] ?? r.status} · {timeAgo(r.created_at)}
                </span>
              </span>
              {r.status === "done" || r.status === "error" ? (
                <Link className="small" to={`/agents?run=${r.id}`}>
                  Ver resultado
                </Link>
              ) : r.status === "queued" || r.status === "claimed" ? (
                <button
                  className="small ghost"
                  onClick={async () => {
                    await post(`/api/ai/tasks/${r.id}/cancel`);
                    runs.reload();
                  }}
                >
                  Cancelar
                </button>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
