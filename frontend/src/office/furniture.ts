// Dibujo pixel art de suelos, muros y mobiliario con primitivas enteras de Phaser.Graphics.

import type Phaser from "phaser";
import type { FurnitureType } from "./map";
import { TILE } from "./map";

type G = Phaser.GameObjects.Graphics;

const px = (g: G, color: number, x: number, y: number, w = 1, h = 1) => {
  g.fillStyle(color, 1);
  g.fillRect(x, y, w, h);
};

export function drawFloorTile(g: G, floor: string, x: number, y: number) {
  const X = x * TILE;
  const Y = y * TILE;
  switch (floor) {
    case "carpetBlue":
      px(g, 0x2b4f8a, X, Y, TILE, TILE);
      for (let i = 0; i < TILE; i += 4)
        for (let j = (i / 4) % 2 ? 2 : 0; j < TILE; j += 4) px(g, 0x335b9c, X + j, Y + i);
      break;
    case "woodDark":
      px(g, 0x5a4232, X, Y, TILE, TILE);
      px(g, 0x4a3528, X, Y + 7, TILE, 1);
      px(g, 0x4a3528, X, Y + 15, TILE, 1);
      px(g, 0x4a3528, X + ((x * 7) % 3) * 5 + 2, Y, 1, 7);
      px(g, 0x6a4f3c, X, Y, TILE, 1);
      break;
    case "woodWarm":
      px(g, 0x8a5a34, X, Y, TILE, TILE);
      px(g, 0x774b2a, X, Y + 5, TILE, 1);
      px(g, 0x774b2a, X, Y + 10, TILE, 1);
      px(g, 0x774b2a, X, Y + 15, TILE, 1);
      px(g, 0x774b2a, X + ((x + y) % 4) * 4, Y, 1, 5);
      px(g, 0x9b6a40, X, Y + 1, TILE, 1);
      break;
    case "techPurple":
      px(g, 0x2e2650, X, Y, TILE, TILE);
      px(g, 0x3d3370, X, Y, TILE, 1);
      px(g, 0x3d3370, X, Y, 1, TILE);
      if ((x + y) % 5 === 0) px(g, 0x7d6bd8, X + 8, Y + 8, 2, 2);
      break;
    case "carpetBeige":
      px(g, 0xb89a6a, X, Y, TILE, TILE);
      for (let i = 2; i < TILE; i += 6) px(g, 0xa98b5c, X + i, Y + i, 2, 2);
      break;
    case "carpetGreen":
      px(g, 0x2f6b4f, X, Y, TILE, TILE);
      for (let i = 0; i < TILE; i += 4) px(g, 0x357a5a, X + i, Y + ((i / 4) % 2) * 8 + 3, 2, 1);
      break;
    case "steel":
      px(g, 0x39404f, X, Y, TILE, TILE);
      px(g, 0x2f3542, X + 15, Y, 1, TILE);
      px(g, 0x2f3542, X, Y + 15, TILE, 1);
      px(g, 0x4f586b, X + 2, Y + 2);
      px(g, 0x4f586b, X + 13, Y + 13);
      break;
    case "tilesLight":
      px(g, (x + y) % 2 ? 0xc9cbd9 : 0xbbbecd, X, Y, TILE, TILE);
      break;
    case "grate":
      px(g, 0x2b303b, X, Y, TILE, TILE);
      for (let i = 1; i < TILE; i += 3) px(g, 0x363c49, X, Y + i, TILE, 1);
      break;
    default:
      px(g, 0x444444, X, Y, TILE, TILE);
  }
}

export function drawWallTile(g: G, x: number, y: number, horizontal: boolean) {
  const X = x * TILE;
  const Y = y * TILE;
  if (horizontal) {
    px(g, 0x2a2e52, X, Y, TILE, 5);
    px(g, 0x3c4378, X, Y + 5, TILE, 9);
    px(g, 0x4a528f, X, Y + 5, TILE, 1);
    px(g, 0x222644, X, Y + 14, TILE, 2);
  } else {
    px(g, 0x2a2e52, X, Y, TILE, TILE);
    px(g, 0x3a4170, X + 4, Y, 8, TILE);
    px(g, 0x23274a, X + 4, Y, 1, TILE);
  }
}

export function drawDoorTile(g: G, x: number, y: number) {
  const X = x * TILE;
  const Y = y * TILE;
  px(g, 0x4b4566, X, Y, TILE, TILE);
  px(g, 0x5a5478, X + 2, Y + 2, TILE - 4, TILE - 4);
}

