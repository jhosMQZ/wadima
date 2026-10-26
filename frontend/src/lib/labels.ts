export const PROJECT_STATUS: Record<string, string> = {
  planned: "Planificado",
  active: "Activo",
  paused: "En pausa",
  done: "Terminado",
  archived: "Archivado",
};

export const NOTE_KIND: Record<string, string> = {
  note: "Nota",
  spec: "Especificación",
  requirement: "Requisitos",
  plan: "Plan",
  doc: "Documentación",
  meeting: "Acta de reunión",
};

export const DOC_STATUS: Record<string, [string, string]> = {
  received: ["recibido", "info"],
  validated: ["en cola", "info"],
  processing: ["procesando", "info"],
  converted: ["convertido", "ok"],
  indexed: ["indexado", "ok"],
  needs_review: ["revisar", "warn"],
  error: ["error", "err"],
};

export const AGENT_STATUS: Record<string, string> = {
  idle: "en espera",
  working: "trabajando",
  error: "error",
  stopped: "detenido",
};

export const RUN_STATUS: Record<string, string> = {
  running: "en curso",
  done: "completada",
  refused: "rechazada",
  error: "error",
  queued: "en cola (esperando a su Claude)",
  claimed: "recogida por su Claude",
  cancelled: "cancelada",
};

export const JOB_STATUS: Record<string, string> = {
  queued: "en cola",
  running: "en curso",
  done: "hecho",
  error: "error",
  dead: "agotado",
};

export const MEMORY_STATUS: Record<string, string> = {
  active: "vigente",
  obsolete: "obsoleto",
  archived: "archivado",
  contradicted: "contradicho",
  deleted: "eliminado",
};

export const CONNECTION_STATUS: Record<string, string> = {
  pending: "pendiente",
  connected: "conectado",
  error: "error",
  revoked: "revocado",
  disconnected: "desconectado",
};

export const REQUEST_STATUS: Record<string, string> = {
  pending: "pendiente",
  approved: "aprobada",
  rejected: "rechazada",
  auto: "automática",
};

/** Etiqueta en español de un estado; si no hay traducción se muestra el valor tal cual. */
export const label = (map: Record<string, string>, value: string) => map[value] ?? value;

export function locatorText(loc: Record<string, unknown>): string {
  const parts: string[] = [];
  if (loc.page_from)
    parts.push(
      loc.page_from === loc.page_to || !loc.page_to
        ? `pág. ${loc.page_from}`
        : `págs. ${loc.page_from}-${loc.page_to}`,
    );
  if (loc.sheet) parts.push(`hoja ${loc.sheet}`);
  if (loc.slide) parts.push(`diapositiva ${loc.slide}`);
  return parts.length ? `(${parts.join(", ")})` : "";
}
