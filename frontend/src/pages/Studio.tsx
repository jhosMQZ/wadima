import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError, del, get, post, put } from "../api/client";
import type { Area } from "../api/types";
import { realtime } from "../api/ws";
import { CodeEditor } from "../components/CodeEditor";
import { DiffView } from "../components/DiffView";
import { Badge, ConfirmDialog, EmptyState, ErrorBox, Modal, Spinner, Tabs } from "../components/ui";
import { formatDateTime, timeAgo } from "../lib/format";
import { canFormat, formatCode } from "../lib/format-code";
import { toast } from "../lib/toast";
import {
  fromDrop,
  fromInput,
  isSkipped,
  reportText,
  uploadToStudio,
  uploadZipToStudio,
  type PickedFile,
  type StudioUploadReport,
} from "../lib/upload";
import { useApi } from "../lib/useApi";
import { useSession } from "../store/session";

interface StudioProject {
  id: string;
  area_id: string;
  name: string;
  slug: string;
  template: string;
  template_name: string;
  profiles: string[];
  updated_at: string;
  preview_url: string;
  preview: { available: boolean; dev_running: boolean; dev_ready: boolean; dev_run_id: string | null };
  git?: { branch: string | null; changes: number | null };
}

interface TreeEntry {
  path: string;
  type: "file" | "dir";
  size?: number;
}

interface Tab {
  path: string;
  content: string;
  base: string;
  hash: string | null;
  dirty: boolean;
  saving: boolean;
  remoteChanged: boolean;
}

interface RunInfo {
  id: string;
  profile: string;
  command: string[];
  status: string;
  exit_code: number | null;
  ready: boolean;
  started_at: string;
  log?: string[];
}

const PROFILE_LABEL: Record<string, string> = {
  install: "Instalar dependencias",
  dev: "Servidor dev",
  build: "Compilar",
  test: "Pruebas",
  run: "Ejecutar",
};

export default function StudioPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const list = useApi<StudioProject[]>("/api/studio/projects", { refreshOn: ["studio.run"] });
  const [creating, setCreating] = useState(false);
  if (!id) {
    return (
      <>
        <div className="page-head">
          <div className="grow">
            <h1>Project Studio</h1>
            <p>
              Editor de código, Git, ejecución aislada y Live Preview compartida de forma segura con los
              socios.
            </p>
          </div>
          <button className="primary" onClick={() => setCreating(true)}>
            Nuevo proyecto de código
          </button>
        </div>
        <ErrorBox error={list.error} />
        {!list.data && <Spinner />}
        {list.data?.length === 0 && (
          <EmptyState title="Sin proyectos de código">
            Cree uno a partir de una plantilla o importe una carpeta o un ZIP de su equipo.
          </EmptyState>
        )}
        <div className="grid cols-3">
          {list.data?.map((p) => (
            <Link key={p.id} to={`/studio/${p.id}`} className="card-link">
              <div className="panel">
                <div className="row">
                  <strong style={{ flex: 1 }}>{p.name}</strong>
                  {p.preview.dev_running && <Badge tone="ok">dev en marcha</Badge>}
                </div>
                <div className="small muted">
                  {p.template_name} · actualizado {timeAgo(p.updated_at)}
                </div>
              </div>
            </Link>
          ))}
        </div>
        <Toolchains />
        {creating && (
          <CreateProject onClose={() => setCreating(false)} onCreated={(p) => navigate(`/studio/${p.id}`)} />
        )}
      </>
    );
  }
  return <Workspace id={id} />;
}

function Toolchains() {
  const { data } = useApi<Record<string, boolean>>("/api/studio/toolchains");
  if (!data) return null;
  return (
    <div className="panel">
      <h3>Herramientas detectadas en el servidor</h3>
      <div className="row">
        {Object.entries(data).map(([k, v]) => (
          <Badge key={k} tone={v ? "ok" : "muted"}>
            {k}
          </Badge>
        ))}
      </div>
      <p className="small muted">
        El editor resalta muchos lenguajes, pero solo se pueden ejecutar los que tienen su herramienta
        instalada. Las ejecuciones usan Job Objects de Windows (límites de memoria, CPU, procesos y tiempo),
        integridad Baja (sin acceso a los datos del servidor ni escritura fuera de los proyectos) y un entorno
        sin secretos. No hay Docker/Podman en este equipo: el código puede acceder a la red y leer otros
        archivos del usuario anfitrión.
      </p>
    </div>
  );
}

type Source = "template" | "folder" | "zip";

