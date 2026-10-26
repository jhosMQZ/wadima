import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, del, get, put } from "../api/client";
import { realtime } from "../api/ws";
import { CodeEditor } from "../components/CodeEditor";
import { Comments } from "../components/Collab";
import { DiffView } from "../components/DiffView";
import { Markdown } from "../components/Markdown";
import { Badge, ConfirmDialog, ErrorBox, Modal, Spinner } from "../components/ui";
import { formatDateTime, timeAgo } from "../lib/format";
import { toast } from "../lib/toast";
import { useSession } from "../store/session";
import { NOTE_KIND } from "../lib/labels";

interface NoteData {
  id: string;
  area_id: string;
  project_id: string | null;
  title: string;
  kind: string;
  version: number;
  content: string;
  updated_at: string;
}

interface Draft {
  base_version: number;
  title: string;
  content: string;
  ts: number;
}

interface Conflict {
  message: string;
  current: NoteData;
  merged_with_markers: string;
}

type SaveState = "saved" | "dirty" | "saving" | "conflict" | "offline" | "error";

const SAVE_LABEL: Record<SaveState, [string, string]> = {
  saved: ["Guardado", "ok"],
  dirty: ["Cambios sin guardar", "warn"],
  saving: ["Guardando…", "info"],
  conflict: ["Conflicto de edición", "conflict"],
  offline: ["Sin conexión: copia local", "err"],
  error: ["Error al guardar", "err"],
};

const draftKey = (id: string) => `wadima:note-draft:${id}`;

function readDraft(id: string): Draft | null {
  try {
    const raw = localStorage.getItem(draftKey(id));
    return raw ? (JSON.parse(raw) as Draft) : null;
  } catch {
    return null;
  }
}

