// Sprites pixel art originales generados por código (sin recursos externos).
// Cada figura se compone de rectángulos enteros y se le añade un contorno automático.

import type { Avatar } from "../api/types";

export const OUTLINE = "#17142b";

export class PixelGrid {
  readonly data: (string | null)[];
  constructor(
    readonly w: number,
    readonly h: number,
  ) {
    this.data = new Array(w * h).fill(null);
  }
  get(x: number, y: number): string | null {
    if (x < 0 || y < 0 || x >= this.w || y >= this.h) return null;
    return this.data[y * this.w + x];
  }
  set(x: number, y: number, c: string | null) {
    if (x < 0 || y < 0 || x >= this.w || y >= this.h) return;
    this.data[y * this.w + x] = c;
  }
  rect(x: number, y: number, w: number, h: number, c: string) {
    for (let j = 0; j < h; j++) for (let i = 0; i < w; i++) this.set(x + i, y + j, c);
    return this;
  }
  /** Contorno de 1 px en las casillas transparentes vecinas (4-conexas) de píxeles opacos. */
  outline(color = OUTLINE) {
    const marks: [number, number][] = [];
    for (let y = 0; y < this.h; y++) {
      for (let x = 0; x < this.w; x++) {
        if (this.get(x, y)) continue;
        if (this.get(x - 1, y) || this.get(x + 1, y) || this.get(x, y - 1) || this.get(x, y + 1))
          marks.push([x, y]);
      }
    }
    for (const [x, y] of marks) this.set(x, y, color);
    return this;
  }
  bounds() {
    let minX = this.w,
      minY = this.h,
      maxX = -1,
      maxY = -1;
    for (let y = 0; y < this.h; y++)
      for (let x = 0; x < this.w; x++)
        if (this.get(x, y)) {
          minX = Math.min(minX, x);
          maxX = Math.max(maxX, x);
          minY = Math.min(minY, y);
          maxY = Math.max(maxY, y);
        }
    return { minX, minY, maxX, maxY };
  }
  paint(ctx: CanvasRenderingContext2D, ox: number, oy: number) {
    for (let y = 0; y < this.h; y++)
      for (let x = 0; x < this.w; x++) {
        const c = this.get(x, y);
        if (c) {
          ctx.fillStyle = c;
          ctx.fillRect(ox + x, oy + y, 1, 1);
        }
      }
  }
}

export function shade(hex: string, factor: number): string {
  const n = parseInt(hex.replace("#", ""), 16);
  const ch = (v: number) => Math.max(0, Math.min(255, Math.round(v * factor)));
  const r = ch((n >> 16) & 255);
  const g = ch((n >> 8) & 255);
  const b = ch(n & 255);
  return `#${((1 << 24) | (r << 16) | (g << 8) | b).toString(16).slice(1)}`;
}

// ------------------------------------------------------------------ personajes
export const CHAR_W = 16;
export const CHAR_H = 24;
export type Dir = "down" | "up" | "side";
export const FRAME_ORDER: Dir[] = ["down", "up", "side"];
export const FRAMES_PER_DIR = 3; // 0 = quieto, 1 y 2 = pasos

interface Palette {
  skin: string;
  skinS: string;
  hair: string;
  hairH: string;
  shirt: string;
  shirtS: string;
  pants: string;
  pantsS: string;
  shoes: string;
  eye: string;
}

export function paletteFor(av: Avatar | undefined): Palette {
  const skin = av?.skin ?? "#f1c7a1";
  const hair = av?.hair ?? "#3b2a20";
  const shirt = av?.shirt ?? "#3f7cd8";
  const pants = av?.pants ?? "#2d3142";
  return {
    skin,
    skinS: shade(skin, 0.85),
    hair,
    hairH: shade(hair, 1.45),
    shirt,
    shirtS: shade(shirt, 0.75),
    pants,
    pantsS: shade(pants, 0.7),
    shoes: "#2a2130",
    eye: "#1b1830",
  };
}

