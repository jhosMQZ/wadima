import { useState, type FormEvent } from "react";
import { del, patch, post, put } from "../api/client";
import { Badge, ConfirmDialog, ErrorBox, Spinner } from "../components/ui";
import { formatDateTime } from "../lib/format";
import { CRED_STATUS as STATUS } from "../lib/labels";
import { toast } from "../lib/toast";
import { useApi } from "../lib/useApi";

export interface Credential {
  provider: string;
  provider_label: string;
  base_url: string | null;
  label: string;
  key_hint: string;
  status: string;
  default_model: string;
  models: { id: string; display_name: string }[];
  organization_id: string | null;
  rate_limits: Record<string, string>;
  verified_at: string | null;
  last_error: string;
}

export interface ProviderMeta {
  key: string;
  label: string;
  short: string;
  key_placeholder: string;
  console_url: string;
  console_label: string;
  supports_effort: boolean;
  supports_count_tokens: boolean;
  needs_base_url: boolean;
  note: string;
}

export interface CredentialsResponse {
  /** Conexión con Claude (Anthropic), por compatibilidad. */
  mine: Credential | null;
  /** Todas mis conexiones (cualquier proveedor), sin las eliminadas. */
  providers: Credential[];
  catalog: ProviderMeta[];
  partners: { user_id: string; display_name: string; status: string; apis: string[] }[];
}

export function ClaudeConnections() {
  const { data, error, reload } = useApi<CredentialsResponse>("/api/ai/credentials", {
    refreshOn: ["ai.changed"],
  });
  const tools = useApi<{ claude_code: { installed: boolean; version: string | null; note: string } }>(
    "/api/ai/local-tools",
  );
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);

  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setFormError(null);
    try {
      const res = await put<Credential>("/api/ai/credentials", { api_key: key.trim() });
      setKey("");
      toast(
        res.status === "verified"
          ? "Conexión verificada con Anthropic"
          : `No se pudo verificar: ${res.last_error}`,
        res.status === "verified" ? "ok" : "err",
      );
      reload();
    } catch (err) {
      setFormError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (error) return <ErrorBox error={error} onRetry={() => reload()} />;
  if (!data) return <Spinner />;
  const mine = data.mine;
  const [label, tone] = STATUS[mine?.status ?? "not_configured"];
  return (
    <div className="grid cols-2">
      <div className="panel">
        <div className="panel-title">
          <h3>Mi conexión con Claude</h3>
          <Badge tone={tone}>{label}</Badge>
        </div>
        <p className="small muted">
          Cada socio conecta <strong>su propia API key</strong> de Anthropic (Claude Console). Se guarda
          cifrada, nunca se muestra de nuevo y solo se usa para sus propias peticiones. La suscripción de
          claude.ai (Pro/Max/Team) <strong>no incluye</strong> acceso a la API: la API se factura aparte en{" "}
          <a href="https://platform.claude.com/settings/keys" target="_blank" rel="noreferrer">
            platform.claude.com
          </a>
          .
        </p>
        {mine && mine.status !== "revoked" && (
          <dl className="kv" style={{ marginBottom: 12 }}>
            <dt>Clave</dt>
            <dd className="mono">sk-ant-…{mine.key_hint.replace("…", "")}</dd>
            <dt>Organización</dt>
            <dd className="small mono">{mine.organization_id ?? "—"}</dd>
            <dt>Verificada</dt>
            <dd>{formatDateTime(mine.verified_at)}</dd>
            {mine.last_error && (
              <>
                <dt>Último error</dt>
                <dd style={{ color: "var(--err)" }}>{mine.last_error}</dd>
              </>
            )}
            <dt>Modelo por defecto</dt>
            <dd>
              <select
                value={mine.default_model}
                style={{ width: 240 }}
                aria-label="Modelo por defecto"
                onChange={async (e) => {
                  try {
                    await patch("/api/ai/credentials", { default_model: e.target.value });
                    reload();
                  } catch (err) {
                    toast((err as Error).message, "err");
                  }
                }}
              >
                {(mine.models.length
                  ? mine.models
                  : [{ id: mine.default_model, display_name: mine.default_model }]
                ).map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.display_name}
                  </option>
                ))}
              </select>
            </dd>
            {Object.keys(mine.rate_limits).length > 0 && (
              <>
                <dt>Límites (último dato)</dt>
                <dd className="small mono">
                  {Object.entries(mine.rate_limits)
                    .filter(([k]) => k.includes("remaining"))
                    .map(([k, v]) => `${k.replace("anthropic-ratelimit-", "")}: ${v}`)
                    .join(" · ") || "—"}
                </dd>
              </>
            )}
          </dl>
        )}
        <form className="stack" onSubmit={save}>
          <ErrorBox error={formError} />
          <label className="field">
            {mine && mine.status !== "revoked" ? "Sustituir API key" : "API key de Anthropic"}
            <input
              type="password"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder="sk-ant-api03-…"
              autoComplete="off"
              required
              minLength={20}
            />
          </label>
          <div className="row">
            <button className="primary" disabled={busy}>
              {busy ? "Verificando…" : "Guardar y verificar"}
            </button>
            {mine && mine.status !== "revoked" && (
              <>
                <button
                  type="button"
                  onClick={async () => {
                    const res = await post<Credential>("/api/ai/credentials/test");
                    toast(
                      res.status === "verified" ? "Conexión correcta" : res.last_error,
                      res.status === "verified" ? "ok" : "err",
                    );
                    reload();
                  }}
                >
                  Probar conexión
                </button>
                <button type="button" className="danger" onClick={() => setConfirm(true)}>
                  Desconectar
                </button>
              </>
            )}
          </div>
        </form>
      </div>
      <div className="stack">
        <div className="panel">
          <h3>Conexiones de los socios</h3>
          <p className="small muted">
            Estado de Claude y de las demás APIs verificadas. Solo se muestra el estado; ningún socio puede
            ver ni usar la cuenta de otro.
          </p>
          <ul className="list">
            {data.partners.map((p) => {
              const [l, t] = STATUS[p.status] ?? [p.status, "info"];
              return (
                <li key={p.user_id} className="row">
                  <span style={{ flex: 1 }}>{p.display_name}</span>
                  {p.apis.length > 0 && <span className="small muted">{p.apis.join(" · ")}</span>}
                  <Badge tone={t}>{l}</Badge>
                </li>
              );
            })}
          </ul>
        </div>
        <div className="panel">
          <h3>Otros modos de uso</h3>
          <ul className="small">
            <li>
              <strong>Suscripción Pro/Max</strong>: se usa desde Claude Code conectando WADIMA como servidor
              MCP (sección superior). WADIMA no inicia sesión por usted ni reutiliza cookies o tokens de
              claude.ai.
            </li>
            <li>
              <strong>Claude Code en el servidor</strong>:{" "}
              {tools.data?.claude_code.installed
                ? `instalado (${tools.data.claude_code.version ?? "versión desconocida"}). `
                : "no detectado. "}
              {tools.data?.claude_code.note}
            </li>
          </ul>
        </div>
      </div>
      {confirm && (
        <ConfirmDialog
          title="Desconectar Claude"
          message="La API key se borrará de WADIMA. Para invalidarla también en Anthropic, revóquela en platform.claude.com."
          danger
          confirmLabel="Desconectar"
          onClose={() => setConfirm(false)}
          onConfirm={async () => {
            await del("/api/ai/credentials");
            reload();
          }}
        />
      )}
    </div>
  );
}