function CreateProject({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (p: StudioProject) => void;
}) {
  const me = useSession((s) => s.me)!;
  const areas = useApi<{ areas: Area[] }>("/api/areas");
  const templates =
    useApi<{ key: string; name: string; available: boolean; needs: string[] }[]>("/api/studio/templates");
  const [form, setForm] = useState({ name: "", area_id: me.active_area?.id ?? "", template: "vite-vanilla" });
  const [source, setSource] = useState<Source>("template");
  const [picked, setPicked] = useState<PickedFile[]>([]);
  const [zip, setZip] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const folderInput = useRef<HTMLInputElement>(null);
  const usable = picked.filter((f) => !isSkipped(f.path));
  const writable = areas.data?.areas.filter((a) => a.my_role !== "viewer" && a.enabled) ?? [];
  // Sin área activa en la sesión se usa la primera en la que el socio puede escribir.
  const areaId = writable.some((a) => a.id === form.area_id) ? form.area_id : (writable[0]?.id ?? "");

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!areaId) return setError("No tiene ningún área en la que pueda crear proyectos");
    if (source === "folder" && !usable.length) return setError("Elija una carpeta con archivos");
    if (source === "zip" && !zip) return setError("Elija un archivo ZIP");
    setBusy("Creando el proyecto…");
    try {
      const template = source === "template" ? form.template : "empty";
      let project = await post<StudioProject>("/api/studio/projects", { ...form, area_id: areaId, template });
      if (source !== "template") {
        const report =
          source === "zip"
            ? await uploadZipToStudio(project.id, zip!)
            : await uploadToStudio(project.id, picked, {
                onProgress: (done, total) => setBusy(`Subiendo archivos… ${done}/${total}`),
              });
        setBusy("Guardando la versión inicial en Git…");
        if (report.written) {
          await post(`/api/studio/projects/${project.id}/git/commit`, {
            message: `Importar proyecto «${form.name}»`,
          });
        }
        project = await get<StudioProject>(`/api/studio/projects/${project.id}`);
        toast(reportText(report), report.rejected_count ? "info" : "ok");
      }
      onCreated(project);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  }
  return (
    <Modal title="Nuevo proyecto de código" onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        <ErrorBox error={error} />
        <label className="field">
          Nombre
          <input
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            required
            minLength={2}
          />
        </label>
        <label className="field">
          Área
          <select value={areaId} onChange={(e) => setForm({ ...form, area_id: e.target.value })}>
            {writable.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
        </label>
        <fieldset className="stack source-choice" style={{ border: 0, padding: 0, margin: 0 }}>
          <legend className="small" style={{ fontWeight: 600 }}>
            Contenido inicial
          </legend>
          <label className="row small">
            <input type="radio" checked={source === "template"} onChange={() => setSource("template")} /> Una
            plantilla
          </label>
          <label className="row small">
            <input type="radio" checked={source === "folder"} onChange={() => setSource("folder")} /> Importar
            una carpeta de mi equipo
          </label>
          <label className="row small">
            <input type="radio" checked={source === "zip"} onChange={() => setSource("zip")} /> Importar un
            archivo ZIP
          </label>
        </fieldset>
        {source === "template" && (
          <label className="field">
            Plantilla
            <select value={form.template} onChange={(e) => setForm({ ...form, template: e.target.value })}>
              {templates.data?.map((t) => (
                <option key={t.key} value={t.key} disabled={!t.available}>
                  {t.name}
                  {!t.available ? ` (falta ${t.needs.join(", ")})` : ""}
                </option>
              ))}
            </select>
          </label>
        )}
        {source === "folder" && (
          <div className="stack">
            <button type="button" onClick={() => folderInput.current?.click()}>
              Elegir carpeta…
            </button>
            <input
              ref={folderInput}
              type="file"
              hidden
              multiple
              // @ts-expect-error: atributo no estándar admitido por Chrome, Edge y Firefox para elegir carpetas
              webkitdirectory=""
              aria-label="Carpeta del proyecto"
              data-testid="import-folder"
              onChange={(e) => {
                const files = fromInput(e.target.files);
                setPicked(files);
                const root = files[0]?.path.split("/")[0];
                if (root && !form.name) setForm((f) => ({ ...f, name: root }));
              }}
            />
            {picked.length > 0 && (
              <span className="small muted">
                {usable.length} archivo(s) para subir
                {picked.length - usable.length > 0 &&
                  ` · ${picked.length - usable.length} omitidos (node_modules, .git, compilados…)`}
              </span>
            )}
          </div>
        )}
        {source === "zip" && (
          <label className="field">
            Archivo ZIP (máx. 300 MB descomprimido)
            <input
              type="file"
              accept=".zip,application/zip"
              data-testid="import-zip"
              onChange={(e) => {
                const f = e.target.files?.[0] ?? null;
                setZip(f);
                if (f && !form.name) setForm((x) => ({ ...x, name: f.name.replace(/\.zip$/i, "") }));
              }}
            />
          </label>
        )}
        {source !== "template" && (
          <p className="small muted" style={{ margin: 0 }}>
            Se omiten dependencias y metadatos (node_modules, .git, dist…). Después podrá instalar las
            dependencias con el perfil «install». El tipo de proyecto se detecta automáticamente (Vite, Node,
            Python o web estática).
          </p>
        )}
        <button className="primary" disabled={Boolean(busy)}>
          {busy ?? (source === "template" ? "Crear" : "Crear e importar")}
        </button>
      </form>
    </Modal>
  );
}

