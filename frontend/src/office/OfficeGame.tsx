import Phaser from "phaser";
import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";
import type { OfficeState, Person } from "../api/types";
import { STATE_LABEL } from "../lib/format";
import { ROOM_BY_KEY, ROOMS, TILE } from "./map";
import {
  deskPositions,
  OfficeScene,
  rackPositions,
  WORLD_H,
  WORLD_W,
  type OfficeBridge,
} from "./OfficeScene";
import type { RobotPhase, RobotTask } from "./robot";
import { ROOM_ICONS, ROOM_NAMES, ROOM_SHORT } from "./rooms";

export interface OfficeGameHandle {
  walkMeTo: (roomKey: string) => void;
  robotCollect: (task: RobotTask) => void;
  focusRoom: (roomKey: string) => void;
  resetView: () => void;
  setAutoZoom: (on: boolean) => void;
}

interface Props {
  state: OfficeState | null;
  selectedId: string | null;
  selectedRoom: string | null;
  onSelectPerson: (id: string) => void;
  onSelectRoom: (key: string) => void;
  onOpenRoom: (key: string) => void;
  onMoveRoom: (key: string) => void;
  onRobotDone?: (task: RobotTask) => void;
  /** Sala enfocada por la cámara (null = vista completa). */
  onFocusChange?: (room: string | null) => void;
  autoZoom?: boolean;
}

const RACK_LABEL: Record<string, string> = {
  database: "BD",
  drive: "Drive",
  claude_api: "API",
  claude_mcp: "MCP",
  jobs: "Cola",
  realtime: "WS",
};

/**
 * Oficina: lienzo Phaser (pixel art) + capa HTML superpuesta para todos los textos (salas, nombres, avisos del
 * robot), que así se leen con una tipografía normal a cualquier escala y son accesibles con teclado.
 */
