import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError, del, patch, post, put } from "../api/client";
import type { Area, AreaTemplate, UserPublic } from "../api/types";
import { ConfirmDialog, EmptyState, ErrorBox, Modal, Spinner, Tabs } from "../components/ui";
import { toast } from "../lib/toast";
import { formatDateTime, ROLE_LABEL } from "../lib/format";
import { useApi } from "../lib/useApi";
import { ROOM_NAMES } from "../office/rooms";
import { useTemplates } from "../lib/areas";
import { useSession } from "../store/session";
import { REQUEST_STATUS, label } from "../lib/labels";

export function CreateAreaForm({ onDone }: { onDone: (area: Area | null) => void }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [room, setRoom] = useState("ops");
  const [color, setColor] = useState("#5b8def");
  const [error, setError] = useState<string | null>(null);
  async function submit(e: FormEvent) {
    e.preventDefault();
    try {
      const area = await post<Area>("/api/areas", { name, description, room_key: room, color });
      toast(`Área «${area.name}» creada`, "ok");
      onDone(area);
    } catch (err) {
      setError((err as Error).message);
    }
  }
  return (
    <form className="stack" onSubmit={submit}>
      <ErrorBox error={error} />
      <label className="field">
        Nombre
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
          minLength={2}
          maxLength={100}
        />
      </label>
      <label className="field">
        Descripción
        <textarea value={description} onChange={(e) => setDescription(e.target.value)} maxLength={2000} />
      </label>
      <div className="row">
        <label className="field" style={{ flex: 1 }}>
          Sala de la oficina
          <select value={room} onChange={(e) => setRoom(e.target.value)}>
            {Object.entries(ROOM_NAMES).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          Color
          <input type="color" value={color} onChange={(e) => setColor(e.target.value)} />
        </label>
      </div>
      <div className="row">
        <button type="button" className="ghost" onClick={() => onDone(null)}>
          Cancelar
        </button>
        <button className="primary">Crear área</button>
      </div>
    </form>
  );
}

interface AreasResponse {
  areas: Area[];
}

function PredefinedAreas() {
  const me = useSession((s) => s.me)!;
  const isAdmin = me.user.role === "admin";
  const templates = useTemplates();
  const [busy, setBusy] = useState<string | null>(null);
  if (!templates.data) return <Spinner />;
  return (
    <div className="panel" data-testid="predefined-areas">
      <h3>Áreas predefinidas</h3>
      <p className="small muted" style={{ marginTop: 0 }}>
        Cada una tiene su sala en la oficina, su módulo y permisos iniciales. Deshabilitar un área la oculta
        de la oficina y del menú sin borrar sus datos.
      </p>
      <div className="grid cols-2">
        {templates.data.map((t) => (
          <div key={t.key} className={`template-card panel${t.enabled ? " on" : ""}`}>
            <span className="row">
              <strong>
                <span aria-hidden="true">{t.icon}</span> {t.name}
              </strong>
              <span className={`badge right ${t.enabled ? "ok" : "muted"}`}>
                {t.enabled ? "habilitada" : t.area_id ? "deshabilitada" : "sin crear"}
              </span>
            </span>
            <span className="small muted">{t.description}</span>
            <span className="small faint">
              Módulo <Link to={t.module}>{t.module}</Link> · miembros: {ROLE_LABEL[t.member_role]}
              {t.area_name && t.area_name !== t.name ? ` · área «${t.area_name}»` : ""}
            </span>
            {isAdmin && !t.required && (
              <div className="row">
                <button
                  className="small"
                  disabled={busy === t.key}
                  onClick={async () => {
                    setBusy(t.key);
                    try {
                      templates.setData(
                        await post<AreaTemplate[]>(
                          `/api/org/templates/${t.key}/${t.enabled ? "disable" : "enable"}`,
                        ),
                      );
                      toast(t.enabled ? `«${t.name}» deshabilitada` : `«${t.name}» habilitada`, "ok");
                    } catch (err) {
                      toast((err as Error).message, "err");
                    } finally {
                      setBusy(null);
                    }
                  }}
                >
                  {t.enabled ? "Deshabilitar" : t.area_id ? "Habilitar" : "Crear y habilitar"}
                </button>
              </div>
            )}
            {t.required && <span className="small faint">Siempre habilitada (punto de entrada).</span>}
          </div>
        ))}
      </div>
    </div>
  );
}

export function AreasPage() {
  const { data, error, loading, reload } = useApi<AreasResponse>("/api/areas", {
    refreshOn: ["area.updated"],
  });
  const [creating, setCreating] = useState(false);
  const custom = data?.areas.filter((a) => !a.template_key) ?? [];
  return (
    <>
      <div className="page-head">
        <div className="grow">
          <h1>Áreas</h1>
          <p>Unidades de trabajo con miembros, permisos, invitaciones y registro de entradas propios.</p>
        </div>
        <Link className="btn" to="/join">
          Usar código de invitación
        </Link>
        <button className="primary" onClick={() => setCreating(true)}>
          Área personalizada
        </button>
      </div>
      <PredefinedAreas />
      <ErrorBox error={error} onRetry={() => reload()} />
      {loading && !data && <Spinner />}
      <h2>Mis áreas</h2>
      {data && data.areas.length === 0 && <EmptyState title="Sin áreas" />}
      <div className="grid cols-3">
        {data?.areas.map((a) => (
          <Link key={a.id} to={`/areas/${a.id}`} className="card-link">
            <div
              className="panel"
              style={{ borderLeft: `6px solid ${a.color}`, opacity: a.enabled ? 1 : 0.6 }}
            >
              <div className="row">
                <strong>
                  {a.icon && <span aria-hidden="true">{a.icon} </span>}
                  {a.name}
                </strong>
                <span className="badge info right">{ROLE_LABEL[a.my_role ?? ""]}</span>
              </div>
              <div className="small muted">
                {ROOM_NAMES[a.room_key] ?? a.room_key} · {a.member_count} miembro(s)
                {a.template_key ? " · predefinida" : " · personalizada"}
                {!a.enabled && " · deshabilitada"}
              </div>
            </div>
          </Link>
        ))}
      </div>
      {custom.length === 0 && data && (
        <p className="small muted">
          No hay áreas personalizadas. Cree una solo si necesita separar un trabajo (p. ej. un cliente) con
          sus propios miembros; no duplique las predefinidas.
        </p>
      )}
      {creating && (
        <Modal title="Nueva área" onClose={() => setCreating(false)}>
          <CreateAreaForm
            onDone={() => {
              setCreating(false);
              reload(true);
            }}
          />
        </Modal>
      )}
    </>
  );
}

interface AreaDetail extends Area {
  members: (UserPublic & { area_role: string; since: string })[];
}
interface Invitation {
  id: string;
  code_hint: string;
  role: string;
  requires_approval: boolean;
  max_uses: number;
  uses: number;
  note: string;
  expires_at: string;
  state: string;
  created_at: string;
}
interface AccessReq {
  id: string;
  user: UserPublic;
  role: string;
  status: string;
  created_at: string;
}
interface Visit {
  id: string;
  user: UserPublic;
  entered_at: string;
  left_at: string | null;
  end_reason: string | null;
  entry_audit_id: number | null;
  exit_audit_id: number | null;
}

const END_REASON: Record<string, string> = {
  leave: "Salida manual",
  logout: "Cierre de sesión",
  switch: "Cambio de área",
  inactivity: "Inactividad",
  session_ended: "Fin de sesión",
  session_revoked: "Sesión revocada",
  area_archived: "Área archivada",
  membership_removed: "Membresía retirada",
  user_disabled: "Usuario desactivado",
  password_reset: "Contraseña restablecida",
};

export function AreaDetailPage() {
  const { id } = useParams();
  const me = useSession((s) => s.me)!;
  const navigate = useNavigate();
  const {
    data: area,
    error,
    reload,
  } = useApi<AreaDetail>(`/api/areas/${id}`, { refreshOn: ["area.updated"] });
  const [tab, setTab] = useState<"members" | "invitations" | "requests" | "visits" | "settings">("members");
  if (error) return <ErrorBox error={error} onRetry={() => reload()} />;
  if (!area) return <Spinner />;
  const isOwner = area.my_role === "owner";
  const tabs = [
    { key: "members" as const, label: "Miembros" },
    { key: "visits" as const, label: "Registro horario" },
    ...(isOwner
      ? [
          { key: "invitations" as const, label: "Invitaciones" },
          { key: "requests" as const, label: "Solicitudes" },
          { key: "settings" as const, label: "Configuración" },
        ]
      : []),
  ];
  return (
    <>
      <div className="page-head">
        <div className="grow">
          <h1 style={{ borderLeft: `6px solid ${area.color}`, paddingLeft: 10 }}>{area.name}</h1>
          <p>
            {area.description || "Sin descripción"} · Sala: {ROOM_NAMES[area.room_key]} · Su rol:{" "}
            {ROLE_LABEL[area.my_role ?? ""]}
          </p>
        </div>
        {me.active_area?.id !== area.id && (
          <button
            className="primary"
            onClick={async () => {
              await useSession.getState().enterArea(area.id);
              toast(`Entrada registrada en «${area.name}»`, "ok");
            }}
          >
            Entrar en el área
          </button>
        )}
      </div>
      <Tabs tabs={tabs} value={tab} onChange={setTab} />
      {tab === "members" && <Members area={area} isOwner={isOwner} reload={() => reload(true)} />}
      {tab === "invitations" && <Invitations areaId={area.id} />}
      {tab === "requests" && <Requests areaId={area.id} />}
      {tab === "visits" && <Visits areaId={area.id} isOwner={isOwner} />}
      {tab === "settings" && (
        <AreaSettings area={area} onArchived={() => navigate("/areas")} reload={() => reload(true)} />
      )}
    </>
  );
}

function Members({ area, isOwner, reload }: { area: AreaDetail; isOwner: boolean; reload: () => void }) {
  const { data: users } = useApi<UserPublic[]>("/api/users");
  const [adding, setAdding] = useState("");
  const [role, setRole] = useState("editor");
  const memberIds = new Set(area.members.map((m) => m.id));
  const candidates = (users ?? []).filter((u) => !memberIds.has(u.id) && u.is_active);
  return (
    <div className="panel">
      <table className="table">
        <thead>
          <tr>
            <th>Usuario</th>
            <th>Rol en el área</th>
            <th>Desde</th>
            {isOwner && <th />}
          </tr>
        </thead>
        <tbody>
          {area.members.map((m) => (
            <tr key={m.id}>
              <td>
                {m.display_name} <span className="faint small">@{m.username}</span>
              </td>
              <td>
                {isOwner ? (
                  <select
                    value={m.area_role}
                    aria-label={`Rol de ${m.display_name}`}
                    onChange={async (e) => {
                      try {
                        await put(`/api/areas/${area.id}/members/${m.id}`, { role: e.target.value });
                        reload();
                      } catch (err) {
                        toast((err as Error).message, "err");
                      }
                    }}
                    style={{ width: 150 }}
                  >
                    <option value="owner">Propietario</option>
                    <option value="editor">Editor</option>
                    <option value="viewer">Lector</option>
                  </select>
                ) : (
                  ROLE_LABEL[m.area_role]
                )}
              </td>
              <td className="small muted">{formatDateTime(m.since)}</td>
              {isOwner && (
                <td>
                  <button
                    className="small danger"
                    onClick={async () => {
                      try {
                        await del(`/api/areas/${area.id}/members/${m.id}`);
                        reload();
                      } catch (err) {
                        toast((err as Error).message, "err");
                      }
                    }}
                  >
                    Quitar
                  </button>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
      {isOwner && candidates.length > 0 && (
        <div className="row" style={{ marginTop: 12 }}>
          <select
            value={adding}
            onChange={(e) => setAdding(e.target.value)}
            style={{ width: 220 }}
            aria-label="Usuario"
          >
            <option value="">Añadir usuario…</option>
            {candidates.map((u) => (
              <option key={u.id} value={u.id}>
                {u.display_name}
              </option>
            ))}
          </select>
          <select
            value={role}
            onChange={(e) => setRole(e.target.value)}
            style={{ width: 140 }}
            aria-label="Rol"
          >
            <option value="editor">Editor</option>
            <option value="viewer">Lector</option>
            <option value="owner">Propietario</option>
          </select>
          <button
            disabled={!adding}
            onClick={async () => {
              await put(`/api/areas/${area.id}/members/${adding}`, { role });
              setAdding("");
              reload();
            }}
          >
            Añadir
          </button>
        </div>
      )}
    </div>
  );
}

function Invitations({ areaId }: { areaId: string }) {
  const { data, reload } = useApi<Invitation[]>(`/api/areas/${areaId}/invitations`);
  const [form, setForm] = useState({
    role: "editor",
    expires_hours: 72,
    max_uses: 1,
    requires_approval: false,
    note: "",
  });
  const [code, setCode] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  return (
    <div className="grid cols-2">
      <form
        className="panel stack"
        onSubmit={async (e) => {
          e.preventDefault();
          setError(null);
          try {
            const res = await post<{ code: string }>(`/api/areas/${areaId}/invitations`, form);
            setCode(res.code);
            reload(true);
          } catch (err) {
            setError((err as Error).message);
          }
        }}
      >
        <h3>Generar código</h3>
        <ErrorBox error={error} />
        <label className="field">
          Permisos
          <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
            <option value="editor">Editor (puede crear y editar)</option>
            <option value="viewer">Lector (solo lectura)</option>
          </select>
        </label>
        <div className="row">
          <label className="field" style={{ flex: 1 }}>
            Caduca en (horas)
            <input
              type="number"
              min={1}
              max={720}
              value={form.expires_hours}
              onChange={(e) => setForm({ ...form, expires_hours: Number(e.target.value) })}
            />
          </label>
          <label className="field" style={{ flex: 1 }}>
            Usos máximos
            <input
              type="number"
              min={1}
              max={20}
              value={form.max_uses}
              onChange={(e) => setForm({ ...form, max_uses: Number(e.target.value) })}
            />
          </label>
        </div>
        <label className="inline">
          <input
            type="checkbox"
            checked={form.requires_approval}
            onChange={(e) => setForm({ ...form, requires_approval: e.target.checked })}
          />
          Requiere aprobación de un propietario
        </label>
        <label className="field">
          Nota (opcional)
          <input
            value={form.note}
            maxLength={200}
            onChange={(e) => setForm({ ...form, note: e.target.value })}
          />
        </label>
        <button className="primary">Generar</button>
        {code && (
          <div className="alert ok">
            Código (se muestra solo ahora): <strong className="mono">{code}</strong>{" "}
            <button type="button" className="small" onClick={() => navigator.clipboard?.writeText(code)}>
              Copiar
            </button>
          </div>
        )}
      </form>
      <div className="panel">
        <h3>Códigos emitidos</h3>
        {!data?.length && <p className="muted">No hay invitaciones.</p>}
        <ul className="list">
          {data?.map((i) => (
            <li key={i.id}>
              <div className="row">
                <span className="mono">{i.code_hint}</span>
                <span className={`badge ${i.state === "active" ? "ok" : "muted"}`}>{i.state}</span>
                <span className="small muted">
                  {ROLE_LABEL[i.role]} · {i.uses}/{i.max_uses} usos · caduca {formatDateTime(i.expires_at)}
                </span>
                {i.state === "active" && (
                  <button
                    className="small danger right"
                    onClick={async () => {
                      await post(`/api/invitations/${i.id}/revoke`);
                      reload(true);
                    }}
                  >
                    Revocar
                  </button>
                )}
              </div>
              {i.note && <div className="small faint">{i.note}</div>}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function Requests({ areaId }: { areaId: string }) {
  const { data, reload } = useApi<AccessReq[]>(`/api/areas/${areaId}/requests`, {
    refreshOn: ["notification.new"],
  });
  if (!data) return <Spinner />;
  if (!data.length) return <EmptyState title="No hay solicitudes de acceso" />;
  return (
    <div className="panel">
      <ul className="list">
        {data.map((r) => (
          <li key={r.id} className="row">
            <strong>{r.user.display_name}</strong>
            <span className="small muted">
              {ROLE_LABEL[r.role]} · {formatDateTime(r.created_at)}
            </span>
            <span className="badge info">{label(REQUEST_STATUS, r.status)}</span>
            {r.status === "pending" && (
              <span className="row right">
                <button
                  className="small primary"
                  onClick={async () => {
                    await post(`/api/access-requests/${r.id}/decide`, { approve: true });
                    reload(true);
                  }}
                >
                  Aprobar
                </button>
                <button
                  className="small danger"
                  onClick={async () => {
                    await post(`/api/access-requests/${r.id}/decide`, { approve: false });
                    reload(true);
                  }}
                >
                  Rechazar
                </button>
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function Visits({ areaId, isOwner }: { areaId: string; isOwner: boolean }) {
  const { data } = useApi<Visit[]>(`/api/areas/${areaId}/visits`, { refreshOn: ["presence.changed"] });
  if (!data) return <Spinner />;
  return (
    <div className="panel">
      <p className="small muted" style={{ marginTop: 0 }}>
        Horas del servidor. Cada entrada y salida está enlazada a la bitácora de auditoría encadenada por hash
        y no puede modificarse.{" "}
        {isOwner
          ? "Como propietario ve los registros de todos los miembros."
          : "Solo ve sus propios registros."}
      </p>
      {!data.length && <EmptyState title="Sin registros" />}
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Usuario</th>
              <th>Entrada</th>
              <th>Salida</th>
              <th>Motivo</th>
              <th>Auditoría</th>
            </tr>
          </thead>
          <tbody>
            {data.map((v) => (
              <tr key={v.id}>
                <td>{v.user.display_name}</td>
                <td>{formatDateTime(v.entered_at)}</td>
                <td>
                  {v.left_at ? formatDateTime(v.left_at) : <span className="badge ok">En el área</span>}
                </td>
                <td className="small">{v.end_reason ? (END_REASON[v.end_reason] ?? v.end_reason) : "—"}</td>
                <td className="small mono faint">
                  #{v.entry_audit_id}
                  {v.exit_audit_id ? ` / #${v.exit_audit_id}` : ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function AreaSettings({
  area,
  onArchived,
  reload,
}: {
  area: AreaDetail;
  onArchived: () => void;
  reload: () => void;
}) {
  const [form, setForm] = useState({
    name: area.name,
    description: area.description,
    room_key: area.room_key,
    color: area.color,
  });
  const [error, setError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);
  return (
    <div className="grid cols-2">
      <form
        className="panel stack"
        onSubmit={async (e) => {
          e.preventDefault();
          setError(null);
          try {
            await patch(`/api/areas/${area.id}`, { ...form, version: area.version });
            toast("Área actualizada", "ok");
            reload();
          } catch (err) {
            setError(
              err instanceof ApiError && err.status === 409
                ? "Otra persona modificó el área. Recargue para ver los cambios."
                : (err as Error).message,
            );
          }
        }}
      >
        <h3>Datos del área</h3>
        <ErrorBox error={error} />
        <label className="field">
          Nombre
          <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        </label>
        <label className="field">
          Descripción
          <textarea
            value={form.description}
            onChange={(e) => setForm({ ...form, description: e.target.value })}
          />
        </label>
        <div className="row">
          <label className="field" style={{ flex: 1 }}>
            Sala
            <select value={form.room_key} onChange={(e) => setForm({ ...form, room_key: e.target.value })}>
              {Object.entries(ROOM_NAMES).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            Color
            <input
              type="color"
              value={form.color}
              onChange={(e) => setForm({ ...form, color: e.target.value })}
            />
          </label>
        </div>
        <button className="primary">Guardar</button>
      </form>
      <div className="panel">
        <h3>Eliminar área</h3>
        <p className="muted">
          El área se archiva: deja de estar disponible para los miembros, se cierran las entradas abiertas y
          sus datos se conservan. Un administrador puede restaurarla.
        </p>
        <button className="danger" onClick={() => setConfirm(true)}>
          Archivar área…
        </button>
      </div>
      {confirm && (
        <ConfirmDialog
          title="Archivar área"
          message={<>¿Archivar «{area.name}»? Los miembros perderán el acceso hasta que se restaure.</>}
          requireText={area.slug}
          danger
          confirmLabel="Archivar"
          onClose={() => setConfirm(false)}
          onConfirm={async () => {
            await post(`/api/areas/${area.id}/archive`, { confirm_slug: area.slug });
            toast("Área archivada", "ok");
            onArchived();
          }}
        />
      )}
    </div>
  );
}
