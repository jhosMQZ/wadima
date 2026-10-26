// WebSocket con reconexión exponencial y latido. Los eventos son señales para recargar datos.
// Si la red cae sin cerrar el socket (wifi perdido, VPN caída), el latido lo detecta: sin respuesta del servidor
// durante STALE_MS se da la conexión por perdida y se reintenta.

const PING_MS = 10_000;
const STALE_MS = 25_000;

export type WsEvent = { type: string; [key: string]: unknown };
type Listener = (ev: WsEvent) => void;
export type WsStatus = "connecting" | "open" | "closed";

class RealtimeClient {
  private ws: WebSocket | null = null;
  private listeners = new Map<string, Set<Listener>>();
  private statusListeners = new Set<(s: WsStatus) => void>();
  private retry = 0;
  private timer: number | undefined;
  private pingTimer: number | undefined;
  private wanted = false;
  private lastMessage = 0;
  status: WsStatus = "closed";

  connect() {
    this.wanted = true;
    if (this.ws && this.ws.readyState <= 1) return;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    this.setStatus("connecting");
    const ws = new WebSocket(`${proto}://${location.host}/ws`);
    this.ws = ws;
    ws.onopen = () => {
      this.retry = 0;
      this.lastMessage = Date.now();
      this.setStatus("open");
      this.pingTimer = window.setInterval(() => {
        if (Date.now() - this.lastMessage > STALE_MS) this.dropStale(ws);
        else this.send({ type: "ping" });
      }, PING_MS);
      this.dispatch({ type: "ws.open" });
    };
    ws.onmessage = (msg) => {
      this.lastMessage = Date.now();
      try {
        this.dispatch(JSON.parse(msg.data) as WsEvent);
      } catch {
        /* mensaje no JSON: se ignora */
      }
    };
    ws.onclose = (ev) => {
      window.clearInterval(this.pingTimer);
      this.ws = null;
      this.setStatus("closed");
      if (ev.code === 4401) {
        this.dispatch({ type: "session.revoked" });
        return;
      }
      if (this.wanted) {
        const delay = Math.min(30000, 1000 * 2 ** this.retry++);
        this.timer = window.setTimeout(() => this.connect(), delay);
      }
    };
  }

  /** Conexión sin respuesta: se abandona sin esperar al cierre TCP y se programa la reconexión. */
  private dropStale(ws: WebSocket) {
    if (this.ws !== ws) return;
    window.clearInterval(this.pingTimer);
    ws.onclose = null;
    ws.onmessage = null;
    try {
      ws.close();
    } catch {
      /* el socket ya no responde */
    }
    this.ws = null;
    this.setStatus("closed");
    if (this.wanted) this.timer = window.setTimeout(() => this.connect(), 1000);
  }

  disconnect() {
    this.wanted = false;
    window.clearTimeout(this.timer);
    window.clearInterval(this.pingTimer);
    this.ws?.close();
    this.ws = null;
    this.setStatus("closed");
  }

  send(data: unknown) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(data));
  }

  on(type: string, fn: Listener): () => void {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type)!.add(fn);
    return () => this.listeners.get(type)?.delete(fn);
  }

  onStatus(fn: (s: WsStatus) => void): () => void {
    this.statusListeners.add(fn);
    return () => this.statusListeners.delete(fn);
  }

  private setStatus(s: WsStatus) {
    this.status = s;
    this.statusListeners.forEach((fn) => fn(s));
  }

  private dispatch(ev: WsEvent) {
    this.listeners.get(ev.type)?.forEach((fn) => fn(ev));
    this.listeners.get("*")?.forEach((fn) => fn(ev));
  }
}

export const realtime = new RealtimeClient();
