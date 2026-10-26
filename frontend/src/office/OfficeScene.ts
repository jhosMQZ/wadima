// Escena Phaser de la oficina compacta. Refleja únicamente el estado real recibido del backend: las personas se
// mueven cuando cambia su sala, los equipos de la sala de servidores muestran el estado comprobado de cada
// integración y el robot recolector solo se mueve cuando llega un evento horario ya guardado en el servidor.
// Los textos (nombres, etiquetas, avisos) no se dibujan en el lienzo: la capa HTML superpuesta los muestra con
// una tipografía legible a cualquier escala.

import Phaser from "phaser";
import type { IntegrationStatus, OfficeState, Person } from "../api/types";
import { drawDoorTile, drawFloorTile, drawFurniture, drawWallTile, furnitureCanvasHeight } from "./furniture";
import {
  buildBlocked,
  COLS,
  furnitureByTag,
  isDoor,
  isFrontWall,
  isWall,
  ROOM_BY_KEY,
  roomAt,
  ROOMS,
  ROWS,
  TILE,
} from "./map";
import { findPath, type Point } from "./pathfinding";
import { COLLECT_KINDS, ROBOT_KIND_LABEL, RobotQueue, type RobotPhase, type RobotTask } from "./robot";
import {
  avatarKey,
  CHAR_H,
  CHAR_W,
  characterSheet,
  COLLECTOR_H,
  COLLECTOR_W,
  drawCheck,
  drawCollector,
  drawIcon,
  drawRobot,
  frameIndex,
  type IconKind,
  type PixelGrid,
  ROBOT_H,
  ROBOT_W,
} from "./sprites";

export const WORLD_W = COLS * TILE;
export const WORLD_H = ROWS * TILE;
const SPEED = 72; // px/s
const ROBOT_SPEED = 96;

export interface OfficeBridge {
  onSelectPerson: (id: string) => void;
  onSelectRoom: (key: string) => void;
  onOpenRoom: (key: string) => void;
  onMoveRoom: (key: string) => void;
  /** Etiquetas HTML que siguen a un personaje (x, y en coordenadas del mundo). */
  onTag?: (id: string, x: number, y: number, visible: boolean) => void;
  /** Bocadillo del robot (null para ocultarlo). */
  onRobot?: (text: string | null, x: number, y: number, phase: RobotPhase) => void;
  onRobotDone?: (task: RobotTask) => void;
  /** Vista de la cámara (esquina superior izquierda visible y zoom) para colocar la capa de textos. */
  onCamera?: (view: CameraView) => void;
}

export interface CameraView {
  x: number;
  y: number;
  zoom: number;
  focused: string | null;
}

const ZOOM_MS = 450;
const MAX_ZOOM = 2.6;

type Dir = "down" | "up" | "left" | "right";

interface Actor {
  id: string;
  sprite: Phaser.GameObjects.Sprite;
  icon: Phaser.GameObjects.Image;
  ring: Phaser.GameObjects.Ellipse;
  path: Point[];
  dir: Dir;
  finalFace?: Dir;
  animT: number;
  moving: boolean;
  textureKey: string;
  targetKey: string;
  online: boolean;
  bob: boolean;
}

interface AgentActor {
  key: string;
  sprite: Phaser.GameObjects.Image;
  color: string;
  state: "idle" | "working" | "error" | "stopped";
  frame: number;
  animT: number;
}

interface Collector {
  sprite: Phaser.GameObjects.Image;
  check: Phaser.GameObjects.Image;
  path: Point[];
  phase: RobotPhase;
  task: RobotTask | null;
  timer: number;
  frame: number;
  animT: number;
}

const CLAUDE_COLOR = "#d97757";

const AGENT_COLORS: Record<string, string> = {
  memory_agent: "#b48cff",
  doc_pipeline: "#ffb347",
  drive_sync: "#5fe0d0",
  claude: "#ff9e7a",
};

const LED_COLORS: Record<IntegrationStatus["status"], [number, number]> = {
  ok: [0x5ee08a, 0x2f7a4a],
  warn: [0xffb347, 0x5a4020],
  error: [0xff5a5a, 0x5a2020],
  off: [0x3a3d4d, 0x3a3d4d],
};

function gridToCanvasTexture(scene: Phaser.Scene, key: string, frames: PixelGrid[], fw: number, fh: number) {
  if (scene.textures.exists(key)) return;
  const canvas = document.createElement("canvas");
  canvas.width = fw * frames.length;
  canvas.height = fh;
  const ctx = canvas.getContext("2d")!;
  frames.forEach((f, i) => f.paint(ctx, i * fw, 0));
  const tex = scene.textures.addCanvas(key, canvas)!;
  frames.forEach((_, i) => tex.add(i, 0, i * fw, 0, fw, fh));
}

