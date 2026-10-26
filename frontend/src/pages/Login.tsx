import { useState, type FormEvent } from "react";
import { post } from "../api/client";
import { BrandLogo } from "../components/BrandMark";
import { ErrorBox } from "../components/ui";
import { useBrand } from "../store/brand";
import { useSession } from "../store/session";

export function Login() {
  const org = useBrand((s) => s.org);
  const login = useSession((s) => s.login);
  const sessionError = useSession((s) => s.error);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username, password);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-wrap">
      <div className="panel login-card">
        <div className="login-brand">
          <BrandLogo size={48} />
          <div>
            <h1 data-testid="login-platform">{org.platform_name}</h1>
            <p className="tagline">{org.legal_name}</p>
          </div>
        </div>
        {sessionError && !error && <div className="alert warn">{sessionError}</div>}
        <ErrorBox error={error} />
        <form className="stack" onSubmit={submit}>
          <label className="field">
            Usuario
            <input
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              autoFocus
            />
          </label>
          <label className="field">
            Contraseña
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </label>
          <button className="primary" disabled={busy}>
            {busy ? "Entrando…" : "Entrar"}
          </button>
        </form>
      </div>
    </div>
  );
}

export function ChangePassword() {
  const me = useSession((s) => s.me)!;
  const refresh = useSession((s) => s.refresh);
  const logout = useSession((s) => s.logout);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (next !== repeat) {
      setError("Las contraseñas no coinciden");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await post("/api/auth/change-password", { current_password: current, new_password: next });
      await refresh();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-wrap">
      <div className="panel login-card">
        <h2>Cambie su contraseña</h2>
        <p className="muted">
          Hola, {me.user.display_name}. Por seguridad debe sustituir la contraseña temporal antes de
          continuar. Mínimo 10 caracteres y tres tipos (minúsculas, mayúsculas, números o símbolos).
        </p>
        <ErrorBox error={error} />
        <form className="stack" onSubmit={submit}>
          <label className="field">
            Contraseña actual
            <input
              type="password"
              autoComplete="current-password"
              value={current}
              onChange={(e) => setCurrent(e.target.value)}
              required
            />
          </label>
          <label className="field">
            Nueva contraseña
            <input
              type="password"
              autoComplete="new-password"
              value={next}
              onChange={(e) => setNext(e.target.value)}
              required
              minLength={10}
            />
          </label>
          <label className="field">
            Repita la nueva contraseña
            <input
              type="password"
              autoComplete="new-password"
              value={repeat}
              onChange={(e) => setRepeat(e.target.value)}
              required
            />
          </label>
          <div className="row">
            <button type="button" className="ghost" onClick={() => logout()}>
              Cancelar
            </button>
            <button className="primary" disabled={busy}>
              {busy ? "Guardando…" : "Guardar y continuar"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