function UploadDialog({
  projectId,
  onClose,
  onDone,
  initial,
}: {
  projectId: string;
  onClose: () => void;
  onDone: () => void;
  initial: PickedFile[];
}) {
  const [picked, setPicked] = useState<PickedFile[]>(initial);
  const [zip, setZip] = useState<File | null>(null);
  const [target, setTarget] = useState("");
  const [overwrite, setOverwrite] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<StudioUploadReport | null>(null);
  const filesInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);
  const usable = picked.filter((f) => !isSkipped(f.path));
  return (
    <Modal title="Subir archivos al proyecto" onClose={onClose}>
      <div className="stack" data-testid="studio-upload">
        <div className="row wrap">
          <button type="button" onClick={() => filesInput.current?.click()}>
            Elegir archivos…
          </button>
          <button type="button" onClick={() => folderInput.current?.click()}>
            Elegir carpeta…
          </button>
          <label className="btn small">
            ZIP…
            <input
              type="file"
              hidden
              accept=".zip,application/zip"
              onChange={(e) => {
                setZip(e.target.files?.[0] ?? null);
                setPicked([]);
              }}
            />
          </label>
        </div>
        <input
          ref={filesInput}
          type="file"
          hidden
          multiple
          aria-label="Archivos"
          data-testid="upload-files"
          onChange={(e) => {
            setPicked(fromInput(e.target.files));
            setZip(null);
          }}
        />
        <input
          ref={folderInput}
          type="file"
          hidden
          multiple
          // @ts-expect-error: atributo no estándar admitido por Chrome, Edge y Firefox para elegir carpetas
          webkitdirectory=""
          aria-label="Carpeta"
          onChange={(e) => {
            setPicked(fromInput(e.target.files));
            setZip(null);
          }}
        />
        <span className="small muted">
          {zip
            ? `ZIP: ${zip.name}`
            : picked.length
              ? `${usable.length} archivo(s)${picked.length - usable.length ? ` · ${picked.length - usable.length} omitidos` : ""}`
              : "También puede arrastrar archivos o carpetas sobre la lista de archivos."}
        </span>
        <label className="field">
          Carpeta de destino dentro del proyecto (vacío = raíz)
          <input value={target} onChange={(e) => setTarget(e.target.value)} placeholder="src/assets" />
        </label>
        <label className="row small">
          <input type="checkbox" checked={overwrite} onChange={(e) => setOverwrite(e.target.checked)} />{" "}
          Sustituir los archivos que ya existan (la versión anterior queda en el historial de Git si estaba
          confirmada)
        </label>
        <ErrorBox error={error} />
        {result && (
          <div className="alert ok" role="status">
            {reportText(result)}
            {result.rejected.slice(0, 5).map((r) => (
              <div key={r.path} className="small">
                {r.path}: {r.reason}
              </div>
            ))}
          </div>
        )}
        <div className="row">
          <button type="button" className="ghost" onClick={onClose}>
            {result ? "Cerrar" : "Cancelar"}
          </button>
          <button
            type="button"
            className="primary"
            disabled={Boolean(busy) || (!zip && !usable.length)}
            onClick={async () => {
              setBusy("Subiendo…");
              setError(null);
              try {
                const r = zip
                  ? await uploadZipToStudio(projectId, zip, { target, overwrite })
                  : await uploadToStudio(projectId, picked, {
                      target,
                      overwrite,
                      onProgress: (d, t) => setBusy(`Subiendo… ${d}/${t}`),
                    });
                setResult(r);
                onDone();
              } catch (err) {
                setError((err as Error).message);
              } finally {
                setBusy(null);
              }
            }}
          >
            {busy ?? "Subir"}
          </button>
        </div>
      </div>
    </Modal>
  );
}