export class OfficeScene extends Phaser.Scene {
  private bridge: OfficeBridge;
  private state: OfficeState | null = null;
  private blocked: boolean[][] = buildBlocked();
  private actors = new Map<string, Actor>();
  private agents = new Map<string, AgentActor>();
  private userOrder: string[] = [];
  private selectedId: string | null = null;
  private wakeUntil = 0;
  private ready = false;
  private keys: Record<string, Phaser.Input.Keyboard.Key> = {};
  private meId: string | null = null;
  private manualRoom: string | null = null;
  private roomChangeTimer: Phaser.Time.TimerEvent | null = null;
  private hover!: Phaser.GameObjects.Rectangle;
  private rackLeds = new Map<string, Phaser.GameObjects.Rectangle[]>();
  private rackStatus = new Map<string, IntegrationStatus["status"]>();
  private stationLights: Phaser.GameObjects.Rectangle[] = [];
  private meetingGlow!: Phaser.GameObjects.Rectangle;
  private terminalFlash!: Phaser.GameObjects.Rectangle;
  private orb!: Phaser.GameObjects.Ellipse;
  private tickN = 0;
  private lastClick = { key: "", t: 0 };
  private robot: Collector | null = null;
  private robotQueue = new RobotQueue();
  private autoZoom = true;
  private camTween: Phaser.Tweens.Tween | null = null;
  private focused: string | null = null;
  private lastView = "";

  constructor(bridge: OfficeBridge) {
    super("office");
    this.bridge = bridge;
  }

  // ---------------------------------------------------------------- creación
  create() {
    this.cameras.main.centerOn(WORLD_W / 2, WORLD_H / 2);
    this.drawStatic();
    this.createFurniture();
    this.createRoomZones();
    for (const k of [
      "working",
      "meeting",
      "paused",
      "offline",
      "task",
      "error",
      "online",
      "notify",
    ] as IconKind[])
      gridToCanvasTexture(this, `icon-${k}`, [drawIcon(k)], 10, 10);
    gridToCanvasTexture(this, "icon-check", [drawCheck()], 10, 10);
    this.hover = this.add
      .rectangle(0, 0, 10, 10)
      .setOrigin(0)
      .setStrokeStyle(2, 0xffd166, 0.9)
      .setVisible(false);
    this.hover.setDepth(5000);
    this.createCollector();
    const kb = this.input.keyboard;
    if (kb) {
      kb.disableGlobalCapture();
      this.keys = kb.addKeys("UP,DOWN,LEFT,RIGHT,W,A,S,D", false) as Record<
        string,
        Phaser.Input.Keyboard.Key
      >;
    }
    this.ready = true;
    // Tras cada dibujado se informa de la vista para que la capa HTML acompañe al zoom.
    this.events.on(Phaser.Scenes.Events.RENDER, () => this.emitCamera());
    if (this.state) this.applyState(this.state);
    this.poke(1000);
  }

  // ---------------------------------------------------------------- cámara
  /** Acerca la cámara hasta encuadrar la sala (con un margen) de forma suave. */
  focusRoom(key: string) {
    const room = ROOM_BY_KEY[key];
    if (!room || !this.ready) return;
    const zoom = Math.min(MAX_ZOOM, WORLD_W / ((room.w + 2) * TILE), WORLD_H / ((room.h + 2) * TILE));
    this.focused = key;
    this.animateCamera(zoom, (room.x + room.w / 2) * TILE, (room.y + room.h / 2) * TILE);
  }

  /** Vuelve a la vista completa de la oficina. */
  resetView() {
    if (!this.ready) return;
    this.focused = null;
    this.animateCamera(1, WORLD_W / 2, WORLD_H / 2);
  }

  /** Zoom y desplazamiento a la vez. El centro final se limita para no mostrar nada fuera del mapa (los efectos
   * pan/zoom de Phaser recortan el destino con el zoom inicial, por eso se interpola a mano). */
  private animateCamera(zoom: number, cx: number, cy: number) {
    const cam = this.cameras.main;
    const halfW = WORLD_W / (2 * zoom);
    const halfH = WORLD_H / (2 * zoom);
    const tx = Math.min(Math.max(cx, halfW), WORLD_W - halfW);
    const ty = Math.min(Math.max(cy, halfH), WORLD_H - halfH);
    const from = { zoom: cam.zoom, x: cam.midPoint.x || WORLD_W / 2, y: cam.midPoint.y || WORLD_H / 2 };
    this.camTween?.stop();
    this.camTween = this.tweens.addCounter({
      from: 0,
      to: 1,
      duration: ZOOM_MS,
      ease: "Sine.easeInOut",
      onUpdate: (tw) => {
        const t = tw.getValue() ?? 1;
        cam.setZoom(from.zoom + (zoom - from.zoom) * t);
        cam.centerOn(from.x + (tx - from.x) * t, from.y + (ty - from.y) * t);
      },
    });
    this.poke(ZOOM_MS + 300);
  }

  setAutoZoom(on: boolean) {
    this.autoZoom = on;
    if (!on && this.focused) this.resetView();
  }

  private maybeFocus(key: string) {
    if (this.autoZoom && key !== this.focused) this.focusRoom(key);
  }

  private emitCamera() {
    const v = this.cameras.main.worldView;
    const view: CameraView = { x: v.x, y: v.y, zoom: this.cameras.main.zoom, focused: this.focused };
    const sig = `${view.x.toFixed(1)}:${view.y.toFixed(1)}:${view.zoom.toFixed(3)}:${view.focused}`;
    if (sig !== this.lastView) {
      this.lastView = sig;
      this.bridge.onCamera?.(view);
    }
  }

