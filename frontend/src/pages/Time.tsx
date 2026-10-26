import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { post } from "../api/client";
import type { TimeEntry, TimeEvent, TimeStatus, TimeSummary, TimeTotals, UserPublic } from "../api/types";
import { ClockOutDialog, DeclareExitDialog } from "../components/TimeClock";
import { clampToEntry, CLOSE_KIND_LABEL, elapsedSeconds, PENDING_LABEL, useTicker } from "../lib/time";
import { Badge, EmptyState, ErrorBox, Modal, Spinner, Tabs } from "../components/ui";
import { toast } from "../lib/toast";
import { utcToZonedInput, zonedDate, zonedToUtcIso } from "../lib/tz";
import { useApi } from "../lib/useApi";
import { ROBOT_KIND_LABEL } from "../office/robot";
import { useBrand } from "../store/brand";
import { useSession } from "../store/session";
import { formatDuration, formatInZone, serverNow, useClock } from "../store/timeclock";

type Tab = "mine" | "history" | "totals" | "review" | "events";

const APPROVAL: Record<string, { label: string; tone: string }> = {
  pending: { label: "Sin aprobar", tone: "warn" },
  approved: { label: "Aprobada", tone: "ok" },
  rejected: { label: "Rechazada", tone: "err" },
};

const STATUS: Record<string, string> = {
  open: "Abierta",
  pending_close: "Pendiente de cierre",
  closed: "Cerrada",
};

function hours(seconds: number): string {
  return `${formatDuration(seconds)} h (${(seconds / 3600).toFixed(2)})`;
}

export default function TimePage() {
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as Tab) || "mine";
  const status = useClock((s) => s.status);
  const tz = useBrand((s) => s.org.timezone);
  const canApprove = status?.can_approve ?? false;
  const tabs: { key: Tab; label: string }[] = [
    { key: "mine", label: "Mi jornada" },
    { key: "history", label: "Historial" },
    { key: "totals", label: "Totales" },
    ...(canApprove ? [{ key: "review" as Tab, label: "Revisión" }] : []),
    { key: "events", label: "Eventos verificables" },
  ];
  return (
    <>
      <div className="page-head">
        <div className="grow">
          <h1>Control horario</h1>
          <p>
            Entradas y salidas con la hora del servidor ({tz}). El tiempo registrado refleja la jornada
            declarada y no equivale necesariamente a tiempo efectivo de trabajo.
          </p>
        </div>
      </div>
      <Tabs tabs={tabs} value={tab} onChange={(t) => setParams({ tab: t })} />
      {tab === "mine" && <Mine status={status} />}
      {tab === "history" && <History canApprove={canApprove} />}
      {tab === "totals" && <Totals />}
      {tab === "review" && canApprove && <Review />}
      {tab === "events" && <Events />}
    </>
  );
}

