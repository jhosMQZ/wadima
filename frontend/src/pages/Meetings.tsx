import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { post } from "../api/client";
import type { MeetingPeer } from "../api/types";
import { EmptyState, ErrorBox, Spinner } from "../components/ui";
import { useWorkArea } from "../lib/areas";
import { formatDateTime } from "../lib/format";
import { MeetingClient, micSupported, type MeetingSnapshot } from "../lib/meeting";
import { useApi } from "../lib/useApi";
import { useBrand } from "../store/brand";
import { useSession } from "../store/session";
import { Decisions } from "./ProjectDetail";

interface NoteRow {
  id: string;
  title: string;
  updated_at: string;
  version: number;
}

interface RoomRow {
  area_id: string;
  name: string;
  template_key: string | null;
  participants: MeetingPeer[];
}

const CONNECTION_LABEL: Record<string, string> = {
  new: "conectando…",
  connecting: "conectando…",
  connected: "conectado",
  disconnected: "conexión inestable",
  failed: "sin audio",
  closed: "cerrado",
};

export default function MeetingsPage() {
  const org = useBrand((s) => s.org);
  const rooms = useApi<RoomRow[]>("/api/meetings/rooms", { refreshOn: ["meeting.changed", "area.updated"] });
  const { area: meetingArea } = useWorkArea("meeting");
  const { area: fallback } = useWorkArea("ops");
  const room = rooms.data?.[0];
  const notesArea = meetingArea ?? fallback;
  return (
    <>
      <div className="page-head">
        <div className="grow">
          <h1>Sala de reuniones</h1>
          <p>
            {org.legal_name}: audio en tiempo real entre los socios, actas y decisiones. Las conversaciones no
            se graban ni se almacenan.
          </p>
        </div>
      </div>
      {rooms.error && <ErrorBox error={rooms.error} onRetry={() => rooms.reload()} />}
      {!rooms.data && !rooms.error && <Spinner />}
      {rooms.data && !room && (
        <EmptyState title="No hay una sala de reuniones habilitada">
          Un administrador puede habilitar el área «Reuniones» en <Link to="/areas">Áreas</Link>.
        </EmptyState>
      )}
      {room && <AudioRoom room={room} />}
      {notesArea && <MinutesAndDecisions areaId={notesArea.id} areaName={notesArea.name} />}
    </>
  );
}

