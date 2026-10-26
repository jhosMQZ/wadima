import { describe, expect, it } from "vitest";
import { buildBlocked, COLS, DOORS, furnitureByTag, isWall, ROOM_BY_KEY, roomAt, ROOMS, ROWS } from "./map";
import { findPath } from "./pathfinding";
import { COLLECT_KINDS, ROBOT_KIND_LABEL, RobotQueue } from "./robot";
import {
  CHAR_H,
  CHAR_W,
  characterSheet,
  drawCheck,
  drawCollector,
  drawIcon,
  drawRobot,
  frameIndex,
  PixelGrid,
  shade,
} from "./sprites";

const neighbours = (x: number, y: number) => [
  [x - 1, y],
  [x + 1, y],
  [x, y - 1],
  [x, y + 1],
];

describe("mapa de la oficina", () => {
  const blocked = buildBlocked();

  it("tiene las siete salas del backend", () => {
    expect(ROOMS.map((r) => r.key).sort()).toEqual([
      "admin",
      "ai",
      "dev",
      "library",
      "meeting",
      "ops",
      "servers",
    ]);
  });

  it("es compacto: cada sala tiene puerta directa al centro de operaciones", () => {
    expect(COLS * ROWS).toBeLessThanOrEqual(40 * 27);
    for (const room of ROOMS.filter((r) => r.key !== "ops")) {
      const door = DOORS.some((d) => {
        const keys = neighbours(d.x, d.y).map(([x, y]) => roomAt(x, y)?.key);
        return keys.includes(room.key) && keys.includes("ops");
      });
      expect(door, `${room.key} sin puerta a operaciones`).toBe(true);
    }
  });

  it("los recorridos son cortos", () => {
    const blockedGrid = buildBlocked();
    const from = ROOM_BY_KEY.ops.spots[0];
    for (const room of ROOMS) {
      const path = findPath(blockedGrid, from, room.spots[0])!;
      expect(path.length, room.key).toBeLessThan(40);
    }
  });

  it("los equipos representan cada integración y cada escritorio a un socio", () => {
    for (const key of ["database", "drive", "claude_api", "claude_mcp", "jobs", "realtime"])
      expect(furnitureByTag(`rack-${key}`)?.type).toBe("rack");
    expect(furnitureByTag("admin-terminal")?.type).toBe("terminal");
    expect(ROOM_BY_KEY.admin.agentSpots.robot).toBeDefined();
    expect(
      [0, 1, 2].every((i) => furnitureByTag(`home-desk-${i}`) && furnitureByTag(`ai-station-${i}`)),
    ).toBe(true);
    for (const room of ROOMS) expect(room.spots.length, room.key).toBeGreaterThanOrEqual(3);
  });

  it("los muebles quedan dentro de su sala y no tapan puertas", () => {
    for (const room of ROOMS) {
      for (const f of room.furniture) {
        expect(f.x).toBeGreaterThanOrEqual(room.x);
        expect(f.y).toBeGreaterThanOrEqual(room.y);
        expect(f.x + f.w).toBeLessThanOrEqual(room.x + room.w);
        expect(f.y + f.h).toBeLessThanOrEqual(room.y + room.h);
      }
    }
    for (const d of DOORS) expect(blocked[d.y][d.x]).toBe(false);
  });

  it("todos los puestos, agentes y el robot son accesibles desde operaciones", () => {
    const origin = ROOM_BY_KEY.ops.spots[0];
    for (const room of ROOMS) {
      const targets = [...room.spots, ...Object.values(room.agentSpots)];
      for (const s of targets) {
        expect(blocked[s.y][s.x], `${room.key} (${s.x},${s.y}) bloqueado`).toBe(false);
        expect(findPath(blocked, origin, s), `${room.key} (${s.x},${s.y}) inaccesible`).not.toBeNull();
      }
    }
  });

  it("los muros perimetrales son sólidos", () => {
    for (let x = 0; x < COLS; x++) {
      expect(isWall(x, 0)).toBe(true);
      expect(isWall(x, ROWS - 1)).toBe(true);
    }
    expect(isWall(-1, 5)).toBe(true);
  });

  it("roomAt localiza la sala de una casilla", () => {
    const lib = ROOM_BY_KEY.library;
    expect(roomAt(lib.x + 1, lib.y + 1)?.key).toBe("library");
    expect(roomAt(13, 1)).toBeNull();
  });
});