export default function NoteEditorPage() {
  const { id } = useParams() as { id: string };
  const me = useSession((s) => s.me)!;
  const [note, setNote] = useState<NoteData | null>(null);
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [saveState, setSaveState] = useState<SaveState>("saved");
  const [error, setError] = useState<string | null>(null);
  const [conflict, setConflict] = useState<Conflict | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [view, setView] = useState<"split" | "edit" | "preview">("split");
  const [showHistory, setShowHistory] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [docKey, setDocKey] = useState(0);
  const baseVersion = useRef(0);
  const latest = useRef({ title: "", content: "" });
  const timer = useRef<number | undefined>(undefined);
  const saving = useRef(false);

  const apply = useCallback(
    (n: NoteData) => {
      setNote(n);
      setTitle(n.title);
      setContent(n.content);
      latest.current = { title: n.title, content: n.content };
      baseVersion.current = n.version;
      setSaveState("saved");
      const d = readDraft(id);
      if (d && (d.content !== n.content || d.title !== n.title)) setDraft(d);
      else localStorage.removeItem(draftKey(id));
      setDocKey((k) => k + 1);
    },
    [id],
  );

  const load = useCallback(
    () =>
      get<NoteData>(`/api/notes/${id}`).then(apply, (err: Error) => {
        setError(err.message);
      }),
    [id, apply],
  );

  useEffect(() => {
    let cancelled = false;
    get<NoteData>(`/api/notes/${id}`).then(
      (n) => !cancelled && apply(n),
      (err: Error) => !cancelled && setError(err.message),
    );
    return () => {
      cancelled = true;
    };
  }, [id, apply]);

  const save = useCallback(
    async (force = false) => {
      if (saving.current) return;
      saving.current = true;
      setSaveState("saving");
      const payload = { base_version: baseVersion.current, ...latest.current, force };
      try {
        const res = await put<NoteData & { merged: boolean }>(`/api/notes/${id}`, payload);
        baseVersion.current = res.version;
        setNote(res);
        if (res.merged) {
          // El servidor fusionó cambios de otra persona: mostrar el resultado combinado.
          latest.current = { title: res.title, content: res.content };
          setTitle(res.title);
          setContent(res.content);
          toast("Se integraron cambios de otra persona en su versión", "info");
        }
        const dirty = latest.current.content !== res.content || latest.current.title !== res.title;
        setSaveState(dirty ? "dirty" : "saved");
        if (!dirty) localStorage.removeItem(draftKey(id));
        setConflict(null);
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) {
          setConflict(err.detail as Conflict);
          setSaveState("conflict");
        } else if (err instanceof ApiError && err.status === 0) {
          setSaveState("offline");
        } else {
          setSaveState("error");
          setError((err as Error).message);
        }
      } finally {
        saving.current = false;
      }
    },
    [id],
  );

  function onEdit(next: { title?: string; content?: string }) {
    latest.current = { ...latest.current, ...next };
    if (next.title !== undefined) setTitle(next.title);
    if (next.content !== undefined) setContent(next.content);
    setSaveState((s) => (s === "conflict" ? s : "dirty"));
    try {
      localStorage.setItem(
        draftKey(id),
        JSON.stringify({
          base_version: baseVersion.current,
          ...latest.current,
          ts: Date.now(),
        } satisfies Draft),
      );
    } catch {
      /* almacenamiento local no disponible */
    }
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      if (!conflictRef.current) void save();
    }, 1500);
  }
  const conflictRef = useRef<Conflict | null>(null);
  useEffect(() => {
    conflictRef.current = conflict;
  }, [conflict]);

  // Cambios remotos: si no hay ediciones locales pendientes, se recarga el contenido.
  useEffect(() => {
    return realtime.on("note.changed", (ev) => {
      if (ev.id !== id || ev.by === me.user.id) return;
      if (typeof ev.version === "number" && ev.version <= baseVersion.current) return;
      if (latest.current.content === note?.content && latest.current.title === note?.title) void load();
      else toast("Otra persona guardó cambios; se integrarán al guardar los suyos", "info");
    });
  }, [id, me.user.id, note, load]);

  useEffect(() => () => window.clearTimeout(timer.current), []);

  // Aviso al cerrar la pestaña con cambios sin guardar.
  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (saveState === "dirty" || saveState === "saving" || saveState === "conflict") e.preventDefault();
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [saveState]);

  if (error && !note) return <ErrorBox error={error} />;
  if (!note) return <Spinner />;
  const [label, tone] = SAVE_LABEL[saveState];

  return (
    <>
      <div className="page-head">
        <div className="grow">
          <div className="small muted">
            {note.project_id ? <Link to={`/projects/${note.project_id}`}>Proyecto</Link> : "Área"} /{" "}
            {NOTE_KIND[note.kind]}
          </div>
          <input
            value={title}
            onChange={(e) => onEdit({ title: e.target.value })}
            aria-label="Título"
            style={{ fontSize: 20, fontWeight: 700, maxWidth: 640 }}
          />
        </div>
        <span className={`badge ${tone}`} role="status" data-testid="save-state">
          {label}
        </span>
        <span className="small faint">v{note.version}</span>
        <select
          value={view}
          onChange={(e) => setView(e.target.value as typeof view)}
          style={{ width: 150 }}
          aria-label="Vista"
        >
          <option value="split">Editor + vista previa</option>
          <option value="edit">Solo editor</option>
          <option value="preview">Solo vista previa</option>
        </select>
        <button onClick={() => save()} disabled={saveState === "saving"}>
          Guardar ahora
        </button>
        <button onClick={() => setShowHistory(true)}>Historial</button>
        <button className="danger" onClick={() => setConfirmDelete(true)}>
          Eliminar
        </button>
      </div>
      {draft && (
        <div className="alert warn row">
          <span>Hay un borrador local sin guardar de {timeAgo(new Date(draft.ts).toISOString())}.</span>
          <button
            className="small"
            onClick={() => {
              baseVersion.current = draft.base_version;
              onEdit({ title: draft.title, content: draft.content });
              setDraft(null);
              setDocKey((k) => k + 1);
            }}
          >
            Recuperar
          </button>
          <button
            className="small ghost"
            onClick={() => {
              localStorage.removeItem(draftKey(id));
              setDraft(null);
            }}
          >
            Descartar
          </button>
        </div>
      )}
      <ErrorBox error={error} />
      <div
        className="grid"
        style={{ gridTemplateColumns: view === "split" ? "1fr 1fr" : "1fr", minHeight: 420 }}
      >
        {view !== "preview" && (
          <div className="panel" style={{ padding: 0, height: "62vh", overflow: "hidden" }}>
            <CodeEditor
              value={content}
              path="nota.md"
              docKey={`${id}-${docKey}`}
              onChange={(v) => onEdit({ content: v })}
              onSave={() => save()}
            />
          </div>
        )}
        {view !== "edit" && (
          <div className="panel" style={{ height: "62vh", overflow: "auto" }}>
            <Markdown source={content} />
          </div>
        )}
      </div>
      <div className="panel">
        <h3>Comentarios</h3>
        <Comments targetType="note" targetId={id} />
      </div>
      {conflict && (
        <Modal title="Conflicto de edición" onClose={() => setConflict(null)} wide>
          <p>
            {conflict.message}. Elija cómo continuar; ninguna versión se pierde (todas quedan en el
            historial).
          </p>
          <h3>Diferencias entre la versión del servidor (v{conflict.current.version}) y la suya</h3>
          <DiffView before={conflict.current.content} after={content} />
          <div className="row" style={{ marginTop: 12 }}>
            <button
              className="primary"
              onClick={() => {
                baseVersion.current = conflict.current.version;
                setConflict(null);
                void save(true);
              }}
            >
              Guardar mi versión
            </button>
            <button
              onClick={() => {
                baseVersion.current = conflict.current.version;
                latest.current = { title: conflict.current.title, content: conflict.current.content };
                setTitle(conflict.current.title);
                setContent(conflict.current.content);
                setNote(conflict.current);
                setConflict(null);
                setSaveState("saved");
                localStorage.removeItem(draftKey(id));
                setDocKey((k) => k + 1);
              }}
            >
              Usar la del servidor
            </button>
            <button
              onClick={() => {
                baseVersion.current = conflict.current.version;
                latest.current = { ...latest.current, content: conflict.merged_with_markers };
                setContent(conflict.merged_with_markers);
                setConflict(null);
                setSaveState("dirty");
                setDocKey((k) => k + 1);
                toast("Resuelva los bloques marcados con <<<<<<< y >>>>>>> y guarde", "info");
              }}
            >
              Resolver manualmente
            </button>
          </div>
        </Modal>
      )}
      {showHistory && (
        <History
          noteId={id}
          current={content}
          onClose={() => setShowHistory(false)}
          onRestore={(rev) => {
            onEdit({ title: rev.title, content: rev.content });
            setDocKey((k) => k + 1);
            setShowHistory(false);
          }}
        />
      )}
      {confirmDelete && (
        <ConfirmDialog
          title="Eliminar documento"
          message={`¿Eliminar «${note.title}»? El historial se conserva en la base de datos.`}
          danger
          confirmLabel="Eliminar"
          onClose={() => setConfirmDelete(false)}
          onConfirm={async () => {
            await del(`/api/notes/${id}`);
            localStorage.removeItem(draftKey(id));
            history.back();
          }}
        />
      )}
    </>
  );
}

