import { Link } from "react-router-dom";
import { useApi } from "../lib/useApi";

export interface SyncSummary {
  configured: boolean;
  connected: boolean;
  status: string;
  counts: Record<string, number>;
  last_sync_at: string | null;
  last_error: string;
}

const LABEL: Record<string, string> = {
  synced: "Drive sincronizado",
  pending: "Cambios pendientes de subir",
  error: "Error de sincronización",
  conflict: "Conflictos de sincronización",
  disconnected: "Drive desconectado",
  not_configured: "Drive no configurado",
};

export function SyncIndicator() {
  const { data } = useApi<SyncSummary>("/api/drive/summary", {
    refreshOn: ["sync.changed", "drive.changed"],
    intervalMs: 60000,
  });
  if (!data) return null;
  const tone =
    data.status === "synced"
      ? "ok"
      : data.status === "conflict"
        ? "conflict"
        : data.status === "error"
          ? "err"
          : "warn";
  const pending = (data.counts.pending ?? 0) + (data.counts.uploading ?? 0);
  return (
    <Link to="/connections" className={`badge ${tone}`} title={data.last_error || LABEL[data.status]}>
      ☁ {LABEL[data.status] ?? data.status}
      {pending > 0 && ` (${pending})`}
    </Link>
  );
}
