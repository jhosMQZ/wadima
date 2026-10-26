import { useCallback, useEffect, useRef, useState } from "react";
import { get } from "../api/client";
import { realtime } from "../api/ws";

interface Options {
  /** Tipos de evento WebSocket que provocan una recarga. */
  refreshOn?: string[];
  /** Filtro opcional para los eventos (p. ej. solo los del proyecto abierto). */
  eventFilter?: (ev: Record<string, unknown>) => boolean;
  /** Recarga periódica en milisegundos. */
  intervalMs?: number;
}

interface State<T> {
  key: string | null;
  data: T | null;
  error: string | null;
}

/** Carga datos REST y los recarga ante eventos en tiempo real. `null` como ruta desactiva la carga. */
export function useApi<T>(path: string | null, opts: Options = {}) {
  const [state, setState] = useState<State<T>>({ key: null, data: null, error: null });
  const ctrl = useRef<AbortController | null>(null);
  const filterRef = useRef(opts.eventFilter);
  useEffect(() => {
    filterRef.current = opts.eventFilter;
  });

  const fetchNow = useCallback(async () => {
    if (!path) return;
    ctrl.current?.abort();
    const c = new AbortController();
    ctrl.current = c;
    try {
      const result = await get<T>(path, c.signal);
      if (!c.signal.aborted) setState({ key: path, data: result, error: null });
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        setState((s) => ({ key: path, data: s.key === path ? s.data : null, error: (err as Error).message }));
      }
    }
  }, [path]);

  useEffect(() => {
    void fetchNow();
    return () => ctrl.current?.abort();
  }, [fetchNow]);

  const refreshKey = (opts.refreshOn ?? []).join(",");
  useEffect(() => {
    if (!refreshKey) return;
    let timer: number | undefined;
    const handler = (ev: Record<string, unknown>) => {
      if (filterRef.current && !filterRef.current(ev)) return;
      window.clearTimeout(timer);
      timer = window.setTimeout(() => void fetchNow(), 150);
    };
    const offs = refreshKey.split(",").map((t) => realtime.on(t, handler));
    const offOpen = realtime.on("ws.open", () => void fetchNow());
    return () => {
      offs.forEach((off) => off());
      offOpen();
      window.clearTimeout(timer);
    };
  }, [refreshKey, fetchNow]);

  useEffect(() => {
    if (!opts.intervalMs) return;
    const t = window.setInterval(() => void fetchNow(), opts.intervalMs);
    return () => window.clearInterval(t);
  }, [opts.intervalMs, fetchNow]);

  const current = state.key === path;
  const setData = useCallback(
    (updater: T | null | ((prev: T | null) => T | null)) =>
      setState((s) => ({
        ...s,
        data: typeof updater === "function" ? (updater as (p: T | null) => T | null)(s.data) : updater,
      })),
    [],
  );
  return {
    data: current ? state.data : null,
    error: current ? state.error : null,
    loading: Boolean(path) && !current,
    // `quiet` se mantiene por compatibilidad: las recargas nunca vacían los datos visibles.
    reload: (_quiet?: boolean) => fetchNow(),
    setData,
  };
}
