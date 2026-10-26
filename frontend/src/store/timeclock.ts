// Estado de la jornada propia. Las horas oficiales las pone siempre el servidor; el reloj local solo se usa
// para animar el tiempo transcurrido, corregido con la diferencia respecto a la hora del servidor.

import { create } from "zustand";
import { ApiError, get, post } from "../api/client";
import type { TimeEntry, TimeStatus } from "../api/types";
import { realtime } from "../api/ws";

interface ClockState {
  status: TimeStatus | null;
  /** hora del servidor − hora local, en ms */
  offsetMs: number;
  error: string | null;
  load: () => Promise<void>;
  clockIn: (note?: string) => Promise<{ entry: TimeEntry; duplicate: boolean }>;
  clockOut: (note?: string) => Promise<TimeEntry>;
  declareExit: (entryId: string, at: string, reason: string) => Promise<TimeEntry>;
  reset: () => void;
}

export const useClock = create<ClockState>((set, getState) => ({
  status: null,
  offsetMs: 0,
  error: null,

  async load() {
    try {
      const t0 = Date.now();
      const status = await get<TimeStatus>("/api/time/status");
      const rtt = Date.now() - t0;
      const offsetMs = new Date(status.server_time).getTime() + rtt / 2 - Date.now();
      set({ status, offsetMs, error: null });
    } catch (err) {
      set({ error: (err as Error).message });
    }
  },

  async clockIn(note = "") {
    try {
      const res = await post<{ entry: TimeEntry }>("/api/time/clock-in", { note });
      await getState().load();
      return { entry: res.entry, duplicate: false };
    } catch (err) {
      // Doble clic, otra pestaña u otro equipo: el servidor no crea una segunda jornada y devuelve la abierta.
      if (err instanceof ApiError && err.status === 409) {
        await getState().load();
        return { entry: (err.body as { entry: TimeEntry }).entry, duplicate: true };
      }
      throw err;
    }
  },

  async clockOut(note = "") {
    const open = getState().status?.open;
    if (!open) throw new Error("No tiene una entrada registrada");
    try {
      const res = await post<{ entry: TimeEntry }>("/api/time/clock-out", { entry_id: open.id, note });
      return res.entry;
    } finally {
      await getState().load();
    }
  },

  async declareExit(entryId, at, reason) {
    try {
      const res = await post<{ entry: TimeEntry }>(`/api/time/entries/${entryId}/declare-exit`, {
        clock_out_at: at,
        reason,
      });
      return res.entry;
    } finally {
      await getState().load();
    }
  },

  reset() {
    set({ status: null, error: null });
  },
}));

/** Milisegundos actuales según el reloj del servidor. */
export function serverNow(offsetMs: number): number {
  return Date.now() + offsetMs;
}

export function formatDuration(seconds: number | null | undefined, withSeconds = false): string {
  if (seconds == null) return "—";
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const base = `${h}:${String(m).padStart(2, "0")}`;
  return withSeconds ? `${base}:${String(sec).padStart(2, "0")}` : base;
}

/** Hora en la zona de la organización (no la del navegador). */
export function formatInZone(
  iso: string | number,
  timeZone: string,
  opts: Intl.DateTimeFormatOptions = {},
): string {
  const base: Intl.DateTimeFormatOptions =
    opts.dateStyle || opts.timeStyle ? {} : { hour: "2-digit", minute: "2-digit", second: "2-digit" };
  return new Intl.DateTimeFormat("es", { timeZone, ...base, ...opts }).format(new Date(iso));
}

export function formatDateInZone(iso: string, timeZone: string): string {
  return new Intl.DateTimeFormat("es", { timeZone, dateStyle: "medium" }).format(new Date(iso));
}

let myId: string | null = null;
export function bindClockUser(userId: string | null) {
  myId = userId;
}

realtime.on("time.recorded", (ev) => {
  if (myId && ev.user_id === myId) void useClock.getState().load();
});
realtime.on("ws.open", () => {
  if (myId) void useClock.getState().load();
});