export const OfficeGame = forwardRef<OfficeGameHandle, Props>(function OfficeGame(props, ref) {
  const hostRef = useRef<HTMLDivElement>(null);
  const canvasHostRef = useRef<HTMLDivElement>(null);
  const tagsRef = useRef<HTMLDivElement>(null);
  const sceneRef = useRef<OfficeScene | null>(null);
  const propsRef = useRef(props);
  propsRef.current = props;
  // scale: escala CSS del lienzo; ox/oy: posición del lienzo; vx/vy/zoom: vista de la cámara (mundo visible).
  const geo = useRef({ scale: 1, ox: 0, oy: 0, vx: 0, vy: 0, zoom: 1 });
  const [geometry, setGeometry] = useState(geo.current);
  const bubblePos = useRef<{ x: number; y: number } | null>(null);
  const tagEls = useRef(new Map<string, HTMLDivElement>());
  const tagPos = useRef(new Map<string, { x: number; y: number; visible: boolean }>());
  const bubbleRef = useRef<HTMLDivElement>(null);
  const overlayRef = useRef<HTMLDivElement>(null);

  // Phaser escucha el ratón también en `window`: un clic en una etiqueta HTML llegaría al mapa de debajo (y dos
  // clics seguidos sobre la misma sala la abrirían). Se corta la propagación en la capa de textos.
  useEffect(() => {
    const el = overlayRef.current;
    if (!el) return;
    const stop = (e: Event) => {
      if (e.target !== el) e.stopPropagation();
    };
    const types = ["mousedown", "mouseup", "pointerdown", "pointerup", "touchstart", "touchend"];
    types.forEach((t) => el.addEventListener(t, stop));
    return () => types.forEach((t) => el.removeEventListener(t, stop));
  }, []);

  const placeTag = useCallback((id: string) => {
    const el = tagEls.current.get(id);
    const pos = tagPos.current.get(id);
    if (!el || !pos) return;
    const { scale, ox, oy, vx, vy, zoom } = geo.current;
    const k = scale * zoom;
    el.style.transform = `translate(${Math.round(ox + (pos.x - vx) * k)}px, ${Math.round(oy + (pos.y - vy) * k)}px) translate(-50%, 0)`;
    el.style.display = pos.visible ? "" : "none";
  }, []);

  const placeBubble = useCallback(() => {
    const el = bubbleRef.current;
    const pos = bubblePos.current;
    if (!el || !pos) return;
    const { scale, ox, oy, vx, vy, zoom } = geo.current;
    const k = scale * zoom;
    el.style.transform = `translate(${Math.round(ox + (pos.x - vx) * k)}px, ${Math.round(oy + (pos.y - vy) * k)}px) translate(-50%, -100%)`;
  }, []);

  useEffect(() => {
    const host = canvasHostRef.current!;
    const bridge: OfficeBridge = {
      onSelectPerson: (id) => propsRef.current.onSelectPerson(id),
      onSelectRoom: (k) => propsRef.current.onSelectRoom(k),
      onOpenRoom: (k) => propsRef.current.onOpenRoom(k),
      onMoveRoom: (k) => propsRef.current.onMoveRoom(k),
      onRobotDone: (t) => propsRef.current.onRobotDone?.(t),
      onTag: (id, x, y, visible) => {
        tagPos.current.set(id, { x, y, visible });
        placeTag(id);
      },
      onRobot: (text, x, y, phase: RobotPhase) => {
        const el = bubbleRef.current;
        if (!el) return;
        if (!text) {
          el.style.display = "none";
          return;
        }
        el.textContent = text;
        el.dataset.phase = phase;
        el.style.display = "";
        bubblePos.current = { x, y };
        placeBubble();
      },
      onCamera: (view) => {
        geo.current = { ...geo.current, vx: view.x, vy: view.y, zoom: view.zoom };
        setGeometry(geo.current);
        for (const id of tagEls.current.keys()) placeTag(id);
        placeBubble();
        propsRef.current.onFocusChange?.(view.focused);
      },
    };
    const scene = new OfficeScene(bridge);
    sceneRef.current = scene;
    const game = new Phaser.Game({
      type: Phaser.CANVAS,
      parent: host,
      width: WORLD_W,
      height: WORLD_H,
      backgroundColor: "#0b0d1c",
      pixelArt: true,
      banner: false,
      audio: { noAudio: true },
      fps: { limit: 30 },
      input: { keyboard: { target: hostRef.current! }, mouse: { preventDefaultWheel: false } },
      scale: { mode: Phaser.Scale.FIT, autoCenter: Phaser.Scale.CENTER_BOTH },
      scene,
    });
    const measure = () => {
      const canvas = host.querySelector("canvas");
      const outer = hostRef.current;
      if (!canvas || !outer) return;
      const c = canvas.getBoundingClientRect();
      const o = outer.getBoundingClientRect();
      const next = { ...geo.current, scale: c.width / WORLD_W, ox: c.left - o.left, oy: c.top - o.top };
      geo.current = next;
      setGeometry(next);
      placeBubble();
      // Por debajo de 1:1 se suaviza el escalado; a partir de 1:1, vecino más cercano (pixel art nítido).
      canvas.style.imageRendering = next.scale >= 1 ? "pixelated" : "auto";
      for (const id of tagEls.current.keys()) placeTag(id);
    };
    game.events.once(Phaser.Core.Events.READY, () => window.setTimeout(measure, 0));
    game.scale.on(Phaser.Scale.Events.RESIZE, () => window.setTimeout(measure, 0));
    const ro = new ResizeObserver(() => window.setTimeout(measure, 30));
    ro.observe(hostRef.current!);
    const wake = () => sceneRef.current?.poke();
    const outer = hostRef.current!;
    outer.addEventListener("pointermove", wake);
    outer.addEventListener("pointerdown", wake);
    outer.addEventListener("keydown", wake);
    // Animaciones discretas a baja frecuencia; nada si la pestaña está oculta.
    const ticker = window.setInterval(() => {
      if (!document.hidden) sceneRef.current?.tick();
    }, 500);
    return () => {
      window.clearInterval(ticker);
      ro.disconnect();
      outer.removeEventListener("pointermove", wake);
      outer.removeEventListener("pointerdown", wake);
      outer.removeEventListener("keydown", wake);
      game.destroy(true);
      sceneRef.current = null;
    };
  }, [placeTag, placeBubble]);

  useEffect(() => {
    if (props.state) sceneRef.current?.setOfficeState(props.state);
  }, [props.state]);

  useEffect(() => {
    sceneRef.current?.setSelected(props.selectedId);
  }, [props.selectedId]);

  useEffect(() => {
    sceneRef.current?.setAutoZoom(props.autoZoom ?? true);
  }, [props.autoZoom]);

  useImperativeHandle(ref, () => ({
    walkMeTo: (roomKey) => sceneRef.current?.walkMeTo(roomKey),
    robotCollect: (task) => sceneRef.current?.robotCollect(task),
    focusRoom: (roomKey) => sceneRef.current?.focusRoom(roomKey),
    resetView: () => sceneRef.current?.resetView(),
    setAutoZoom: (on) => sceneRef.current?.setAutoZoom(on),
  }));

  const { scale, ox, oy, vx, vy, zoom } = geometry;
  const at = (x: number, y: number) => ({
    left: Math.round(ox + (x - vx) * scale * zoom),
    top: Math.round(oy + (y - vy) * scale * zoom),
  });
  const state = props.state;
  const people = state?.people ?? [];
  const compact = scale * zoom < 0.9;
  const detailed = scale * zoom >= 1.6;
  const integrations = new Map((state?.integrations ?? []).map((i) => [i.key, i]));

  return (
    <div
      ref={hostRef}
      className="office-stage"
      tabIndex={0}
      role="application"
      aria-label="Oficina virtual. Use las flechas o WASD para mover su avatar; elija una sala o una persona para ver detalles."
      data-testid="office-canvas"
    >
      <div ref={canvasHostRef} className="office-canvas" />
      <div className="office-overlay" aria-hidden={false} ref={overlayRef}>
        {ROOMS.map((room) => {
          const info = state?.rooms.find((r) => r.key === room.key);
          const occupants = people.filter((p) => p.online && (p.room_key ?? "ops") === room.key).length;
          const disabled = info ? !info.enabled : false;
          const worst =
            room.key === "servers"
              ? (["error", "warn", "ok", "off"] as const).find((s) =>
                  [...integrations.values()].some((i) => i.status === s),
                )
              : undefined;
          return (
            <button
              key={room.key}
              type="button"
              className={`room-label${props.selectedRoom === room.key ? " selected" : ""}${disabled ? " disabled" : ""}`}
              style={at(room.x * TILE + 3, room.y * TILE + 2)}
              onClick={() => props.onSelectRoom(room.key)}
              onDoubleClick={() => props.onOpenRoom(room.key)}
              title={`${ROOM_NAMES[room.key]}${disabled ? " (deshabilitada)" : ""} — doble clic para abrir el módulo`}
              data-testid={`room-${room.key}`}
            >
              <span aria-hidden="true">{ROOM_ICONS[room.key]}</span>{" "}
              {compact ? ROOM_SHORT[room.key] : ROOM_NAMES[room.key]}
              {occupants > 0 && <span className="room-count">{occupants}</span>}
              {worst && <span className={`room-dot ${worst}`} aria-label={`Estado: ${worst}`} />}
              {disabled && <span className="room-off">deshabilitada</span>}
            </button>
          );
        })}
        {!compact &&
          deskPositions().map((d) => {
            const idx = Number(d.tag.slice(-1));
            const owner = people[idx];
            if (!owner) return null;
            const label = owner.display_name.split(" ")[0];
            return (
              <span key={d.tag} className="desk-plate" style={at(d.x, d.y)}>
                {label}
              </span>
            );
          })}
        {!compact &&
          rackPositions().map((r) => {
            const i = integrations.get(r.key);
            return (
              <span
                key={r.key}
                className={`rack-plate ${i?.status ?? "off"}`}
                style={at(r.x, r.y)}
                title={i ? `${i.label}: ${i.detail}` : ""}
              >
                {RACK_LABEL[r.key]}
              </span>
            );
          })}
        {state && (
          <span
            className="status-board"
            style={at((ROOM_BY_KEY.ops.x + 2.3) * TILE, ROOM_BY_KEY.ops.y * TILE - 3)}
          >
            {people.filter((p) => p.online).length}/{people.length} en línea ·{" "}
            {people.filter((p) => p.clock).length} en jornada
          </span>
        )}
        <div ref={tagsRef}>
          {people.map((p) => (
            <div
              key={p.id}
              ref={(el) => {
                if (el) {
                  tagEls.current.set(p.id, el);
                  placeTag(p.id);
                } else tagEls.current.delete(p.id);
              }}
              // En pantallas pequeñas solo se etiquetan las personas conectadas (o la seleccionada).
              className={`name-tag ${p.state}${props.selectedId === p.id ? " selected" : ""}${
                compact && !p.online && props.selectedId !== p.id ? " compact-hidden" : ""
              }`}
              onClick={() => props.onSelectPerson(p.id)}
              title={`${p.display_name} · ${STATE_LABEL[p.state]}`}
              data-testid={`tag-${p.username}`}
            >
              <span className={`dot ${p.state}`} aria-hidden="true" />
              {p.display_name.split(" ")[0]}
            </div>
          ))}
          {people.slice(0, 3).map((p) => (
            <div
              key={`claude:${p.id}`}
              ref={(el) => {
                if (el) {
                  tagEls.current.set(`claude:${p.id}`, el);
                  placeTag(`claude:${p.id}`);
                } else tagEls.current.delete(`claude:${p.id}`);
              }}
              className={`name-tag agent claude ${p.claude?.state ?? "offline"}${compact ? " compact-hidden" : ""}`}
              onClick={() => props.onSelectPerson(`claude:${p.id}`)}
              title={`${p.claude?.name ?? "Claude"} · ${claudeStateText(p.claude)}`}
              data-testid={`claude-tag-${p.username}`}
              data-state={p.claude?.state ?? "offline"}
            >
              <span className={`dot ${CLAUDE_DOT[p.claude?.state ?? "offline"]}`} aria-hidden="true" />
              🤖 {p.display_name.split(" ")[0]}
              {/* Con la sala ampliada hay sitio para el detalle; en la vista completa basta el color. */}
              {detailed && ` · ${claudeStateText(p.claude)}`}
            </div>
          ))}
          {(state?.agents.map((a) => a.key) ?? [])
            .filter((k) => ROOMS.some((r) => r.agentSpots[k]))
            .map((k) => (
              <div
                key={`agent:${k}`}
                ref={(el) => {
                  if (el) {
                    tagEls.current.set(`agent:${k}`, el);
                    placeTag(`agent:${k}`);
                  } else tagEls.current.delete(`agent:${k}`);
                }}
                className={`name-tag agent${compact ? " compact-hidden" : ""}`}
                onClick={() => props.onSelectPerson(`agent:${k}`)}
              >
                {AGENT_SHORT[k] ?? k}
              </div>
            ))}
        </div>
        <div
          ref={bubbleRef}
          className="robot-bubble"
          style={{ display: "none" }}
          role="status"
          data-testid="robot-bubble"
        />
      </div>
    </div>
  );
});

const CLAUDE_DOT: Record<string, string> = {
  working: "working",
  waiting: "paused",
  idle: "online",
  offline: "offline",
};

function claudeStateText(c: Person["claude"] | undefined): string {
  if (!c || c.state === "offline") return "sin conectar";
  if (c.state === "working") return "trabajando";
  if (c.state === "waiting") return `${c.pending} en cola`;
  return "disponible";
}

const AGENT_SHORT: Record<string, string> = {
  memory_agent: "Memory Agent",
  doc_pipeline: "Conversor",
  drive_sync: "Sync Drive",
  claude: "Claude",
};