function Mine({ status }: { status: TimeStatus | null }) {
  const offset = useClock((s) => s.offsetMs);
  const [dialog, setDialog] = useState<"out" | "declare" | null>(null);
  const [busy, setBusy] = useState(false);
  useTicker(1000);
  if (!status) return <Spinner />;
  const tz = status.timezone;
  const open = status.open;
  const p = status.policy;
  return (
    <div className="grid cols-2">
      <div className="panel" data-testid="my-day">
        <h3>Hoy</h3>
        <div className="server-clock">
          <span className="small muted">Hora del servidor</span>
          <strong>{formatInZone(serverNow(offset), tz)}</strong>
        </div>
        {open ? (
          <div className="stack">
            <div className={`alert ${open.status === "pending_close" ? "warn" : "ok"}`}>
              {open.status === "pending_close"
                ? `Jornada pendiente de cierre (${open.pending_reason ? PENDING_LABEL[open.pending_reason] : ""}).`
                : "Jornada en curso."}{" "}
              Entrada: <strong>{formatInZone(open.clock_in_at, tz)}</strong> · transcurrido{" "}
              <strong>{formatDuration(elapsedSeconds(open, offset), true)}</strong>
            </div>
            <div className="row wrap">
              <button className="primary" onClick={() => setDialog("out")}>
                Registrar salida
              </button>
              <button onClick={() => setDialog("declare")}>Declarar hora real de salida</button>
            </div>
          </div>
        ) : (
          <div className="stack">
            <p className="muted" style={{ margin: 0 }}>
              No tiene una entrada registrada.
            </p>
            <button
              className="primary"
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                try {
                  const r = await useClock.getState().clockIn();
                  toast(`Entrada registrada a las ${formatInZone(r.entry.clock_in_at, tz)}`, "ok");
                } catch (err) {
                  toast(`No se registró la entrada: ${(err as Error).message}`, "err");
                } finally {
                  setBusy(false);
                }
              }}
            >
              Registrar entrada
            </button>
          </div>
        )}
        <dl className="kv" style={{ marginTop: 12 }}>
          <dt>Registrado hoy</dt>
          <dd>{formatDuration(status.today.registered_seconds)} h</dd>
          <dt>Jornada en curso</dt>
          <dd>{open ? `${formatDuration(elapsedSeconds(open, offset))} h` : "—"}</dd>
          <dt>Aprobado hoy</dt>
          <dd>{formatDuration(status.today.approved_seconds)} h</dd>
          {status.last_closed && (
            <>
              <dt>Última salida</dt>
              <dd>
                {formatInZone(status.last_closed.clock_out_at!, tz, {
                  dateStyle: "medium",
                  timeStyle: "short",
                })}{" "}
                · {CLOSE_KIND_LABEL[status.last_closed.close_kind ?? "manual"]}
              </dd>
            </>
          )}
        </dl>
      </div>
      <div className="panel">
        <h3>Reglas de esta organización</h3>
        <ul className="small">
          <li>
            La entrada y la salida usan siempre el reloj del servidor; cambiar la hora del equipo no las
            altera.
          </li>
          <li>
            Solo puede haber una jornada abierta por persona; los intentos duplicados quedan registrados.
          </li>
          <li>
            Si se pierde la conexión más de {p.disconnect_minutes} min o la jornada supera {p.max_open_hours}{" "}
            h, se marca como <strong>pendiente de cierre</strong>.
          </li>
          <li>
            {p.auto_close === "last_activity"
              ? "Cierre automático activado: una jornada abandonada se cierra a la hora de la última conexión registrada, marcada como automática."
              : "Sin cierre automático: nunca se inventa una hora de salida; usted la registra o la declara."}
          </li>
          <li>
            Las salidas declaradas, automáticas o fijadas por un aprobador se distinguen de las registradas.
          </li>
          <li>
            {p.visibility === "all"
              ? "Los socios pueden consultar el historial de la organización."
              : "Cada socio ve su historial; los aprobadores ven el de todos."}
          </li>
        </ul>
      </div>
      {dialog === "out" && open && (
        <ClockOutDialog entry={open} onClose={() => setDialog(null)} onDeclare={() => setDialog("declare")} />
      )}
      {dialog === "declare" && open && <DeclareExitDialog entry={open} onClose={() => setDialog(null)} />}
    </div>
  );
}

function usePeriod() {
  const tz = useBrand((s) => s.org.timezone);
  const [today] = useState(() => zonedDate(Date.now(), tz));
  const [from, setFrom] = useState(`${today.slice(0, 8)}01`);
  const [to, setTo] = useState(today);
  const [user, setUser] = useState("");
  const users = useApi<UserPublic[]>("/api/users");
  const query = new URLSearchParams({ from, to, ...(user ? { user_id: user } : {}) }).toString();
  const filters = (
    <div className="row wrap filters" role="group" aria-label="Filtros">
      <label className="field inline">
        Socio
        <select value={user} onChange={(e) => setUser(e.target.value)} aria-label="Socio">
          <option value="">Todos</option>
          {users.data?.map((u) => (
            <option key={u.id} value={u.id}>
              {u.display_name}
            </option>
          ))}
        </select>
      </label>
      <label className="field inline">
        Desde
        <input type="date" value={from} max={to} onChange={(e) => setFrom(e.target.value)} />
      </label>
      <label className="field inline">
        Hasta
        <input type="date" value={to} min={from} onChange={(e) => setTo(e.target.value)} />
      </label>
      <a className="btn small" href={`/api/time/export?format=csv&${query}`} download>
        Exportar CSV
      </a>
      <a className="btn small" href={`/api/time/export?format=xlsx&${query}`} download>
        Exportar Excel
      </a>
    </div>
  );
  return { query, filters, tz };
}

