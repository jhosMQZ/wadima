import { useState } from "react";
import type { TimeEntry } from "../api/types";
import { clampToEntry, elapsedSeconds, PENDING_LABEL, useTicker } from "../lib/time";
import { toast } from "../lib/toast";
import { utcToZonedInput, zonedToUtcIso } from "../lib/tz";
import { useBrand } from "../store/brand";
import { useSession } from "../store/session";
import { formatDuration, formatInZone, serverNow, useClock } from "../store/timeclock";
import { ErrorBox, Modal } from "./ui";

export function ClockWidget() {
  const status = useClock((s) => s.status);
  const offset = useClock((s) => s.offsetMs);
  const [dialog, setDialog] = useState<"out" | "declare" | null>(null);
  const [busy, setBusy] = useState(false);
  useTicker(1000);
  if (!status) return null;
  const open = status.open;
  const tz = status.timezone;
  return (
    <>
      {open ? (
        <span
          className={`clock-chip ${open.status === "pending_close" ? "warn" : "on"}`}
          data-testid="clock-widget"
        >
          <span className="dot" aria-hidden="true" />
          <span>
            {open.status === "pending_close" ? "Jornada pendiente de cierre" : "En jornada"} ·{" "}
            <strong>{formatDuration(elapsedSeconds(open, offset))}</strong>
            <span className="muted small">
              {" "}
              desde {formatInZone(open.clock_in_at, tz, { second: undefined })}
            </span>
          </span>
          <button className="small" onClick={() => setDialog("out")}>
            Registrar salida
          </button>
        </span>
      ) : (
        <span className="clock-chip off" data-testid="clock-widget">
          <span className="muted small">Sin entrada</span>
          <button
            className="small primary"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                const { entry, duplicate } = await useClock.getState().clockIn();
                toast(
                  duplicate
                    ? `Ya tenía una entrada desde las ${formatInZone(entry.clock_in_at, tz)}`
                    : `Entrada registrada a las ${formatInZone(entry.clock_in_at, tz)} (hora del servidor)`,
                  "ok",
                );
              } catch (err) {
                toast(`No se registró la entrada: ${(err as Error).message}`, "err");
              } finally {
                setBusy(false);
              }
            }}
          >
            Registrar entrada
          </button>
        </span>
      )}
      {dialog === "out" && open && (
        <ClockOutDialog entry={open} onClose={() => setDialog(null)} onDeclare={() => setDialog("declare")} />
      )}
      {dialog === "declare" && open && <DeclareExitDialog entry={open} onClose={() => setDialog(null)} />}
    </>
  );
}

export function ClockOutDialog({
  entry,
  onClose,
  onDone,
  onDeclare,
}: {
  entry: TimeEntry;
  onClose: () => void;
  onDone?: (e: TimeEntry) => void;
  onDeclare?: () => void;
}) {
  const offset = useClock((s) => s.offsetMs);
  const tz = useClock((s) => s.status?.timezone ?? useBrand.getState().org.timezone);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useTicker(1000);
  const long = elapsedSeconds(entry, offset) > 12 * 3600;
  return (
    <Modal title="Registrar salida" onClose={onClose}>
      <div className="stack">
        <dl className="kv">
          <dt>Entrada</dt>
          <dd>{formatInZone(entry.clock_in_at, tz, { dateStyle: "medium", timeStyle: "medium" })}</dd>
          <dt>Hora del servidor</dt>
          <dd>{formatInZone(serverNow(offset), tz)}</dd>
          <dt>Duración</dt>
          <dd>
            <strong>{formatDuration(elapsedSeconds(entry, offset), true)}</strong>
          </dd>
        </dl>
        {(entry.status === "pending_close" || long) && (
          <div className="alert warn">
            Esta jornada {entry.pending_reason ? PENDING_LABEL[entry.pending_reason] : "es muy larga"}. Si
            olvidó registrar la salida, declare la hora real en lugar de registrar la de ahora.
            {onDeclare && (
              <div style={{ marginTop: 6 }}>
                <button className="small" onClick={onDeclare}>
                  Declarar la hora real de salida
                </button>
              </div>
            )}
          </div>
        )}
        <label className="field">
          Nota (opcional)
          <input value={note} maxLength={300} onChange={(e) => setNote(e.target.value)} />
        </label>
        <p className="small muted">
          Se registrará la hora del servidor. El tiempo registrado no equivale necesariamente a tiempo
          efectivo de trabajo.
        </p>
        <ErrorBox error={error} />
        <div className="row">
          <button className="ghost" onClick={onClose}>
            Cancelar
          </button>
          <button
            className="primary"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              setError(null);
              try {
                const closed = await useClock.getState().clockOut(note);
                toast(
                  `Salida registrada a las ${formatInZone(closed.clock_out_at!, tz)} · duración ${formatDuration(closed.duration_seconds)}`,
                  "ok",
                );
                onDone?.(closed);
                onClose();
              } catch (err) {
                setError(`No se registró la salida: ${(err as Error).message}`);
              } finally {
                setBusy(false);
              }
            }}
          >
            {busy ? "Registrando…" : "Registrar salida"}
          </button>
        </div>
      </div>
    </Modal>
  );
}