  private drawStatic() {
    const g = this.make.graphics({ x: 0, y: 0 }, false);
    g.fillStyle(0x0b0d1c, 1);
    g.fillRect(0, 0, WORLD_W, WORLD_H);
    for (const room of ROOMS) {
      for (let y = room.y; y < room.y + room.h; y++)
        for (let x = room.x; x < room.x + room.w; x++) drawFloorTile(g, room.floor, x, y);
    }
    for (let y = 0; y < ROWS; y++) {
      for (let x = 0; x < COLS; x++) {
        if (isDoor(x, y)) drawDoorTile(g, x, y);
        else if (isWall(x, y)) drawWallTile(g, x, y, isFrontWall(x, y));
      }
    }
    const rt = this.add.renderTexture(0, 0, WORLD_W, WORLD_H).setOrigin(0).setDepth(-10);
    rt.draw(g);
    g.destroy();
  }

  private createFurniture() {
    for (const room of ROOMS) {
      for (const f of room.furniture) {
        const key = `furn-${f.type}-${f.w}x${f.h}`;
        const ch = furnitureCanvasHeight(f.type, f.h);
        if (!this.textures.exists(key)) {
          const g = this.make.graphics({ x: 0, y: 0 }, false);
          drawFurniture(g, f.type, f.w, f.h);
          g.generateTexture(key, f.w * TILE, ch);
          g.destroy();
        }
        const wall = ["wallscreen", "whiteboard", "kanban"].includes(f.type);
        const x = f.x * TILE;
        const y = wall ? f.y * TILE - 6 : f.y * TILE - 8;
        const img = this.add.image(x, y, key).setOrigin(0);
        img.setDepth(wall ? 1 : (f.y + f.h) * TILE);
        if (f.tag?.startsWith("home-desk-")) {
          const idx = Number(f.tag.slice(-1));
          img.setInteractive({ useHandCursor: true });
          img.on("pointerdown", (_p: unknown, _x: number, _y: number, ev: Phaser.Types.Input.EventData) => {
            const owner = this.userOrder[idx];
            if (owner) {
              ev.stopPropagation();
              this.bridge.onSelectPerson(owner);
            }
          });
        }
        if (f.type === "rack" && f.tag) {
          const leds = [0, 1, 2].map((i) =>
            this.add
              .rectangle(x + 4, y + 4 + i * 6, 8, 2, 0x3a3d4d)
              .setOrigin(0)
              .setDepth(img.depth + 1),
          );
          this.rackLeds.set(f.tag.replace("rack-", ""), leds);
        }
        if (f.tag?.startsWith("ai-station-")) {
          this.stationLights.push(
            this.add
              .rectangle(x + f.w * 8 - 5, y + 3, 10, 5, 0x2c3150)
              .setOrigin(0)
              .setDepth(img.depth + 1),
          );
        }
        if (f.type === "labtable") {
          this.orb = this.add
            .ellipse(x + (f.w * TILE) / 2, y + 8 + 17, 10, 10, 0x3b3760)
            .setDepth(img.depth + 1);
        }
        if (f.type === "meetingtable") {
          this.meetingGlow = this.add
            .rectangle(x + (f.w * TILE) / 2 - 10, y + 8 + 14, 20, 4, 0x5a5050)
            .setOrigin(0)
            .setDepth(img.depth + 1);
        }
        if (f.tag === "admin-terminal") {
          this.terminalFlash = this.add
            .rectangle(x + 4, y + 2, f.w * TILE - 8, 8, 0x5ee08a, 0)
            .setOrigin(0)
            .setDepth(img.depth + 1);
        }
      }
    }
  }

  private createRoomZones() {
    for (const room of ROOMS) {
      const zone = this.add
        .zone(room.x * TILE, room.y * TILE, room.w * TILE, room.h * TILE)
        .setOrigin(0)
        .setInteractive({ useHandCursor: true });
      zone.setDepth(-5);
      zone.on("pointerover", () => {
        this.hover
          .setPosition(room.x * TILE, room.y * TILE)
          .setSize(room.w * TILE, room.h * TILE)
          .setVisible(true);
        this.poke();
      });
      zone.on("pointerout", () => {
        this.hover.setVisible(false);
        this.poke();
      });
      zone.on("pointerdown", () => {
        const now = Date.now();
        if (this.lastClick.key === room.key && now - this.lastClick.t < 350) this.bridge.onOpenRoom(room.key);
        else this.bridge.onSelectRoom(room.key);
        this.lastClick = { key: room.key, t: now };
      });
    }
  }

