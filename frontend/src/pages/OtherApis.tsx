import { useState, type FormEvent } from "react";
import { del, patch, post, put } from "../api/client";
import { Badge, ConfirmDialog, ErrorBox, Spinner } from "../components/ui";
import { formatDateTime } from "../lib/format";
import { toast } from "../lib/toast";
import { useApi } from "../lib/useApi";
import { CRED_STATUS as STATUS } from "../lib/labels";
import type { Credential, CredentialsResponse, ProviderMeta } from "./ClaudeConnections";

/** APIs de IA distintas de Claude, cada una con la clave propia del socio. */
export function OtherApis() {
  const { data, error, reload } = useApi<CredentialsResponse>("/api/ai/credentials", {
    refreshOn: ["ai.changed"],
  });
  if (error) return <ErrorBox error={error} onRetry={() => reload()} />;
  if (!data) return <Spinner />;
  const mine = Object.fromEntries(data.providers.map((c) => [c.provider, c]));
  return (
    <div className="grid cols-2" data-testid="other-apis">
      {data.catalog
        .filter((m) => m.key !== "anthropic_api")
        .map((meta) => (
          <ProviderCard key={meta.key} meta={meta} cred={mine[meta.key] ?? null} onChange={reload} />
        ))}
    </div>
  );
}

function ProviderCard({
  meta,
  cred,
  onChange,
}: {
  meta: ProviderMeta;
  cred: Credential | null;
  onChange: () => void;
}) {
  const [key, setKey] = useState("");
  const [baseUrl, setBaseUrl] = useState(cred?.base_url ?? "");
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [label, tone] = STATUS[cred?.status ?? "not_configured"] ?? [cred?.status ?? "", "info"];
  const connected = cred && cred.status !== "revoked";

  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setFormError(null);
    try {
      const res = await put<Credential>("/api/ai/credentials", {
        provider: meta.key,
        api_key: key.trim(),
        base_url: meta.needs_base_url ? baseUrl.trim() : "",
      });
      setKey("");
      toast(
        res.status === "verified"
          ? `Conexión verificada con ${meta.label}`
          : `No se pudo verificar: ${res.last_error}`,
        res.status === "verified" ? "ok" : "err",
      );
      onChange();
    } catch (err) {
      setFormError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="panel" data-testid={`api-${meta.key}`}>
      <div className="panel-title">
        <h3>{meta.label}</h3>
        <Badge tone={tone}>{label}</Badge>
      </div>
      <p className="small muted">
        {meta.note}{" "}
        {meta.console_url && (
          <>
            Clave en{" "}
            <a href={meta.console_url} target="_blank" rel="noreferrer">
              {meta.console_label}
            </a>
            .
          </>
        )}
      </p>
      {connected && (
        <dl className="kv" style={{ marginBottom: 12 }}>
          <dt>Clave</dt>
          <dd className="mono">{cred.key_hint}</dd>
          {cred.base_url && (
            <>
              <dt>Dirección</dt>
              <dd className="small mono">{cred.base_url}</dd>
            </>
          )}
          <dt>Verificada</dt>
          <dd>{formatDateTime(cred.verified_at)}</dd>
          {cred.last_error && (
            <>
              <dt>Último error</dt>
              <dd style={{ color: "var(--err)" }}>{cred.last_error}</dd>
            </>
          )}
          {cred.models.length > 0 && (
            <>
              <dt>Modelo por defecto</dt>
              <dd>
                <select
                  value={cred.default_model}
                  style={{ maxWidth: 280 }}
                  aria-label={`Modelo por defecto de ${meta.label}`}
                  onChange={async (e) => {
                    try {
                      await patch("/api/ai/credentials", {
                        provider: meta.key,
                        default_model: e.target.value,
                      });
                      onChange();
                    } catch (err) {
                      toast((err as Error).message, "err");
                    }
                  }}
                >
                  {cred.models.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.display_name}
                    </option>
                  ))}
                </select>
              </dd>
            </>
          )}
        </dl>
      )}
      <form className="stack" onSubmit={save}>
        <ErrorBox error={formError} />
        {meta.needs_base_url && (
          <label className="field">
            Dirección de la API
            <input
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="https://openrouter.ai/api/v1"
              required
            />
            <span className="small muted">
              Debe ser https:// (OpenRouter, Groq, Mistral…). Un administrador puede usar http://127.0.0.1
              para Ollama o LM Studio en el equipo de WADIMA.
            </span>
          </label>
        )}
        <label className="field">
          {connected ? "Sustituir clave" : `API key de ${meta.label}`}
          <input
            type="password"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            placeholder={meta.key_placeholder}
            autoComplete="off"
            required
          />
        </label>
        <div className="row">
          <button className="primary" disabled={busy}>
            {busy ? "Verificando…" : "Guardar y verificar"}
          </button>
          {connected && (
            <>
              <button
                type="button"
                onClick={async () => {
                  try {
                    const res = await post<Credential>(`/api/ai/credentials/test?provider=${meta.key}`);
                    toast(
                      res.status === "verified" ? "Conexión correcta" : res.last_error,
                      res.status === "verified" ? "ok" : "err",
                    );
                    onChange();
                  } catch (err) {
                    toast((err as Error).message, "err");
                  }
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
      {confirm && (
        <ConfirmDialog
          title={`Desconectar ${meta.label}`}
          message={`La clave se borrará de WADIMA. Para invalidarla también en ${meta.label}, revóquela en su panel.`}
          danger
          confirmLabel="Desconectar"
          onClose={() => setConfirm(false)}
          onConfirm={async () => {
            await del(`/api/ai/credentials?provider=${meta.key}`);
            onChange();
          }}
        />
      )}
    </div>
  );
}
