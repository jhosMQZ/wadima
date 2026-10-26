import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { post } from "../api/client";
import { Badge, ConfirmDialog, EmptyState, ErrorBox, Spinner, Tabs } from "../components/ui";
import { formatDateTime, timeAgo } from "../lib/format";
import { toast } from "../lib/toast";
import { useApi } from "../lib/useApi";
import { useSession } from "../store/session";
import { ClaudeConnections } from "./ClaudeConnections";
import { CONNECTION_STATUS, label } from "../lib/labels";

interface DriveStatus {
  configured: boolean;
  redirect_uri: string;
  scope: string;
  can_manage: boolean;
  counts: Record<string, number>;
  connection: {
    id: string;
    status: string;
    google_email: string | null;
    google_name: string | null;
    root_folder_id: string | null;
    folders: Record<string, string>;
    last_error: string;
    last_checked_at: string | null;
    last_sync_at: string | null;
    created_at: string;
  } | null;
}

interface SyncFile {
  id: string;
  local_kind: string;
  name: string;
  folder_key: string;
  sync_state: string;
  drive_file_id: string | null;
  attempts: number;
  last_error: string;
  conflict: Record<string, string>;
  last_synced_at: string | null;
  updated_at: string;
  web_link: string | null;
}

const STATE_TONE: Record<string, string> = {
  synced: "ok",
  pending: "warn",
  uploading: "info",
  error: "err",
  conflict: "conflict",
  remote_deleted: "conflict",
};
const STATE_LABEL: Record<string, string> = {
  synced: "sincronizado",
  pending: "pendiente",
  uploading: "subiendo",
  error: "error",
  conflict: "conflicto",
  remote_deleted: "borrado en Drive",
};
const KIND_LABEL: Record<string, string> = {
  note: "Documento Markdown",
  document_original: "Original de biblioteca",
  document_markdown: "Markdown convertido",
  report: "Informe",
  memory_export: "Memoria consolidada",
  backup: "Copia lógica",
  audit_anchor: "Ancla de auditoría",
};

const DRIVE_MESSAGES: Record<string, [string, "ok" | "err" | "info"]> = {
  connected: ["Google Drive conectado y verificado", "ok"],
  denied: ["Se canceló la autorización en Google", "info"],
  invalid_state: ["El enlace de autorización no es válido o caducó; vuelva a intentarlo", "err"],
  error: ["Google rechazó la autorización; revise las credenciales OAuth", "err"],
};

export default function ConnectionsPage() {
  const [params, setParams] = useSearchParams();
  const [tab, setTab] = useState<"drive" | "claude">("drive");
  useEffect(() => {
    const d = params.get("drive");
    if (d && DRIVE_MESSAGES[d]) {
      toast(...DRIVE_MESSAGES[d]);
      setParams({}, { replace: true });
    }
  }, [params, setParams]);
  return (
    <>
      <div className="page-head">
        <div className="grow">
          <h1>Servidores e integraciones</h1>
          <p>Almacenamiento central en Google Drive y conexiones de cada socio con Claude.</p>
        </div>
      </div>
      <Tabs
        tabs={[
          { key: "drive", label: "Google Drive" },
          { key: "claude", label: "Claude" },
        ]}
        value={tab}
        onChange={setTab}
      />
      {tab === "drive" ? <DriveSection /> : <ClaudeConnections />}
    </>
  );
}