function Workspace({ id }: { id: string }) {
  const me = useSession((s) => s.me)!;
  const navigate = useNavigate();
  const project = useApi<StudioProject>(`/api/studio/projects/${id}`, {
    refreshOn: ["studio.run", "studio.git"],
    eventFilter: (ev) => ev.project_id === id,
  });
  const tree = useApi<TreeEntry[]>(`/api/studio/projects/${id}/tree`, {
    refreshOn: ["studio.file_changed"],
    eventFilter: (ev) => ev.project_id === id,
  });
  const [tabs, setTabs] = useState<Tab[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [rightTab, setRightTab] = useState<"preview" | "console" | "git">("preview");
  const [conflict, setConflict] = useState<{
    path: string;
    current: string;
    markers?: string;
    hash: string;
  } | null>(null);
  const [confirmArchive, setConfirmArchive] = useState(false);
  const [uploading, setUploading] = useState<PickedFile[] | null>(null);
  const [dropping, setDropping] = useState(false);
  const timers = useRef<Record<string, number>>({});
  const tabsRef = useRef<Tab[]>([]);
  useEffect(() => {
    tabsRef.current = tabs;
  }, [tabs]);
  const isOwner = me.user.role === "admin";

  const openFile = useCallback(
    async (path: string) => {
      setActive(path);
      if (tabsRef.current.some((t) => t.path === path)) return;
      try {
        const f = await get<{ content: string; hash: string }>(
          `/api/studio/projects/${id}/file?path=${encodeURIComponent(path)}`,
        );
        setTabs((ts) => [
          ...ts,
          {
            path,
            content: f.content,
            base: f.content,
            hash: f.hash,
            dirty: false,
            saving: false,
            remoteChanged: false,
          },
        ]);
      } catch (err) {
        toast((err as Error).message, "err");
      }
    },
    [id],
  );

  const save = useCallback(
    async (path: string, force = false) => {
      const tab = tabsRef.current.find((t) => t.path === path);
      if (!tab || tab.saving) return;
      setTabs((ts) => ts.map((t) => (t.path === path ? { ...t, saving: true } : t)));
      try {
        const res = await put<{ hash: string; merged: boolean; content: string | null }>(
          `/api/studio/projects/${id}/file`,
          {
            path,
            content: tab.content,
            base_hash: tab.hash,
            base_content: tab.base,
            force,
          },
        );
        setTabs((ts) =>
          ts.map((t) => {
            if (t.path !== path) return t;
            const content = res.merged && res.content !== null ? res.content : t.content;
            return {
              ...t,
              hash: res.hash,
              base: content,
              content,
              dirty: t.content !== tab.content,
              saving: false,
              remoteChanged: false,
            };
          }),
        );
        if (res.merged) toast(`Se integraron cambios de otro socio en ${path}`, "info");
      } catch (err) {
        setTabs((ts) => ts.map((t) => (t.path === path ? { ...t, saving: false } : t)));
        if (err instanceof ApiError && err.status === 409) {
          const d = err.detail as { current: string; merged_with_markers?: string; hash: string };
          setConflict({ path, current: d.current ?? "", markers: d.merged_with_markers, hash: d.hash });
        } else {
          toast((err as Error).message, "err");
        }
      }
    },
    [id],
  );

  function edit(path: string, content: string) {
    setTabs((ts) => ts.map((t) => (t.path === path ? { ...t, content, dirty: content !== t.base } : t)));
    window.clearTimeout(timers.current[path]);
    timers.current[path] = window.setTimeout(() => void save(path), 1000);
  }

  // Cambios de otros socios en archivos abiertos
  useEffect(() => {
    return realtime.on("studio.file_changed", async (ev) => {
      if (ev.project_id !== id || ev.by === me.user.id) return;
      const path = String(ev.path);
      const tab = tabsRef.current.find((t) => t.path === path);
      if (!tab) return;
      if (tab.dirty) {
        setTabs((ts) => ts.map((t) => (t.path === path ? { ...t, remoteChanged: true } : t)));
        return;
      }
      try {
        const f = await get<{ content: string; hash: string }>(
          `/api/studio/projects/${id}/file?path=${encodeURIComponent(path)}`,
        );
        setTabs((ts) =>
          ts.map((t) => (t.path === path ? { ...t, content: f.content, base: f.content, hash: f.hash } : t)),
        );
      } catch {
        /* el archivo pudo eliminarse */
      }
    });
  }, [id, me.user.id]);

  useEffect(() => () => Object.values(timers.current).forEach((t) => window.clearTimeout(t)), []);

  if (project.error) return <ErrorBox error={project.error} />;
  if (!project.data) return <Spinner />;
  const p = project.data;
  const current = tabs.find((t) => t.path === active);
  return (
    <>
      <div className="page-head">
        <div className="grow">
          <div className="small muted">
            <Link to="/studio">Project Studio</Link> / {p.template_name}
          </div>
          <h1>{p.name}</h1>
        </div>
        <RunButtons project={p} onStarted={() => setRightTab("console")} />
        {isOwner && (
          <button className="ghost small" onClick={() => setConfirmArchive(true)}>
            Archivar
          </button>
        )}
      </div>
      <div className="studio">
        <div className="pane">
          <div className="pane-head">
            <strong className="small">Archivos</strong>
            <button
              className="small right"
              title="Subir archivos, una carpeta o un ZIP"
              onClick={() => setUploading([])}
            >
              Subir
            </button>
            <NewFileButton
              projectId={id}
              onCreated={(path) => {
                tree.reload();
                if (!path.endsWith("/")) void openFile(path);
              }}
            />
          </div>
          <div
            className={`pane-body file-tree${dropping ? " dropping" : ""}`}
            onDragOver={(e) => {
              e.preventDefault();
              setDropping(true);
            }}
            onDragLeave={() => setDropping(false)}
            onDrop={async (e) => {
              e.preventDefault();
              setDropping(false);
              setUploading(await fromDrop(e.dataTransfer));
            }}
          >
            {!tree.data && <Spinner />}
            {tree.data?.map((e) => (
              <div key={e.path} className="row" style={{ flexWrap: "nowrap", gap: 0 }}>
                {e.type === "dir" ? (
                  <span className="small faint mono" style={{ padding: "3px 8px" }}>
                    {"  ".repeat(e.path.split("/").length - 1)}▸ {e.path.split("/").pop()}/
                  </span>
                ) : (
                  <button
                    className={active === e.path ? "active" : ""}
                    onClick={() => openFile(e.path)}
                    title={e.path}
                  >
                    {"  ".repeat(e.path.split("/").length - 1)}
                    {e.path.split("/").pop()}
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
        <div className="pane">
          <div className="editor-tabs" role="tablist">
            {tabs.map((t) => (
              <div
                key={t.path}
                className={`tab${t.path === active ? " active" : ""}`}
                onClick={() => setActive(t.path)}
                role="tab"
                aria-selected={t.path === active}
              >
                {t.path.split("/").pop()}
                {t.dirty && <span title="Cambios sin guardar">●</span>}
                {t.saving && <span className="faint">…</span>}
                {t.remoteChanged && (
                  <span title="Modificado por otro socio" style={{ color: "var(--warn)" }}>
                    ⚠
                  </span>
                )}
                <button
                  className="link small"
                  aria-label={`Cerrar ${t.path}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    if (t.dirty) void save(t.path);
                    setTabs((ts) => ts.filter((x) => x.path !== t.path));
                    if (active === t.path) setActive(tabs.find((x) => x.path !== t.path)?.path ?? null);
                  }}
                >
                  ✕
                </button>
              </div>
            ))}
          </div>
          <div className="pane-body" style={{ overflow: "hidden" }}>
            {current ? (
              <CodeEditor
                value={current.content}
                path={current.path}
                docKey={`${id}:${current.path}`}
                onChange={(v) => edit(current.path, v)}
                onSave={() => save(current.path)}
              />
            ) : (
              <div className="empty" style={{ margin: 16 }}>
                Abra un archivo del explorador.
              </div>
            )}
          </div>
          {current && (
            <div className="pane-head small">
              <span className="mono">{current.path}</span>
              <span className="right faint">
                {current.saving
                  ? "Guardando…"
                  : current.dirty
                    ? "Sin guardar (autoguardado en 1 s)"
                    : "Guardado"}
              </span>
              {canFormat(current.path) && (
                <button
                  className="small"
                  onClick={async () => {
                    try {
                      edit(current.path, await formatCode(current.path, current.content));
                    } catch (err) {
                      const first = (err as Error).message.split(/\r?\n/)[0];
                      toast(`No se pudo formatear: ${first}`, "err");
                    }
                  }}
                >
                  Formatear
                </button>
              )}
              <button className="small" onClick={() => save(current.path)}>
                Guardar
              </button>
              <button
                className="small danger"
                onClick={async () => {
                  await del(`/api/studio/projects/${id}/file?path=${encodeURIComponent(current.path)}`);
                  setTabs((ts) => ts.filter((t) => t.path !== current.path));
                  setActive(null);
                  tree.reload();
                }}
              >
                Eliminar
              </button>
            </div>
          )}
        </div>
        <div className="pane">
          <div className="pane-head">
            <Tabs
              tabs={[
                { key: "preview", label: "Vista previa" },
                { key: "console", label: "Consola" },
                { key: "git", label: "Git" },
              ]}
              value={rightTab}
              onChange={setRightTab}
            />
          </div>
          <div className="pane-body">
            {rightTab === "preview" && <Preview project={p} />}
            {rightTab === "console" && <Console project={p} />}
            {rightTab === "git" && (
              <GitPanel
                projectId={id}
                onRestored={(path) => {
                  setTabs((ts) => ts.filter((t) => t.path !== path));
                  void openFile(path);
                }}
              />
            )}
          </div>
        </div>
      </div>
      {conflict && (
        <Modal title={`Conflicto en ${conflict.path}`} onClose={() => setConflict(null)} wide>
          <p>Otro socio guardó cambios que se solapan con los suyos. Nada se ha sobrescrito.</p>
          <DiffView
            before={conflict.current}
            after={tabs.find((t) => t.path === conflict.path)?.content ?? ""}
          />
          <div className="row" style={{ marginTop: 10 }}>
            <button
              className="primary"
              onClick={() => {
                const path = conflict.path;
                setConflict(null);
                void save(path, true);
              }}
            >
              Guardar mi versión
            </button>
            <button
              onClick={() => {
                setTabs((ts) =>
                  ts.map((t) =>
                    t.path === conflict.path
                      ? {
                          ...t,
                          content: conflict.current,
                          base: conflict.current,
                          hash: conflict.hash,
                          dirty: false,
                        }
                      : t,
                  ),
                );
                setConflict(null);
              }}
            >
              Usar la del servidor
            </button>
            {conflict.markers && (
              <button
                onClick={() => {
                  setTabs((ts) =>
                    ts.map((t) =>
                      t.path === conflict.path
                        ? {
                            ...t,
                            content: conflict.markers!,
                            base: conflict.current,
                            hash: conflict.hash,
                            dirty: true,
                          }
                        : t,
                    ),
                  );
                  setConflict(null);
                }}
              >
                Resolver manualmente
              </button>
            )}
          </div>
        </Modal>
      )}
      {uploading && (
        <UploadDialog
          projectId={id}
          initial={uploading}
          onClose={() => setUploading(null)}
          onDone={() => {
            tree.reload();
            project.reload();
          }}
        />
      )}
      {confirmArchive && (
        <ConfirmDialog
          title="Archivar proyecto de código"
          message="Se detendrán sus procesos y dejará de aparecer en Studio. Los archivos y el historial Git se conservan en disco."
          requireText={p.slug}
          danger
          confirmLabel="Archivar"
          onClose={() => setConfirmArchive(false)}
          onConfirm={async () => {
            await post(`/api/studio/projects/${id}/archive`, { confirm_slug: p.slug });
            navigate("/studio");
          }}
        />
      )}
    </>
  );
}

function NewFileButton({ projectId, onCreated }: { projectId: string; onCreated: (path: string) => void }) {
  return (
    <button
      className="small right"
      title="Nuevo archivo (termine en / para crear una carpeta)"
      onClick={async () => {
        const path = window.prompt("Ruta del nuevo archivo (termine en / para carpeta):", "src/nuevo.js");
        if (!path) return;
        const isDir = path.endsWith("/");
        try {
          await post(`/api/studio/projects/${projectId}/files`, {
            path: isDir ? path.slice(0, -1) : path,
            kind: isDir ? "dir" : "file",
          });
          onCreated(path);
        } catch (err) {
          toast((err as Error).message, "err");
        }
      }}
    >
      ＋
    </button>
  );
}

function RunButtons({ project, onStarted }: { project: StudioProject; onStarted: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const profiles = project.profiles.filter((x) => x !== "console");
  return (
    <div className="row">
      {profiles.map((profile) => (
        <button
          key={profile}
          className={profile === "dev" ? "primary" : ""}
          disabled={busy !== null}
          onClick={async () => {
            setBusy(profile);
            try {
              await post(`/api/studio/projects/${project.id}/runs`, { profile });
              onStarted();
            } catch (err) {
              toast((err as Error).message, "err");
            } finally {
              setBusy(null);
            }
          }}
        >
          {busy === profile
            ? "Iniciando…"
            : profile === "dev" && project.preview.dev_running
              ? "Reiniciar dev"
              : (PROFILE_LABEL[profile] ?? profile)}
        </button>
      ))}
      {project.preview.dev_running && project.preview.dev_run_id && (
        <button
          className="danger"
          onClick={() => post(`/api/studio/projects/${project.id}/runs/${project.preview.dev_run_id}/stop`)}
        >
          Detener dev
        </button>
      )}
    </div>
  );
}

function Preview({ project }: { project: StudioProject }) {
  const [nonce, setNonce] = useState(0);
  const status =
    project.template === "static"
      ? "Servida por WADIMA (recarga al guardar)"
      : project.preview.dev_ready
        ? "Servidor de desarrollo listo (recarga en caliente)"
        : project.preview.dev_running
          ? "Arrancando el servidor de desarrollo…"
          : "Servidor detenido: pulse «Servidor dev»";
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div className="row small" style={{ padding: 6 }}>
        <Badge
          tone={
            project.preview.available
              ? project.preview.dev_ready || project.template === "static"
                ? "ok"
                : "warn"
              : "muted"
          }
        >
          {project.preview.available ? "disponible" : "detenida"}
        </Badge>
        <span className="muted">{status}</span>
        <button className="small right" onClick={() => setNonce((n) => n + 1)}>
          Recargar
        </button>
        <a className="btn small" href={project.preview_url} target="_blank" rel="noreferrer">
          Abrir
        </a>
      </div>
      {project.preview.available && (project.preview.dev_ready || project.template === "static") ? (
        <iframe
          key={nonce}
          title="Vista previa en vivo"
          className="preview-frame"
          src={project.preview_url}
          sandbox="allow-scripts allow-same-origin allow-forms allow-modals allow-popups"
          data-testid="preview-frame"
        />
      ) : (
        <div className="empty" style={{ margin: 12 }}>
          La vista previa se publica en un origen separado y solo pueden verla los miembros del área.
        </div>
      )}
    </div>
  );
}

function Console({ project }: { project: StudioProject }) {
  const runs = useApi<RunInfo[]>(`/api/studio/projects/${project.id}/runs`, {
    refreshOn: ["studio.run"],
    eventFilter: (ev) => ev.project_id === project.id,
  });
  const [selected, setSelected] = useState<string | null>(null);
  const runId = selected ?? runs.data?.[0]?.id ?? null;
  const [lines, setLines] = useState<string[]>([]);
  const [command, setCommand] = useState("");
  const box = useRef<HTMLDivElement>(null);
  const run = runs.data?.find((r) => r.id === runId);

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    get<RunInfo>(`/api/studio/projects/${project.id}/runs/${runId}`).then((r) => {
      if (!cancelled) setLines(r.log ?? []);
    });
    const off = realtime.on("studio.log", (ev) => {
      if (ev.run_id === runId) setLines((ls) => [...ls, ...(ev.lines as string[])].slice(-3000));
    });
    return () => {
      cancelled = true;
      off();
    };
  }, [runId, project.id]);

  useEffect(() => {
    box.current?.scrollTo(0, box.current.scrollHeight);
  }, [lines]);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div className="row small" style={{ padding: 6 }}>
        <select
          value={runId ?? ""}
          onChange={(e) => setSelected(e.target.value)}
          style={{ flex: 1 }}
          aria-label="Ejecución"
        >
          {runs.data?.map((r) => (
            <option key={r.id} value={r.id}>
              {PROFILE_LABEL[r.profile] ?? r.profile} · {r.status} · {formatDateTime(r.started_at)}
            </option>
          ))}
        </select>
        {run && (
          <Badge
            tone={
              run.status === "succeeded"
                ? "ok"
                : run.status === "running"
                  ? "info"
                  : run.status === "stopped"
                    ? "muted"
                    : "err"
            }
          >
            {run.status}
          </Badge>
        )}
        {run?.status === "running" && (
          <button
            className="small danger"
            onClick={() => post(`/api/studio/projects/${project.id}/runs/${run.id}/stop`)}
          >
            Detener
          </button>
        )}
      </div>
      <div className="console" ref={box} data-testid="console-output">
        {lines.length ? lines.join("\n") : "Sin salida todavía."}
      </div>
      <form
        className="row"
        style={{ padding: 6 }}
        onSubmit={async (e) => {
          e.preventDefault();
          try {
            const r = await post<RunInfo>(`/api/studio/projects/${project.id}/runs`, {
              profile: "console",
              command,
            });
            setSelected(r.id);
            setCommand("");
            runs.reload();
          } catch (err) {
            toast((err as Error).message, "err");
          }
        }}
      >
        <input
          value={command}
          onChange={(e) => setCommand(e.target.value)}
          placeholder="node -v · python main.py · git status"
          className="mono"
          style={{ flex: 1 }}
          aria-label="Comando"
        />
        <button className="small">Ejecutar</button>
      </form>
    </div>
  );
}

function GitPanel({ projectId, onRestored }: { projectId: string; onRestored: (path: string) => void }) {
  const status = useApi<{
    branch: string;
    changes: { path: string; index: string; worktree: string }[];
    branches: string[];
  }>(`/api/studio/projects/${projectId}/git/status`, {
    refreshOn: ["studio.file_changed", "studio.git"],
    eventFilter: (ev) => ev.project_id === projectId,
  });
  const log = useApi<{ hash: string; author: string; date: string; message: string }[]>(
    `/api/studio/projects/${projectId}/git/log`,
    {
      refreshOn: ["studio.git"],
      eventFilter: (ev) => ev.project_id === projectId,
    },
  );
  const [message, setMessage] = useState("");
  const [diff, setDiff] = useState<{ title: string; text: string } | null>(null);
  const [branch, setBranch] = useState("");
  const changesText = useMemo(
    () => status.data?.changes.map((c) => `${c.index}${c.worktree} ${c.path}`).join("\n"),
    [status.data],
  );
  return (
    <div className="stack" style={{ padding: 8 }}>
      <div className="row small">
        <span>Rama:</span>
        <select
          value={status.data?.branch ?? ""}
          style={{ width: 160 }}
          aria-label="Rama"
          onChange={async (e) => {
            try {
              await post(`/api/studio/projects/${projectId}/git/checkout`, { name: e.target.value });
              toast(`Rama ${e.target.value}`, "ok");
            } catch (err) {
              toast((err as Error).message, "err");
            }
            status.reload();
          }}
        >
          {status.data?.branches.map((b) => (
            <option key={b} value={b}>
              {b}
            </option>
          ))}
        </select>
        <input
          value={branch}
          onChange={(e) => setBranch(e.target.value)}
          placeholder="nueva-rama"
          style={{ width: 130 }}
          aria-label="Nueva rama"
        />
        <button
          className="small"
          disabled={!branch}
          onClick={async () => {
            try {
              await post(`/api/studio/projects/${projectId}/git/branches`, { name: branch });
              setBranch("");
              status.reload();
            } catch (err) {
              toast((err as Error).message, "err");
            }
          }}
        >
          Crear
        </button>
      </div>
      <div>
        <strong className="small">Cambios sin confirmar</strong>
        <div className="code-box" style={{ maxHeight: 120 }}>
          {changesText || "Sin cambios."}
        </div>
        {status.data && status.data.changes.length > 0 && (
          <button
            className="small"
            style={{ marginTop: 4 }}
            onClick={async () => {
              const r = await get<{ diff: string }>(`/api/studio/projects/${projectId}/git/diff`);
              setDiff({ title: "Cambios sin confirmar", text: r.diff });
            }}
          >
            Ver diferencias
          </button>
        )}
      </div>
      <form
        className="row"
        onSubmit={async (e) => {
          e.preventDefault();
          try {
            await post(`/api/studio/projects/${projectId}/git/commit`, { message });
            setMessage("");
            toast("Commit creado", "ok");
            status.reload();
            log.reload();
          } catch (err) {
            toast((err as Error).message, "err");
          }
        }}
      >
        <input
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          placeholder="Mensaje del commit"
          required
          minLength={3}
          style={{ flex: 1 }}
          aria-label="Mensaje del commit"
        />
        <button className="small primary">Commit</button>
      </form>
      <strong className="small">Historial</strong>
      <ul className="list">
        {log.data?.map((c, i) => (
          <li key={c.hash} className="small">
            <div className="row">
              <span className="mono faint">{c.hash.slice(0, 7)}</span>
              <span style={{ flex: 1 }}>{c.message}</span>
            </div>
            <div className="faint">
              {c.author} · {formatDateTime(c.date)}
              {log.data && i < log.data.length - 1 && (
                <button
                  className="link small"
                  style={{ marginLeft: 8 }}
                  onClick={async () => {
                    const r = await get<{ diff: string }>(
                      `/api/studio/projects/${projectId}/git/diff?ref_from=${log.data![i + 1].hash}&ref_to=${c.hash}`,
                    );
                    setDiff({ title: `${c.hash.slice(0, 7)} · ${c.message}`, text: r.diff });
                  }}
                >
                  diff
                </button>
              )}
              <button
                className="link small"
                style={{ marginLeft: 8 }}
                onClick={async () => {
                  const path = window.prompt("Archivo a restaurar desde este commit:");
                  if (!path) return;
                  try {
                    await post(`/api/studio/projects/${projectId}/git/restore`, { ref: c.hash, path });
                    toast(`${path} restaurado (sin confirmar)`, "ok");
                    onRestored(path);
                  } catch (err) {
                    toast((err as Error).message, "err");
                  }
                }}
              >
                restaurar archivo
              </button>
            </div>
          </li>
        ))}
      </ul>
      {diff && (
        <Modal title={diff.title} onClose={() => setDiff(null)} wide>
          <div className="diff">
            {diff.text.split("\n").map((l, i) => (
              <div
                key={i}
                className={
                  l.startsWith("+") && !l.startsWith("+++")
                    ? "add"
                    : l.startsWith("-") && !l.startsWith("---")
                      ? "del"
                      : undefined
                }
              >
                {l || " "}
              </div>
            ))}
          </div>
        </Modal>
      )}
    </div>
  );
}
