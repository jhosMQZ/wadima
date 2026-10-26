import { useEffect, useState } from "react";
import { serverNow } from "../store/timeclock";

/** Fuerza un nuevo render cada `ms` (para relojes y duraciones en curso). Devuelve el número de ticks. */
export function useTicker(ms = 1000): number {
  const [n, set] = useState(0);
  useEffect(() => {
    const t = window.setInterval(() => set((v) => v + 1), ms);
    return () => window.clearInterval(t);
  }, [ms]);
  return n;
}

export function elapsedSeconds(entry: { clock_in_at: string }, offsetMs: number): number {
  return (serverNow(offsetMs) - new Date(entry.clock_in_at).getTime()) / 1000;
}

export const CLOSE_KIND_LABEL: Record<string, string> = {
  manual: "Registrada",
  auto: "Automática (última conexión)",
  declared: "Declarada por el socio",
  admin: "Fijada por un aprobador",
};

export const PENDING_LABEL: Record<string, string> = {
  max_duration: "supera la duración máxima configurada",
  disconnected: "se perdió la conexión sin registrar la salida",
};

/** El selector trabaja por minutos: el minuto de la entrada significa «justo después de entrar». */
export function clampToEntry(iso: string, entry: { clock_in_at: string }): string {
  return new Date(iso).getTime() < new Date(entry.clock_in_at).getTime() ? entry.clock_in_at : iso;
}
