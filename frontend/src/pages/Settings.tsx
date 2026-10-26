import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { patch, post, put } from "../api/client";
import type { Avatar, OrgFull, TimePolicy } from "../api/types";
import { IdentityFields, LogoUploader } from "../components/OrgIdentity";
import { identityOf, type IdentityValue } from "../lib/identity";
import { ErrorBox, Spinner } from "../components/ui";
import { useApi } from "../lib/useApi";
import { useBrand } from "../store/brand";
import { toast } from "../lib/toast";
import { characterSheet } from "../office/sprites";
import { useSession } from "../store/session";

function AvatarPreview({ avatar }: { avatar: Avatar }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const ctx = ref.current?.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, 160, 24);
    characterSheet(avatar)
      .filter((_, i) => i % 3 === 0 || i === 7)
      .forEach((f, i) => f.paint(ctx, i * 18, 0));
  }, [avatar]);
  return (
    <canvas
      ref={ref}
      width={80}
      height={24}
      style={{ width: 320, height: 96, imageRendering: "pixelated", background: "#0b0d1c", borderRadius: 4 }}
      aria-label="Vista previa del avatar"
    />
  );
}

export default function SettingsPage() {
  const me = useSession((s) => s.me)!;
  const refresh = useSession((s) => s.refresh);
  const [name, setName] = useState(me.user.display_name);
  const [email, setEmail] = useState(me.user.email ?? "");
  const [avatar, setAvatar] = useState<Avatar>({
    skin: "#f1c7a1",
    hair: "#3b2a20",
    shirt: "#3f7cd8",
    pants: "#2d3142",
    style: 0,
    ...me.user.avatar,
  });
  const [error, setError] = useState<string | null>(null);
  const [pw, setPw] = useState({ current: "", next: "", repeat: "" });
  const [pwError, setPwError] = useState<string | null>(null);

  async function saveProfile(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await patch("/api/users/me", { display_name: name, email, avatar });
      await refresh();
      toast("Perfil guardado", "ok");
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function changePassword(e: FormEvent) {
    e.preventDefault();
    setPwError(null);
    if (pw.next !== pw.repeat) return setPwError("Las contraseñas no coinciden");
    try {
      const res = await post<{ revoked_sessions: number }>("/api/auth/change-password", {
        current_password: pw.current,
        new_password: pw.next,
      });
      setPw({ current: "", next: "", repeat: "" });
      toast(`Contraseña cambiada. Sesiones cerradas en otros dispositivos: ${res.revoked_sessions}`, "ok");
    } catch (err) {
      setPwError((err as Error).message);
    }
  }

  const colorField = (key: keyof Avatar, label: string) => (
    <label className="field">
      {label}
      <input
        type="color"
        value={String(avatar[key] ?? "#000000")}
        onChange={(e) => setAvatar({ ...avatar, [key]: e.target.value })}
      />
    </label>
  );

  return (
    <>
      <div className="page-head">
        <div className="grow">
          <h1>Configuración</h1>
          <p>Identidad de la organización, perfil, avatar de la oficina y contraseña.</p>
        </div>
      </div>
      <OrgSettings />
      <div className="grid cols-2">
        <form className="panel stack" onSubmit={saveProfile}>
          <h3>Perfil</h3>
          <ErrorBox error={error} />
          <label className="field">
            Nombre visible
            <input value={name} onChange={(e) => setName(e.target.value)} maxLength={80} required />
          </label>
          <label className="field">
            Correo (opcional, para compartir carpetas de Drive)
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} maxLength={200} />
          </label>
          <h3>Avatar</h3>
          <AvatarPreview avatar={avatar} />
          <div className="row">
            {colorField("skin", "Piel")}
            {colorField("hair", "Pelo")}
            {colorField("shirt", "Camisa")}
            {colorField("pants", "Pantalón")}
            <label className="field">
              Peinado
              <select
                value={avatar.style ?? 0}
                onChange={(e) => setAvatar({ ...avatar, style: Number(e.target.value) })}
                style={{ width: 120 }}
              >
                <option value={0}>Corto</option>
                <option value={1}>Puntas</option>
                <option value={2}>Largo</option>
                <option value={3}>Moño</option>
              </select>
            </label>
          </div>
          <button className="primary">Guardar perfil</button>
        </form>
        <form className="panel stack" onSubmit={changePassword}>
          <h3>Cambiar contraseña</h3>
          <ErrorBox error={pwError} />
          <label className="field">
            Contraseña actual
            <input
              type="password"
              autoComplete="current-password"
              value={pw.current}
              onChange={(e) => setPw({ ...pw, current: e.target.value })}
              required
            />
          </label>
          <label className="field">
            Nueva contraseña
            <input
              type="password"
              autoComplete="new-password"
              value={pw.next}
              minLength={10}
              onChange={(e) => setPw({ ...pw, next: e.target.value })}
              required
            />
          </label>
          <label className="field">
            Repetir
            <input
              type="password"
              autoComplete="new-password"
              value={pw.repeat}
              onChange={(e) => setPw({ ...pw, repeat: e.target.value })}
              required
            />
          </label>
          <button className="primary">Cambiar contraseña</button>
          <p className="small muted">Al cambiarla se cierran sus demás sesiones abiertas.</p>
        </form>
      </div>
    </>
  );
}

