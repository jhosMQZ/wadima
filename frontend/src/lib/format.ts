const rtf = new Intl.RelativeTimeFormat("es", { numeric: "auto" });
const dtf = new Intl.DateTimeFormat("es", { dateStyle: "medium", timeStyle: "short" });

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return dtf.format(new Date(iso));
}

export function timeAgo(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "nunca";
  const diff = (new Date(iso).getTime() - now) / 1000;
  const abs = Math.abs(diff);
  if (abs < 45) return "hace un momento";
  if (abs < 3600) return rtf.format(Math.round(diff / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(diff / 3600), "hour");
  if (abs < 86400 * 30) return rtf.format(Math.round(diff / 86400), "day");
  return formatDateTime(iso);
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export const STATE_LABEL: Record<string, string> = {
  online: "Conectado",
  working: "Trabajando",
  meeting: "En reunión",
  paused: "En pausa",
  offline: "Desconectado",
};

export const ROLE_LABEL: Record<string, string> = {
  owner: "Propietario",
  editor: "Editor",
  viewer: "Lector",
  admin: "Administrador",
  member: "Miembro",
};