function DriveSection() {
  const me = useSession((s) => s.me)!;
  const isAdmin = me.user.role === "admin";
  const { data, error, reload } = useApi<DriveStatus>("/api/drive/status", {
    refreshOn: ["drive.changed", "sync.changed"],
  });
  const [filter, setFilter] = useState("");
  const files = useApi<SyncFile[]>(`/api/drive/files${filter ? `?state=${filter}` : ""}`, {
    refreshOn: ["sync.changed"],
  });
  const [testResult, setTestResult] = useState<{
    ok: boolean;
    steps: { step: string; ok: boolean; detail: string }[];
  } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);

  async function run(label: string, fn: () => Promise<void>) {
    setBusy(label);
    try {
      await fn();
    } catch (err) {
      toast((err as Error).message, "err");
    } finally {
      setBusy(null);
    }
  }

  if (error) return <ErrorBox error={error} onRetry={() => reload()} />;
  if (!data) return <Spinner />;
  const c = data.connection;
  return (
    <>
      <div className="grid cols-2">
        <div className="panel">
          <div className="panel-title">
            <h3>Unidad central</h3>
            {c ? (
              <Badge tone={c.status === "connected" ? "ok" : c.status === "error" ? "err" : "muted"}>
                {label(CONNECTION_STATUS, c.status)}
              </Badge>
            ) : (
              <Badge tone="muted">sin conectar</Badge>
            )}
          </div>
          {!data.configured && (
            <div className="alert warn">
              Falta configurar las credenciales OAuth de Google en el servidor (
              <code>WADIMA_GOOGLE_CLIENT_ID</code> y <code>WADIMA_GOOGLE_CLIENT_SECRET</code> en{" "}
              <code>.env</code>). Cree un cliente OAuth en Google Cloud con la API de Drive habilitada y la
              URI de redirección <code>{data.redirect_uri}</code>.
            </div>
          )}
          {c ? (
            <dl className="kv">
              <dt>Cuenta</dt>
              <dd>
                {c.google_name} {c.google_email && <span className="muted">({c.google_email})</span>}
              </dd>
              <dt>Carpeta raíz</dt>
              <dd>
                {c.root_folder_id ? (
                  <a
                    href={`https://drive.google.com/drive/folders/${c.root_folder_id}`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    WADIMA/ en Google Drive
                  </a>
                ) : (
                  "—"
                )}
              </dd>
              <dt>Subcarpetas</dt>
              <dd className="small">
                {Object.keys(c.folders)
                  .filter((k) => k !== "_root")
                  .join(", ") || "—"}
              </dd>
              <dt>Permiso</dt>
              <dd className="small mono">{data.scope.split("/").pop()}</dd>
              <dt>Última comprobación</dt>
              <dd>{timeAgo(c.last_checked_at)}</dd>
              <dt>Última sincronización</dt>
              <dd>{timeAgo(c.last_sync_at)}</dd>
              {c.last_error && (
                <>
                  <dt>Último error</dt>
                  <dd className="small" style={{ color: "var(--err)" }}>
                    {c.last_error}
                  </dd>
                </>
              )}
            </dl>
          ) : (
            <p className="muted">
              Los documentos y el conocimiento compartido se guardan localmente y quedan{" "}
              <strong>pendientes</strong> hasta conectar la unidad central.
            </p>
          )}
          {isAdmin && (
            <div className="row" style={{ marginTop: 12 }}>
              <button
                className="primary"
                disabled={!data.configured || busy !== null}
                onClick={() =>
                  run("connect", async () => {
                    const res = await post<{ auth_url: string }>("/api/drive/connect");
                    window.location.assign(res.auth_url);
                  })
                }
              >
                {c ? "Reconectar" : "Conectar Google Drive"}
              </button>
              {c && (
                <>
                  <button
                    disabled={busy !== null}
                    onClick={() => run("test", async () => setTestResult(await post("/api/drive/test")))}
                  >
                    {busy === "test" ? "Probando…" : "Probar conexión"}
                  </button>
                  <button
                    disabled={busy !== null}
                    onClick={() =>
                      run("reconcile", async () => {
                        await post("/api/drive/reconcile");
                        reload();
                        toast("Reconciliación completada", "ok");
                      })
                    }
                  >
                    Reconciliar ahora
                  </button>
                  <button
                    disabled={busy !== null}
                    onClick={() =>
                      run("backup", async () => {
                        await post("/api/drive/backup");
                        toast("Copia lógica y ancla de auditoría en cola", "ok");
                      })
                    }
                  >
                    Copia de seguridad
                  </button>
                  <button className="danger" onClick={() => setConfirmDisconnect(true)}>
                    Desconectar
                  </button>
                </>
              )}
            </div>
          )}
          {!isAdmin && (
            <p className="small muted">
              Solo un administrador puede conectar o desconectar la unidad central.
            </p>
          )}
          {testResult && (
            <ul className="list" style={{ marginTop: 10 }}>
              {testResult.steps.map((s, i) => (
                <li key={i} className="row small">
                  <Badge tone={s.ok ? "ok" : "err"}>{s.ok ? "ok" : "fallo"}</Badge>
                  <strong>{s.step}</strong>
                  <span className="muted">{s.detail}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="panel">
          <h3>Estado de sincronización</h3>
          <div className="row" style={{ marginBottom: 10 }}>
            {Object.entries(data.counts).map(([k, v]) => (
              <Badge key={k} tone={STATE_TONE[k] ?? "info"}>
                {STATE_LABEL[k] ?? k}: {v}
              </Badge>
            ))}
            {Object.keys(data.counts).length === 0 && (
              <span className="muted small">Todavía no hay archivos registrados.</span>
            )}
          </div>
          <p className="small muted">
            Cada subida se verifica comparando el MD5 calculado por Google con el local. Los cambios
            simultáneos en WADIMA y en Drive se marcan como conflicto y nunca se sobrescriben en silencio.
            Drive conserva el historial de versiones.
          </p>
          {isAdmin && c?.status === "connected" && <PartnerAccess />}
        </div>
      </div>
      <div className="panel">
        <div className="panel-title">
          <h3>Archivos sincronizados</h3>
          <select
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            style={{ width: 200 }}
            aria-label="Filtrar estado"
          >
            <option value="">Todos los estados</option>
            {Object.entries(STATE_LABEL).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </div>
        {files.data?.length === 0 && <EmptyState title="Sin archivos en esta vista" />}
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Archivo</th>
                <th>Tipo</th>
                <th>Carpeta</th>
                <th>Estado</th>
                <th>Última sincronización</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {files.data?.map((f) => (
                <tr key={f.id}>
                  <td>
                    {f.web_link ? (
                      <a href={f.web_link} target="_blank" rel="noreferrer">
                        {f.name}
                      </a>
                    ) : (
                      f.name
                    )}
                    {f.last_error && (
                      <div className="small" style={{ color: "var(--err)" }}>
                        {f.last_error}
                      </div>
                    )}
                  </td>
                  <td className="small">{KIND_LABEL[f.local_kind] ?? f.local_kind}</td>
                  <td className="small mono">{f.folder_key}</td>
                  <td>
                    <Badge tone={STATE_TONE[f.sync_state]}>{STATE_LABEL[f.sync_state] ?? f.sync_state}</Badge>
                  </td>
                  <td className="small">{formatDateTime(f.last_synced_at)}</td>
                  <td>
                    {f.sync_state === "conflict" ? (
                      <ConflictActions file={f} onDone={() => files.reload()} />
                    ) : ["error", "pending", "remote_deleted"].includes(f.sync_state) ? (
                      <button
                        className="small"
                        onClick={async () => {
                          await post(`/api/drive/files/${f.id}/retry`);
                          files.reload();
                        }}
                      >
                        {f.sync_state === "remote_deleted" ? "Volver a subir" : "Reintentar"}
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      {isAdmin && <SyncLog />}
      {confirmDisconnect && (
        <ConfirmDialog
          title="Desconectar Google Drive"
          message="Se revocará el acceso de WADIMA en Google y se borrarán los tokens locales. Los archivos ya subidos permanecen en Drive; los cambios nuevos quedarán pendientes."
          danger
          confirmLabel="Desconectar"
          onClose={() => setConfirmDisconnect(false)}
          onConfirm={async () => {
            await post("/api/drive/disconnect");
            reload();
          }}
        />
      )}
    </>
  );
}

function ConflictActions({ file, onDone }: { file: SyncFile; onDone: () => void }) {
  const resolve = async (strategy: string) => {
    try {
      await post(`/api/drive/files/${file.id}/resolve`, { strategy });
      toast("Conflicto resuelto; se sincronizará en breve", "ok");
      onDone();
    } catch (err) {
      toast((err as Error).message, "err");
    }
  };
  return (
    <span className="row" title={`Remoto modificado ${file.conflict.remote_modified ?? ""}`}>
      <button className="small" onClick={() => resolve("keep_local")}>
        Usar WADIMA
      </button>
      <button className="small" onClick={() => resolve("keep_remote")}>
        Usar Drive
      </button>
      <button className="small" onClick={() => resolve("keep_both")}>
        Conservar ambas
      </button>
    </span>
  );
}

function PartnerAccess() {
  const { data, error, reload } = useApi<{
    partners: { user_id: string; display_name: string; email: string | null; access: string | null }[];
  }>("/api/drive/permissions");
  if (error) return <div className="alert err small">{error}</div>;
  if (!data) return <Spinner label="Comprobando permisos" />;
  return (
    <>
      <h3 style={{ marginTop: 12 }}>Acceso de los socios a WADIMA/</h3>
      <ul className="list">
        {data.partners.map((p) => (
          <li key={p.user_id} className="row small">
            <strong>{p.display_name}</strong>
            <span className="muted">{p.email ?? "sin correo"}</span>
            {p.access ? (
              <Badge tone="ok">{p.access}</Badge>
            ) : (
              <button
                className="small right"
                disabled={!p.email}
                title={p.email ? "" : "El socio debe indicar su correo de Google en Configuración"}
                onClick={async () => {
                  try {
                    await post(`/api/drive/share/${p.user_id}`);
                    reload();
                  } catch (err) {
                    toast((err as Error).message, "err");
                  }
                }}
              >
                Compartir carpeta
              </button>
            )}
          </li>
        ))}
      </ul>
    </>
  );
}

function SyncLog() {
  const { data } = useApi<
    { id: number; ts: string; op: string; result: string; detail: string; file: string | null }[]
  >("/api/drive/log?limit=60", { refreshOn: ["sync.changed"] });
  return (
    <div className="panel">
      <h3>Registro de sincronización</h3>
      <div className="code-box">
        {data?.map((l) => (
          <div key={l.id}>
            [{formatDateTime(l.ts)}] {l.op} {l.result.toUpperCase()} {l.file ?? ""} {l.detail}
          </div>
        ))}
        {data?.length === 0 && "Sin operaciones registradas."}
      </div>
    </div>
  );
}