  private createCollector() {
    for (const carrying of [false, true])
      for (const processing of [false, true])
        gridToCanvasTexture(
          this,
          `collector-${carrying ? 1 : 0}${processing ? 1 : 0}`,
          [drawCollector(0, carrying, processing), drawCollector(1, carrying, processing)],
          COLLECTOR_W,
          COLLECTOR_H,
        );
    const home = ROOM_BY_KEY.admin.agentSpots.robot;
    const sprite = this.add
      .image(home.x * TILE + 8, home.y * TILE + 16, "collector-00", 0)
      .setOrigin(0.5, 1)
      .setDepth(home.y * TILE + 16);
    const check = this.add.image(0, 0, "icon-check").setOrigin(0.5, 1).setVisible(false).setDepth(4700);
    this.robot = { sprite, check, path: [], phase: "idle", task: null, timer: 0, frame: 0, animT: 0 };
  }

  // ---------------------------------------------------------------- API pública
  setOfficeState(state: OfficeState) {
    this.state = state;
    if (this.ready) this.applyState(state);
  }

  setSelected(id: string | null) {
    this.selectedId = id;
    for (const a of this.actors.values()) a.ring.setVisible(a.id === id);
    this.emitTags();
    this.poke();
  }

  /** Lleva el avatar propio caminando hasta un puesto libre de la sala indicada. */
  walkMeTo(roomKey: string) {
    const me = this.meId ? this.actors.get(this.meId) : undefined;
    const room = ROOM_BY_KEY[roomKey];
    if (!me || !room) return;
    const idx = Math.max(0, this.userOrder.indexOf(me.id));
    const spot = room.spots[idx % room.spots.length];
    this.manualRoom = roomKey;
    this.walkTo(me, spot, spot.face);
    this.scheduleRoomChange(roomKey);
    this.maybeFocus(roomKey);
  }

  /** Representa un evento horario real: el robot recoge el registro y lo lleva a la estación administrativa. */
  robotCollect(task: RobotTask) {
    if (!this.robotQueue.push(task)) return;
    if (this.robot && this.robot.phase === "idle") this.startNextRobotTask();
    this.poke(500);
  }

  /** Avance de animaciones discretas (≈2 veces por segundo): respiración, luces de equipos. */
  tick() {
    if (!this.ready) return;
    this.tickN++;
    for (const a of this.actors.values()) {
      if (!a.online || a.moving) continue;
      if (this.tickN % 2 === 0) {
        a.bob = !a.bob;
        a.sprite.setOrigin(0.5, a.bob ? 1 + 1 / CHAR_H : 1);
      }
    }
    this.paintRacks();
    this.poke(60);
  }

  /** Despierta el bucle de render durante `ms` milisegundos. */
  poke(ms = 1200) {
    if (!this.sys || !this.game) return;
    this.wakeUntil = Math.max(this.wakeUntil, performance.now() + ms);
    if (!this.game.loop.running) this.game.loop.wake();
  }

  positionOf(id: string): Point | null {
    const a = this.actors.get(id);
    return a ? { x: a.sprite.x, y: a.sprite.y } : null;
  }

  // ---------------------------------------------------------------- estado
  private applyState(state: OfficeState) {
    this.userOrder = state.people.map((p) => p.id);
    const seen = new Set<string>();
    for (const person of state.people) {
      seen.add(person.id);
      if (person.is_me) this.meId = person.id;
      this.upsertActor(person);
    }
    for (const [id, actor] of this.actors) {
      if (!seen.has(id)) {
        actor.sprite.destroy();
        actor.icon.destroy();
        actor.ring.destroy();
        this.actors.delete(id);
        this.bridge.onTag?.(id, 0, 0, false);
      }
    }
    this.applyAgents(state);
    this.applyEquipment(state);
    this.emitTags();
    this.poke(1500);
  }

  private homeSpot(person: Person): { room: string; spot: Point; face: Dir } {
    const idx = Math.max(0, this.userOrder.indexOf(person.id));
    const roomKey =
      person.online && person.room_key && ROOM_BY_KEY[person.room_key] ? person.room_key : "ops";
    const room = ROOM_BY_KEY[roomKey];
    const s = room.spots[idx % room.spots.length];
    return { room: roomKey, spot: { x: s.x, y: s.y }, face: s.face };
  }

