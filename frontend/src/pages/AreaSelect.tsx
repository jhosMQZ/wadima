import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { post } from "../api/client";
import type { Area } from "../api/types";
import { EmptyState, ErrorBox, Spinner } from "../components/ui";
import { toast } from "../lib/toast";
import { ROLE_LABEL, timeAgo } from "../lib/format";
import { useApi } from "../lib/useApi";
import { useSession } from "../store/session";
import { CreateAreaForm } from "./Areas";

interface AreasResponse {
  areas: Area[];
  pending_requests: { id: string; area_name: string; created_at: string }[];
}

/** Entrar en un área, usar un código de invitación o crear un área (también en /join dentro del workspace). */
export function AreaSelect({ embedded = false }: { embedded?: boolean }) {
  const me = useSession((s) => s.me)!;
  const navigate = useNavigate();
  const enterArea = useSession((s) => s.enterArea);
  const logout = useSession((s) => s.logout);
  const { data, error, loading, reload } = useApi<AreasResponse>("/api/areas", {
    refreshOn: ["area.updated"],
  });
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [codeError, setCodeError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  async function enter(area: Area) {
    setBusy(area.id);
    try {
      await enterArea(area.id);
      if (embedded) navigate("/");
    } catch (err) {
      toast((err as Error).message, "err");
    } finally {
      setBusy(null);
    }
  }

  async function redeem(e: FormEvent) {
    e.preventDefault();
    setCodeError(null);
    try {
      const res = await post<{ status: string; area: Area }>("/api/invitations/redeem", { code });
      setCode("");
      toast(
        res.status === "auto"
          ? `Acceso concedido a «${res.area.name}»`
          : `Solicitud enviada a los propietarios de «${res.area.name}»`,
        "ok",
      );
      reload(true);
    } catch (err) {
      setCodeError((err as Error).message);
    }
  }

  return (
    <div className={embedded ? "" : "login-wrap"}>
      <div style={{ width: "min(760px, 100%)" }}>
        <div className="panel">
          <div className="panel-title">
            <h2>{embedded ? "Áreas de trabajo" : `Hola, ${me.user.display_name}`}</h2>
            {!embedded && (
              <button className="small ghost" onClick={() => logout()}>
                Cerrar sesión
              </button>
            )}
          </div>
          <p className="muted" style={{ marginTop: 0 }}>
            Elija el área en la que va a trabajar. Cada entrada y salida de un área queda registrada con la
            hora del servidor.
          </p>
          <ErrorBox error={error} onRetry={() => reload()} />
          {loading && !data && <Spinner />}
          {data && data.areas.length === 0 && (
            <EmptyState title="Todavía no tiene áreas autorizadas">
              Use un código de invitación o cree un área nueva.
            </EmptyState>
          )}
          <div className="grid cols-2">
            {data?.areas
              .filter((a) => a.enabled)
              .map((a) => (
                <button
                  key={a.id}
                  className="panel"
                  style={{
                    textAlign: "left",
                    display: "block",
                    margin: 0,
                    borderLeft: `6px solid ${a.color}`,
                  }}
                  onClick={() => enter(a)}
                  disabled={busy !== null}
                >
                  <div className="row">
                    <strong style={{ fontSize: 16, margin: 0 }}>
                      {a.icon && <span aria-hidden="true">{a.icon} </span>}
                      {a.name}
                    </strong>
                    <span className="badge info right">{ROLE_LABEL[a.my_role ?? ""] ?? a.my_role}</span>
                  </div>
                  <div className="small muted" style={{ fontWeight: 400 }}>
                    {a.description || "Sin descripción"} · {a.member_count} miembro(s)
                  </div>
                  {busy === a.id && <Spinner label="Entrando" />}
                </button>
              ))}
          </div>
          {data?.pending_requests.length ? (
            <div className="alert info" style={{ marginTop: 12 }}>
              Solicitudes pendientes:{" "}
              {data.pending_requests.map((r) => `${r.area_name} (${timeAgo(r.created_at)})`).join(", ")}
            </div>
          ) : null}
        </div>
        <div className="grid cols-2">
          <form className="panel stack" onSubmit={redeem}>
            <h3>Usar código de invitación</h3>
            <ErrorBox error={codeError} />
            <label className="field">
              Código
              <input
                value={code}
                onChange={(e) => setCode(e.target.value)}
                placeholder="WAD-XXXX-XXXX-XXXX"
                autoComplete="off"
                required
                minLength={6}
              />
            </label>
            <button className="primary">Validar código</button>
          </form>
          <div className="panel">
            <h3>Nueva área</h3>
            {creating ? (
              <CreateAreaForm
                onDone={async (area) => {
                  setCreating(false);
                  await reload(true);
                  if (area) await enter(area);
                }}
              />
            ) : (
              <button onClick={() => setCreating(true)}>Crear un área</button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
