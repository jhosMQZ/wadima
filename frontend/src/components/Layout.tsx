import { useEffect, useState, type ReactNode } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { put } from "../api/client";
import { realtime, type WsStatus } from "../api/ws";
import { useTemplates } from "../lib/areas";
import { toast } from "../lib/toast";
import { useSession } from "../store/session";
import { BrandMark } from "./BrandMark";
import { NotificationsBell } from "./NotificationsBell";
import { SyncIndicator } from "./SyncIndicator";
import { ClockWidget, LogoutButton } from "./TimeClock";

interface NavItem {
  to: string;
  label: string;
  icon: string;
  admin?: boolean;
  /** Plantilla de área asociada: el enlace se oculta si esa área está deshabilitada. */
  template?: string;
}

const NAV: { group: string; items: NavItem[] }[] = [
  {
    group: "Oficina",
    items: [
      { to: "/", label: "Oficina virtual", icon: "▦" },
      { to: "/ops", label: "Centro de operaciones", icon: "◉" },
      { to: "/projects", label: "Proyectos", icon: "▤" },
    ],
  },
  {
    group: "Áreas de trabajo",
    items: [
      { to: "/studio", label: "Desarrollo", icon: "⌘", template: "dev" },
      { to: "/library", label: "Biblioteca", icon: "▥", template: "library" },
      { to: "/memory", label: "Memoria compartida", icon: "◈", template: "library" },
      { to: "/agents", label: "Laboratorio de IA", icon: "✦", template: "ai" },
      { to: "/meetings", label: "Reuniones", icon: "☰", template: "meeting" },
      { to: "/connections", label: "Servidores e integraciones", icon: "⇄", template: "servers" },
    ],
  },
  {
    group: "Gestión",
    items: [
      { to: "/time", label: "Control horario", icon: "◷" },
      { to: "/areas", label: "Áreas", icon: "◧" },
      { to: "/ai", label: "Configuración de IA", icon: "✧" },
      { to: "/settings", label: "Configuración", icon: "⚙" },
      { to: "/security", label: "Seguridad", icon: "⚿" },
      { to: "/admin", label: "Administración", icon: "⛭", admin: true },
    ],
  },
];

function WsIndicator() {
  const [status, setStatus] = useState<WsStatus>(realtime.status);
  useEffect(() => realtime.onStatus(setStatus), []);
  const label =
    status === "open"
      ? "Tiempo real activo"
      : status === "connecting"
        ? "Conectando…"
        : "Sin conexión con el servidor";
  return (
    <span className="row small muted" title={label} data-testid="ws-indicator" data-status={status}>
      <span
        className={`dot ${status === "open" ? "online" : status === "connecting" ? "paused" : "offline"}`}
      />
      <span className="sr-only">{label}</span>
    </span>
  );
}

/** Aviso visible cuando se pierde la conexión: nada se da por guardado hasta que el servidor responde. */
function OfflineBanner() {
  const [status, setStatus] = useState<WsStatus>(realtime.status);
  const [delayed, setDelayed] = useState(false);
  useEffect(
    () =>
      realtime.onStatus((s) => {
        setStatus(s);
        if (s === "open") setDelayed(false);
      }),
    [],
  );
  useEffect(() => {
    if (status === "open") return;
    const t = window.setTimeout(() => setDelayed(true), 4000); // evita parpadeos en reconexiones rápidas
    return () => window.clearTimeout(t);
  }, [status]);
  if (status === "open" || !delayed) return null;
  return (
    <div className="offline-banner" role="alert">
      Sin conexión con el servidor. Reintentando… Los cambios que haga ahora pueden no guardarse; las entradas
      y salidas solo cuentan cuando el servidor las confirma.
    </div>
  );
}

const STATUS_OPTIONS = [
  { value: "available", label: "Disponible" },
  { value: "working", label: "Trabajando" },
  { value: "meeting", label: "En reunión" },
  { value: "paused", label: "En pausa" },
];

function StatusSelect() {
  const initial = useSession((s) => s.me?.status?.declared ?? "available");
  const [value, setValue] = useState(initial);
  return (
    <label className="row small status-select" style={{ gap: 4 }}>
      <span className="sr-only">Mi estado</span>
      <select
        value={value}
        style={{ width: 132, padding: "3px 6px" }}
        aria-label="Mi estado declarado"
        onChange={async (e) => {
          const next = e.target.value;
          try {
            await put("/api/me/status", { declared: next, message: "" });
            setValue(next);
          } catch (err) {
            toast((err as Error).message, "err");
          }
        }}
      >
        {STATUS_OPTIONS.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

export function Layout({ children }: { children: ReactNode }) {
  const me = useSession((s) => s.me)!;
  const leaveArea = useSession((s) => s.leaveArea);
  const navigate = useNavigate();
  const isAdmin = me.user.role === "admin";
  const [navOpen, setNavOpen] = useState(false);
  const templates = useTemplates();
  const disabled = new Set((templates.data ?? []).filter((t) => t.area_id && !t.enabled).map((t) => t.key));
  return (
    <div className={navOpen ? "app nav-open" : "app"}>
      <a href="#main" className="skip-link">
        Saltar al contenido
      </a>
      <header className="topbar">
        <button
          className="small menu-toggle"
          aria-expanded={navOpen}
          aria-controls="main-nav"
          onClick={() => setNavOpen((v) => !v)}
        >
          {navOpen ? "Cerrar menú" : "Menú"}
        </button>
        <BrandMark />
        {me.active_area && (
          <span className="row small area-chip">
            <span className="dot" style={{ background: me.active_area.color }} />
            <strong>{me.active_area.name}</strong>
            <button
              className="small ghost"
              onClick={async () => {
                await leaveArea();
                navigate("/");
              }}
              title="Registrar salida del área (no cierra la jornada)"
            >
              Salir del área
            </button>
          </span>
        )}
        <span className="spacer" />
        <ClockWidget />
        <SyncIndicator />
        <StatusSelect />
        <NotificationsBell />
        <WsIndicator />
        <span className="small muted user-name">{me.user.display_name}</span>
        <LogoutButton />
      </header>
      <aside className="sidebar" id="main-nav" aria-label="Navegación principal">
        <nav>
          {NAV.map((g) => (
            <div key={g.group}>
              <div className="group">{g.group}</div>
              {g.items
                .filter((i) => (!i.admin || isAdmin) && !(i.template && disabled.has(i.template)))
                .map((i) => (
                  <NavLink key={i.to} to={i.to} end={i.to === "/"} onClick={() => setNavOpen(false)}>
                    <span className="nav-icon" aria-hidden="true">
                      {i.icon}
                    </span>
                    {i.label}
                  </NavLink>
                ))}
            </div>
          ))}
        </nav>
      </aside>
      <main id="main" className="content" tabIndex={-1}>
        <OfflineBanner />
        {children}
      </main>
    </div>
  );
}
