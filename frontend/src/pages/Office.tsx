import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { put } from "../api/client";
import type { OfficeState, Person, TimeEvent } from "../api/types";
import { realtime } from "../api/ws";
import { elapsedSeconds, useTicker } from "../lib/time";
import { ErrorBox, Spinner } from "../components/ui";
import { useAreas } from "../lib/areas";
import { formatDateTime, STATE_LABEL, timeAgo } from "../lib/format";
import { AGENT_STATUS, label } from "../lib/labels";
import { toast } from "../lib/toast";
import { useApi } from "../lib/useApi";
import { OfficeGame, type OfficeGameHandle } from "../office/OfficeGame";
import { ROBOT_KIND_LABEL } from "../office/robot";
import { ROOM_DESCRIPTIONS, ROOM_ICONS, ROOM_MODULES, ROOM_NAMES, ROOM_ORDER } from "../office/rooms";
import { useBrand } from "../store/brand";
import { useSession } from "../store/session";
import { formatDuration, formatInZone, useClock } from "../store/timeclock";
import { OfficeGate } from "./OfficeGate";

const AGENT_NAMES: Record<string, string> = {
  memory_agent: "Memory Agent",
  doc_pipeline: "Conversor documental",
  drive_sync: "Sincronizador de Drive",
  claude: "Claude",
};

const GATE_KEY = "wadima.office.viewWithoutClock";
const ZOOM_KEY = "wadima.office.autoZoom";

export default function OfficePage() {
  const clock = useClock((s) => s.status);
  const [skipGate, setSkipGate] = useState(() => {
    try {
      return sessionStorage.getItem(GATE_KEY) === "1";
    } catch {
      return false;
    }
  });
  const [entered, setEntered] = useState(false);
  // Tras registrar la entrada, la pantalla de acceso sigue visible para que el socio verifique la hora.
  const [confirming, setConfirming] = useState(false);
  const open = clock?.open;
  const showGate = !entered && (confirming || !(open && open.status === "open")) && !skipGate;
  if (showGate) {
    return (
      <OfficeGate
        onRegistered={() => setConfirming(true)}
        onEnter={() => {
          setEntered(true);
          if (!useClock.getState().status?.open) {
            setSkipGate(true);
            try {
              sessionStorage.setItem(GATE_KEY, "1");
            } catch {
              /* almacenamiento no disponible: no pasa nada */
            }
          }
        }}
      />
    );
  }
  return <Office />;
}

