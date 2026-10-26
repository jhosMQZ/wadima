import { useRef, useState, type DragEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import type { Area } from "../api/types";
import { Badge, EmptyState, ErrorBox, Spinner } from "../components/ui";
import { timeAgo } from "../lib/format";
import { toast } from "../lib/toast";
import { fromDrop, fromInput, isSkipped, type PickedFile } from "../lib/upload";
import { useApi } from "../lib/useApi";
import { DOC_STATUS, locatorText } from "../lib/labels";
import { useSession } from "../store/session";
import type { ProjectRow } from "./Projects";

export interface DocRow {
  id: string;
  area_id: string;
  project_id: string | null;
  title: string;
  filename: string;
  ext: string;
  category: string;
  tags: string[];
  status: string;
  error: string;
  current_version: number;
  updated_at: string;
  drive_state?: string | null;
}

interface SearchHit {
  chunk_id: string;
  document_id: string;
  title: string;
  heading: string;
  locator: Record<string, unknown>;
  snippet: string;
  version: number;
}

const ACCEPT =
  ".pdf,.docx,.xlsx,.xlsm,.xls,.pptx,.csv,.tsv,.txt,.md,.json,.yaml,.yml,.xml,.py,.js,.ts,.tsx,.jsx,.java,.cs,.php,.go,.c,.h,.cpp,.rs,.sql,.html,.css,.sh,.ps1";

const ACCEPTED = new Set(ACCEPT.split(","));

function extension(path: string): string {
  const name = path.split("/").at(-1) ?? "";
  return name.includes(".") ? `.${name.split(".").at(-1)!.toLowerCase()}` : "";
}

/** Etiquetas a partir de las carpetas de origen (p. ej. «contratos/2026/acta.pdf» → contratos, 2026). */
function folderTags(path: string): string {
  return path
    .split("/")
    .slice(0, -1)
    .slice(-3)
    .map((t) => t.toLowerCase().slice(0, 40))
    .join(",");
}

interface UploadState {
  id: number;
  name: string;
  progress: number;
  status: "uploading" | "done" | "error" | "duplicate";
  message?: string;
}

function uploadFile(
  form: FormData,
  onProgress: (p: number) => void,
  csrf: string,
): Promise<{ status: number; body: unknown }> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/library/upload");
    xhr.setRequestHeader("X-CSRF-Token", csrf);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(Math.round((e.loaded / e.total) * 100));
    xhr.onload = () => {
      let body: unknown;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        body = xhr.responseText;
      }
      resolve({ status: xhr.status, body });
    };
    xhr.onerror = () => reject(new Error("Error de red durante la subida"));
    xhr.send(form);
  });
}

function detailText(body: unknown): string {
  const d = (body as { detail?: unknown })?.detail;
  if (typeof d === "string") return d;
  if (d && typeof d === "object" && "message" in d) return String((d as { message: string }).message);
  return "Error al subir";
}

