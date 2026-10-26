// Audio en tiempo real entre socios con WebRTC en malla (cada participante conecta con cada uno de los demás).
// La señalización viaja por el WebSocket de WADIMA; el audio va directo entre navegadores (o por un TURN propio
// si se configura). Nada se graba. El micrófono solo se pide cuando el socio pulsa «Activar micrófono».

import type { MeetingPeer } from "../api/types";
import { realtime, type WsEvent } from "../api/ws";

export type MeetingStatus = "idle" | "joining" | "joined" | "reconnecting" | "error";

export interface RemotePeer extends MeetingPeer {
  connection: RTCPeerConnectionState | "new";
  speaking: boolean;
  volume: number;
  error: string | null;
}

export interface MeetingSnapshot {
  status: MeetingStatus;
  roomId: string | null;
  peerId: string | null;
  peers: RemotePeer[];
  micOn: boolean;
  micError: string | null;
  speaking: boolean;
  error: string | null;
  inputs: MediaDeviceInfo[];
  outputs: MediaDeviceInfo[];
  inputId: string;
  outputId: string;
}

interface PeerConn {
  info: RemotePeer;
  pc: RTCPeerConnection;
  audio: HTMLAudioElement;
  offerer: boolean;
  pending: RTCIceCandidateInit[];
  restarts: number;
}

export function micErrorMessage(err: unknown): string {
  const name = (err as DOMException)?.name ?? "";
  if (name === "NotAllowedError" || name === "SecurityError")
    return "Permiso de micrófono denegado. Permítalo desde el icono junto a la dirección del navegador y pulse «Activar micrófono» de nuevo.";
  if (name === "NotFoundError" || name === "OverconstrainedError")
    return "No se encontró ningún micrófono (o el elegido ya no está conectado).";
  if (name === "NotReadableError" || name === "AbortError")
    return "El micrófono está en uso por otra aplicación o el sistema lo bloquea.";
  return `No se pudo acceder al micrófono: ${(err as Error)?.message ?? String(err)}`;
}

/** El micrófono solo está disponible en contextos seguros (HTTPS o localhost). */
export function micSupported(): { ok: boolean; reason?: string } {
  if (typeof window === "undefined") return { ok: false, reason: "Sin navegador" };
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia)
    return {
      ok: false,
      reason:
        "El navegador solo permite el micrófono en conexiones seguras: abra WADIMA con https:// (o en este equipo con localhost). Consulte la documentación de acceso remoto.",
    };
  if (typeof RTCPeerConnection === "undefined")
    return { ok: false, reason: "Este navegador no admite WebRTC." };
  return { ok: true };
}

export class MeetingClient {
  private snap: MeetingSnapshot = {
    status: "idle",
    roomId: null,
    peerId: null,
    peers: [],
    micOn: false,
    micError: null,
    speaking: false,
    error: null,
    inputs: [],
    outputs: [],
    inputId: "",
    outputId: "",
  };
  private conns = new Map<string, PeerConn>();
  private iceServers: RTCIceServer[] = [];
  private local: MediaStream | null = null;
  private listeners = new Set<(s: MeetingSnapshot) => void>();
  private offs: (() => void)[] = [];
  private levelTimer: number | undefined;
  private audioCtx: AudioContext | null = null;
  private analyser: AnalyserNode | null = null;
  private wantRoom: string | null = null;