  private upsertActor(person: Person) {
    const texKey = avatarKey(person.avatar);
    gridToCanvasTexture(this, texKey, characterSheet(person.avatar), CHAR_W, CHAR_H);
    const home = this.homeSpot(person);
    let actor = this.actors.get(person.id);
    if (!actor) {
      const sprite = this.add
        .sprite(home.spot.x * TILE + 8, home.spot.y * TILE + 16, texKey, frameIndex("down", 0))
        .setOrigin(0.5, 1)
        .setInteractive({ useHandCursor: true, pixelPerfect: false });
      sprite.on("pointerdown", (_p: unknown, _x: number, _y: number, ev: Phaser.Types.Input.EventData) => {
        ev.stopPropagation();
        this.bridge.onSelectPerson(person.id);
      });
      const icon = this.add.image(0, 0, "icon-online").setOrigin(0.5, 1);
      const ring = this.add.ellipse(0, 0, 18, 7).setStrokeStyle(1, 0xffd166).setVisible(false);
      actor = {
        id: person.id,
        sprite,
        icon,
        ring,
        path: [],
        dir: home.face,
        animT: 0,
        moving: false,
        textureKey: texKey,
        targetKey: `${home.room}:${home.spot.x},${home.spot.y}`,
        online: person.online,
        bob: false,
      };
      this.actors.set(person.id, actor);
      if (person.is_me && person.online) this.manualRoom = home.room;
    }
    if (actor.textureKey !== texKey) {
      actor.sprite.setTexture(texKey, frameIndex("down", 0));
      actor.textureKey = texKey;
    }
    actor.online = person.online;
    if (!person.online) {
      actor.bob = false;
      actor.sprite.setOrigin(0.5, 1);
    }
    const iconKind: IconKind = !person.online
      ? "offline"
      : person.is_me && person.unread_notifications > 0
        ? "notify"
        : person.state === "working"
          ? "working"
          : person.state === "meeting"
            ? "meeting"
            : person.state === "paused"
              ? "paused"
              : person.task
                ? "task"
                : "online";
    actor.icon.setTexture(`icon-${iconKind}`);
    actor.sprite.setAlpha(person.online ? 1 : 0.45);
    actor.ring.setVisible(this.selectedId === person.id);
    const targetKey = `${home.room}:${home.spot.x},${home.spot.y}`;
    const isMeManual = person.is_me && person.online && this.manualRoom === home.room;
    if (targetKey !== actor.targetKey) {
      actor.targetKey = targetKey;
      if (!isMeManual) {
        if (person.online) this.walkTo(actor, home.spot, home.face);
        else this.teleport(actor, home.spot, home.face);
        if (person.is_me && person.online) this.maybeFocus(home.room);
      }
    } else if (!actor.moving && !isMeManual) {
      actor.dir = home.face;
    }
    if (person.is_me && person.online) this.manualRoom = home.room;
    this.syncDecor(actor);
  }

  private applyAgents(state: OfficeState) {
    const list: { key: string; status: string; heartbeat: string | null }[] = state.agents.map((a) => ({
      key: a.key,
      status: a.status,
      heartbeat: a.heartbeat_at,
    }));
    const anyClaude = state.people.some((p) => p.claude?.connected);
    const now = Date.now();
    for (const a of list) {
      const room = ROOMS.find((r) => r.agentSpots[a.key]);
      if (!room) continue;
      const spot = room.agentSpots[a.key];
      const stale = !a.heartbeat || now - new Date(a.heartbeat).getTime() > 3 * 60_000;
      const st: AgentActor["state"] =
        a.status === "error"
          ? "error"
          : a.status === "working" && !stale
            ? "working"
            : stale || a.status === "stopped"
              ? "stopped"
              : "idle";
      let actor = this.agents.get(a.key);
      if (!actor) {
        const sprite = this.add.image(spot.x * TILE + 8, spot.y * TILE + 16, "__DEFAULT").setOrigin(0.5, 1);
        sprite.setInteractive({ useHandCursor: true });
        sprite.on("pointerdown", (_p: unknown, _x: number, _y: number, ev: Phaser.Types.Input.EventData) => {
          ev.stopPropagation();
          this.bridge.onSelectPerson(`agent:${a.key}`);
        });
        actor = {
          key: a.key,
          sprite,
          color: AGENT_COLORS[a.key] ?? "#6fd3ff",
          state: st,
          frame: 0,
          animT: 0,
        };
        this.agents.set(a.key, actor);
      }
      actor.state = st;
      this.setRobotTexture(actor);
      actor.sprite.setDepth(spot.y * TILE + 16);
      this.bridge.onTag?.(`agent:${a.key}`, spot.x * TILE + 8, spot.y * TILE + 17, true);
    }
    this.applyClaudes(state);
    this.orb?.setFillStyle(anyClaude ? 0xff9e7a : 0x3b3760);
  }

  /** Un Claude por socio en su puesto del laboratorio de IA: trabajando, en espera, disponible o sin conectar. */
  private applyClaudes(state: OfficeState) {
    for (let i = 0; i < 3; i++) {
      const key = `claude-${i}`;
      const spot = ROOM_BY_KEY.ai.agentSpots[key];
      const person = state.people[i];
      let actor = this.agents.get(key);
      if (!person || !spot) {
        actor?.sprite.setVisible(false);
        continue;
      }
      const c = person.claude;
      const st: AgentActor["state"] = c?.state === "working" ? "working" : c?.connected ? "idle" : "stopped";
      if (!actor) {
        const sprite = this.add.image(spot.x * TILE + 8, spot.y * TILE + 16, "__DEFAULT").setOrigin(0.5, 1);
        sprite.setInteractive({ useHandCursor: true });
        sprite.on("pointerdown", (_p: unknown, _x: number, _y: number, ev: Phaser.Types.Input.EventData) => {
          ev.stopPropagation();
          const owner = this.state?.people[i];
          if (owner) this.bridge.onSelectPerson(`claude:${owner.id}`);
        });
        actor = { key, sprite, color: CLAUDE_COLOR, state: st, frame: 0, animT: 0 };
        this.agents.set(key, actor);
      }
      actor.sprite.setVisible(true);
      actor.state = st;
      this.setRobotTexture(actor);
      actor.sprite.setDepth(spot.y * TILE + 16);
      this.bridge.onTag?.(`claude:${person.id}`, spot.x * TILE + 8, spot.y * TILE + 17, true);
    }
  }