export function drawCharacter(dir: Dir, frame: number, av?: Avatar): PixelGrid {
  const p = paletteFor(av);
  const style = av?.style ?? 0;
  const g = new PixelGrid(CHAR_W, CHAR_H);
  const step = frame === 0 ? 0 : frame === 1 ? 1 : -1;
  const bob = frame === 0 ? 0 : 1; // al caminar el cuerpo baja 1 px

  if (dir === "side") {
    // Mirando a la izquierda (la derecha se obtiene con flipX).
    const y0 = 2 + bob;
    g.rect(4, y0 + 1, 8, 7, p.skin);
    g.rect(4, y0, 8, 3, p.hair);
    g.rect(8, y0 + 1, 4, 5, p.hair);
    g.rect(5, y0, 3, 1, p.hairH);
    g.rect(5, y0 + 4, 1, 2, p.eye);
    g.rect(3, y0 + 5, 1, 1, p.skinS);
    if (style === 1) [5, 7, 9].forEach((x) => g.set(x, y0 - 1, p.hair));
    if (style === 2) g.rect(9, y0 + 1, 3, 8, p.hair);
    if (style === 3) g.rect(10, y0 - 1, 3, 3, p.hair);
    g.rect(6, y0 + 8, 3, 1, p.skinS);
    g.rect(5, y0 + 9, 6, 6, p.shirt);
    g.rect(6 - step, y0 + 10, 2, 4, p.shirtS);
    g.rect(6 - step, y0 + 14, 2, 1, p.skin);
    g.rect(5, y0 + 14, 6, 1, p.pantsS);
    const legY = y0 + 15;
    g.rect(6 + step, legY, 2, 4, p.pants);
    g.rect(8 - step, legY, 2, 4, p.pantsS);
    g.rect(5 + step, legY + 4, 3, 1, p.shoes);
    g.rect(7 - step, legY + 4, 3, 1, p.shoes);
    return g.outline();
  }

  const y0 = 2 + bob;
  // Cabeza
  g.rect(4, y0 + 1, 8, 7, p.skin);
  if (dir === "down") {
    g.rect(4, y0, 8, 3, p.hair);
    g.rect(3, y0 + 1, 1, 4, p.hair);
    g.rect(12, y0 + 1, 1, 4, p.hair);
    g.rect(5, y0, 3, 1, p.hairH);
    g.rect(6, y0 + 4, 1, 2, p.eye);
    g.rect(9, y0 + 4, 1, 2, p.eye);
    g.rect(7, y0 + 7, 2, 1, p.skinS);
    if (style === 2) {
      g.rect(3, y0 + 1, 1, 9, p.hair);
      g.rect(12, y0 + 1, 1, 9, p.hair);
    }
  } else {
    g.rect(3, y0, 10, 8, p.hair);
    g.rect(5, y0, 4, 1, p.hairH);
    if (style === 2) g.rect(4, y0 + 7, 8, 3, p.hair);
  }
  if (style === 1) [5, 7, 9, 11].forEach((x) => g.set(x, y0 - 1, p.hair));
  if (style === 3) g.rect(6, y0 - 2, 4, 2, p.hair);
  // Cuello y torso
  g.rect(7, y0 + 8, 2, 1, p.skinS);
  g.rect(4, y0 + 9, 8, 6, p.shirt);
  g.rect(4, y0 + 9, 8, 1, p.shirtS);
  // Brazos (balanceo al caminar)
  g.rect(3, y0 + 10 + Math.max(0, step), 1, 4, p.shirtS);
  g.rect(12, y0 + 10 + Math.max(0, -step), 1, 4, p.shirtS);
  g.rect(3, y0 + 14 + Math.max(0, step), 1, 1, p.skin);
  g.rect(12, y0 + 14 + Math.max(0, -step), 1, 1, p.skin);
  g.rect(4, y0 + 14, 8, 1, p.pantsS);
  // Piernas
  const legY = y0 + 15;
  const lLen = step === 1 ? 3 : 4;
  const rLen = step === -1 ? 3 : 4;
  g.rect(5, legY, 3, lLen, p.pants);
  g.rect(8, legY, 3, rLen, p.pants);
  g.rect(7, legY, 1, Math.min(lLen, rLen), p.pantsS);
  g.rect(5, legY + lLen, 3, 1, p.shoes);
  g.rect(8, legY + rLen, 3, 1, p.shoes);
  return g.outline();
}

