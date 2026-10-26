import { useState } from "react";
import { Link } from "react-router-dom";
import { ClockOutDialog, DeclareExitDialog } from "../components/TimeClock";
import { PENDING_LABEL, useTicker } from "../lib/time";
import { ErrorBox, Spinner } from "../components/ui";
import { toast } from "../lib/toast";
import { useBrand } from "../store/brand";
import { useSession } from "../store/session";
import { formatInZone, serverNow, useClock } from "../store/timeclock";

/** Pantalla de acceso: registrar la entrada (hora del servidor) antes de entrar en la oficina. */
export function OfficeGate({ onEnter, onRegistered }: { onEnter: () => void; onRegistered?: () => void }) {
  const me = useSession((s) => s.me)!;
  const org = useBrand((s) => s.org);
  const status = useClock((s) => s.status);
  const offset = useClock((s) => s.offsetMs);
  const loadError = useClock((s) => s.error);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [registered, setRegistered] = useState<string | null>(null);
  const [dialog, setDialog] = useState<"out" | "declare" | null>(null);
  useTicker(1000);

  if (!status) {
    return (
      <div className="gate">
        <div className="panel gate-card">
          {loadError ? (
            <ErrorBox error={loadError} onRetry={() => void useClock.getState().load()} />
          ) : (
            <Spinner />
          )}
        </div>
      </div>
    );
  }
  const tz = status.timezone;
  const open = status.open;

  return (
    <div className="gate" data-testid="office-gate">
      <div className="panel gate-card">
        <h1>Acceso a la oficina</h1>
        <p className="muted" style={{ marginTop: 0 }}>
          Hola, {me.user.display_name}. Bienvenido a {org.platform_name}.
        </p>
        <div className="server-clock" aria-live="off">
          <span className="small muted">Hora del servidor ({tz})</span>
          <strong data-testid="server-clock">{formatInZone(serverNow(offset), tz)}</strong>
        </div>

        {registered ? (
          <div className="stack">
            <div className="alert ok" role="status" data-testid="clock-in-confirmation">
              Entrada registrada a las <strong>{formatInZone(registered, tz)}</strong> (hora del servidor).
            </div>
            <button className="primary big" onClick={onEnter} autoFocus>
              Entrar a la oficina
            </button>
          </div>
        ) : open?.status === "pending_close" ? (
          <div className="stack">
            <div className="alert warn" role="alert">
              Tiene una jornada sin cerrar desde{" "}
              <strong>
                {formatInZone(open.clock_in_at, tz, { dateStyle: "medium", timeStyle: "short" })}
              </strong>
              : {open.pending_reason ? PENDING_LABEL[open.pending_reason] : "pendiente de cierre"}. No se ha
              inventado ninguna hora de salida.
            </div>
            <div className="row wrap">
              <button className="primary" onClick={() => setDialog("declare")}>
                Declarar la hora real de salida
              </button>
              <button onClick={() => setDialog("out")}>Registrar la salida ahora</button>
              <button className="ghost" onClick={onEnter}>
                Entrar sin cambiar nada
              </button>
            </div>
          </div>
        ) : open ? (
          <div className="stack">
            <div className="alert ok">
              Su entrada está registrada desde las <strong>{formatInZone(open.clock_in_at, tz)}</strong>.
            </div>
            <button className="primary big" onClick={onEnter} autoFocus>
              Entrar a la oficina
            </button>
          </div>
        ) : (
          <form
            className="stack"
            onSubmit={async (e) => {
              e.preventDefault();
              setBusy(true);
              setError(null);
              try {
                onRegistered?.();
                const { entry, duplicate } = await useClock.getState().clockIn(note);
                if (duplicate) toast("Ya tenía una entrada registrada; no se creó otra.", "info");
                setRegistered(entry.clock_in_at);
              } catch (err) {
                setError(
                  `No se registró la entrada: ${(err as Error).message}. Compruebe la conexión y reintente.`,
                );
              } finally {
                setBusy(false);
              }
            }}
          >
            <label className="field">
              Nota de entrada (opcional)
              <input
                value={note}
                maxLength={300}
                onChange={(e) => setNote(e.target.value)}
                placeholder="Ej.: trabajo remoto"
              />
            </label>
            <ErrorBox error={error} />
            <button className="primary big" disabled={busy} data-testid="clock-in">
              {busy ? "Registrando…" : "Registrar entrada"}
            </button>
            <p className="small muted" style={{ margin: 0 }}>
              La hora la pone el servidor; cambiar el reloj de este equipo no altera el registro. El tiempo
              registrado no equivale necesariamente a tiempo efectivo de trabajo.
            </p>
            <button type="button" className="link" onClick={onEnter}>
              Ver la oficina sin registrar entrada
            </button>
          </form>
        )}
        <p className="small muted" style={{ marginBottom: 0 }}>
          Historial y totales en <Link to="/time">Control horario</Link>.
        </p>
      </div>
      {dialog === "out" && open && (
        <ClockOutDialog entry={open} onClose={() => setDialog(null)} onDeclare={() => setDialog("declare")} />
      )}
      {dialog === "declare" && open && <DeclareExitDialog entry={open} onClose={() => setDialog(null)} />}
    </div>
  );
}
