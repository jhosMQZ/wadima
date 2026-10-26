import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { get, post } from "../api/client";
import type { AreaTemplate, OrgFull, UserPublic } from "../api/types";
import { BrandMark } from "../components/BrandMark";
import { IdentityFields, LogoUploader, TimezoneField } from "../components/OrgIdentity";
import { identityOf, type IdentityValue } from "../lib/identity";
import { ErrorBox, Spinner } from "../components/ui";
import { ROLE_LABEL } from "../lib/format";
import { toast } from "../lib/toast";
import { useApi } from "../lib/useApi";
import { useBrand } from "../store/brand";
import { useSession } from "../store/session";

interface SetupState {
  org: OrgFull;
  templates: AreaTemplate[];
  users: (UserPublic & { email: string | null })[];
}

const STEPS = [
  "Organización",
  "Logotipo",
  "Zona horaria",
  "Usuarios iniciales",
  "Áreas",
  "Almacenamiento",
  "Integraciones de IA",
] as const;

/** Configuración inicial guiada. Con `firstRun` se muestra a pantalla completa antes del workspace. */
export default function SetupPage({ firstRun = false }: { firstRun?: boolean }) {
  const me = useSession((s) => s.me)!;
  const refresh = useSession((s) => s.refresh);
  const navigate = useNavigate();
  const [state, setState] = useState<SetupState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [step, setStep] = useState(0);
  const [identity, setIdentity] = useState<IdentityValue | null>(null);
  const [areas, setAreas] = useState<Set<string>>(new Set());
  const [users, setUsers] = useState<Record<string, { display_name: string; email: string }>>({});
  const [busy, setBusy] = useState(false);
  const drive = useApi<{ configured: boolean; connected: boolean; status: string }>("/api/drive/summary");

  useEffect(() => {
    if (me.user.role !== "admin") return;
    get<SetupState>("/api/org/setup")
      .then((s) => {
        setState(s);
        setIdentity(identityOf(s.org));
        const enabled = s.templates.filter((t) => t.enabled || !t.area_id).map((t) => t.key);
        setAreas(
          new Set(s.org.setup_completed ? s.templates.filter((t) => t.enabled).map((t) => t.key) : enabled),
        );
        setUsers(
          Object.fromEntries(
            s.users.map((u) => [u.id, { display_name: u.display_name, email: u.email ?? "" }]),
          ),
        );
      })
      .catch((err) => setError((err as Error).message));
  }, [me.user.role]);

  if (me.user.role !== "admin") {
    return <div className="alert warn">Solo un administrador puede ejecutar la configuración inicial.</div>;
  }

  async function finish(useDefaults = false) {
    if (!state || !identity) return;
    setBusy(true);
    setError(null);
    try {
      const body = {
        version: state.org.version,
        ...identity,
        areas: useDefaults ? state.templates.map((t) => t.key) : [...areas],
        users: useDefaults
          ? []
          : state.users.map((u) => ({
              id: u.id,
              display_name: users[u.id].display_name,
              email: users[u.id].email,
            })),
      };
      const org = await post<OrgFull>("/api/org/setup", body);
      useBrand.getState().setOrg(org);
      await refresh();
      toast(`${org.platform_name} está listo`, "ok");
      navigate("/");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const content = (
    <div className="setup" data-testid="setup-wizard">
      <div className="panel">
        <div className="panel-title">
          <div>
            <h1 style={{ marginBottom: 4 }}>
              {firstRun ? "Configuración inicial" : "Asistente de configuración"}
            </h1>
            <p className="muted" style={{ margin: 0 }}>
              Paso {step + 1} de {STEPS.length}: {STEPS[step]}. Podrá cambiar todo más adelante en
              Configuración y Áreas.
            </p>
          </div>
          {firstRun && <BrandMark />}
        </div>
        <ol className="steps" aria-label="Pasos">
          {STEPS.map((s, i) => (
            <li key={s} className={i === step ? "current" : i < step ? "done" : ""}>
              <button
                type="button"
                className="link"
                onClick={() => setStep(i)}
                aria-current={i === step ? "step" : undefined}
              >
                {i + 1}. {s}
              </button>
            </li>
          ))}
        </ol>
        <ErrorBox error={error} />
        {!state || !identity ? (
          <Spinner />
        ) : (
          <div className="setup-body">
            {step === 0 && <IdentityFields value={identity} onChange={setIdentity} />}
            {step === 1 && (
              <LogoUploader
                org={state.org}
                onUpdated={(org) => {
                  setState({ ...state, org });
                  useBrand.getState().setOrg(org);
                }}
              />
            )}
            {step === 2 && (
              <div className="stack" style={{ maxWidth: 420 }}>
                <TimezoneField
                  value={identity.timezone}
                  onChange={(tz) => setIdentity({ ...identity, timezone: tz })}
                />
                <p className="small muted">Para Colombia: America/Bogota (UTC−5, sin horario de verano).</p>
              </div>
            )}
            {step === 3 && (
              <div className="stack">
                <p className="small muted" style={{ margin: 0 }}>
                  Las cuentas iniciales se crearon con contraseñas temporales (python manage.py users); cada
                  socio debe cambiarla en su primer acceso. Aquí puede ajustar el nombre visible y el correo.
                  Para añadir más personas use Administración → Usuarios.
                </p>
                <div className="table-wrap">
                  <table className="table">
                    <thead>
                      <tr>
                        <th>Usuario</th>
                        <th>Rol</th>
                        <th>Nombre visible</th>
                        <th>Correo</th>
                      </tr>
                    </thead>
                    <tbody>
                      {state.users.map((u) => (
                        <tr key={u.id}>
                          <td>{u.username}</td>
                          <td>{ROLE_LABEL[u.role]}</td>
                          <td>
                            <input
                              aria-label={`Nombre de ${u.username}`}
                              value={users[u.id].display_name}
                              minLength={2}
                              maxLength={80}
                              onChange={(e) =>
                                setUsers({
                                  ...users,
                                  [u.id]: { ...users[u.id], display_name: e.target.value },
                                })
                              }
                            />
                          </td>
                          <td>
                            <input
                              aria-label={`Correo de ${u.username}`}
                              type="email"
                              value={users[u.id].email}
                              onChange={(e) =>
                                setUsers({ ...users, [u.id]: { ...users[u.id], email: e.target.value } })
                              }
                            />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
            {step === 4 && (
              <div className="grid cols-2" role="group" aria-label="Áreas que se habilitarán">
                {state.templates.map((t) => (
                  <label key={t.key} className={`panel template-card${areas.has(t.key) ? " on" : ""}`}>
                    <span className="row">
                      <input
                        type="checkbox"
                        checked={t.required || areas.has(t.key)}
                        disabled={t.required}
                        onChange={(e) => {
                          const next = new Set(areas);
                          if (e.target.checked) next.add(t.key);
                          else next.delete(t.key);
                          setAreas(next);
                        }}
                      />
                      <strong>
                        <span aria-hidden="true">{t.icon}</span> {t.name}
                      </strong>
                      {t.required && <span className="badge info right">siempre</span>}
                    </span>
                    <span className="small muted">{t.description}</span>
                    <span className="small faint">
                      Módulo {t.module} · miembros: {ROLE_LABEL[t.member_role]}
                      {t.area_name && t.area_name !== t.name
                        ? ` · usa el área existente «${t.area_name}»`
                        : ""}
                    </span>
                  </label>
                ))}
              </div>
            )}
            {step === 5 && (
              <div className="stack">
                <div className="panel">
                  <h3>Base de datos y archivos locales</h3>
                  <p className="small" style={{ margin: 0 }}>
                    Los datos operativos (jornadas, proyectos, notas, configuración) se guardan en PostgreSQL
                    en este equipo; los originales de la biblioteca en <code>var/blobs</code>. Copia completa:{" "}
                    <code>python manage.py db backup</code>.
                  </p>
                </div>
                <div className="panel">
                  <h3>Google Drive (documentos compartidos)</h3>
                  <p className="small" style={{ margin: 0 }}>
                    Estado:{" "}
                    <strong>
                      {drive.data
                        ? drive.data.connected
                          ? "conectado"
                          : drive.data.configured
                            ? "configurado, sin conectar"
                            : "sin credenciales de Google en .env"
                        : "comprobando…"}
                    </strong>
                    . Drive es el almacenamiento central de documentos y de la copia lógica diaria. Puede
                    conectarlo después en Servidores e integraciones.
                  </p>
                </div>
              </div>
            )}
            {step === 6 && (
              <div className="stack">
                <p style={{ margin: 0 }}>Cada socio conecta su propia cuenta; WADIMA funciona sin IA.</p>
                <ul className="small">
                  <li>
                    <strong>Suscripción de Claude (Pro/Max):</strong> desde Claude Code, conectando WADIMA
                    como servidor MCP con un token personal. WADIMA nunca ve credenciales de claude.ai.
                  </li>
                  <li>
                    <strong>API de Anthropic:</strong> API key propia de Claude Console (pago por uso,
                    independiente de la suscripción).
                  </li>
                </ul>
                <p className="small muted" style={{ margin: 0 }}>
                  Cada socio lo configura en <Link to="/ai">Configuración de IA</Link>.
                </p>
              </div>
            )}
            <div className="row wrap setup-nav">
              <button type="button" className="ghost" disabled={step === 0} onClick={() => setStep(step - 1)}>
                Anterior
              </button>
              {step < STEPS.length - 1 ? (
                <button type="button" className="primary" onClick={() => setStep(step + 1)}>
                  Siguiente
                </button>
              ) : (
                <button
                  type="button"
                  className="primary"
                  disabled={busy}
                  onClick={() => void finish()}
                  data-testid="setup-finish"
                >
                  {busy ? "Guardando…" : "Terminar y entrar"}
                </button>
              )}
              {firstRun && (
                <button
                  type="button"
                  className="link right"
                  disabled={busy}
                  onClick={() => void finish(true)}
                >
                  Usar los valores predeterminados
                </button>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
  return firstRun ? <div className="login-wrap">{content}</div> : content;
}