/** Hoja de sprites: 9 fotogramas en horizontal (abajo×3, arriba×3, lado×3). */
export function characterSheet(av?: Avatar): PixelGrid[] {
  const frames: PixelGrid[] = [];
  for (const dir of FRAME_ORDER)
    for (let f = 0; f < FRAMES_PER_DIR; f++) frames.push(drawCharacter(dir, f, av));
  return frames;
}

export function frameIndex(dir: Dir, frame: number): number {
  return FRAME_ORDER.indexOf(dir) * FRAMES_PER_DIR + frame;
}

export function avatarKey(av?: Avatar): string {
  return `char-${av?.skin ?? ""}-${av?.hair ?? ""}-${av?.shirt ?? ""}-${av?.pants ?? ""}-${av?.style ?? 0}`.replace(
    /#/g,
    "",
  );
}

// ------------------------------------------------------------------ agentes (robots)
export const ROBOT_W = 16;
export const ROBOT_H = 20;

export function drawRobot(
  color: string,
  frame: number,
  state: "idle" | "working" | "error" | "stopped",
): PixelGrid {
  const g = new PixelGrid(ROBOT_W, ROBOT_H);
  const metal = state === "stopped" ? "#6b6f80" : "#c9cfe3";
  const metalS = shade(metal, 0.75);
  const glow = state === "error" ? "#ff5a5a" : state === "stopped" ? "#3a3d4d" : color;
  const hover = state === "working" && frame === 1 ? -1 : 0;
  g.rect(7, 1 + hover, 2, 2, metalS);
  g.set(7, 0 + hover, glow);
  g.set(8, 0 + hover, glow);
  g.rect(3, 3 + hover, 10, 6, metal);
  g.rect(4, 4 + hover, 8, 3, "#1c2033");
  if (state === "working") {
    const pos = 4 + ((frame * 3) % 8);
    g.rect(Math.min(pos, 10), 5 + hover, 2, 1, glow);
  } else {
    g.rect(5, 5 + hover, 2, 1, glow);
    g.rect(9, 5 + hover, 2, 1, glow);
  }
  g.rect(4, 10 + hover, 8, 5, metal);
  g.rect(4, 14 + hover, 8, 1, metalS);
  g.rect(6, 11 + hover, 4, 2, state === "stopped" ? "#4a4d5c" : color);
  g.rect(2, 11 + hover, 1, 3, metalS);
  g.rect(13, 11 + hover, 1, 3, metalS);
  g.rect(5, 16, 6, 2, "#3a3f55");
  return g.outline();
}

// ------------------------------------------------------------------ iconos de estado (10x10)
export type IconKind = "working" | "meeting" | "paused" | "offline" | "task" | "error" | "online" | "notify";

