// Mapa compacto de la oficina: 40×27 casillas de 16 px. El centro de operaciones ocupa la columna central y
// tiene puerta directa a las otras seis salas, de modo que ningún recorrido cruza más de una sala intermedia.

export const TILE = 16;
export const COLS = 40;
export const ROWS = 27;

export type FurnitureType =
  | "desk"
  | "wallscreen"
  | "whiteboard"
  | "bookshelf"
  | "table"
  | "meetingtable"
  | "cooler"
  | "coffee"
  | "rack"
  | "cabinet"
  | "labtable"
  | "sofa"
  | "readingtable"
  | "kanban"
  | "terminal"
  | "kiosk";

export interface Furniture {
  type: FurnitureType;
  x: number; // casilla global
  y: number;
  w: number;
  h: number;
  blocks: boolean;
  /** Identificador para enlazar el mueble con datos reales (escritorio de un socio, rack de una integración). */
  tag?: string;
}

export interface Spot {
  x: number;
  y: number;
  face: "down" | "up" | "left" | "right";
}

export interface RoomDef {
  key: string;
  x: number; // interior: primera casilla transitable
  y: number;
  w: number;
  h: number;
  floor: string;
  furniture: Furniture[];
  spots: Spot[];
  agentSpots: Record<string, { x: number; y: number }>;
}

type Face = Spot["face"];
type LocalItem = [FurnitureType, number, number, number, number, string?];

interface Layout {
  x: number;
  y: number;
  w: number;
  h: number;
  floor: string;
  items: LocalItem[];
  spots: [number, number, Face?][];
  agents?: Record<string, [number, number]>;
}

// Puesto de trabajo: mesa de 2 casillas con monitor; la persona se sienta detrás (mirando hacia abajo).
const ws = (x: number, y: number, tag?: string): LocalItem => ["desk", x, y, 2, 1, tag];

const LAYOUTS: Record<string, Layout> = {
  dev: {
    x: 1,
    y: 1,
    w: 12,
    h: 8,
    floor: "woodDark",
    items: [
      ["whiteboard", 0, 0, 4, 1],
      ["kanban", 5, 0, 4, 1],
      ["coffee", 10, 0, 1, 1],
      ws(0, 3, "dev-desk-0"),
      ws(4, 3, "dev-desk-1"),
      ws(8, 3, "dev-desk-2"),
    ],
    spots: [
      [0, 2],
      [4, 2],
      [8, 2],
      [2, 6, "up"],
      [9, 6, "up"],
    ],
  },
  ops: {
    x: 14,
    y: 1,
    w: 12,
    h: 17,
    floor: "carpetBlue",
    items: [
      ["wallscreen", 2, 0, 8, 1, "status-board"],
      ["table", 3, 6, 6, 2],
      ws(1, 10, "home-desk-0"),
      ws(5, 10, "home-desk-1"),
      ws(9, 10, "home-desk-2"),
      ["sofa", 4, 14, 4, 1],
      ["cooler", 11, 15, 1, 1],
    ],
    spots: [
      [1, 9],
      [5, 9],
      [9, 9],
      [3, 5, "down"],
      [8, 5, "down"],
      [4, 13, "down"],
      [7, 13, "down"],
    ],
  },
  library: {
    x: 27,
    y: 1,
    w: 12,
    h: 8,
    floor: "woodWarm",
    items: [
      ["bookshelf", 0, 0, 4, 2],
      ["bookshelf", 8, 0, 4, 2],
      ["readingtable", 4, 4, 4, 2],
      ["bookshelf", 9, 5, 3, 2],
    ],
    spots: [
      [5, 3],
      [6, 3],
      [4, 6, "up"],
      [7, 6, "up"],
      [3, 4, "right"],
    ],
    agents: { doc_pipeline: [2, 4], memory_agent: [5, 2] },
  },
  ai: {
    x: 1,
    y: 10,
    w: 12,
    h: 8,
    floor: "techPurple",
    items: [
      ["wallscreen", 1, 0, 3, 1],
      ["wallscreen", 8, 0, 3, 1],
      ["labtable", 3, 2, 5, 3],
      ws(0, 6, "ai-station-0"),
      ws(5, 6, "ai-station-1"),
      ws(9, 6, "ai-station-2"),
    ],
    spots: [
      [0, 5],
      [5, 5],
      [9, 5],
      [2, 3, "right"],
      [8, 3, "left"],
    ],
    agents: { "claude-0": [2, 6], "claude-1": [7, 6], "claude-2": [11, 6] },
  },
  meeting: {
    x: 27,
    y: 10,
    w: 12,
    h: 8,
    floor: "carpetBeige",
    items: [
      ["whiteboard", 8, 0, 3, 1],
      ["meetingtable", 3, 3, 6, 2],
      ["sofa", 8, 7, 3, 1],
    ],
    spots: [
      [3, 2],
      [6, 2],
      [8, 2],
      [3, 5, "up"],
      [6, 5, "up"],
      [8, 5, "up"],
      [2, 3, "right"],
      [9, 4, "left"],
    ],
  },
  admin: {
    x: 1,
    y: 19,
    w: 19,
    h: 7,
    floor: "carpetGreen",
    items: [
      ["kiosk", 11, 0, 1, 1, "time-kiosk"],
      ["terminal", 13, 0, 2, 1, "admin-terminal"],
      ["cabinet", 17, 0, 2, 2],
      ws(2, 4, "admin-desk-0"),
      ws(6, 4, "admin-desk-1"),
    ],
    spots: [
      [2, 3],
      [6, 3],
      [11, 3],
      [12, 5, "up"],
      [4, 6, "up"],
    ],
    agents: { robot: [14, 1] },
  },
  servers: {
    x: 21,
    y: 19,
    w: 18,
    h: 7,
    floor: "grate",
    items: [
      ["rack", 5, 0, 1, 2, "rack-database"],
      ["rack", 7, 0, 1, 2, "rack-drive"],
      ["rack", 9, 0, 1, 2, "rack-claude_api"],
      ["rack", 11, 0, 1, 2, "rack-claude_mcp"],
      ["rack", 13, 0, 1, 2, "rack-jobs"],
      ["rack", 15, 0, 1, 2, "rack-realtime"],
      ws(8, 4, "monitor-desk"),
    ],
    spots: [
      [8, 3],
      [4, 5, "up"],
      [12, 5, "up"],
    ],
    agents: { drive_sync: [13, 4] },
  },
};