/** Dibuja un mueble en (0,0) de su propio lienzo de w×h casillas (+ margen superior de 8 px). */
export function drawFurniture(g: G, type: FurnitureType, wTiles: number, hTiles: number) {
  const W = wTiles * TILE;
  const H = hTiles * TILE;
  const top = 8; // margen para elementos que sobresalen hacia arriba (monitores)
  switch (type) {
    case "desk": {
      px(g, 0x5c3d26, 1, top + 2, W - 2, H - 3);
      px(g, 0x8a6040, 0, top + 1, W, 8);
      px(g, 0xa8784f, 0, top + 1, W, 2);
      px(g, 0x4a301d, 2, top + 9, 2, H - 10);
      px(g, 0x4a301d, W - 4, top + 9, 2, H - 10);
      // Monitor visto por detrás
      px(g, 0x1f2338, W / 2 - 7, top - 7, 14, 9);
      px(g, 0x2c3150, W / 2 - 6, top - 6, 12, 7);
      px(g, 0x1f2338, W / 2 - 1, top + 2, 2, 2);
      px(g, 0x6fd3ff, W / 2 + 4, top - 2, 1, 1);
      // Teclado
      px(g, 0xd5d8e8, 4, top + 4, 8, 2);
      break;
    }
    case "table":
    case "readingtable":
    case "meetingtable": {
      const wood = type === "meetingtable" ? 0x7a5236 : type === "readingtable" ? 0x6b4428 : 0x5d6488;
      const hi = type === "meetingtable" ? 0x9a6a46 : type === "readingtable" ? 0x8a5a34 : 0x7880a8;
      px(g, 0x1a1a2a, 2, top + H - 2, W - 4, 2);
      px(g, wood, 0, top + 2, W, H - 4);
      px(g, hi, 0, top + 2, W, 3);
      px(g, 0x3a2614, 2, top + H - 3, 2, 3);
      px(g, 0x3a2614, W - 4, top + H - 3, 2, 3);
      if (type === "readingtable") {
        px(g, 0xf2e8cf, 6, top + 8, 10, 7);
        px(g, 0xe0d2ad, 11, top + 8, 1, 7);
        px(g, 0xffd166, W - 10, top + 5, 4, 4);
      }
      if (type === "meetingtable") {
        for (let i = 12; i < W - 8; i += 24) px(g, 0xe8eaf6, i, top + 12, 8, 6);
      }
      if (type === "table") {
        px(g, 0x6fd3ff, W / 2 - 10, top + 8, 20, 10);
        px(g, 0x1b2140, W / 2 - 9, top + 9, 18, 8);
        px(g, 0x6fd3ff, W / 2 - 7, top + 14, 3, 2);
        px(g, 0x6fd3ff, W / 2 - 3, top + 12, 3, 4);
        px(g, 0x6fd3ff, W / 2 + 1, top + 11, 3, 5);
      }
      break;
    }
    case "labtable": {
      px(g, 0x4d4a6e, 0, top + 4, W, H - 6);
      px(g, 0x6c68a0, 0, top + 4, W, 3);
      px(g, 0x2a2840, 2, top + H - 3, W - 4, 3);
      // Orbe central (se ilumina desde la escena según el estado real)
      px(g, 0x2a2840, W / 2 - 7, top + 10, 14, 14);
      px(g, 0x3b3760, W / 2 - 6, top + 11, 12, 12);
      for (let i = 6; i < W - 6; i += 10) px(g, 0x9fe8ff, i, top + 7, 3, 2);
      break;
    }
    case "wallscreen":
    case "whiteboard":
    case "kanban": {
      const frame = 0x23263f;
      px(g, frame, 0, 0, W, 14);
      if (type === "whiteboard") {
        px(g, 0xeef0f8, 1, 1, W - 2, 12);
        px(g, 0x3f7cd8, 4, 4, W / 3, 1);
        px(g, 0xd8613f, 4, 7, W / 2, 1);
        px(g, 0x3fa66b, 4, 10, W / 4, 1);
      } else if (type === "kanban") {
        px(g, 0xe4e1d4, 1, 1, W - 2, 12);
        const cols = 3;
        const cw = Math.floor((W - 2) / cols);
        const colors = [0xffd166, 0x6fd3ff, 0x5ee08a];
        for (let c = 0; c < cols; c++) {
          px(g, 0xb9b5a6, 1 + c * cw, 1, 1, 12);
          for (let r = 0; r < 2 + ((c + 1) % 2); r++) px(g, colors[c], 3 + c * cw, 3 + r * 3, cw - 5, 2);
        }
      } else {
        px(g, 0x0d1424, 1, 1, W - 2, 12);
      }
      px(g, 0x14172a, W / 2 - 2, 14, 4, 2);
      break;
    }
    case "bookshelf": {
      px(g, 0x4e3320, 0, top - 6, W, H + 6);
      px(g, 0x6b4428, 0, top - 6, W, 2);
      const colors = [0xd8613f, 0x3f7cd8, 0x3fa66b, 0xffd166, 0x8b5cf6, 0xe8eaf6, 0xc94f6d];
      const rows = Math.max(2, Math.floor((H + 4) / 9));
      for (let r = 0; r < rows; r++) {
        const yy = top - 3 + r * 9;
        px(g, 0x2e1e12, 2, yy, W - 4, 7);
        for (let i = 3, k = r * 3; i < W - 4; i += 3, k++) {
          const hgt = 5 + ((k * 13) % 3);
          px(g, colors[(k * 5) % colors.length], i, yy + 7 - hgt, 2, hgt);
        }
        px(g, 0x6b4428, 0, yy + 7, W, 2);
      }
      break;
    }
    case "rack": {
      px(g, 0x15181f, 0, top - 6, W, H + 6);
      px(g, 0x2b313d, 1, top - 5, W - 2, H + 4);
      for (let yy = top - 3; yy < top + H - 2; yy += 4) px(g, 0x1c2029, 2, yy, W - 4, 3);
      break;
    }
    case "cooler": {
      px(g, 0xd8dbe8, 3, top + 2, 10, 13);
      px(g, 0x6fb8ff, 4, top - 6, 8, 9);
      px(g, 0x9fd3ff, 5, top - 5, 3, 7);
      px(g, 0x8a8fa8, 6, top + 6, 4, 2);
      break;
    }
    case "coffee": {
      px(g, 0x2d3142, 2, top - 2, 12, 17);
      px(g, 0x4a4f66, 3, top - 1, 10, 6);
      px(g, 0xd8613f, 10, top + 1, 2, 2);
      px(g, 0xeef0f8, 6, top + 9, 4, 4);
      break;
    }
    case "cabinet": {
      px(g, 0x7d8296, 0, top - 4, W, H + 4);
      for (let yy = top - 2; yy < top + H - 4; yy += 8) {
        px(g, 0x959ab0, 2, yy, W - 4, 6);
        px(g, 0x4a4f66, W / 2 - 3, yy + 2, 6, 1);
      }
      break;
    }
    case "sofa": {
      px(g, 0x5a3f8a, 0, top, W, H);
      px(g, 0x7350ab, 2, top + 2, W - 4, 6);
      px(g, 0x47306e, 0, top + H - 3, W, 3);
      break;
    }
    case "terminal": {
      // Estación administrativa: consola con pantalla grande (el robot deposita aquí los registros).
      px(g, 0x2d3a4f, 0, top + 2, W, H - 2);
      px(g, 0x3e5068, 0, top + 2, W, 3);
      px(g, 0x14202e, 3, top - 7, W - 6, 10);
      px(g, 0x1c3a2c, 4, top - 6, W - 8, 8);
      px(g, 0x5ee08a, 6, top - 4, 6, 1);
      px(g, 0x5ee08a, 6, top - 2, 10, 1);
      px(g, 0x9aa6b8, W / 2 - 4, top + 6, 8, 2);
      break;
    }
    case "kiosk": {
      // Terminal de control horario (entrada/salida).
      px(g, 0x4a5568, 4, top + 2, 8, 13);
      px(g, 0x2d3748, 3, top - 6, 10, 9);
      px(g, 0x63b3ed, 4, top - 5, 8, 6);
      px(g, 0xe8eaf6, 6, top - 3, 4, 1);
      px(g, 0xe8eaf6, 6, top - 1, 2, 1);
      px(g, 0x5ee08a, 6, top + 5, 4, 2);
      break;
    }
  }
}

/** Altura total del lienzo de un mueble (incluye el margen superior). */
export function furnitureCanvasHeight(type: FurnitureType, hTiles: number): number {
  if (["wallscreen", "whiteboard", "kanban"].includes(type)) return 16;
  return hTiles * TILE + 8;
}