export function DeclareExitDialog({ entry, onClose }: { entry: TimeEntry; onClose: () => void }) {
  const tz = useClock((s) => s.status?.timezone ?? useBrand.getState().org.timezone);
  const offset = useClock((s) => s.offsetMs);
  const [value, setValue] = useState(() => utcToZonedInput(serverNow(offset), tz));
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <Modal title="Declarar la hora real de salida" onClose={onClose}>
      <form
        className="stack"
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setError(null);
          try {
            const closed = await useClock
              .getState()
              .declareExit(entry.id, clampToEntry(zonedToUtcIso(value, tz), entry), reason);
            toast(
              `Salida declarada a las ${formatInZone(closed.clock_out_at!, tz)}; queda pendiente de revisión`,
              "ok",
            );
            onClose();
          } catch (err) {
            setError((err as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <p className="muted small" style={{ margin: 0 }}>
          Entrada: {formatInZone(entry.clock_in_at, tz, { dateStyle: "medium", timeStyle: "short" })} ({tz}).
          La salida quedará marcada como <strong>declarada</strong> y un aprobador la revisará.
        </p>
        <label className="field">
          Hora real de salida ({tz})
          <input
            type="datetime-local"
            required
            value={value}
            min={utcToZonedInput(entry.clock_in_at, tz)}
            max={utcToZonedInput(serverNow(offset), tz)}
            onChange={(e) => setValue(e.target.value)}
          />
        </label>
        <label className="field">
          Motivo
          <input
            required
            minLength={5}
            maxLength={300}
            value={reason}
            placeholder="Olvidé registrar la salida al terminar"
            onChange={(e) => setReason(e.target.value)}
          />
        </label>
        <ErrorBox error={error} />
        <div className="row">
          <button type="button" className="ghost" onClick={onClose}>
            Cancelar
          </button>
          <button className="primary" disabled={busy}>
            {busy ? "Guardando…" : "Declarar salida"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

/** Cerrar sesión no registra la salida: si hay jornada abierta se pregunta qué hacer. */
export function LogoutButton() {
  const logout = useSession((s) => s.logout);
  const status = useClock((s) => s.status);
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const open = status?.open;
  return (
    <>
      <button className="small" onClick={() => (open ? setAsking(true) : void logout())}>
        Cerrar sesión
      </button>
      {asking && open && (
        <Modal title="Tiene una jornada abierta" onClose={() => setAsking(false)}>
          <div className="stack">
            <p style={{ margin: 0 }}>
              Registró su entrada a las {formatInZone(open.clock_in_at, status!.timezone)}. Cerrar sesión no
              registra la salida.
            </p>
            <div className="row wrap">
              <button className="ghost" onClick={() => setAsking(false)}>
                Cancelar
              </button>
              <button onClick={() => void logout()} disabled={busy}>
                Cerrar sesión sin registrar salida
              </button>
              <button
                className="primary"
                disabled={busy}
                onClick={async () => {
                  setBusy(true);
                  try {
                    await useClock.getState().clockOut();
                    await logout();
                  } catch (err) {
                    toast(`No se registró la salida: ${(err as Error).message}`, "err");
                    setBusy(false);
                  }
                }}
              >
                Registrar salida y cerrar sesión
              </button>
            </div>
          </div>
        </Modal>
      )}
    </>
  );
}