  /** Equipos ligados a datos reales: racks por integración, puestos de IA por socio y la mesa de reuniones. */
  private applyEquipment(state: OfficeState) {
    this.rackStatus = new Map(state.integrations.map((i) => [i.key, i.status]));
    this.paintRacks(true);
    this.stationLights.forEach((light, i) => {
      const c = state.people[i]?.claude;
      light.setFillStyle(
        c?.state === "working"
          ? 0xff9e7a
          : c?.state === "waiting"
            ? 0xffb347
            : c?.connected
              ? 0x5ee08a
              : 0x2c3150,
      );
    });
    const inMeeting = state.people.some((p) => p.online && p.state === "meeting" && p.room_key === "meeting");
    this.meetingGlow?.setFillStyle(inMeeting ? 0x5ee08a : 0x5a5050);
  }

  private paintRacks(force = false) {
    const on = this.tickN % 2 === 0;
    for (const [key, leds] of this.rackLeds) {
      const status = this.rackStatus.get(key) ?? "off";
      const [bright, dim] = LED_COLORS[status];
      // ok: luz fija con un parpadeo breve de actividad; warn/error: parpadeo; off: apagado.
      if (!force && status === "off") continue;
      leds.forEach((led, i) => {
        let c = bright;
        if (status === "ok") c = i === this.tickN % 3 && on ? dim : bright;
        else if (status !== "off") c = (i + this.tickN) % 2 ? bright : dim;
        led.setFillStyle(c);
      });
    }
  }

  private setRobotTexture(actor: AgentActor) {
    const key = `robot-${actor.key}-${actor.state}-${actor.frame}`;
    gridToCanvasTexture(this, key, [drawRobot(actor.color, actor.frame, actor.state)], ROBOT_W, ROBOT_H);
    actor.sprite.setTexture(key, 0);
    actor.sprite.setAlpha(actor.state === "stopped" ? 0.55 : 1);
  }

  // ---------------------------------------------------------------- robot recolector
  private startNextRobotTask() {
    const robot = this.robot;
    if (!robot) return;
    const task = this.robotQueue.next();
    if (!task) {
      robot.phase = "idle";
      robot.task = null;
      this.returnRobotHome();
      return;
    }
    robot.task = task;
    const target = COLLECT_KINDS.has(task.kind) ? this.positionOf(task.userId) : null;
    if (target) {
      robot.phase = "to_person";
      const goal = this.freeNear({ x: Math.floor(target.x / TILE), y: Math.floor((target.y - 1) / TILE) });
      this.robotWalk(goal);
      this.robotSay(`Voy a recoger la ${ROBOT_KIND_LABEL[task.kind] ?? "operación"} de ${task.userName}`);
    } else {
      robot.phase = "to_station";
      this.robotWalk(ROOM_BY_KEY.admin.agentSpots.robot);
      this.robotSay(`Registro de ${task.userName}: ${ROBOT_KIND_LABEL[task.kind] ?? task.kind}`);
    }
  }

  private returnRobotHome() {
    const home = ROOM_BY_KEY.admin.agentSpots.robot;
    this.robotWalk(home);
    this.robotSay(null);
  }

  private robotWalk(goal: Point) {
    const robot = this.robot!;
    const from = { x: Math.floor(robot.sprite.x / TILE), y: Math.floor((robot.sprite.y - 1) / TILE) };
    const path = findPath(this.blocked, from, goal);
    robot.path = path ? path.slice(1) : [];
    if (!path) robot.sprite.setPosition(goal.x * TILE + 8, goal.y * TILE + 16);
  }

  /** Casilla libre más cercana a un punto (el robot se coloca al lado de la persona). */
  private freeNear(p: Point): Point {
    const cand = [
      { x: p.x + 1, y: p.y },
      { x: p.x - 1, y: p.y },
      { x: p.x, y: p.y + 1 },
      { x: p.x, y: p.y - 1 },
      { x: p.x + 1, y: p.y + 1 },
      { x: p.x - 1, y: p.y + 1 },
    ];
    return cand.find((c) => c.y >= 0 && c.y < ROWS && c.x >= 0 && c.x < COLS && !this.blocked[c.y][c.x]) ?? p;
  }

  private robotSay(text: string | null) {
    const r = this.robot;
    if (!r) return;
    this.bridge.onRobot?.(text, r.sprite.x, r.sprite.y - COLLECTOR_H, r.phase);
  }