describe("robot recolector", () => {
  it("encola eventos reales sin duplicados y descarta los más antiguos si se acumulan", () => {
    const q = new RobotQueue(3);
    const t = (id: number) => ({ eventId: id, kind: "clock_in", userId: "u", userName: "Ana" });
    expect(q.push(t(1))).toBe(true);
    expect(q.push(t(1))).toBe(false);
    q.push(t(2));
    q.push(t(3));
    q.push(t(4));
    expect(q.size).toBe(3);
    expect(q.next()?.eventId).toBe(2);
  });

  it("solo va a recoger entradas y salidas; el resto se procesa en la estación", () => {
    expect(COLLECT_KINDS.has("clock_in")).toBe(true);
    expect(COLLECT_KINDS.has("approve")).toBe(false);
    expect(ROBOT_KIND_LABEL.clock_out).toBe("salida");
  });

  it("se dibuja con y sin la ficha recogida", () => {
    const empty = drawCollector(0, false).data.filter(Boolean).length;
    expect(drawCollector(0, true).data.filter(Boolean).length).toBeGreaterThan(empty);
    expect(drawCollector(1, false, true).data).toContain("#5ee08a");
    expect(drawCheck().data).toContain("#ffffff");
  });
});

describe("A*", () => {
  it("encuentra el camino más corto y evita obstáculos", () => {
    const grid = [
      [false, false, false],
      [true, true, false],
      [false, false, false],
    ];
    const path = findPath(grid, { x: 0, y: 0 }, { x: 0, y: 2 })!;
    expect(path.length).toBe(7);
    expect(path.every((p) => !grid[p.y][p.x])).toBe(true);
  });

  it("devuelve null si el destino está bloqueado o aislado", () => {
    const grid = [
      [false, true],
      [true, false],
    ];
    expect(findPath(grid, { x: 0, y: 0 }, { x: 1, y: 1 })).toBeNull();
    expect(findPath(grid, { x: 0, y: 0 }, { x: 1, y: 0 })).toBeNull();
  });
});

describe("sprites", () => {
  it("genera 9 fotogramas de personaje con contorno dentro del lienzo", () => {
    const frames = characterSheet({ shirt: "#ff0000", style: 2 });
    expect(frames).toHaveLength(9);
    for (const f of frames) {
      expect(f.w).toBe(CHAR_W);
      expect(f.h).toBe(CHAR_H);
      const b = f.bounds();
      expect(b.minX).toBeGreaterThanOrEqual(0);
      expect(b.maxY).toBeLessThan(CHAR_H);
      expect(f.data.filter(Boolean).length).toBeGreaterThan(60);
    }
    expect(frames[frameIndex("down", 0)].data).toContain("#ff0000");
  });

  it("el contorno rodea a los píxeles opacos", () => {
    const g = new PixelGrid(5, 5).rect(2, 2, 1, 1, "#fff").outline("#000");
    expect(g.get(2, 1)).toBe("#000");
    expect(g.get(1, 2)).toBe("#000");
    expect(g.get(1, 1)).toBeNull();
  });

  it("robots e iconos", () => {
    expect(drawRobot("#00ff00", 1, "working").data).toContain("#00ff00");
    expect(drawRobot("#00ff00", 0, "stopped").data).not.toContain("#00ff00");
    for (const k of [
      "working",
      "meeting",
      "paused",
      "offline",
      "task",
      "error",
      "online",
      "notify",
    ] as const) {
      expect(drawIcon(k).data.some(Boolean)).toBe(true);
    }
  });

  it("shade oscurece y aclara colores", () => {
    expect(shade("#808080", 0.5)).toBe("#404040");
    expect(shade("#808080", 3)).toBe("#ffffff");
  });
});