/** Aberturas de 2 casillas. */
export const DOORS: { x: number; y: number }[] = [
  [13, 4],
  [13, 5], // desarrollo ↔ operaciones
  [13, 13],
  [13, 14], // laboratorio de IA ↔ operaciones
  [26, 4],
  [26, 5], // biblioteca ↔ operaciones
  [26, 13],
  [26, 14], // reuniones ↔ operaciones
  [16, 18],
  [17, 18], // operaciones ↔ administración
  [23, 18],
  [24, 18], // operaciones ↔ servidores
  [6, 9],
  [7, 9], // desarrollo ↔ laboratorio de IA
  [32, 9],
  [33, 9], // biblioteca ↔ reuniones
  [20, 22],
  [20, 23], // administración ↔ servidores
].map(([x, y]) => ({ x, y }));

export const ROOMS: RoomDef[] = Object.entries(LAYOUTS).map(([key, l]) => ({
  key,
  x: l.x,
  y: l.y,
  w: l.w,
  h: l.h,
  floor: l.floor,
  furniture: l.items.map(([type, lx, ly, fw, fh, tag]) => ({
    type,
    x: l.x + lx,
    y: l.y + ly,
    w: fw,
    h: fh,
    blocks: true,
    tag,
  })),
  spots: l.spots.map(([sx, sy, face]) => ({ x: l.x + sx, y: l.y + sy, face: face ?? "down" })),
  agentSpots: Object.fromEntries(
    Object.entries(l.agents ?? {}).map(([k, [ax, ay]]) => [k, { x: l.x + ax, y: l.y + ay }]),
  ),
}));

export const ROOM_BY_KEY: Record<string, RoomDef> = Object.fromEntries(ROOMS.map((r) => [r.key, r]));

const FLOOR: boolean[][] = Array.from({ length: ROWS }, () => Array.from({ length: COLS }, () => false));
for (const r of ROOMS)
  for (let y = r.y; y < r.y + r.h; y++) for (let x = r.x; x < r.x + r.w; x++) FLOOR[y][x] = true;
for (const d of DOORS) FLOOR[d.y][d.x] = true;

export function isDoor(x: number, y: number): boolean {
  return DOORS.some((d) => d.x === x && d.y === y);
}

/** Todo lo que no es suelo de una sala ni una puerta es muro. */
export function isWall(x: number, y: number): boolean {
  if (x < 0 || y < 0 || x >= COLS || y >= ROWS) return true;
  return !FLOOR[y][x];
}

/** Muro con cara frontal visible (hay suelo justo debajo). */
export function isFrontWall(x: number, y: number): boolean {
  return isWall(x, y) && y + 1 < ROWS && !isWall(x, y + 1) && !isDoor(x, y + 1);
}

/** Matriz de casillas bloqueadas (muros + muebles). */
export function buildBlocked(): boolean[][] {
  const grid: boolean[][] = Array.from({ length: ROWS }, (_, y) =>
    Array.from({ length: COLS }, (_, x) => isWall(x, y)),
  );
  for (const room of ROOMS) {
    for (const f of room.furniture) {
      if (!f.blocks) continue;
      for (let dy = 0; dy < f.h; dy++) for (let dx = 0; dx < f.w; dx++) grid[f.y + dy][f.x + dx] = true;
    }
  }
  return grid;
}

export function roomAt(tx: number, ty: number): RoomDef | null {
  return ROOMS.find((r) => tx >= r.x && tx < r.x + r.w && ty >= r.y && ty < r.y + r.h) ?? null;
}

export function furnitureByTag(tag: string): Furniture | undefined {
  for (const r of ROOMS) for (const f of r.furniture) if (f.tag === tag) return f;
  return undefined;
}