  private stepRobot(dt: number): boolean {
    const r = this.robot;
    if (!r) return false;
    try {
      if (r.path.length) {
        const next = r.path[0];
        const tx = next.x * TILE + 8;
        const ty = next.y * TILE + 16;
        const dx = tx - r.sprite.x;
        const dy = ty - r.sprite.y;
        const dist = Math.hypot(dx, dy);
        const step = ROBOT_SPEED * dt;
        if (dist <= step) {
          r.sprite.setPosition(tx, ty);
          r.path.shift();
        } else {
          r.sprite.setPosition(r.sprite.x + (dx / dist) * step, r.sprite.y + (dy / dist) * step);
        }
        if (dx) r.sprite.setFlipX(dx > 0);
        r.animT += dt;
        r.frame = Math.floor(r.animT / 0.12) % 2;
        r.sprite.setDepth(r.sprite.y);
        this.setCollectorTexture();
        this.robotSay(this.robotText());
        return true;
      }
      if (r.phase === "to_person") {
        r.phase = "collecting";
        r.timer = 0.9;
        this.robotSay(this.robotText());
        return true;
      }
      if (r.phase === "collecting") {
        r.timer -= dt;
        r.sprite.setOrigin(0.5, Math.floor(r.timer * 8) % 2 ? 1.06 : 1);
        if (r.timer <= 0) {
          r.sprite.setOrigin(0.5, 1);
          r.phase = "to_station";
          this.robotWalk(ROOM_BY_KEY.admin.agentSpots.robot);
          this.setCollectorTexture();
        }
        return true;
      }
      if (r.phase === "to_station") {
        r.phase = "processing";
        r.timer = 1.1;
        this.terminalFlash?.setFillStyle(0x5ee08a, 0.6);
        r.check.setPosition(r.sprite.x, r.sprite.y - COLLECTOR_H - 2).setVisible(true);
        this.setCollectorTexture();
        this.robotSay(this.robotText());
        return true;
      }
      if (r.phase === "processing") {
        r.timer -= dt;
        this.terminalFlash?.setFillStyle(
          0x5ee08a,
          r.timer > 0 ? 0.2 + 0.4 * (Math.floor(r.timer * 6) % 2) : 0,
        );
        if (r.timer <= 0) {
          r.check.setVisible(false);
          const done = r.task;
          r.task = null;
          r.phase = "idle";
          this.setCollectorTexture();
          if (done) this.bridge.onRobotDone?.(done);
          this.startNextRobotTask();
        }
        return true;
      }
    } catch (err) {
      // La animación nunca afecta al registro: si falla, el robot vuelve a su sitio y se informa en consola.
      console.warn("[oficina] fallo en la animación del robot", err);
      r.phase = "idle";
      r.task = null;
      r.path = [];
      r.check.setVisible(false);
      this.robotSay(null);
    }
    return false;
  }

  private robotText(): string | null {
    const r = this.robot;
    if (!r?.task) return null;
    const what = ROBOT_KIND_LABEL[r.task.kind] ?? r.task.kind;
    switch (r.phase) {
      case "to_person":
        return `Voy a recoger la ${what} de ${r.task.userName}`;
      case "collecting":
        return `Recogiendo la ${what} de ${r.task.userName}`;
      case "to_station":
        return `Llevando la ${what} de ${r.task.userName} a Administración`;
      case "processing":
        return `${what[0].toUpperCase()}${what.slice(1)} de ${r.task.userName} registrada ✓`;
      default:
        return null;
    }
  }

  private setCollectorTexture() {
    const r = this.robot!;
    const carrying = r.phase === "to_station" || r.phase === "collecting";
    const processing = r.phase === "processing";
    r.sprite.setTexture(`collector-${carrying ? 1 : 0}${processing ? 1 : 0}`, r.frame);
  }

  // ---------------------------------------------------------------- movimiento
  private teleport(actor: Actor, spot: Point, face: Dir) {
    actor.path = [];
    actor.moving = false;
    actor.sprite.setPosition(spot.x * TILE + 8, spot.y * TILE + 16);
    actor.dir = face;
    this.syncDecor(actor);
  }

  private walkTo(actor: Actor, spot: Point, finalFace?: Dir) {
    const from = { x: Math.floor(actor.sprite.x / TILE), y: Math.floor((actor.sprite.y - 1) / TILE) };
    const path = findPath(this.blocked, from, spot);
    if (!path) {
      this.teleport(actor, spot, finalFace ?? "down");
      return;
    }
    actor.path = path.slice(1);
    actor.moving = actor.path.length > 0;
    actor.finalFace = finalFace;
    actor.sprite.setOrigin(0.5, 1);
    this.poke(500);
  }

  private scheduleRoomChange(roomKey: string) {
    this.roomChangeTimer?.remove();
    this.roomChangeTimer = this.time.delayedCall(700, () => this.bridge.onMoveRoom(roomKey));
  }

  private stepActor(actor: Actor, dt: number) {
    if (!actor.path.length) {
      if (actor.moving) {
        actor.moving = false;
        if (actor.finalFace) actor.dir = actor.finalFace;
      }
      return;
    }
    const next = actor.path[0];
    const tx = next.x * TILE + 8;
    const ty = next.y * TILE + 16;
    const dx = tx - actor.sprite.x;
    const dy = ty - actor.sprite.y;
    const dist = Math.hypot(dx, dy);
    const step = SPEED * dt;
    if (Math.abs(dx) > Math.abs(dy)) actor.dir = dx < 0 ? "left" : "right";
    else if (dy !== 0) actor.dir = dy < 0 ? "up" : "down";
    if (dist <= step) {
      actor.sprite.setPosition(tx, ty);
      actor.path.shift();
    } else {
      actor.sprite.setPosition(actor.sprite.x + (dx / dist) * step, actor.sprite.y + (dy / dist) * step);
    }
    actor.moving = true;
  }

