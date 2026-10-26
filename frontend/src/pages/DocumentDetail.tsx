import { useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { del, patch, post } from "../api/client";
import { Comments } from "../components/Collab";
import { DiffView } from "../components/DiffView";
import { Markdown } from "../components/Markdown";
import { Badge, ConfirmDialog, ErrorBox, Spinner, Tabs } from "../components/ui";
import { formatBytes, formatDateTime } from "../lib/format";
import { DOC_STATUS, locatorText } from "../lib/labels";
import { toast } from "../lib/toast";
import { useApi } from "../lib/useApi";
import { useSession } from "../store/session";

interface Version {
  id: string;
  version: number;
  sha256: string;
  size: number;
  status: string;
  error: string;
  report: Report;
  suspicious: boolean;
  created_at: string;
  processed_at: string | null;
  author: string | null;
}

interface Report {
  chars_original?: number;
  chars_markdown?: number;
  words?: number;
  pages?: number | null;
  tables?: number;
  chunks?: number;
  warnings?: string[];
  issues?: string[];
  noise_removed?: string[];
  needs_ocr?: boolean;
  ocr_available?: boolean;
  token_estimate_original?: number;
  token_estimate_markdown?: number;
  token_estimate_method?: string;
  suspicious_instructions?: string[];
  meta?: Record<string, unknown>;
  token_measurement?: { model: string; original: number; markdown: number; measured_at: string };
}

interface DocDetail {
  id: string;
  area_id: string;
  project_id: string | null;
  title: string;
  filename: string;
  ext: string;
  mime: string;
  category: string;
  tags: string[];
  status: string;
  error: string;
  current_version: number;
  created_at: string;
  versions: Version[];
  summaries: Record<string, string>;
  drive: Record<string, { state: string; drive_file_id: string | null; error: string }>;
  uploaded_by_name: string | null;
}

interface VersionContent {
  markdown: string | null;
  plain_text: string | null;
  tables: {
    name: string;
    headers: string[];
    rows: string[][];
    total_rows: number;
    formulas?: { cell: string; formula: string; value: string }[];
    units?: Record<string, string>;
  }[];
  report: Report;
  status: string;
  error: string;
  suspicious: boolean;
}

interface ChunkRow {
  id: string;
  ord: number;
  heading: string;
  locator: Record<string, unknown>;
  content: string;
  token_estimate: number;
  suspicious: boolean;
}

type Tab =
  "preview" | "markdown" | "diff" | "tables" | "chunks" | "summary" | "report" | "versions" | "comments";

export default function DocumentDetailPage() {
  const { id } = useParams() as { id: string };
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const me = useSession((s) => s.me)!;
  const {
    data: doc,
    error,
    reload,
  } = useApi<DocDetail>(`/api/library/documents/${id}`, {
    refreshOn: ["document.changed", "sync.changed"],
    eventFilter: (ev) => !ev.id || ev.id === id || ev.type === "sync.changed",
  });
  const [version, setVersion] = useState<number | null>(null);
  const [tab, setTab] = useState<Tab>(params.get("chunk") ? "chunks" : "preview");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const v = version ?? doc?.current_version ?? 1;
  const vc = useApi<VersionContent>(doc ? `/api/library/documents/${id}/versions/${v}/content` : null, {
    refreshOn: ["document.changed"],
    eventFilter: (ev) => ev.id === id,
  });
  if (error) return <ErrorBox error={error} onRetry={() => reload()} />;
  if (!doc) return <Spinner />;
  const [label, tone] = DOC_STATUS[doc.status] ?? [doc.status, "info"];
  const current = doc.versions.find((x) => x.version === v);
  const base = `/api/library/documents/${id}/versions/${v}`;
  return (
    <>
      <div className="page-head">
        <div className="grow">
          <div className="small muted">
            <Link to="/library">Biblioteca</Link> / {doc.filename}
          </div>
          <h1>
            {doc.title} <Badge tone={tone}>{label}</Badge>
          </h1>
          <p>
            {doc.ext.toUpperCase()} · versión {v} de {doc.current_version} · subido por{" "}
            {doc.uploaded_by_name ?? "—"} el {formatDateTime(doc.created_at)}
            {doc.project_id && (
              <>
                {" "}
                · <Link to={`/projects/${doc.project_id}`}>proyecto</Link>
              </>
            )}
          </p>
        </div>
        <select
          value={v}
          onChange={(e) => setVersion(Number(e.target.value))}
          style={{ width: 130 }}
          aria-label="Versión"
        >
          {doc.versions.map((x) => (
            <option key={x.version} value={x.version}>
              Versión {x.version}
            </option>
          ))}
        </select>
        <a className="btn" href={`${base}/original`}>
          Descargar original
        </a>
        <button
          onClick={async () => {
            await post(`/api/library/documents/${id}/reprocess`);
            toast("Documento enviado de nuevo al conversor", "ok");
            reload();
          }}
        >
          Reprocesar
        </button>
        <button className="danger" onClick={() => setConfirmDelete(true)}>
          Eliminar
        </button>
      </div>
      {doc.error && (
        <div className="alert err">Error de procesamiento: {doc.error}. El original sigue disponible.</div>
      )}
      {current?.suspicious && (
        <div className="alert warn">
          El documento contiene texto con aspecto de instrucciones para una IA (
          {(current.report.suspicious_instructions ?? []).slice(0, 3).join("; ")}). Se trata como dato no
          confiable: nunca se ejecuta y se marca al entregarlo como contexto.
        </div>
      )}
      <DriveBadges drive={doc.drive} />
      <Tabs<Tab>
        tabs={[
          { key: "preview", label: "Original" },
          { key: "markdown", label: "Markdown" },
          { key: "diff", label: "Diferencias" },
          { key: "tables", label: "Tablas" },
          { key: "chunks", label: "Fragmentos" },
          { key: "summary", label: "Resumen" },
          { key: "report", label: "Informe de extracción" },
          { key: "versions", label: "Versiones" },
          { key: "comments", label: "Comentarios" },
        ]}
        value={tab}
        onChange={setTab}
      />
      {!vc.data && tab !== "versions" && tab !== "comments" && tab !== "summary" && <Spinner />}
      {tab === "preview" && vc.data && <Preview doc={doc} base={base} content={vc.data} />}
      {tab === "markdown" && vc.data && <MarkdownTab markdown={vc.data.markdown} status={vc.data.status} />}
      {tab === "diff" && vc.data && (
        <div className="panel">
          <p className="small muted">
            Texto extraído del original (izquierda) frente al Markdown generado (derecha). Las diferencias
            incluyen el formato añadido (encabezados, tablas) y el ruido eliminado.
          </p>
          <DiffView before={vc.data.plain_text ?? ""} after={vc.data.markdown ?? ""} />
        </div>
      )}
      {tab === "tables" && vc.data && <TablesTab tables={vc.data.tables} />}
      {tab === "chunks" && <ChunksTab base={base} focus={params.get("chunk")} />}
      {tab === "summary" && (
        <div className="grid cols-2">
          <div className="panel">
            <h3>Resumen breve</h3>
            <p>{doc.summaries.short || <span className="muted">Aún no disponible.</span>}</p>
            <h3>Palabras clave</h3>
            <p className="small">{doc.summaries.keywords || "—"}</p>
            <p className="small faint">
              Resumen extractivo (frases del propio documento), sin IA generativa.
            </p>
          </div>
          <div className="panel">
            <h3>Por secciones</h3>
            <Markdown source={doc.summaries.sections || "_Sin secciones detectadas._"} />
            <h3>Esquema</h3>
            <Markdown source={doc.summaries.outline || "_Sin encabezados._"} />
          </div>
        </div>
      )}
      {tab === "report" && vc.data && (
        <ReportTab report={vc.data.report} versionId={current?.id} onMeasured={() => vc.reload()} />
      )}
      {tab === "versions" && <VersionsTab doc={doc} onUploaded={() => reload()} csrf={me.csrf_token} />}
      {tab === "comments" && (
        <div className="panel">
          <Comments targetType="document" targetId={doc.id} />
        </div>
      )}
      <MetaEditor doc={doc} onSaved={() => reload()} />
      {confirmDelete && (
        <ConfirmDialog
          title="Eliminar documento"
          message="El documento deja de aparecer en la biblioteca y en las búsquedas. Los originales se conservan en el almacén local y en Drive, y el Memory Agent revisará los recuerdos que dependían de él."
          danger
          confirmLabel="Eliminar"
          onClose={() => setConfirmDelete(false)}
          onConfirm={async () => {
            await del(`/api/library/documents/${id}`);
            navigate("/library");
          }}
        />
      )}
    </>
  );
}

function DriveBadges({ drive }: { drive: DocDetail["drive"] }) {
  const entries = Object.entries(drive);
  if (!entries.length) return null;
  const name: Record<string, string> = { document_original: "Original", document_markdown: "Markdown" };
  return (
    <div className="row small" style={{ marginBottom: 10 }}>
      <span className="muted">Google Drive:</span>
      {entries.map(([k, d]) => (
        <span key={k} className="row" title={d.error}>
          {name[k]}{" "}
          <Badge
            tone={
              d.state === "synced"
                ? "ok"
                : d.state === "conflict"
                  ? "conflict"
                  : d.state === "error"
                    ? "err"
                    : "warn"
            }
          >
            {d.state}
          </Badge>
          {d.drive_file_id && (
            <a
              href={`https://drive.google.com/file/d/${d.drive_file_id}/view`}
              target="_blank"
              rel="noreferrer"
            >
              abrir
            </a>
          )}
        </span>
      ))}
    </div>
  );
}

function Preview({ doc, base, content }: { doc: DocDetail; base: string; content: VersionContent }) {
  if (doc.ext === "pdf") {
    return (
      <div className="panel" style={{ padding: 0, height: "75vh" }}>
        <iframe
          title="Vista previa del PDF"
          src={`${base}/original?inline=true`}
          style={{ width: "100%", height: "100%", border: 0 }}
        />
      </div>
    );
  }
  if (
    ["txt", "md", "markdown", "json", "yaml", "yml", "xml", "csv", "tsv"].includes(doc.ext) ||
    content.plain_text
  ) {
    return (
      <div className="panel">
        <p className="small muted">
          Vista del texto original. Los formatos Office se muestran como el texto extraído; descargue el
          original para verlo con su aplicación.
        </p>
        <div className="code-box" style={{ maxHeight: "70vh" }}>
          {content.plain_text ?? ""}
        </div>
      </div>
    );
  }
  return <div className="panel muted">Vista previa no disponible; descargue el original.</div>;
}

function MarkdownTab({ markdown, status }: { markdown: string | null; status: string }) {
  const [raw, setRaw] = useState(false);
  if (!markdown) return <div className="panel muted">Aún no hay Markdown (estado: {status}).</div>;
  return (
    <div className="panel">
      <div className="row" style={{ marginBottom: 8 }}>
        <button className="small" onClick={() => setRaw((r) => !r)}>
          {raw ? "Ver renderizado" : "Ver fuente"}
        </button>
        <button className="small" onClick={() => navigator.clipboard?.writeText(markdown)}>
          Copiar Markdown
        </button>
        <span className="small faint right">{markdown.length.toLocaleString()} caracteres</span>
      </div>
      {raw ? (
        <div className="code-box" style={{ maxHeight: "70vh" }}>
          {markdown}
        </div>
      ) : (
        <Markdown source={markdown} />
      )}
    </div>
  );
}

function TablesTab({ tables }: { tables: VersionContent["tables"] }) {
  const [idx, setIdx] = useState(0);
  if (!tables.length) return <div className="panel muted">No se detectaron tablas.</div>;
  const t = tables[Math.min(idx, tables.length - 1)];
  return (
    <div className="panel">
      <div className="row" style={{ marginBottom: 8 }}>
        <select
          value={idx}
          onChange={(e) => setIdx(Number(e.target.value))}
          style={{ width: 260 }}
          aria-label="Tabla"
        >
          {tables.map((tb, i) => (
            <option key={i} value={i}>
              {tb.name} ({tb.total_rows} filas)
            </option>
          ))}
        </select>
        {t.units && Object.keys(t.units).length > 0 && (
          <span className="small muted">
            Unidades:{" "}
            {Object.entries(t.units)
              .map(([k, u]) => `${k}: ${u}`)
              .join(", ")}
          </span>
        )}
      </div>
      <div className="table-wrap" style={{ maxHeight: "60vh", overflow: "auto" }}>
        <table className="table">
          <thead>
            <tr>
              {t.headers.map((h, i) => (
                <th key={i}>{h || `Col${i + 1}`}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {t.rows.slice(0, 1000).map((r, i) => (
              <tr key={i}>
                {r.map((c, j) => (
                  <td key={j} className="small">
                    {c}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {t.rows.length > 1000 && <p className="small muted">Se muestran 1000 de {t.total_rows} filas.</p>}
      {t.formulas && t.formulas.length > 0 && (
        <>
          <h3 style={{ marginTop: 12 }}>Fórmulas ({t.formulas.length})</h3>
          <div className="code-box">
            {t.formulas
              .slice(0, 300)
              .map((f) => `${f.cell}: ${f.formula} → ${f.value || "(sin valor calculado)"}`)
              .join("\n")}
          </div>
        </>
      )}
    </div>
  );
}

function ChunksTab({ base, focus }: { base: string; focus: string | null }) {
  const { data } = useApi<ChunkRow[]>(`${base}/chunks`);
  const refs = useRef<Record<string, HTMLLIElement | null>>({});
  if (!data) return <Spinner />;
  if (!data.length) return <div className="panel muted">Sin fragmentos indexados.</div>;
  const total = data.reduce((n, c) => n + c.token_estimate, 0);
  return (
    <div className="panel">
      <p className="small muted">
        {data.length} fragmentos · ~{total.toLocaleString()} tokens (estimación de 4 caracteres/token). Los
        agentes reciben solo los fragmentos relevantes, no el documento completo.
      </p>
      <ul className="list">
        {data.map((c) => (
          <li
            key={c.id}
            ref={(el) => {
              refs.current[c.id] = el;
              if (el && focus === c.id) el.scrollIntoView({ block: "center" });
            }}
            style={focus === c.id ? { outline: "2px solid var(--accent-2)" } : undefined}
          >
            <div className="row small">
              <strong>#{c.ord + 1}</strong>
              <span className="muted">{c.heading}</span>
              <span className="faint">{locatorText(c.locator)}</span>
              {c.suspicious && <Badge tone="warn">instrucciones sospechosas</Badge>}
              <span className="faint right">~{c.token_estimate} tokens</span>
            </div>
            <div className="small" style={{ whiteSpace: "pre-wrap", maxHeight: 160, overflow: "auto" }}>
              {c.content}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ReportTab({
  report,
  versionId,
  onMeasured,
}: {
  report: Report;
  versionId?: string;
  onMeasured: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const measured = report.token_measurement;
  return (
    <div className="grid cols-2">
      <div className="panel">
        <h3>Calidad de la extracción</h3>
        {report.issues && report.issues.length > 0 ? (
          <div className="alert warn">
            {report.issues.map((i) => (
              <div key={i}>{i}</div>
            ))}
          </div>
        ) : (
          <div className="alert ok">Sin problemas detectados.</div>
        )}
        <dl className="kv">
          <dt>Caracteres (original)</dt>
          <dd>{report.chars_original?.toLocaleString() ?? "—"}</dd>
          <dt>Caracteres (Markdown)</dt>
          <dd>{report.chars_markdown?.toLocaleString() ?? "—"}</dd>
          <dt>Palabras</dt>
          <dd>{report.words?.toLocaleString() ?? "—"}</dd>
          <dt>Páginas</dt>
          <dd>{report.pages ?? "—"}</dd>
          <dt>Tablas</dt>
          <dd>{report.tables ?? 0}</dd>
          <dt>Fragmentos</dt>
          <dd>{report.chunks ?? 0}</dd>
          <dt>Tokens estimados</dt>
          <dd>
            original ~{report.token_estimate_original?.toLocaleString()} · Markdown ~
            {report.token_estimate_markdown?.toLocaleString()}
            <div className="small faint">{report.token_estimate_method}</div>
          </dd>
          <dt>Tokens medidos</dt>
          <dd>
            {measured ? (
              <>
                original {measured.original.toLocaleString()} · Markdown {measured.markdown.toLocaleString()}{" "}
                ({measured.model}, count_tokens)
                {measured.original > 0 && (
                  <div className="small faint">
                    Diferencia medida:{" "}
                    {(((measured.original - measured.markdown) / measured.original) * 100).toFixed(1)}%
                  </div>
                )}
              </>
            ) : (
              <span className="muted">sin medir</span>
            )}{" "}
            {versionId && (
              <button
                className="small"
                disabled={busy}
                onClick={async () => {
                  setBusy(true);
                  try {
                    await post("/api/ai/count-tokens", { document_version_id: versionId });
                    onMeasured();
                  } catch (err) {
                    toast((err as Error).message, "err");
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                {busy ? "Midiendo…" : "Medir con Claude"}
              </button>
            )}
          </dd>
          <dt>OCR</dt>
          <dd>
            {report.needs_ocr ? "necesario" : "no necesario"} · Tesseract{" "}
            {report.ocr_available ? "disponible" : "no instalado"}
          </dd>
        </dl>
      </div>
      <div className="panel">
        <h3>Avisos</h3>
        {report.warnings?.length ? (
          <ul>
            {report.warnings.map((w) => (
              <li key={w} className="small">
                {w}
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted small">Ninguno.</p>
        )}
        <h3>Ruido eliminado del Markdown</h3>
        {report.noise_removed?.length ? (
          <ul>
            {report.noise_removed.map((w) => (
              <li key={w} className="small mono">
                {w}
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted small">Nada.</p>
        )}
      </div>
    </div>
  );
}

function VersionsTab({ doc, onUploaded, csrf }: { doc: DocDetail; onUploaded: () => void; csrf: string }) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  return (
    <div className="panel">
      <div className="panel-title">
        <h3>Historial de versiones</h3>
        <button className="small primary" disabled={busy} onClick={() => input.current?.click()}>
          {busy ? "Subiendo…" : "Subir nueva versión"}
        </button>
        <input
          ref={input}
          type="file"
          hidden
          aria-label="Nueva versión"
          onChange={async (e) => {
            const file = e.target.files?.[0];
            if (!file) return;
            setBusy(true);
            const form = new FormData();
            form.append("file", file);
            form.append("area_id", doc.area_id);
            form.append("document_id", doc.id);
            const res = await fetch("/api/library/upload", {
              method: "POST",
              body: form,
              headers: { "X-CSRF-Token": csrf },
            });
            setBusy(false);
            if (res.ok) {
              toast("Nueva versión subida", "ok");
              onUploaded();
            } else {
              const body = await res.json().catch(() => ({}));
              const d = body.detail;
              toast(typeof d === "string" ? d : (d?.message ?? "Error al subir"), "err");
            }
          }}
        />
      </div>
      <table className="table">
        <thead>
          <tr>
            <th>Versión</th>
            <th>Estado</th>
            <th>Tamaño</th>
            <th>SHA-256</th>
            <th>Autor</th>
            <th>Fecha</th>
          </tr>
        </thead>
        <tbody>
          {doc.versions.map((x) => (
            <tr key={x.id}>
              <td>v{x.version}</td>
              <td>
                <Badge tone={(DOC_STATUS[x.status] ?? ["", "info"])[1]}>
                  {(DOC_STATUS[x.status] ?? [x.status])[0]}
                </Badge>
              </td>
              <td className="small">{formatBytes(x.size)}</td>
              <td className="mono small faint">{x.sha256.slice(0, 16)}…</td>
              <td className="small">{x.author ?? "—"}</td>
              <td className="small">{formatDateTime(x.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MetaEditor({ doc, onSaved }: { doc: DocDetail; onSaved: () => void }) {
  const [title, setTitle] = useState(doc.title);
  const [tags, setTags] = useState(doc.tags.join(", "));
  const [category, setCategory] = useState(doc.category);
  return (
    <details className="panel">
      <summary>Clasificación y etiquetas</summary>
      <form
        className="row"
        style={{ marginTop: 10 }}
        onSubmit={async (e) => {
          e.preventDefault();
          try {
            await patch(`/api/library/documents/${doc.id}`, { title, tags: tags.split(","), category });
            toast("Metadatos guardados", "ok");
            onSaved();
          } catch (err) {
            toast((err as Error).message, "err");
          }
        }}
      >
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          aria-label="Título"
          style={{ flex: 2 }}
        />
        <input
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          aria-label="Categoría"
          style={{ flex: 1 }}
        />
        <input
          value={tags}
          onChange={(e) => setTags(e.target.value)}
          aria-label="Etiquetas separadas por comas"
          style={{ flex: 2 }}
        />
        <button className="primary">Guardar</button>
      </form>
    </details>
  );
}