function History({
  noteId,
  current,
  onClose,
  onRestore,
}: {
  noteId: string;
  current: string;
  onClose: () => void;
  onRestore: (rev: { title: string; content: string }) => void;
}) {
  const [revs, setRevs] = useState<
    { version: number; created_at: string; author: string | null; size: number }[] | null
  >(null);
  const [selected, setSelected] = useState<{ version: number; title: string; content: string } | null>(null);
  useEffect(() => {
    get<typeof revs>(`/api/notes/${noteId}/revisions`).then(setRevs);
  }, [noteId]);
  return (
    <Modal title="Historial de versiones" onClose={onClose} wide>
      <div className="grid" style={{ gridTemplateColumns: "240px 1fr" }}>
        <ul className="list">
          {revs?.map((r) => (
            <li key={r.version}>
              <button
                className="link"
                onClick={async () => setSelected(await get(`/api/notes/${noteId}/revisions/${r.version}`))}
              >
                v{r.version}
              </button>{" "}
              <span className="small muted">
                {r.author ?? "—"} · {formatDateTime(r.created_at)}
              </span>
            </li>
          ))}
        </ul>
        <div>
          {selected ? (
            <>
              <div className="row" style={{ marginBottom: 8 }}>
                <Badge tone="info">v{selected.version}</Badge>
                <span className="small muted">Diferencias con el texto actual</span>
                <button className="primary small right" onClick={() => onRestore(selected)}>
                  Restaurar esta versión
                </button>
              </div>
              <DiffView before={selected.content} after={current} />
            </>
          ) : (
            <p className="muted">Seleccione una versión para compararla.</p>
          )}
        </div>
      </div>
    </Modal>
  );
}