function AudioRoom({ room }: { room: RoomRow }) {
  const me = useSession((s) => s.me)!;
  const [client] = useState(() => new MeetingClient());
  const [snap, setSnap] = useState<MeetingSnapshot>(() => client.state);
  const [busy, setBusy] = useState(false);
  const support = micSupported();

  useEffect(() => {
    client.attach();
    const off = client.subscribe(setSnap);
    return () => {
      off();
      client.detach(); // salir de la página saca de la reunión sin cerrar la aplicación
    };
  }, [client]);

  const inRoom = snap.status === "joined" || snap.status === "joining" || snap.status === "reconnecting";
  const others = room.participants.filter((p) => p.user_id !== me.user.id);

  return (
    <div className="panel meeting" data-testid="meeting-room" data-status={snap.status}>
      <div className="panel-title">
        <h3>{room.name}</h3>
        <span className="small muted">
          {room.participants.length} conectado(s)
          {inRoom ? "" : others.length ? `: ${others.map((p) => p.display_name).join(", ")}` : ""}
        </span>
      </div>
      {!support.ok && <div className="alert warn">{support.reason}</div>}
      {snap.error && (
        <div className={`alert ${snap.status === "reconnecting" ? "warn" : "err"}`} role="alert">
          {snap.error}
        </div>
      )}
      {!inRoom ? (
        <div className="row wrap">
          <button className="primary" onClick={() => client.join(room.area_id)} data-testid="meeting-join">
            Unirse a la reunión
          </button>
          <span className="small muted">Entrará sin micrófono; podrá activarlo después.</span>
        </div>
      ) : (
        <>
          <div className="row wrap meeting-controls">
            {snap.micOn ? (
              <button onClick={() => client.disableMic()} data-testid="mic-toggle" aria-pressed="true">
                Silenciar micrófono
              </button>
            ) : (
              <button
                className="primary"
                disabled={busy || !support.ok}
                data-testid="mic-toggle"
                aria-pressed="false"
                onClick={async () => {
                  setBusy(true);
                  await client.enableMic(snap.inputId || undefined);
                  setBusy(false);
                }}
              >
                {busy ? "Solicitando permiso…" : "Activar micrófono"}
              </button>
            )}
            {snap.inputs.length > 0 && (
              <label className="field inline">
                Micrófono
                <select
                  value={snap.inputId}
                  onChange={(e) => void client.setInput(e.target.value)}
                  aria-label="Dispositivo de entrada"
                >
                  {snap.inputs.map((d, i) => (
                    <option key={d.deviceId || i} value={d.deviceId}>
                      {d.label || `Micrófono ${i + 1}`}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {snap.outputs.length > 0 && (
              <label className="field inline">
                Altavoz
                <select
                  value={snap.outputId}
                  onChange={(e) => client.setOutput(e.target.value)}
                  aria-label="Dispositivo de salida"
                >
                  <option value="">Predeterminado</option>
                  {snap.outputs.map((d, i) => (
                    <option key={d.deviceId || i} value={d.deviceId}>
                      {d.label || `Salida ${i + 1}`}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <button className="danger right" onClick={() => client.leave()} data-testid="meeting-leave">
              Salir de la reunión
            </button>
          </div>
          {snap.micError && (
            <div className="alert err" role="alert" data-testid="mic-error">
              {snap.micError}
            </div>
          )}
          <ul className="meeting-peers" aria-label="Participantes">
            <li className={`peer-tile me${snap.speaking ? " speaking" : ""}`} data-testid="peer-me">
              <strong>{me.user.display_name} (usted)</strong>
              <span className="small">
                {snap.micOn ? (snap.speaking ? "hablando" : "micrófono activo") : "micrófono apagado"}
              </span>
              <span className="small muted">
                {snap.status === "joined"
                  ? "en la sala"
                  : snap.status === "joining"
                    ? "entrando…"
                    : "reconectando…"}
              </span>
            </li>
            {snap.peers.map((p) => (
              <li
                key={p.peer_id}
                className={`peer-tile${p.speaking ? " speaking" : ""}${p.connection === "failed" ? " failed" : ""}`}
                data-testid="peer-remote"
                data-connection={p.connection}
              >
                <strong>{p.display_name}</strong>
                <span className="small">
                  {p.muted ? "micrófono apagado" : p.speaking ? "hablando" : "micrófono activo"}
                </span>
                <span className="small muted">{CONNECTION_LABEL[p.connection] ?? p.connection}</span>
                <label className="small">
                  Volumen
                  <input
                    type="range"
                    min={0}
                    max={1}
                    step={0.05}
                    value={p.volume}
                    onChange={(e) => client.setVolume(p.peer_id, Number(e.target.value))}
                    aria-label={`Volumen de ${p.display_name}`}
                  />
                </label>
                {p.error && <span className="small err-text">{p.error}</span>}
              </li>
            ))}
          </ul>
          {snap.peers.length === 0 && snap.status === "joined" && (
            <p className="muted small">Está solo en la sala. Los demás socios aparecerán al unirse.</p>
          )}
        </>
      )}
    </div>
  );
}

function MinutesAndDecisions({ areaId, areaName }: { areaId: string; areaName: string }) {
  const navigate = useNavigate();
  const notes = useApi<NoteRow[]>(`/api/notes?area_id=${areaId}&kind=meeting`, {
    refreshOn: ["note.changed"],
  });
  const org = useBrand((s) => s.org);
  const [title, setTitle] = useState("");
  return (
    <>
      <div className="grid cols-2">
        <div className="panel">
          <h3>Actas</h3>
          <form
            className="row"
            onSubmit={async (e) => {
              e.preventDefault();
              const today = new Date().toISOString().slice(0, 10);
              const name = title || `Reunión ${today}`;
              const n = await post<{ id: string }>("/api/notes", {
                area_id: areaId,
                title: name,
                kind: "meeting",
                content: `# ${name}\n\n**Organización:** ${org.legal_name}\n\n**Asistentes:** \n\n## Orden del día\n\n## Acuerdos\n\n## Tareas\n`,
              });
              navigate(`/notes/${n.id}`);
            }}
          >
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Título del acta (opcional)"
              style={{ flex: 1 }}
              aria-label="Título del acta"
            />
            <button className="primary">Nueva acta</button>
          </form>
          {notes.data?.length === 0 && <EmptyState title="Sin actas" />}
          <ul className="list" style={{ marginTop: 8 }}>
            {notes.data?.map((n) => (
              <li key={n.id} className="row">
                <Link to={`/notes/${n.id}`}>{n.title}</Link>
                <span className="small faint right">{formatDateTime(n.updated_at)}</span>
              </li>
            ))}
          </ul>
        </div>
        <div className="panel">
          <h3>Privacidad</h3>
          <ul className="small">
            <li>
              El audio va directamente entre los navegadores de los participantes; el servidor solo coordina
              la conexión.
            </li>
            <li>
              No se graba ni se guarda audio. Una futura grabación sería una función aparte, explícita y
              autorizada.
            </li>
            <li>El permiso del micrófono se pide únicamente al pulsar «Activar micrófono».</li>
          </ul>
        </div>
      </div>
      <h2>Decisiones del área «{areaName}»</h2>
      <Decisions areaId={areaId} />
    </>
  );
}