  private moveManually(actor: Actor, dt: number): boolean {
    const k = this.keys;
    if (!k.UP) return false;
    let vx = 0;
    let vy = 0;
    if (k.LEFT.isDown || k.A.isDown) vx -= 1;
    if (k.RIGHT.isDown || k.D.isDown) vx += 1;
    if (k.UP.isDown || k.W.isDown) vy -= 1;
    if (k.DOWN.isDown || k.S.isDown) vy += 1;
    if (!vx && !vy) return false;
    actor.path = [];
    const len = Math.hypot(vx, vy);
    const nx = actor.sprite.x + (vx / len) * SPEED * dt;
    const ny = actor.sprite.y + (vy / len) * SPEED * dt;
    const free = (x: number, y: number) =>
      [-4, 4].every((ox) => {
        const tx = Math.floor((x + ox) / TILE);
        const ty = Math.floor((y - 2) / TILE);
        return ty >= 0 && ty < ROWS && tx >= 0 && tx < COLS && !this.blocked[ty][tx];
      });
    if (free(nx, actor.sprite.y)) actor.sprite.x = nx;
    if (free(actor.sprite.x, ny)) actor.sprite.y = ny;
    actor.dir = Math.abs(vx) >= Math.abs(vy) ? (vx < 0 ? "left" : "right") : vy < 0 ? "up" : "down";
    actor.moving = true;
    const room = roomAt(Math.floor(actor.sprite.x / TILE), Math.floor((actor.sprite.y - 2) / TILE));
    if (room && room.key !== this.manualRoom) {
      this.manualRoom = room.key;
      this.scheduleRoomChange(room.key);
      this.maybeFocus(room.key);
    }
    return true;
  }

  private syncDecor(actor: Actor) {
    const { x, y } = actor.sprite;
    actor.sprite.setDepth(y);
    actor.icon.setPosition(Math.round(x + 7), Math.round(y - CHAR_H + 2)).setDepth(4600);
    actor.ring.setPosition(Math.round(x), Math.round(y - 1)).setDepth(y - 1);
    const sideDir = actor.dir === "left" || actor.dir === "right";
    const frame = actor.moving ? 1 + (Math.floor(actor.animT / 0.15) % 2) : 0;
    actor.sprite.setFrame(frameIndex(sideDir ? "side" : (actor.dir as "down" | "up"), frame));
    actor.sprite.setFlipX(actor.dir === "right");
    this.bridge.onTag?.(actor.id, x, y + 1, true);
  }

  private emitTags() {
    for (const a of this.actors.values()) this.bridge.onTag?.(a.id, a.sprite.x, a.sprite.y + 1, true);
  }

  // ---------------------------------------------------------------- bucle
  update(_time: number, deltaMs: number) {
    const dt = Math.min(deltaMs, 100) / 1000;
    let busy = false;
    for (const actor of this.actors.values()) {
      const manual = actor.id === this.meId && this.moveManually(actor, dt);
      if (!manual) this.stepActor(actor, dt);
      if (actor.moving) {
        actor.animT += dt;
        busy = true;
        this.syncDecor(actor);
      }
      if (manual && !this.keysDown()) actor.moving = false;
    }
    for (const agent of this.agents.values()) {
      if (agent.state === "working") {
        agent.animT += dt;
        const f = Math.floor(agent.animT / 0.25) % 3;
        if (f !== agent.frame) {
          agent.frame = f;
          this.setRobotTexture(agent);
        }
        busy = true;
      } else if (agent.frame !== 0) {
        agent.frame = 0;
        this.setRobotTexture(agent);
      }
    }
    if (this.stepRobot(dt)) busy = true;
    if (this.keysDown()) busy = true;
    if (this.camTween?.isPlaying()) busy = true;
    // Ahorro de recursos: sin movimiento ni animaciones reales, el bucle se duerme.
    if (!busy && performance.now() > this.wakeUntil) this.game.loop.sleep();
  }

  private keysDown(): boolean {
    return Object.values(this.keys).some((k) => k.isDown);
  }
}

export function deskPositions(): { tag: string; x: number; y: number }[] {
  const out: { tag: string; x: number; y: number }[] = [];
  for (const tag of ["home-desk-0", "home-desk-1", "home-desk-2"]) {
    const f = furnitureByTag(tag);
    if (f) out.push({ tag, x: (f.x + f.w / 2) * TILE, y: (f.y + f.h) * TILE + 2 });
  }
  return out;
}

export function rackPositions(): { key: string; x: number; y: number }[] {
  return ["database", "drive", "claude_api", "claude_mcp", "jobs", "realtime"].map((key) => {
    const f = furnitureByTag(`rack-${key}`)!;
    return { key, x: (f.x + 0.5) * TILE, y: (f.y + f.h) * TILE + 2 };
  });
}
