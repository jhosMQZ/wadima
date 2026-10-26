import { describe, expect, it } from "vitest";
import { formatDuration } from "../store/timeclock";
import { contrast, ensureContrast, isHexColor, mix, readableOn } from "./color";
import { utcToZonedInput, zonedDate, zonedToUtcIso } from "./tz";

describe("colores de marca", () => {
  it("calcula el contraste WCAG", () => {
    expect(contrast("#000000", "#ffffff")).toBeCloseTo(21, 0);
    expect(contrast("#777777", "#777777")).toBeCloseTo(1, 5);
  });

  it("ajusta un color oscuro para que sea legible sobre el fondo oscuro", () => {
    const bg = "#12152b";
    const fixed = ensureContrast("#123456", bg, 4.5);
    expect(contrast(fixed, bg)).toBeGreaterThanOrEqual(4.5);
    expect(ensureContrast("#6fd3ff", bg, 4.5)).toBe("#6fd3ff"); // ya legible: no se toca
  });

  it("elige texto negro o blanco", () => {
    expect(readableOn("#ffd166")).toBe("#000000");
    expect(readableOn("#1b2140")).toBe("#ffffff");
    expect(mix("#000000", "#ffffff", 0.5)).toBe("#808080");
    expect(isHexColor("#abc")).toBe(false);
    expect(isHexColor("#a1b2c3")).toBe(true);
  });
});

describe("zona horaria de la organización", () => {
  it("convierte la hora de pared de Bogotá a UTC y de vuelta", () => {
    expect(zonedToUtcIso("2026-10-25T09:30", "America/Bogota")).toBe("2026-10-25T14:30:00.000Z");
    expect(utcToZonedInput("2026-10-25T14:30:00Z", "America/Bogota")).toBe("2026-10-25T09:30");
    expect(zonedDate("2026-10-26T03:00:00Z", "America/Bogota")).toBe("2026-10-25");
  });

  it("respeta el horario de verano", () => {
    // Madrid: el 25-10-2026 a las 03:00 vuelve a ser 02:00 (UTC+2 → UTC+1)
    expect(zonedToUtcIso("2026-10-25T01:00", "Europe/Madrid")).toBe("2026-10-24T23:00:00.000Z");
    expect(zonedToUtcIso("2026-10-25T05:00", "Europe/Madrid")).toBe("2026-10-25T04:00:00.000Z");
  });

  it("formatea duraciones", () => {
    expect(formatDuration(8 * 3600 + 28 * 60 + 5)).toBe("8:28");
    expect(formatDuration(65, true)).toBe("0:01:05");
    expect(formatDuration(null)).toBe("—");
    expect(formatDuration(-5)).toBe("0:00");
  });
});
