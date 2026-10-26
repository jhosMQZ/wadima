import { useEffect, useRef, useState, type ReactNode } from "react";
import { useToasts } from "../lib/toast";

export function Spinner({ label = "Cargando" }: { label?: string }) {
  return (
    <span className="row muted" role="status">
      <span className="spinner" aria-hidden="true" />
      <span>{label}…</span>
    </span>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <div className="pixel">{title}</div>
      {children && <div style={{ marginTop: 6 }}>{children}</div>}
    </div>
  );
}

export function ErrorBox({ error, onRetry }: { error: string | null; onRetry?: () => void }) {
  if (!error) return null;
  return (
    <div className="alert err" role="alert">
      <div className="row">
        <span>{error}</span>
        {onRetry && (
          <button className="small right" onClick={onRetry}>
            Reintentar
          </button>
        )}
      </div>
    </div>
  );
}

export function Badge({ tone = "info", children }: { tone?: string; children: ReactNode }) {
  return <span className={`badge ${tone}`}>{children}</span>;
}

export function Modal({
  title,
  onClose,
  children,
  wide,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      prev?.focus?.();
    };
  }, [onClose]);
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        className={`modal${wide ? " wide" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        ref={ref}
      >
        <div className="panel-title">
          <h2>{title}</h2>
          <button className="ghost small" onClick={onClose} aria-label="Cerrar">
            ✕
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function ConfirmDialog({
  title,
  message,
  confirmLabel = "Confirmar",
  requireText,
  danger,
  onConfirm,
  onClose,
}: {
  title: string;
  message: ReactNode;
  confirmLabel?: string;
  requireText?: string;
  danger?: boolean;
  onConfirm: () => Promise<void> | void;
  onClose: () => void;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ok = !requireText || text === requireText;
  return (
    <Modal title={title} onClose={onClose}>
      <div className="stack">
        <div>{message}</div>
        {requireText && (
          <label className="field">
            Escriba <code>{requireText}</code> para confirmar
            <input value={text} onChange={(e) => setText(e.target.value)} autoFocus />
          </label>
        )}
        <ErrorBox error={error} />
        <div className="row">
          <button className="ghost" onClick={onClose}>
            Cancelar
          </button>
          <button
            className={danger ? "danger" : "primary"}
            disabled={!ok || busy}
            onClick={async () => {
              setBusy(true);
              setError(null);
              try {
                await onConfirm();
                onClose();
              } catch (e) {
                setError((e as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          >
            {busy ? "Procesando…" : confirmLabel}
          </button>
        </div>
      </div>
    </Modal>
  );
}

export function Toasts() {
  const items = useToasts((s) => s.items);
  return (
    <div className="toasts" aria-live="polite">
      {items.map((t) => (
        <div key={t.id} className={`toast ${t.tone}`} role={t.tone === "err" ? "alert" : "status"}>
          {t.text}
        </div>
      ))}
    </div>
  );
}

export function Tabs<T extends string>({
  tabs,
  value,
  onChange,
}: {
  tabs: { key: T; label: string }[];
  value: T;
  onChange: (key: T) => void;
}) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button key={t.key} role="tab" aria-selected={value === t.key} onClick={() => onChange(t.key)}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function AvatarChip({ name, color }: { name: string; color?: string }) {
  return (
    <span className="avatar-chip" style={{ background: color ?? "#3f7cd8" }} aria-hidden="true">
      {name.slice(0, 1).toUpperCase()}
    </span>
  );
}