function EntryRow({
  e,
  tz,
  canApprove,
  meId,
}: {
  e: TimeEntry;
  tz: string;
  canApprove: boolean;
  meId: string;
}) {
  const [action, setAction] = useState<"approve" | "reject" | "close" | null>(null);
  const reviewable = canApprove && e.user_id !== meId;
  return (
    <tr>
      <td>{e.local_date}</td>
      <td>{e.user_name}</td>
      <td>{formatInZone(e.clock_in_at, tz)}</td>
      <td>
        {e.clock_out_at
          ? formatInZone(e.clock_out_at, tz, {
              day: "2-digit",
              month: "2-digit",
              hour: "2-digit",
              minute: "2-digit",
              second: "2-digit",
            })
          : "—"}
      </td>
      <td>
        {e.duration_seconds != null ? (
          formatDuration(e.duration_seconds)
        ) : (
          <span className="muted">en curso</span>
        )}
      </td>
      <td>
        {STATUS[e.status]}
        {e.close_kind && e.close_kind !== "manual" && (
          <div className="small muted">
            {CLOSE_KIND_LABEL[e.close_kind]}
            {e.close_reason && `: ${e.close_reason}`}
          </div>
        )}
        {e.pending_reason && <div className="small warn-text">{PENDING_LABEL[e.pending_reason]}</div>}
      </td>
      <td>
        <Badge tone={APPROVAL[e.approval_status].tone}>{APPROVAL[e.approval_status].label}</Badge>
        {e.approved_by_name && <div className="small muted">por {e.approved_by_name}</div>}
      </td>
      <td className="small">{[e.in_note, e.out_note].filter(Boolean).join(" · ") || "—"}</td>
      <td>
        {reviewable && e.status === "closed" && e.approval_status !== "approved" && (
          <button className="small" onClick={() => setAction("approve")}>
            Aprobar
          </button>
        )}
        {reviewable && e.status === "closed" && e.approval_status !== "rejected" && (
          <button className="small ghost" onClick={() => setAction("reject")}>
            Rechazar
          </button>
        )}
        {reviewable && e.status !== "closed" && (
          <button className="small" onClick={() => setAction("close")}>
            Cerrar con hora
          </button>
        )}
        {action && <ReviewDialog entry={e} action={action} tz={tz} onClose={() => setAction(null)} />}
      </td>
    </tr>
  );
}

