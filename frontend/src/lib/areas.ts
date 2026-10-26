import type { Area, AreaTemplate } from "../api/types";
import { useSession } from "../store/session";
import { useApi } from "./useApi";

/** Plantillas de áreas predefinidas y si están habilitadas. */
export function useTemplates() {
  return useApi<AreaTemplate[]>("/api/org/templates", { refreshOn: ["area.updated", "org.updated"] });
}

/** Áreas accesibles para el socio. */
export function useAreas() {
  return useApi<{ areas: Area[] }>("/api/areas", { refreshOn: ["area.updated"] });
}

/**
 * Área de trabajo de un módulo: la de su plantilla (p. ej. «meeting») o, para el centro de operaciones, el área
 * activa de la sesión, la de operaciones o la primera disponible.
 */
export function useWorkArea(templateKey = "ops"): { area: Area | null; loading: boolean } {
  const me = useSession((s) => s.me)!;
  const { data, loading } = useAreas();
  const list = (data?.areas ?? []).filter((a) => a.enabled);
  if (templateKey === "ops") {
    if (me.active_area) return { area: me.active_area, loading: false };
    return { area: list.find((a) => a.template_key === "ops") ?? list[0] ?? null, loading };
  }
  return { area: list.find((a) => a.template_key === templateKey) ?? null, loading };
}