function Office() {
  const navigate = useNavigate();
  const me = useSession((s) => s.me)!;
  const enterArea = useSession((s) => s.enterArea);
  const org = useBrand((s) => s.org);
  const { data, error, reload } = useApi<OfficeState>("/api/office/state", {
    refreshOn: [
      "presence.changed",
      "activity.new",
      "agent.changed",
      "area.updated",
      "ai.changed",
      "time.recorded",
      "org.updated",
      "meeting.changed",
    ],
    intervalMs: 60000,
  });
  const areas = useAreas();
  const robotLog = useApi<TimeEvent[]>("/api/time/events?limit=6", { refreshOn: ["time.recorded"] });
  const [selected, setSelected] = useState<{
    kind: "person" | "room" | "agent" | "claude";
    id: string;
  } | null>(null);
  const game = useRef<OfficeGameHandle>(null);
  const [focused, setFocused] = useState<string | null>(null);
  const [autoZoom, setAutoZoom] = useState(() => {
    try {
      return localStorage.getItem(ZOOM_KEY) !== "0";
    } catch {
      return true;
    }
  });
  const dataRef = useRef(data);
  useEffect(() => {
    dataRef.current = data;
  }, [data]);

  // Al entrar en la oficina se registra la entrada al centro de operaciones (si no hay un área activa).
  const autoEntered = useRef(false);
  useEffect(() => {
    if (autoEntered.current || me.active_area || !areas.data) return;
    const ops = areas.data.areas.find((a) => a.template_key === "ops" && a.enabled);
    if (ops) {
      autoEntered.current = true;
      enterArea(ops.id).catch(() => undefined);
    }
  }, [areas.data, me.active_area, enterArea]);

  // Robot recolector: cada evento horario real (ya guardado) se representa en la oficina.
  useEffect(
    () =>
      realtime.on("time.recorded", (ev) => {
        try {
          const person = dataRef.current?.people.find((p) => p.id === ev.user_id);
          game.current?.robotCollect({
            eventId: Number(ev.event_id),
            kind: String(ev.kind),
            userId: String(ev.user_id),
            userName: person?.display_name.split(" ")[0] ?? "un socio",
          });
        } catch (err) {
          console.warn("[oficina] el robot no pudo representar el evento", err);
        }
      }),
    [],
  );

  const onMoveRoom = useCallback(
    async (roomKey: string) => {
      try {
        const room = dataRef.current?.rooms.find((r) => r.key === roomKey);
        const area = areas.data?.areas.find((a) => a.id === room?.template_area_id && a.enabled);
        if (area && me.active_area?.id !== area.id) {
          await enterArea(area.id); // entrada al área registrada con la hora del servidor
        } else if (!area) {
          await put("/api/me/status", {
            declared: me.status?.declared ?? "available",
            message: me.status?.message ?? "",
            task_id: me.status?.task_id ?? null,
            room_key: roomKey,
          });
        }
      } catch (err) {
        toast((err as Error).message, "err");
      }
    },
    [areas.data, me.active_area, me.status, enterArea],
  );

  const person = selected?.kind === "person" ? data?.people.find((p) => p.id === selected.id) : undefined;
  const agent = selected?.kind === "agent" ? data?.agents.find((a) => a.key === selected.id) : undefined;
  const room = selected?.kind === "room" ? data?.rooms.find((r) => r.key === selected.id) : undefined;
  const areaName = (id: string | null) => areas.data?.areas.find((a) => a.id === id)?.name;

  return (
    <>
      <div className="page-head">
        <div className="grow">
          <h1>Oficina virtual</h1>
          <p>
            {org.legal_name}: estados reales de los socios, salas y equipos. Haga clic en el mapa y mueva su
            avatar con las flechas, o use los listados de la derecha.
          </p>
        </div>
      </div>
      <ErrorBox error={error} onRetry={() => reload()} />
      <div className="row wrap office-toolbar">
        <label className="row small">
          <input
            type="checkbox"
            checked={autoZoom}
            onChange={(e) => {
              setAutoZoom(e.target.checked);
              try {
                localStorage.setItem(ZOOM_KEY, e.target.checked ? "1" : "0");
              } catch {
                /* preferencia no persistente */
              }
            }}
            data-testid="auto-zoom"
          />
          Acercar la sala al entrar en ella
        </label>
        {focused && (
          <>
            <span className="small muted" data-testid="zoomed-room">
              Viendo: {ROOM_NAMES[focused]}
            </span>
            <button className="small" onClick={() => game.current?.resetView()} data-testid="reset-view">
              Vista completa
            </button>
          </>
        )}
      </div>
      <div className="office-layout">
        <div>
          <OfficeGame
            ref={game}
            state={data}
            selectedId={selected?.kind === "person" ? selected.id : null}
            selectedRoom={selected?.kind === "room" ? selected.id : null}
            onSelectPerson={(id) =>
              setSelected(
                id.startsWith("agent:")
                  ? { kind: "agent", id: id.slice(6) }
                  : id.startsWith("claude:")
                    ? { kind: "claude", id: id.slice(7) }
                    : { kind: "person", id },
              )
            }
            onSelectRoom={(key) => setSelected({ kind: "room", id: key })}
            onOpenRoom={(key) => navigate(ROOM_MODULES[key])}
            onMoveRoom={onMoveRoom}
            onFocusChange={setFocused}
            autoZoom={autoZoom}
          />
          {!data && !error && <Spinner label="Cargando oficina" />}
          <RobotLog events={robotLog.data} />
        </div>
        <aside className="side-panel" aria-label="Detalles y navegación alternativa">
          {person && (
            <PersonPanel
              person={person}
              areaName={areaName(person.area_id)}
              timezone={org.timezone}
              onClose={() => setSelected(null)}
            />
          )}
          {agent && (
            <div className="panel">
              <div className="panel-title">
                <h3>{AGENT_NAMES[agent.key] ?? agent.name}</h3>
                <button className="small ghost" onClick={() => setSelected(null)} aria-label="Cerrar">
                  ✕
                </button>
              </div>
              <dl className="kv">
                <dt>Estado</dt>
                <dd>{label(AGENT_STATUS, agent.status)}</dd>
                <dt>Tarea actual</dt>
                <dd>{agent.current || "—"}</dd>
                <dt>Último resultado</dt>
                <dd>{agent.last_result || "—"}</dd>
                <dt>Latido</dt>
                <dd>{timeAgo(agent.heartbeat_at)}</dd>
              </dl>
            </div>
          )}
          {selected?.kind === "claude" &&
            (() => {
              const owner = data?.people.find((p) => p.id === selected.id);
              return owner ? <ClaudePanel owner={owner} onClose={() => setSelected(null)} /> : null;
            })()}
          {room && (
            <div className="panel" data-testid="room-panel">
              <div className="panel-title">
                <h3>
                  <span aria-hidden="true">{ROOM_ICONS[room.key]}</span> {room.name}
                </h3>
                <button className="small ghost" onClick={() => setSelected(null)} aria-label="Cerrar">
                  ✕
                </button>
              </div>
              <p className="small muted" style={{ marginTop: 0 }}>
                {org.platform_name} · {ROOM_DESCRIPTIONS[room.key]}
              </p>
              {!room.enabled && (
                <div className="alert warn">
                  Área deshabilitada. Un administrador puede habilitarla en Áreas.
                </div>
              )}
              {room.areas.length > 0 && (
                <p className="small">Áreas: {room.areas.map((a) => a.name).join(", ")}</p>
              )}
              {room.key === "servers" && data && (
                <ul className="list integrations">
                  {data.integrations.map((i) => (
                    <li key={i.key} className="row">
                      <span
                        className={`dot ${i.status === "ok" ? "online" : i.status === "error" ? "err" : i.status === "warn" ? "paused" : "offline"}`}
                      />
                      <strong>{i.label}</strong>
                      <span className="small muted right">{i.detail}</span>
                    </li>
                  ))}
                </ul>
              )}
              <p className="small">
                Personas aquí:{" "}
                {data?.people
                  .filter((p) => p.online && (p.room_key ?? "ops") === room.key)
                  .map((p) => p.display_name)
                  .join(", ") || "nadie"}
              </p>
              <div className="row wrap">
                <button className="primary" onClick={() => navigate(room.module)}>
                  Abrir módulo
                </button>
                <button onClick={() => game.current?.walkMeTo(room.key)} disabled={!room.enabled}>
                  Ir con mi avatar
                </button>
                <button onClick={() => game.current?.focusRoom(room.key)}>Acercar</button>
              </div>
            </div>
          )}
          <div className="panel">
            <h3>Personas</h3>
            <ul className="list">
              {data?.people.map((p) => (
                <li key={p.id} style={{ padding: 0 }}>
                  <button className="person-row" onClick={() => setSelected({ kind: "person", id: p.id })}>
                    <span className={`dot ${p.state}`} aria-hidden="true" />
                    <span style={{ flex: 1 }}>
                      {p.display_name} {p.is_me && <span className="faint">(usted)</span>}
                      <span className="small muted" style={{ display: "block" }}>
                        {STATE_LABEL[p.state]}
                        {p.online && p.room_key ? ` · ${ROOM_NAMES[p.room_key]}` : ""}
                        {p.clock
                          ? ` · en jornada desde ${formatInZone(p.clock.clock_in_at, org.timezone, { second: undefined })}`
                          : ""}
                      </span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
          <div className="panel">
            <h3>Salas</h3>
            <ul className="list">
              {ROOM_ORDER.map((key) => {
                const r = data?.rooms.find((x) => x.key === key);
                if (!r) return null;
                return (
                  <li key={key} className="row">
                    <button className="link" onClick={() => setSelected({ kind: "room", id: key })}>
                      <span aria-hidden="true">{ROOM_ICONS[key]}</span> {r.name}
                    </button>
                    {r.enabled ? (
                      <Link className="small right" to={r.module}>
                        Abrir
                      </Link>
                    ) : (
                      <span className="small faint right">deshabilitada</span>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        </aside>
      </div>
    </>
  );
}

const CLAUDE_STATE_LABEL: Record<string, string> = {
  working: "Trabajando en una tarea",
  waiting: "Tiene tareas en cola",
  idle: "Disponible",
  offline: "Sin conectar",
};

/** La IA de un socio: su Claude (suscripción vía Claude Code o API key), su cola y lo que está haciendo. */
function ClaudePanel({ owner, onClose }: { owner: Person; onClose: () => void }) {
  const c = owner.claude;
  return (
    <div className="panel" data-testid="claude-panel">
      <div className="panel-title">
        <h3>Claude de {owner.display_name}</h3>
        <button className="small ghost" onClick={onClose} aria-label="Cerrar">
          ✕
        </button>
      </div>
      <dl className="kv">
        <dt>Estado</dt>
        <dd>{CLAUDE_STATE_LABEL[c?.state ?? "offline"]}</dd>
        <dt>Conexión</dt>
        <dd>
          {c?.subscription
            ? "Suscripción de Claude (Claude Code vía MCP)"
            : c?.api
              ? "API de Anthropic (clave propia)"
              : "Sin conectar"}
          {c?.subscription && c.api && " y API key"}
        </dd>
        <dt>Cliente</dt>
        <dd>{c?.client ?? "—"}</dd>
        <dt>Última actividad</dt>
        <dd>{c?.last_used_at ? timeAgo(c.last_used_at) : "nunca"}</dd>
        <dt>En cola</dt>
        <dd>{c?.pending ?? 0} tarea(s)</dd>
        <dt>Trabajando en</dt>
        <dd>
          {c?.working_on ? (
            <Link to={`/projects/${c.working_on.project_id}`}>
              {c.working_on.title} <span className="muted">({c.working_on.project_name})</span>
            </Link>
          ) : c?.state === "working" ? (
            "una tarea privada"
          ) : (
            <span className="muted">nada ahora</span>
          )}
        </dd>
      </dl>
      <p className="small muted">
        {c?.assignable
          ? "Se le pueden asignar tareas desde cualquier proyecto (campo «IA asignada»). Su dueño decide cuándo las atiende su Claude Code."
          : "Para recibir tareas, su dueño debe conectar Claude Code en Configuración de IA."}
      </p>
      <div className="row wrap">
        {owner.is_me ? (
          <Link className="btn small" to="/ai">
            Configuración de IA y tareas
          </Link>
        ) : (
          <Link className="btn small" to="/projects">
            Asignarle una tarea de proyecto
          </Link>
        )}
      </div>
    </div>
  );
}

function RobotLog({ events }: { events: TimeEvent[] | null }) {
  const tz = useBrand((s) => s.org.timezone);
  if (!events) return null;
  return (
    <div className="panel robot-log" data-testid="robot-log">
      <h3>Registros recogidos por el robot</h3>
      {events.length === 0 ? (
        <p className="muted small">Todavía no hay entradas ni salidas registradas.</p>
      ) : (
        <ul className="list">
          {events.map((ev) => (
            <li key={ev.id} className="row small">
              <span>{formatInZone(ev.at, tz)}</span>
              <span>
                {ROBOT_KIND_LABEL[ev.kind] ?? ev.kind} de <strong>{ev.user_name}</strong>
              </span>
              <span className="faint right">
                {ev.source} · auditoría #{ev.audit_id ?? "—"}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function PersonPanel({
  person,
  areaName,
  timezone,
  onClose,
}: {
  person: Person;
  areaName?: string;
  timezone: string;
  onClose: () => void;
}) {
  const offset = useClock((s) => s.offsetMs);
  useTicker(1000);
  return (
    <div className="panel" aria-live="polite" data-testid="person-panel">
      <div className="panel-title">
        <h3>{person.display_name}</h3>
        <button className="small ghost" onClick={onClose} aria-label="Cerrar">
          ✕
        </button>
      </div>
      <dl className="kv">
        <dt>Conexión</dt>
        <dd>
          <span className={`dot ${person.state}`} /> {STATE_LABEL[person.state]}
          {person.message && <span className="muted"> — {person.message}</span>}
        </dd>
        <dt>Área actual</dt>
        <dd>
          {person.area_id ? (
            <Link to={`/areas/${person.area_id}`}>{areaName ?? "ver área"}</Link>
          ) : (
            <span className="muted">ninguna visible</span>
          )}
          {person.area_entered_at && (
            <span className="small muted">
              {" "}
              desde {formatInZone(person.area_entered_at, timezone, { second: undefined })}
            </span>
          )}
        </dd>
        <dt>Sala</dt>
        <dd>{person.online && person.room_key ? ROOM_NAMES[person.room_key] : "—"}</dd>
        <dt>Tarea registrada</dt>
        <dd>
          {person.task ? (
            <Link to={`/projects/${person.task.project_id}`}>
              {person.task.title} <span className="muted">({person.task.project_name})</span>
            </Link>
          ) : (
            <span className="muted">no declarada</span>
          )}
        </dd>
        <dt>Hora de entrada</dt>
        <dd data-testid="person-clock-in">
          {person.clock ? (
            <>
              {formatInZone(person.clock.clock_in_at, timezone)}
              {person.clock.status === "pending_close" && (
                <span className="badge warn"> pendiente de cierre</span>
              )}
            </>
          ) : (
            <span className="muted">sin entrada registrada</span>
          )}
        </dd>
        <dt>Tiempo desde la entrada</dt>
        <dd>
          {person.clock ? (
            formatDuration(elapsedSeconds(person.clock, offset))
          ) : (
            <span className="muted">—</span>
          )}
        </dd>
        <dt>Última actividad</dt>
        <dd>
          {person.last_activity ? (
            <>
              {person.last_activity.summary}{" "}
              <span className="small muted">({timeAgo(person.last_activity.ts)})</span>
            </>
          ) : (
            <span className="muted">sin actividad registrada</span>
          )}
        </dd>
        <dt>Resumen</dt>
        <dd>
          {person.work_summary.in_progress} en curso · {person.work_summary.todo} pendientes ·{" "}
          {person.work_summary.blocked} bloqueadas · {person.work_summary.done_last_7_days} terminadas (7
          días)
        </dd>
        <dt>Proyectos</dt>
        <dd>
          {person.projects.length ? (
            person.projects.map((p, i) => (
              <span key={p.id}>
                {i > 0 && ", "}
                <Link to={`/projects/${p.id}`}>{p.name}</Link>
              </span>
            ))
          ) : (
            <span className="muted">ninguno</span>
          )}
        </dd>
        <dt>Última conexión</dt>
        <dd>
          {person.online ? "ahora" : person.last_seen_at ? formatDateTime(person.last_seen_at) : "nunca"}
        </dd>
      </dl>
    </div>
  );
}