  /** Registra los manejadores de señalización (idempotente). */
  attach() {
    if (this.offs.length) return;
    this.offs.push(realtime.on("meeting.joined", (ev) => this.onJoined(ev)));
    this.offs.push(realtime.on("meeting.peer_joined", (ev) => this.onPeerJoined(ev)));
    this.offs.push(realtime.on("meeting.peer_left", (ev) => this.closePeer(String(ev.peer_id))));
    this.offs.push(
      realtime.on("meeting.peer_state", (ev) =>
        this.updatePeer(String(ev.peer_id), { muted: Boolean(ev.muted) }),
      ),
    );
    this.offs.push(realtime.on("meeting.signal", (ev) => void this.onSignal(ev)));
    this.offs.push(realtime.on("meeting.error", (ev) => this.onError(ev)));
    this.offs.push(
      realtime.on("meeting.replaced", () => this.reset("Se unió a la reunión desde otra ventana.")),
    );
    this.offs.push(
      realtime.onStatus((s) => {
        if (s !== "open" && (this.snap.status === "joined" || this.snap.status === "joining")) {
          // El servidor ya nos sacó de la sala: se cierran las conexiones y se vuelve a entrar al reconectar.
          this.closeAll();
          this.set({ status: "reconnecting", error: "Conexión con el servidor perdida. Reconectando…" });
        }
      }),
    );
    this.offs.push(
      realtime.on("ws.open", () => {
        if (this.wantRoom && this.snap.status === "reconnecting") this.join(this.wantRoom);
      }),
    );
  }

  /** Sale de la reunión (si está dentro) y deja de escuchar la señalización. */
  detach() {
    if (this.snap.status !== "idle" && this.snap.status !== "error") this.leave();
    this.offs.forEach((off) => off());
    this.offs = [];
  }

  get state(): MeetingSnapshot {
    return this.snap;
  }

  subscribe(fn: (s: MeetingSnapshot) => void): () => void {
    this.listeners.add(fn);
    fn(this.snap);
    return () => this.listeners.delete(fn);
  }

  private set(patch: Partial<MeetingSnapshot>) {
    this.snap = { ...this.snap, ...patch, peers: [...this.conns.values()].map((c) => c.info) };
    this.listeners.forEach((fn) => fn(this.snap));
  }

  // ------------------------------------------------------------------ sala
  join(roomId: string) {
    this.wantRoom = roomId;
    if (realtime.status !== "open") {
      this.set({
        status: "reconnecting",
        roomId,
        error: "Sin conexión con el servidor; se entrará al reconectar.",
      });
      return;
    }
    this.set({ status: "joining", roomId, error: null });
    realtime.send({ type: "meeting.join", area_id: roomId });
  }

  leave() {
    this.wantRoom = null;
    realtime.send({ type: "meeting.leave" });
    this.reset(null);
  }

  private reset(error: string | null) {
    this.wantRoom = error ? null : this.wantRoom;
    this.closeAll();
    this.stopMic();
    window.clearInterval(this.levelTimer);
    this.levelTimer = undefined;
    this.set({ status: error ? "error" : "idle", peerId: null, error, micOn: false, speaking: false });
  }

  private closeAll() {
    for (const id of [...this.conns.keys()]) this.closePeer(id, true);
  }

  private onJoined(ev: WsEvent) {
    if (ev.room_id !== this.wantRoom) return;
    this.iceServers = (ev.ice_servers as RTCIceServer[]) ?? [];
    this.set({ status: "joined", peerId: String(ev.peer_id), error: null });
    // El recién llegado ofrece a los que ya estaban: así nunca se cruzan dos ofertas.
    for (const p of ev.participants as MeetingPeer[]) void this.connect(p, true);
    if (this.snap.micOn) realtime.send({ type: "meeting.state", muted: false });
    if (!this.levelTimer) this.levelTimer = window.setInterval(() => void this.pollLevels(), 200);
  }

  private onPeerJoined(ev: WsEvent) {
    if (this.snap.status !== "joined") return;
    const p = ev.peer as MeetingPeer;
    if (!this.conns.has(p.peer_id)) this.createPeer(p, false);
  }

  private onError(ev: WsEvent) {
    if (ev.code === "peer_gone") {
      this.closePeer(String(ev.peer_id));
      return;
    }
    if (ev.request === "meeting.join") this.reset(String(ev.message ?? "No se pudo entrar en la reunión"));
    else this.set({ error: String(ev.message ?? "Error de señalización") });
  }