export default function LibraryPage() {
  const me = useSession((s) => s.me)!;
  const [params] = useSearchParams();
  const [areaId, setAreaId] = useState(params.get("area") ?? me.active_area?.id ?? "");
  const [projectId, setProjectId] = useState(params.get("project") ?? "");
  const [status, setStatus] = useState("");
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [uploads, setUploads] = useState<UploadState[]>([]);
  const [dragging, setDragging] = useState(false);
  const [batch, setBatch] = useState<{
    done: number;
    total: number;
    ok: number;
    skipped: number;
    failed: number;
  } | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);
  const areas = useApi<{ areas: Area[] }>("/api/areas");
  const projects = useApi<ProjectRow[]>(areaId ? `/api/projects?area_id=${areaId}` : null);
  const qs = new URLSearchParams();
  if (areaId) qs.set("area_id", areaId);
  if (projectId) qs.set("project_id", projectId);
  if (status) qs.set("status", status);
  if (query) qs.set("q", query);
  const docs = useApi<DocRow[]>(`/api/library/documents?${qs}`, {
    refreshOn: ["document.changed", "sync.changed"],
  });
  const hits = useApi<SearchHit[]>(
    search
      ? `/api/library/search?q=${encodeURIComponent(search)}${areaId ? `&area_id=${areaId}` : ""}`
      : null,
  );
  const writableAreas = areas.data?.areas.filter((a) => a.my_role !== "viewer") ?? [];
  const canUpload = writableAreas.some((a) => a.id === areaId);

  async function handleFiles(picked: PickedFile[]) {
    if (!areaId || !canUpload) {
      toast("Seleccione un área en la que pueda escribir", "err");
      return;
    }
    // De una carpeta solo se suben los formatos admitidos; el resto se cuenta como omitido.
    const wanted = picked.filter((f) => !isSkipped(f.path) && ACCEPTED.has(extension(f.path)));
    const stats = { done: 0, total: wanted.length, ok: 0, skipped: picked.length - wanted.length, failed: 0 };
    if (picked.length > 1) setBatch({ ...stats });
    for (const { file, path } of wanted) {
      const id = Date.now() + Math.random();
      setUploads((u) => [{ id, name: path, progress: 0, status: "uploading" as const }, ...u].slice(0, 10));
      const update = (patch: Partial<UploadState>) =>
        setUploads((u) => u.map((x) => (x.id === id ? { ...x, ...patch } : x)));
      const form = new FormData();
      form.append("file", file);
      form.append("area_id", areaId);
      if (projectId) form.append("project_id", projectId);
      const tags = folderTags(path);
      if (tags) form.append("tags", tags);
      try {
        const res = await uploadFile(form, (p) => update({ progress: p }), me.csrf_token);
        if (res.status === 200) {
          update({ status: "done", progress: 100 });
          stats.ok += 1;
        } else if (res.status === 409) {
          update({ status: "duplicate", message: detailText(res.body) });
          stats.skipped += 1;
        } else {
          update({ status: "error", message: detailText(res.body) });
          stats.failed += 1;
        }
      } catch (err) {
        update({ status: "error", message: (err as Error).message });
        stats.failed += 1;
      }
      stats.done += 1;
      if (picked.length > 1) setBatch({ ...stats });
    }
    if (picked.length > 1)
      toast(
        `${stats.ok} documento(s) subidos · ${stats.skipped} omitidos (formato no admitido o duplicado) · ${stats.failed} con error`,
        stats.failed ? "err" : "ok",
      );
    docs.reload();
  }

  async function onDrop(e: DragEvent) {
    e.preventDefault();
    setDragging(false);
    void handleFiles(await fromDrop(e.dataTransfer));
  }

  return (
    <>
      <div className="page-head">
        <div className="grow">
          <h1>Biblioteca documental</h1>
          <p>Originales intactos en Drive, conversión a Markdown estructurado, búsqueda y versiones.</p>
        </div>
      </div>
      <div className="grid cols-2">
        <div
          className="panel"
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          style={{
            borderStyle: dragging ? "dashed" : undefined,
            borderColor: dragging ? "var(--accent)" : undefined,
          }}
        >
          <h3>Subir documentos</h3>
          <div className="row">
            <select
              value={areaId}
              onChange={(e) => {
                setAreaId(e.target.value);
                setProjectId("");
              }}
              aria-label="Área"
              style={{ flex: 1 }}
            >
              <option value="">Área…</option>
              {areas.data?.areas.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                  {a.my_role === "viewer" ? " (solo lectura)" : ""}
                </option>
              ))}
            </select>
            <select
              value={projectId}
              onChange={(e) => setProjectId(e.target.value)}
              aria-label="Proyecto"
              style={{ flex: 1 }}
            >
              <option value="">Sin proyecto</option>
              {projects.data?.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </div>
          <div className="empty" style={{ marginTop: 10 }}>
            <div className="pixel">Arrastre archivos o carpetas aquí</div>
            <div className="small">
              PDF, Word, Excel, PowerPoint, CSV, texto, Markdown, JSON, YAML, XML y código
            </div>
            <div className="row" style={{ marginTop: 8, justifyContent: "center" }}>
              <button disabled={!canUpload} onClick={() => fileInput.current?.click()}>
                Elegir archivos
              </button>
              <button disabled={!canUpload} onClick={() => folderInput.current?.click()}>
                Elegir carpeta
              </button>
            </div>
            <input
              ref={folderInput}
              type="file"
              multiple
              hidden
              // @ts-expect-error: atributo no estándar admitido por Chrome, Edge y Firefox para elegir carpetas
              webkitdirectory=""
              aria-label="Carpeta a subir"
              onChange={(e) => void handleFiles(fromInput(e.target.files))}
              data-testid="folder-input"
            />
            <input
              ref={fileInput}
              type="file"
              multiple
              accept={ACCEPT}
              hidden
              aria-label="Archivos a subir"
              onChange={(e) => void handleFiles(fromInput(e.target.files))}
              data-testid="file-input"
            />
          </div>
          {batch && (
            <div className="small" role="status" style={{ marginTop: 8 }} data-testid="batch-status">
              {batch.done < batch.total
                ? `Subiendo ${batch.done + 1} de ${batch.total}…`
                : `Terminado: ${batch.total} procesados`}{" "}
              · {batch.ok} subidos · {batch.skipped} omitidos · {batch.failed} con error
            </div>
          )}
          {uploads.length > 0 && (
            <ul className="list" style={{ marginTop: 8 }}>
              {uploads.map((u) => (
                <li key={u.id} className="small">
                  <div className="row">
                    <strong style={{ flex: 1 }}>{u.name}</strong>
                    <Badge
                      tone={
                        u.status === "done"
                          ? "ok"
                          : u.status === "uploading"
                            ? "info"
                            : u.status === "duplicate"
                              ? "warn"
                              : "err"
                      }
                    >
                      {u.status === "uploading"
                        ? `${u.progress}%`
                        : u.status === "done"
                          ? "subido"
                          : u.status === "duplicate"
                            ? "duplicado"
                            : "error"}
                    </Badge>
                  </div>
                  {u.message && <div className="muted">{u.message}</div>}
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="panel">
          <h3>Buscar en el contenido</h3>
          <form
            className="row"
            onSubmit={(e) => {
              e.preventDefault();
              setSearch(query);
            }}
          >
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Palabras, frases entre comillas, -excluir"
              aria-label="Buscar en documentos"
              style={{ flex: 1 }}
            />
            <button className="primary">Buscar</button>
          </form>
          {search && !hits.data && <Spinner />}
          {hits.data?.length === 0 && <p className="muted">Sin resultados para «{search}».</p>}
          <ul className="list">
            {hits.data?.map((h) => (
              <li key={h.chunk_id}>
                <Link to={`/library/${h.document_id}?chunk=${h.chunk_id}`}>{h.title}</Link>{" "}
                <span className="small faint">
                  {h.heading && `· ${h.heading}`} {locatorText(h.locator)}
                </span>
                <div className="small" dangerouslySetInnerHTML={{ __html: highlight(h.snippet) }} />
              </li>
            ))}
          </ul>
        </div>
      </div>
      <div className="panel">
        <div className="panel-title">
          <h3>Documentos</h3>
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            style={{ width: 180 }}
            aria-label="Estado"
          >
            <option value="">Todos los estados</option>
            {Object.entries(DOC_STATUS).map(([k, [label]]) => (
              <option key={k} value={k}>
                {label}
              </option>
            ))}
          </select>
        </div>
        <ErrorBox error={docs.error} onRetry={() => docs.reload()} />
        {!docs.data && <Spinner />}
        {docs.data?.length === 0 && <EmptyState title="No hay documentos" />}
        <div className="table-wrap" hidden={!docs.data?.length}>
          <table className="table">
            <thead>
              <tr>
                <th>Título</th>
                <th>Tipo</th>
                <th>Etiquetas</th>
                <th>Estado</th>
                <th>Drive</th>
                <th>Actualizado</th>
              </tr>
            </thead>
            <tbody>
              {docs.data?.map((d) => {
                const [label, tone] = DOC_STATUS[d.status] ?? [d.status, "info"];
                return (
                  <tr key={d.id}>
                    <td>
                      <Link to={`/library/${d.id}`}>{d.title}</Link>
                      {d.current_version > 1 && <span className="faint small"> · v{d.current_version}</span>}
                      {d.error && (
                        <div className="small" style={{ color: "var(--err)" }}>
                          {d.error}
                        </div>
                      )}
                    </td>
                    <td>
                      <Badge tone="muted">{d.ext}</Badge>
                    </td>
                    <td className="small">{d.tags.join(", ")}</td>
                    <td>
                      <Badge tone={tone}>{label}</Badge>
                    </td>
                    <td className="small">{d.drive_state ?? "—"}</td>
                    <td className="small">{timeAgo(d.updated_at)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}

function highlight(snippet: string): string {
  const esc = snippet.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return esc.replace(/&lt;&lt;/g, "<mark>").replace(/&gt;&gt;/g, "</mark>");
}