export function drawIcon(kind: IconKind): PixelGrid {
  const g = new PixelGrid(10, 10);
  const bubble = () => {
    g.rect(1, 1, 8, 6, "#f4f1ff");
    g.rect(3, 7, 2, 1, "#f4f1ff");
    g.set(3, 8, "#f4f1ff");
  };
  switch (kind) {
    case "working":
      bubble();
      g.rect(3, 2, 4, 3, "#2f7fb8");
      g.rect(2, 5, 6, 1, "#5a6380");
      break;
    case "meeting":
      bubble();
      g.rect(2, 3, 1, 1, "#d39b1f");
      g.rect(4, 3, 1, 1, "#d39b1f");
      g.rect(6, 3, 1, 1, "#d39b1f");
      break;
    case "paused":
      bubble();
      g.rect(3, 3, 3, 3, "#8a5a3c");
      g.rect(6, 3, 1, 2, "#8a5a3c");
      g.rect(3, 2, 1, 1, "#c9c9d6");
      g.rect(5, 2, 1, 1, "#c9c9d6");
      break;
    case "offline":
      g.rect(1, 1, 4, 1, "#a3a9d1");
      g.rect(4, 2, 1, 1, "#a3a9d1");
      g.rect(3, 3, 1, 1, "#a3a9d1");
      g.rect(2, 4, 1, 1, "#a3a9d1");
      g.rect(1, 5, 4, 1, "#a3a9d1");
      g.rect(6, 5, 3, 1, "#6e76ab");
      g.rect(7, 6, 1, 1, "#6e76ab");
      g.rect(6, 7, 3, 1, "#6e76ab");
      break;
    case "task":
      g.rect(2, 1, 6, 8, "#e8d6a8");
      g.rect(4, 0, 2, 2, "#8a8fa8");
      g.rect(3, 3, 4, 1, "#5a6380");
      g.rect(3, 5, 4, 1, "#5a6380");
      g.rect(3, 7, 3, 1, "#5a6380");
      break;
    case "error":
      g.rect(4, 1, 2, 5, "#ff5a5a");
      g.rect(4, 7, 2, 2, "#ff5a5a");
      break;
    case "online":
      g.rect(3, 3, 4, 4, "#5ee08a");
      break;
    case "notify":
      g.rect(1, 1, 8, 8, "#ffd166");
      g.rect(4, 2, 2, 4, "#5a3a00");
      g.rect(4, 7, 2, 1, "#5a3a00");
      break;
  }
  return g.outline();
}

// ------------------------------------------------------------------ robot recolector de registros horarios
export const COLLECTOR_W = 16;
export const COLLECTOR_H = 18;

/** Robot de ruedas con bandeja. `frame` alterna las ruedas; `carrying` muestra la ficha recogida. */
export function drawCollector(frame: number, carrying: boolean, processing = false): PixelGrid {
  const g = new PixelGrid(COLLECTOR_W, COLLECTOR_H);
  const body = "#f0b429";
  const bodyS = shade(body, 0.72);
  const eye = processing ? "#5ee08a" : "#1b2140";
  // Antena
  g.set(8, 0, processing ? "#5ee08a" : "#ff6b6b");
  g.rect(8, 1, 1, 2, "#8a8fa8");
  // Cabeza con visor
  g.rect(4, 3, 9, 5, body);
  g.rect(5, 4, 7, 3, "#d8e6ff");
  g.rect(6, 5, 1, 1, eye);
  g.rect(10, 5, 1, 1, eye);
  // Cuerpo y bandeja
  g.rect(3, 8, 11, 6, body);
  g.rect(3, 12, 11, 2, bodyS);
  g.rect(2, 9, 1, 3, bodyS);
  g.rect(14, 9, 1, 3, bodyS);
  if (carrying) {
    g.rect(5, 6, 7, 1, "#8a8fa8");
    g.rect(6, 1, 5, 5, "#fffbe8");
    g.rect(7, 2, 3, 1, "#5a6380");
    g.rect(7, 4, 2, 1, "#5a6380");
  }
  // Ruedas
  const off = frame % 2;
  g.rect(4, 14, 3, 3, "#2a2e3f");
  g.rect(10, 14, 3, 3, "#2a2e3f");
  g.set(5 + off, 15, "#8a8fa8");
  g.set(11 - off, 15, "#8a8fa8");
  return g.outline();
}

/** Marca de verificación (registro procesado). */
export function drawCheck(): PixelGrid {
  const g = new PixelGrid(10, 10);
  g.rect(0, 0, 10, 10, "#1f6f43");
  g.set(2, 5, "#ffffff");
  g.set(3, 6, "#ffffff");
  g.set(4, 7, "#ffffff");
  g.set(5, 6, "#ffffff");
  g.set(6, 5, "#ffffff");
  g.set(7, 4, "#ffffff");
  g.set(8, 3, "#ffffff");
  return g.outline();
}