/** Identidad de la organización y políticas de control horario (solo administradores editan). */
function OrgSettings() {
  const org = useApi<OrgFull>("/api/org", { refreshOn: ["org.updated"] });
  if (!org.data) return <Spinner />;
  return <OrgForm key={org.data.version} org={org.data} onSaved={(o) => org.setData(o)} />;
}

function OrgForm({ org, onSaved }: { org: OrgFull; onSaved: (o: OrgFull) => void }) {
  const me = useSession((s) => s.me)!;
  const isAdmin = me.user.role === "admin";
  const [value, setValue] = useState<IdentityValue>(() => identityOf(org));
  const [policy, setPolicy] = useState<TimePolicy>(org.time);
  const [logoOrg, setLogoOrg] = useState(org);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <form
      className="panel stack"
      data-testid="org-identity"
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        setError(null);
        try {
          const saved = await put<OrgFull>("/api/org", { version: logoOrg.version, ...value, time: policy });
          useBrand.getState().setOrg(saved);
          onSaved({ ...org, ...saved });
          toast("Identidad de la organización guardada", "ok");
        } catch (err) {
          setError((err as Error).message);
        } finally {
          setBusy(false);
        }
      }}
    >
      <div className="panel-title">
        <h3>Identidad de la organización</h3>
        {isAdmin && (
          <Link className="small" to="/setup">
            Volver a ejecutar el asistente
          </Link>
        )}
      </div>
      {!isAdmin && <p className="small muted">Solo un administrador puede cambiar estos datos.</p>}
      <IdentityFields value={value} onChange={setValue} disabled={!isAdmin} />
      <h4>Logotipo</h4>
      {isAdmin ? (
        <LogoUploader
          org={logoOrg}
          onUpdated={(o) => {
            setLogoOrg(o); // la versión cambia; el formulario conserva lo escrito
            useBrand.getState().setOrg(o);
          }}
        />
      ) : (
        <p className="small muted">{org.logo_url ? "Con logotipo" : "Sin logotipo"}</p>
      )}
      <h4>Control horario</h4>
      <fieldset disabled={!isAdmin} className="grid cols-2" style={{ border: 0, padding: 0, margin: 0 }}>
        <label className="field">
          Jornadas sin salida
          <select
            value={policy.auto_close}
            onChange={(e) => setPolicy({ ...policy, auto_close: e.target.value as TimePolicy["auto_close"] })}
          >
            <option value="none">Marcar «pendiente de cierre» (no inventar la salida)</option>
            <option value="last_activity">
              Cerrar a la hora de la última conexión (marcada como automática)
            </option>
          </select>
        </label>
        <label className="field">
          Quién ve el historial de todos
          <select
            value={policy.visibility}
            onChange={(e) => setPolicy({ ...policy, visibility: e.target.value as TimePolicy["visibility"] })}
          >
            <option value="all">Todos los socios</option>
            <option value="admins">Solo aprobadores (cada socio ve el suyo)</option>
          </select>
        </label>
        <label className="field">
          Duración máxima antes de marcar la jornada (horas)
          <input
            type="number"
            min={1}
            max={48}
            value={policy.max_open_hours}
            onChange={(e) => setPolicy({ ...policy, max_open_hours: Number(e.target.value) })}
          />
        </label>
        <label className="field">
          Desconexión que deja la jornada pendiente (minutos)
          <input
            type="number"
            min={5}
            max={720}
            value={policy.disconnect_minutes}
            onChange={(e) => setPolicy({ ...policy, disconnect_minutes: Number(e.target.value) })}
          />
        </label>
      </fieldset>
      <ErrorBox error={error} />
      {isAdmin && (
        <button className="primary" disabled={busy}>
          {busy ? "Guardando…" : "Guardar identidad y políticas"}
        </button>
      )}
    </form>
  );
}