  // ------------------------------------------------------------------ conexiones
  private createPeer(p: MeetingPeer, offerer: boolean): PeerConn {
    const pc = new RTCPeerConnection({ iceServers: this.iceServers });
    const audio = new Audio();
    audio.autoplay = true;
    if (this.snap.outputId && "setSinkId" in audio)
      void (audio as HTMLAudioElement & { setSinkId: (id: string) => Promise<void> })
        .setSinkId(this.snap.outputId)
        .catch(() => undefined);
    const conn: PeerConn = {
      info: { ...p, connection: "new", speaking: false, volume: 1, error: null },
      pc,
      audio,
      offerer,
      pending: [],
      restarts: 0,
    };
    this.conns.set(p.peer_id, conn);
    pc.onicecandidate = (e) => {
      if (e.candidate)
        realtime.send({ type: "meeting.signal", to: p.peer_id, data: { candidate: e.candidate.toJSON() } });
    };
    pc.ontrack = (e) => {
      audio.srcObject = e.streams[0] ?? new MediaStream([e.track]);
      void audio
        .play()
        .catch(() =>
          this.updatePeer(p.peer_id, { error: "El navegador bloqueó la reproducción: pulse en la página." }),
        );
    };
    pc.onconnectionstatechange = () => {
      const st = pc.connectionState;
      this.updatePeer(p.peer_id, {
        connection: st,
        error:
          st === "failed"
            ? "No se pudo establecer el audio con esta persona. Si están en redes distintas sin VPN hace falta un servidor TURN (ver documentación)."
            : null,
      });
      if (st === "failed" && conn.offerer && conn.restarts < 2) {
        conn.restarts += 1;
        pc.restartIce();
        void this.sendOffer(conn, true);
      }
    };
    if (offerer) pc.addTransceiver("audio", { direction: "sendrecv" });
    this.set({});
    return conn;
  }

  private async connect(p: MeetingPeer, offerer: boolean) {
    const conn = this.createPeer(p, offerer);
    await this.attachMic(conn);
    if (offerer) await this.sendOffer(conn, false);
  }

  private async sendOffer(conn: PeerConn, iceRestart: boolean) {
    const offer = await conn.pc.createOffer({ iceRestart });
    await conn.pc.setLocalDescription(offer);
    realtime.send({
      type: "meeting.signal",
      to: conn.info.peer_id,
      data: { description: conn.pc.localDescription?.toJSON() },
    });
  }

  private async onSignal(ev: WsEvent) {
    if (this.snap.status !== "joined") return;
    const from = String(ev.from);
    const data = ev.data as { description?: RTCSessionDescriptionInit; candidate?: RTCIceCandidateInit };
    let conn = this.conns.get(from);
    try {
      if (data.description) {
        if (!conn) {
          const known = this.snap.peers.find((p) => p.peer_id === from);
          conn = this.createPeer(
            known ?? { peer_id: from, user_id: "", display_name: "Participante", muted: true, joined_at: "" },
            false,
          );
        }
        await conn.pc.setRemoteDescription(data.description);
        for (const c of conn.pending.splice(0)) await conn.pc.addIceCandidate(c);
        if (data.description.type === "offer") {
          for (const t of conn.pc.getTransceivers())
            if (t.receiver.track.kind === "audio") t.direction = "sendrecv";
          await this.attachMic(conn);
          const answer = await conn.pc.createAnswer();
          await conn.pc.setLocalDescription(answer);
          realtime.send({
            type: "meeting.signal",
            to: from,
            data: { description: conn.pc.localDescription?.toJSON() },
          });
        }
      } else if (data.candidate && conn) {
        if (conn.pc.remoteDescription) await conn.pc.addIceCandidate(data.candidate);
        else conn.pending.push(data.candidate);
      }
    } catch (err) {
      this.updatePeer(from, { error: `Error de negociación: ${(err as Error).message}` });
    }
  }

  private closePeer(peerId: string, silent = false) {
    const conn = this.conns.get(peerId);
    if (!conn) return;
    conn.pc.close();
    conn.audio.srcObject = null;
    this.conns.delete(peerId);
    if (!silent) this.set({});
  }

  private updatePeer(peerId: string, patch: Partial<RemotePeer>) {
    const conn = this.conns.get(peerId);
    if (!conn) return;
    conn.info = { ...conn.info, ...patch };
    this.set({});
  }

  setVolume(peerId: string, volume: number) {
    const conn = this.conns.get(peerId);
    if (!conn) return;
    conn.audio.volume = Math.max(0, Math.min(1, volume));
    this.updatePeer(peerId, { volume: conn.audio.volume });
  }

