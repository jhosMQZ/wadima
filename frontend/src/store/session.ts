import { create } from "zustand";
import { ApiError, get, post, setCsrfToken, setUnauthorizedHandler } from "../api/client";
import type { Area, Me } from "../api/types";
import { realtime } from "../api/ws";
import { useBrand } from "./brand";
import { bindClockUser, useClock } from "./timeclock";

interface SessionState {
  me: Me | null;
  loading: boolean;
  error: string | null;
  init: () => Promise<void>;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
  enterArea: (areaId: string) => Promise<void>;
  leaveArea: () => Promise<void>;
  setActiveArea: (area: Area | null) => void;
  expire: () => void;
}

function apply(me: Me | null) {
  setCsrfToken(me?.csrf_token ?? "");
  if (me?.org) useBrand.getState().setOrg(me.org);
  const active = Boolean(me && !me.user.must_change_password);
  bindClockUser(active ? me!.user.id : null);
  if (active) {
    realtime.connect();
    void useClock.getState().load();
  } else {
    realtime.disconnect();
    useClock.getState().reset();
  }
}

export const useSession = create<SessionState>((set, getState) => ({
  me: null,
  loading: true,
  error: null,

  async init() {
    setUnauthorizedHandler(() => getState().expire());
    try {
      const me = await get<Me>("/api/auth/me");
      apply(me);
      set({ me, loading: false });
    } catch (err) {
      apply(null);
      set({
        me: null,
        loading: false,
        error: err instanceof ApiError && err.status !== 401 ? err.message : null,
      });
    }
  },

  async login(username, password) {
    const me = await post<Me>("/api/auth/login", { username, password });
    apply(me);
    set({ me, error: null });
  },

  async logout() {
    try {
      await post("/api/auth/logout");
    } finally {
      apply(null);
      set({ me: null });
    }
  },

  async refresh() {
    const me = await get<Me>("/api/auth/me");
    apply(me);
    set({ me });
  },

  async enterArea(areaId) {
    const res = await post<{ area: Area }>(`/api/areas/${areaId}/enter`);
    const me = getState().me;
    if (me) set({ me: { ...me, active_area: res.area } });
  },

  async leaveArea() {
    await post("/api/areas/leave");
    const me = getState().me;
    if (me) set({ me: { ...me, active_area: null } });
  },

  setActiveArea(area) {
    const me = getState().me;
    if (me) set({ me: { ...me, active_area: area } });
  },

  expire() {
    if (getState().me) {
      apply(null);
      set({ me: null, error: "Su sesión expiró o fue cerrada. Inicie sesión de nuevo." });
    }
  },
}));

realtime.on("session.revoked", () => useSession.getState().expire());
