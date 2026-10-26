import { del } from "../api/client";
import { Badge, ErrorBox, Spinner } from "../components/ui";
import { toast } from "../lib/toast";
import { formatDateTime } from "../lib/format";
import { useApi } from "../lib/useApi";
import { useSession } from "../store/session";

interface SessionRow {
  id: string;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  revoked_at: string | null;
  revoke_reason: string | null;
  ip: string | null;
  user_agent: string | null;
  current: boolean;
}

interface AuditRow {
  id: number;
  ts: string;
  actor_id: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  ip: string | null;
  data: Record<string, unknown>;
  hash: string;
}

export default function SecurityPage() {
  const me = useSession((s) => s.me)!;
  const isAdmin = me.user.role === "admin";
  const sessions = useApi<SessionRow[]>("/api/auth/sessions");
  const history = useApi<{ ts: string; ip: string | null; success: boolean }[]>("/api/auth/history");
  const verify = useApi<{ ok: boolean; checked: number; broken_at?: number; head?: string }>(
    isAdmin ? "/api/admin/audit/verify" : null,
  );
  const audit = useApi<AuditRow[]>(isAdmin ? "/api/admin/audit?limit=100" : null, {
    refreshOn: ["presence.changed"],
  });

  return (
    <>
      <div className="page-head">
        <div className="grow">
          <h1>Ciberseguridad</h1>
          <p>Sesiones activas, historial de acceso y verificación de la bitácora de auditoría.</p>
        </div>
      </div>
      <div className="grid cols-2">
        <div className="panel">
          <h3>Mis sesiones</h3>
          <ErrorBox error={sessions.error} />
          {!sessions.data && <Spinner />}
          <ul className="list">
            {sessions.data?.map((s) => (
              <li key={s.id}>
                <div className="row">
                  <strong className="small">{(s.user_agent ?? "Cliente desconocido").slice(0, 60)}</strong>
                  {s.current && <Badge tone="ok">actual</Badge>}
                  {s.revoked_at && <Badge tone="muted">{s.revoke_reason ?? "cerrada"}</Badge>}
                  {!s.current && !s.revoked_at && (
                    <button
                      className="small danger right"
                      onClick={async () => {
                        await del(`/api/auth/sessions/${s.id}`);
                        toast("Sesión revocada", "ok");
                        sessions.reload(true);
                      }}
                    >
                      Revocar
                    </button>
                  )}
                </div>
                <div className="small muted">
                  IP {s.ip ?? "—"} · inicio {formatDateTime(s.created_at)} · última actividad{" "}
                  {formatDateTime(s.last_seen_at)}
                </div>
              </li>
            ))}
          </ul>
        </div>
        <div className="panel">
          <h3>Historial de acceso</h3>
          <ul className="list">
            {history.data?.map((h, i) => (
              <li key={i} className="row small">
                <Badge tone={h.success ? "ok" : "err"}>{h.success ? "correcto" : "fallido"}</Badge>
                <span>{formatDateTime(h.ts)}</span>
                <span className="muted">{h.ip ?? ""}</span>
              </li>
            ))}
            {history.data?.length === 0 && <li className="muted">Sin registros.</li>}
          </ul>
        </div>
      </div>
      {isAdmin && (
        <div className="panel">
          <div className="panel-title">
            <h3>Bitácora de auditoría</h3>
            <button className="small" onClick={() => verify.reload()}>
              Verificar cadena
            </button>
          </div>
          {verify.data &&
            (verify.data.ok ? (
              <div className="alert ok">
                Cadena íntegra: {verify.data.checked} entradas verificadas. Hash cabeza{" "}
                <span className="mono small">{verify.data.head?.slice(0, 16)}…</span>
              </div>
            ) : (
              <div className="alert err">
                ¡Cadena rota en la entrada #{verify.data.broken_at}! Alguien modificó registros fuera de
                WADIMA.
              </div>
            ))}
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Fecha (servidor)</th>
                  <th>Acción</th>
                  <th>Objetivo</th>
                  <th>IP</th>
                  <th>Hash</th>
                </tr>
              </thead>
              <tbody>
                {audit.data?.map((a) => (
                  <tr key={a.id}>
                    <td className="mono small">{a.id}</td>
                    <td className="small">{formatDateTime(a.ts)}</td>
                    <td>
                      <code>{a.action}</code>
                    </td>
                    <td className="small muted">
                      {a.target_type} {a.target_id?.slice(0, 8)}
                    </td>
                    <td className="small">{a.ip ?? "—"}</td>
                    <td className="mono small faint">{a.hash.slice(0, 10)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  );
}