function EntriesTable({
  entries,
  tz,
  canApprove,
}: {
  entries: TimeEntry[];
  tz: string;
  canApprove: boolean;
}) {
  const me = useSession((s) => s.me)!;
  if (!entries.length) return <EmptyState title="Sin jornadas en el periodo" />;
  return (
    <div className="table-wrap">
      <table className="table" data-testid="time-entries">
        <thead>
          <tr>
            <th>Fecha</th>
            <th>Socio</th>
            <th>Entrada</th>
            <th>Salida</th>
            <th>Duración</th>
            <th>Estado</th>
            <th>Aprobación</th>
            <th>Notas</th>
            <th>
              <span className="sr-only">Acciones</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {entries.map((e) => (
            <EntryRow key={e.id} e={e} tz={tz} canApprove={canApprove} meId={me.user.id} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function History({ canApprove }: { canApprove: boolean }) {
  const { query, filters, tz } = usePeriod();
  const { data, error, reload } = useApi<{ entries: TimeEntry[] }>(`/api/time/entries?${query}`, {
    refreshOn: ["time.recorded"],
  });
  return (
    <div className="panel">
      {filters}
      <ErrorBox error={error} onRetry={() => reload()} />
      {!data && !error && <Spinner />}
      {data && <EntriesTable entries={data.entries} tz={tz} canApprove={canApprove} />}
    </div>
  );
}

function TotalsTable<T extends TimeTotals>({
  rows,
  keyLabel,
  keyOf,
}: {
  rows: T[];
  keyLabel: string;
  keyOf: (r: T) => string;
}) {
  if (!rows.length) return <p className="muted small">Sin datos.</p>;
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>{keyLabel}</th>
            <th>Registradas</th>
            <th>Aprobadas</th>
            <th>Sin aprobar</th>
            <th>En curso</th>
            <th>Rechazadas</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={keyOf(r)}>
              <td>{keyOf(r)}</td>
              <td>{formatDuration(r.registered_seconds)}</td>
              <td>{formatDuration(r.approved_seconds)}</td>
              <td>{formatDuration(r.pending_approval_seconds)}</td>
              <td>{formatDuration(r.in_progress_seconds)}</td>
              <td>{formatDuration(r.rejected_seconds)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Totals() {
  const { query, filters } = usePeriod();
  const { data, error, reload } = useApi<TimeSummary>(`/api/time/summary?${query}`, {
    refreshOn: ["time.recorded"],
  });
  return (
    <div className="stack">
      <div className="panel">{filters}</div>
      <ErrorBox error={error} onRetry={() => reload()} />
      {!data && !error && <Spinner />}
      {data && (
        <>
          <div className="grid cols-3" data-testid="time-totals">
            <div className="panel stat">
              <span className="small muted">Registradas</span>
              <strong>{hours(data.totals.registered_seconds)}</strong>
            </div>
            <div className="panel stat">
              <span className="small muted">Aprobadas</span>
              <strong>{hours(data.totals.approved_seconds)}</strong>
            </div>
            <div className="panel stat">
              <span className="small muted">En curso (no sumadas)</span>
              <strong>{hours(data.totals.in_progress_seconds)}</strong>
            </div>
          </div>
          <div className="panel">
            <h3>Por socio</h3>
            <TotalsTable rows={data.people} keyLabel="Socio" keyOf={(r) => r.user_name} />
          </div>
          <div className="grid cols-2">
            <div className="panel">
              <h3>Por semana (lunes a domingo)</h3>
              <TotalsTable rows={data.weeks} keyLabel="Semana" keyOf={(r) => r.week} />
            </div>
            <div className="panel">
              <h3>Por mes</h3>
              <TotalsTable rows={data.months} keyLabel="Mes" keyOf={(r) => r.month} />
            </div>
          </div>
          <div className="panel">
            <h3>Por día</h3>
            <TotalsTable rows={data.days} keyLabel="Día" keyOf={(r) => r.date} />
          </div>
        </>
      )}
    </div>
  );
}

interface Incidents {
  can_approve: boolean;
  open_too_long: TimeEntry[];
  pending_close: TimeEntry[];
  needs_review: TimeEntry[];
  duplicates: TimeEvent[];
}

function Review() {
  const tz = useBrand((s) => s.org.timezone);
  const { data, error, reload } = useApi<Incidents>("/api/time/incidents", { refreshOn: ["time.recorded"] });
  const unique = useMemo(() => {
    if (!data) return [];
    const map = new Map<string, TimeEntry>();
    for (const e of [...data.open_too_long, ...data.pending_close, ...data.needs_review]) map.set(e.id, e);
    return [...map.values()];
  }, [data]);
  return (
    <div className="stack">
      <ErrorBox error={error} onRetry={() => reload()} />
      {!data && !error && <Spinner />}
      {data && (
        <>
          <div className="panel">
            <h3>Jornadas que requieren atención</h3>
            <p className="small muted">
              Abiertas demasiado tiempo, pendientes de cierre y cierres no registrados en el momento
              (automáticos, declarados o fijados) sin revisar. Nadie puede aprobar ni cerrar sus propias
              jornadas.
            </p>
            <EntriesTable entries={unique} tz={tz} canApprove />
          </div>
          <div className="panel">
            <h3>Intentos de entrada duplicada (30 días)</h3>
            {data.duplicates.length === 0 ? (
              <p className="muted small">Ninguno.</p>
            ) : (
              <ul className="list small">
                {data.duplicates.map((d) => (
                  <li key={d.id}>
                    {formatInZone(d.at, tz, { dateStyle: "medium", timeStyle: "medium" })} · {d.user_name} ·
                    origen {d.source}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function ReviewDialog({
  entry,
  action,
  tz,
  onClose,
}: {
  entry: TimeEntry;
  action: "approve" | "reject" | "close";
  tz: string;
  onClose: () => void;
}) {
  const offset = useClock((s) => s.offsetMs);
  const [note, setNote] = useState("");
  const [at, setAt] = useState(() => utcToZonedInput(serverNow(offset), tz));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const title = { approve: "Aprobar jornada", reject: "Rechazar jornada", close: "Cerrar jornada abierta" }[
    action
  ];
  return (
    <Modal title={title} onClose={onClose}>
      <form
        className="stack"
        onSubmit={async (ev) => {
          ev.preventDefault();
          setBusy(true);
          setError(null);
          try {
            if (action === "close")
              await post(`/api/time/entries/${entry.id}/close`, {
                clock_out_at: clampToEntry(zonedToUtcIso(at, tz), entry),
                reason: note,
              });
            else await post(`/api/time/entries/${entry.id}/${action}`, { note });
            toast(
              action === "approve"
                ? "Jornada aprobada"
                : action === "reject"
                  ? "Jornada rechazada"
                  : "Jornada cerrada",
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
        <p className="small" style={{ margin: 0 }}>
          {entry.user_name} · entrada{" "}
          {formatInZone(entry.clock_in_at, tz, { dateStyle: "medium", timeStyle: "short" })}
          {entry.clock_out_at &&
            ` · salida ${formatInZone(entry.clock_out_at, tz, { dateStyle: "medium", timeStyle: "short" })}`}
        </p>
        {action === "close" && (
          <label className="field">
            Hora de salida ({tz})
            <input
              type="datetime-local"
              required
              value={at}
              min={utcToZonedInput(entry.clock_in_at, tz)}
              onChange={(e) => setAt(e.target.value)}
            />
          </label>
        )}
        <label className="field">
          {action === "approve" ? "Nota (opcional)" : "Motivo"}
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            required={action !== "approve"}
            minLength={action === "approve" ? 0 : 5}
            maxLength={300}
          />
        </label>
        <ErrorBox error={error} />
        <div className="row">
          <button type="button" className="ghost" onClick={onClose}>
            Cancelar
          </button>
          <button className={action === "reject" ? "danger" : "primary"} disabled={busy}>
            {busy ? "Guardando…" : title}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function Events() {
  const tz = useBrand((s) => s.org.timezone);
  const { data, error, reload } = useApi<TimeEvent[]>("/api/time/events?limit=200", {
    refreshOn: ["time.recorded"],
  });
  return (
    <div className="panel">
      <p className="small muted" style={{ marginTop: 0 }}>
        Historial de solo inserción: cada evento guarda la persona, la hora del servidor, el tipo, el origen y
        su entrada en la bitácora de auditoría encadenada (verificable en Administración).
      </p>
      <ErrorBox error={error} onRetry={() => reload()} />
      {!data && !error && <Spinner />}
      {data && data.length === 0 && <EmptyState title="Sin eventos" />}
      {data && data.length > 0 && (
        <div className="table-wrap">
          <table className="table" data-testid="time-events">
            <thead>
              <tr>
                <th>#</th>
                <th>Hora del servidor</th>
                <th>Socio</th>
                <th>Evento</th>
                <th>Realizado por</th>
                <th>Origen</th>
                <th>Auditoría</th>
              </tr>
            </thead>
            <tbody>
              {data.map((ev) => (
                <tr key={ev.id}>
                  <td>{ev.id}</td>
                  <td>{formatInZone(ev.at, tz, { dateStyle: "short", timeStyle: "medium" })}</td>
                  <td>{ev.user_name}</td>
                  <td>
                    {ROBOT_KIND_LABEL[ev.kind] ?? ev.kind}
                    {ev.note && <div className="small muted">{ev.note}</div>}
                  </td>
                  <td>{ev.actor_name}</td>
                  <td>{ev.source}</td>
                  <td>{ev.audit_id ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
