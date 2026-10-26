// Utilidades de color para aplicar la marca de la organización sin perder contraste (WCAG).

export function hexToRgb(hex: string): [number, number, number] {
  const n = parseInt(hex.replace("#", ""), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

export function rgbToHex([r, g, b]: [number, number, number]): string {
  return `#${[r, g, b]
    .map((v) =>
      Math.round(Math.max(0, Math.min(255, v)))
        .toString(16)
        .padStart(2, "0"),
    )
    .join("")}`;
}

export function luminance(hex: string): number {
  const lin = hexToRgb(hex).map((v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2];
}

export function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

export function mix(a: string, b: string, t: number): string {
  const ca = hexToRgb(a);
  const cb = hexToRgb(b);
  return rgbToHex([0, 1, 2].map((i) => ca[i] + (cb[i] - ca[i]) * t) as [number, number, number]);
}

/** Texto negro o blanco, el que mejor contraste sobre `bg`. */
export function readableOn(bg: string): string {
  return contrast(bg, "#000000") >= contrast(bg, "#ffffff") ? "#000000" : "#ffffff";
}

/** Aclara u oscurece `color` lo mínimo necesario para alcanzar `min` de contraste sobre `bg`. */
export function ensureContrast(color: string, bg: string, min = 4.5): string {
  if (contrast(color, bg) >= min) return color;
  const target = luminance(bg) < 0.5 ? "#ffffff" : "#000000";
  for (let t = 0.05; t <= 1; t += 0.05) {
    const c = mix(color, target, t);
    if (contrast(c, bg) >= min) return c;
  }
  return target;
}

export function isHexColor(value: string): boolean {
  return /^#[0-9a-fA-F]{6}$/.test(value);
}