  // ------------------------------------------------------------------ micrófono
  async enableMic(deviceId?: string): Promise<boolean> {
    const support = micSupported();
    if (!support.ok) {
      this.set({ micError: support.reason ?? "Micrófono no disponible" });
      return false;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          ...(deviceId ? { deviceId: { exact: deviceId } } : {}),
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      this.stopMic();
      this.local = stream;
      const track = stream.getAudioTracks()[0];
      track.onended = () => {
        this.stopMic();
        this.set({ micOn: false, micError: "El micrófono se desconectó." });
        realtime.send({ type: "meeting.state", muted: true });
      };
      for (const conn of this.conns.values()) await this.attachMic(conn);
      this.setupAnalyser(stream);
      await this.refreshDevices(track.getSettings().deviceId ?? deviceId ?? "");
      this.set({ micOn: true, micError: null });
      realtime.send({ type: "meeting.state", muted: false });
      return true;
    } catch (err) {
      this.set({ micOn: false, micError: micErrorMessage(err) });
      return false;
    }
  }

  disableMic() {
    this.stopMic();
    for (const conn of this.conns.values()) void this.attachMic(conn);
    this.set({ micOn: false, speaking: false });
    realtime.send({ type: "meeting.state", muted: true });
  }

  async setInput(deviceId: string) {
    this.set({ inputId: deviceId });
    if (this.snap.micOn) await this.enableMic(deviceId);
  }

  setOutput(deviceId: string) {
    this.set({ outputId: deviceId });
    for (const conn of this.conns.values())
      if ("setSinkId" in conn.audio)
        void (conn.audio as HTMLAudioElement & { setSinkId: (id: string) => Promise<void> })
          .setSinkId(deviceId)
          .catch(() => undefined);
  }

  private async attachMic(conn: PeerConn) {
    const track = this.local?.getAudioTracks()[0] ?? null;
    for (const t of conn.pc.getTransceivers()) {
      if (t.receiver.track.kind !== "audio" || t.currentDirection === "stopped") continue;
      await t.sender.replaceTrack(track);
    }
  }

  private stopMic() {
    this.local?.getTracks().forEach((t) => t.stop());
    this.local = null;
    this.analyser = null;
    void this.audioCtx?.close().catch(() => undefined);
    this.audioCtx = null;
  }

  private setupAnalyser(stream: MediaStream) {
    try {
      this.audioCtx = new AudioContext();
      const src = this.audioCtx.createMediaStreamSource(stream);
      this.analyser = this.audioCtx.createAnalyser();
      this.analyser.fftSize = 512;
      src.connect(this.analyser);
    } catch {
      this.analyser = null; // sin indicador local, el audio sigue funcionando
    }
  }

  private async refreshDevices(current: string) {
    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      this.set({
        inputs: devices.filter((d) => d.kind === "audioinput"),
        outputs: devices.filter((d) => d.kind === "audiooutput"),
        inputId: current || this.snap.inputId,
      });
    } catch {
      /* la lista de dispositivos es opcional */
    }
  }

  private async pollLevels() {
    let changed = false;
    for (const conn of this.conns.values()) {
      let level = 0;
      for (const r of conn.pc.getReceivers()) {
        const src = r.getSynchronizationSources?.()[0];
        if (src && performance.now() - src.timestamp < 1000) level = Math.max(level, src.audioLevel ?? 0);
      }
      const speaking = level > 0.03 && !conn.info.muted;
      if (speaking !== conn.info.speaking) {
        conn.info = { ...conn.info, speaking };
        changed = true;
      }
    }
    let local = false;
    if (this.analyser && this.snap.micOn) {
      const buf = new Uint8Array(this.analyser.fftSize);
      this.analyser.getByteTimeDomainData(buf);
      let sum = 0;
      for (const v of buf) sum += ((v - 128) / 128) ** 2;
      local = Math.sqrt(sum / buf.length) > 0.04;
    }
    if (changed || local !== this.snap.speaking) this.set({ speaking: local });
  }
}
