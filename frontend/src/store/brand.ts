// Identidad visible de la organización (nombre comercial, iniciales, logotipo y colores).
// Se carga sin sesión para la pantalla de acceso y se actualiza en vivo con el evento «org.updated».

import { create } from "zustand";
import { get } from "../api/client";
import type { OrgPublic } from "../api/types";
import { realtime } from "../api/ws";
import { ensureContrast, mix, readableOn } from "../lib/color";

export const DEFAULT_ORG: OrgPublic = {
  platform_name: "WADIMA",
  legal_name: "WADIMA INDUSTRIE",
  short_mark: "W",
  primary_color: "#6fd3ff",
  secondary_color: "#ffd166",
  logo_url: null,
  timezone: "America/Bogota",
};

const BG = "#12152b";

interface BrandState {
  org: OrgPublic;
  loaded: boolean;
  setOrg: (org: OrgPublic) => void;
  load: () => Promise<void>;
}

export function applyBrand(org: OrgPublic) {
  const root = document.documentElement.style;
  // Enlaces y acentos sobre el fondo oscuro: legibles aunque la marca use un color oscuro.
  root.setProperty("--accent", ensureContrast(org.primary_color, BG, 4.5));
  root.setProperty("--accent-2", ensureContrast(org.secondary_color, BG, 3));
  // Botón principal: color de marca ajustado para que el texto mantenga contraste AA.
  const button = ensureContrast(mix(org.primary_color, "#000000", 0.25), "#ffffff", 4.5);
  root.setProperty("--primary-bg", button);
  root.setProperty("--on-primary", readableOn(button));
  root.setProperty("--brand", org.primary_color);
  root.setProperty("--brand-2", org.secondary_color);
  root.setProperty("--on-brand", readableOn(org.primary_color));
}

export const useBrand = create<BrandState>((set) => ({
  org: DEFAULT_ORG,
  loaded: false,
  setOrg(org) {
    applyBrand(org);
    set({ org, loaded: true });
  },
  async load() {
    try {
      const org = await get<OrgPublic>("/api/org/public");
      applyBrand(org);
      set({ org, loaded: true });
    } catch {
      set({ loaded: true }); // sin servidor se mantiene la marca por defecto
    }
  },
}));

/** Título de la pestaña: «Sección · Nombre de la plataforma». */
export function pageTitle(section?: string): string {
  const name = useBrand.getState().org.platform_name;
  return section ? `${section} · ${name}` : name;
}

realtime.on("org.updated", () => void useBrand.getState().load());
