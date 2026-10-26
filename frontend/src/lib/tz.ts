// Conversión entre «hora de pared» en la zona de la organización y UTC sin dependencias externas.

function parts(ms: number, timeZone: string) {
  const fmt = new Intl.DateTimeFormat("en-US", {
    timeZone,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
  const out: Record<string, number> = {};
  for (const p of fmt.formatToParts(new Date(ms))) if (p.type !== "literal") out[p.type] = Number(p.value);
  return out;
}

/** Desfase (ms) de la zona respecto a UTC en el instante `ms`. */
export function zoneOffset(ms: number, timeZone: string): number {
  const p = parts(ms, timeZone);
  const asUtc = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second);
  return asUtc - Math.floor(ms / 1000) * 1000;
}

/** «AAAA-MM-DDTHH:mm» interpretado en `timeZone` → ISO 8601 en UTC. */
export function zonedToUtcIso(local: string, timeZone: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(local);
  if (!m) throw new Error("Fecha y hora no válidas");
  const guess = Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +(m[6] ?? 0));
  let ms = guess - zoneOffset(guess, timeZone);
  ms = guess - zoneOffset(ms, timeZone); // segunda pasada por si el desfase cambia (horario de verano)
  return new Date(ms).toISOString();
}

/** ISO → «AAAA-MM-DDTHH:mm» en `timeZone` (valor para <input type="datetime-local">). */
export function utcToZonedInput(iso: string | number, timeZone: string): string {
  const p = parts(typeof iso === "number" ? iso : new Date(iso).getTime(), timeZone);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${p.year}-${pad(p.month)}-${pad(p.day)}T${pad(p.hour)}:${pad(p.minute)}`;
}

/** Fecha local (AAAA-MM-DD) en la zona indicada. */
export function zonedDate(iso: string | number, timeZone: string): string {
  return utcToZonedInput(iso, timeZone).slice(0, 10);
}
