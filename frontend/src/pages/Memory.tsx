import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { ApiError, del, patch, post } from "../api/client";
import type { Area } from "../api/types";
import { Badge, ConfirmDialog, EmptyState, ErrorBox, Modal, Spinner, Tabs } from "../components/ui";
import { formatDateTime, timeAgo } from "../lib/format";
import { toast } from "../lib/toast";
import { useApi } from "../lib/useApi";
import { useSession } from "../store/session";
import { MEMORY_STATUS, label } from "../lib/labels";

export interface RetrievedItem {
  type: "document_chunk" | "memory";
  id: string;
  title: string;
  content: string;
  layer?: string;
  kind?: string;
  score: number;
  suspicious: boolean;
  validation: string;
  confidence?: number;
  reference: Record<string, string | number | null> & { link: string };
}

export interface QueryResult {
  items: RetrievedItem[];
  context: string;
  token_estimate: number;
  token_estimate_method?: string;
  contradictions: { id: string; a_title: string; b_title: string; detail: string }[];
}

interface MemoryItem {
  id: string;
  layer: string;
  kind: string;
  title: string;
  content: string;
  area_id: string;
  source_type: string;
  source_id: string | null;
  source_version: number | null;
  source_ref: Record<string, string>;
  confidence: number;
  validation: string;
  status: string;
  valid_until: string | null;
  created_at: string;
  updated_at: string;
  version: number;
}

const LAYER_LABEL: Record<string, string> = {
  documental: "Documental",
  semantic: "Semántica",
  operational: "Operativa",
  consolidated: "Consolidada",
};
const VALIDATION_TONE: Record<string, string> = {
  confirmed: "ok",
  unverified: "info",
  inferred: "warn",
  rejected: "err",
};
const VALIDATION_LABEL: Record<string, string> = {
  confirmed: "confirmado",
  unverified: "sin verificar",
  inferred: "inferido",
  rejected: "rechazado",
  documento: "documento",
};

function sourceLink(m: { source_ref: Record<string, string>; source_type: string }): string | null {
  if (m.source_ref.document_id) return `/library/${m.source_ref.document_id}`;
  if (m.source_ref.note_id) return `/notes/${m.source_ref.note_id}`;
  if (m.source_ref.project_id) return `/projects/${m.source_ref.project_id}`;
  return null;
}

export default function MemoryPage() {
  const [tab, setTab] = useState<"query" | "browse" | "contradictions">("query");
  const stats = useApi<{
    by_layer: Record<string, number>;
    by_validation: Record<string, number>;
    open_contradictions: number;
  }>("/api/memory/stats", { refreshOn: ["memory.changed"] });
  return (
    <>
      <div className="page-head">
        <div className="grow">
          <h1>Memoria compartida</h1>
          <p>
            Conocimiento organizado por el Memory Agent con fuente, fecha, confianza, validación y vigencia.
          </p>
        </div>
        {stats.data && (
          <div className="row">
            {Object.entries(stats.data.by_layer).map(([k, v]) => (
              <Badge key={k} tone="info">
                {LAYER_LABEL[k] ?? k}: {v}
              </Badge>
            ))}
            {stats.data.open_contradictions > 0 && (
              <Badge tone="conflict">contradicciones: {stats.data.open_contradictions}</Badge>
            )}
          </div>
        )}
      </div>
      <Tabs
        tabs={[
          { key: "query", label: "Consultar" },
          { key: "browse", label: "Explorar y revisar" },
          {
            key: "contradictions",
            label: `Contradicciones${stats.data?.open_contradictions ? ` (${stats.data.open_contradictions})` : ""}`,
          },
        ]}
        value={tab}
        onChange={setTab}
      />
      {tab === "query" && <QueryTab />}
      {tab === "browse" && <BrowseTab />}
      {tab === "contradictions" && <ContradictionsTab />}
    </>
  );
}

export function RetrievedList({ result }: { result: QueryResult }) {
  return (
    <>
      {result.contradictions.length > 0 && (
        <div className="alert warn">
          Atención: hay contradicciones sin resolver entre las fuentes (
          {result.contradictions.map((c) => c.detail).join("; ")}).
        </div>
      )}
      <ul className="list">
        {result.items.map((it, n) => (
          <li key={it.id}>
            <div className="row">
              <strong>[{n + 1}]</strong>
              <Link to={it.reference.link}>{it.title}</Link>
              <Badge tone={it.type === "memory" ? (VALIDATION_TONE[it.validation] ?? "info") : "muted"}>
                {it.type === "memory"
                  ? `${LAYER_LABEL[it.layer ?? ""] ?? it.layer} · ${VALIDATION_LABEL[it.validation] ?? it.validation}`
                  : "fragmento"}
              </Badge>
              {it.suspicious && <Badge tone="warn">instrucciones sospechosas</Badge>}
              <span className="small faint right">relevancia {it.score.toFixed(3)}</span>
            </div>
            <div className="small faint">
              {it.type === "document_chunk"
                ? [
                    `v${it.reference.version}`,
                    it.reference.page_from ? `pág. ${it.reference.page_from}` : null,
                    it.reference.sheet ? `hoja ${it.reference.sheet}` : null,
                    it.reference.section ? `«${it.reference.section}»` : null,
                  ]
                    .filter(Boolean)
                    .join(" · ")
                : `${it.kind} · confianza ${(it.confidence ?? 0).toFixed(2)}`}
            </div>
            <div className="small" style={{ whiteSpace: "pre-wrap", maxHeight: 140, overflow: "auto" }}>
              {it.content}
            </div>
          </li>
        ))}
      </ul>
    </>
  );
}

function QueryTab() {
  const me = useSession((s) => s.me)!;
  const areas = useApi<{ areas: Area[] }>("/api/areas");
  const [q, setQ] = useState("");
  const [areaId, setAreaId] = useState("");
  const [result, setResult] = useState<QueryResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showContext, setShowContext] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setResult(await post<QueryResult>("/api/memory/query", { q, area_id: areaId || null }));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="panel">
      <form className="row" onSubmit={submit}>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="¿Qué sabemos sobre…?"
          aria-label="Consulta"
          style={{ flex: 1, minWidth: 240 }}
          required
          minLength={2}
        />
        <select
          value={areaId}
          onChange={(e) => setAreaId(e.target.value)}
          style={{ width: 220 }}
          aria-label="Área"
        >
          <option value="">Todas mis áreas</option>
          {areas.data?.areas.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
        </select>
        <button className="primary" disabled={busy}>
          {busy ? "Buscando…" : "Consultar"}
        </button>
      </form>
      <p className="small muted">
        Solo se consultan las áreas a las que tiene acceso ({me.user.display_name}). Cada resultado enlaza con
        su fuente original.
      </p>
      <ErrorBox error={error} />
      {result && result.items.length === 0 && <EmptyState title="Sin resultados pertinentes" />}
      {result && result.items.length > 0 && (
        <>
          <div className="row small muted" style={{ margin: "6px 0" }}>
            {result.items.length} fuentes · ~{result.token_estimate} tokens de contexto (
            {result.token_estimate_method})
            <button className="small right" onClick={() => setShowContext((s) => !s)}>
              {showContext ? "Ocultar" : "Ver"} contexto para agentes
            </button>
          </div>
          {showContext && <div className="code-box">{result.context}</div>}
          <RetrievedList result={result} />
        </>
      )}
    </div>
  );
}

function BrowseTab() {
  const [filters, setFilters] = useState({ layer: "", kind: "", status: "active", validation: "", q: "" });
  const qs = new URLSearchParams(Object.entries(filters).filter(([, v]) => v) as [string, string][]);
  if (!filters.status) qs.set("status", "");
  const { data, error } = useApi<{ items: MemoryItem[]; total: number }>(`/api/memory/items?${qs}`, {
    refreshOn: ["memory.changed"],
  });
  const [open, setOpen] = useState<string | null>(null);
  const set = (k: keyof typeof filters, v: string) => setFilters({ ...filters, [k]: v });
  return (
    <div className="panel">
      <div className="row" style={{ marginBottom: 10 }}>
        <select
          value={filters.layer}
          onChange={(e) => set("layer", e.target.value)}
          style={{ width: 160 }}
          aria-label="Capa"
        >
          <option value="">Todas las capas</option>
          {Object.entries(LAYER_LABEL).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>
        <select
          value={filters.kind}
          onChange={(e) => set("kind", e.target.value)}
          style={{ width: 160 }}
          aria-label="Tipo"
        >
          <option value="">Todos los tipos</option>
          {["fact", "concept", "procedure", "agreement", "decision", "summary", "document", "note"].map(
            (k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ),
          )}
        </select>
        <select
          value={filters.status}
          onChange={(e) => set("status", e.target.value)}
          style={{ width: 150 }}
          aria-label="Estado"
        >
          <option value="active">Vigentes</option>
          <option value="obsolete">Obsoletos</option>
          <option value="archived">Archivados</option>
          <option value="">Todos</option>
        </select>
        <select
          value={filters.validation}
          onChange={(e) => set("validation", e.target.value)}
          style={{ width: 160 }}
          aria-label="Validación"
        >
          <option value="">Cualquier validación</option>
          {Object.entries(VALIDATION_LABEL)
            .filter(([k]) => k !== "documento")
            .map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
        </select>
        <input
          value={filters.q}
          onChange={(e) => set("q", e.target.value)}
          placeholder="Filtrar texto"
          aria-label="Filtrar texto"
          style={{ flex: 1, minWidth: 160 }}
        />
      </div>
      <ErrorBox error={error} />
      {!data && <Spinner />}
      {data?.items.length === 0 && <EmptyState title="Sin recuerdos con estos filtros" />}
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Recuerdo</th>
              <th>Capa / tipo</th>
              <th>Validación</th>
              <th>Confianza</th>
              <th>Fuente</th>
              <th>Actualizado</th>
            </tr>
          </thead>
          <tbody>
            {data?.items.map((m) => (
              <tr key={m.id}>
                <td>
                  <button className="link" onClick={() => setOpen(m.id)} style={{ textAlign: "left" }}>
                    {m.title}
                  </button>
                  {m.status !== "active" && <Badge tone="muted">{label(MEMORY_STATUS, m.status)}</Badge>}
                </td>
                <td className="small">
                  {LAYER_LABEL[m.layer]} · {m.kind}
                </td>
                <td>
                  <Badge tone={VALIDATION_TONE[m.validation]}>{VALIDATION_LABEL[m.validation]}</Badge>
                </td>
                <td className="small">{m.confidence.toFixed(2)}</td>
                <td className="small">
                  {sourceLink(m) ? (
                    <Link to={sourceLink(m)!}>{m.source_ref.title ?? m.source_type}</Link>
                  ) : (
                    m.source_type
                  )}
                  {m.source_version != null && m.source_type !== "area_consolidation" && (
                    <span className="faint"> v{m.source_version}</span>
                  )}
                </td>
                <td className="small">{timeAgo(m.updated_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {data && data.total > data.items.length && (
        <p className="small muted">
          Mostrando {data.items.length} de {data.total}.
        </p>
      )}
      {open && <ItemModal id={open} onClose={() => setOpen(null)} />}
    </div>
  );
}

interface ItemDetail extends MemoryItem {
  relations: {
    id: string;
    relation: string;
    score: number;
    detail: string;
    resolved: boolean;
    other: { id: string; title: string; status: string };
  }[];
  history: {
    version: number;
    change: string;
    reason: string;
    actor: string | null;
    ts: string;
    before: Record<string, unknown> | null;
  }[];
}

function ItemModal({ id, onClose }: { id: string; onClose: () => void }) {
  const me = useSession((s) => s.me)!;
  const { data, reload } = useApi<ItemDetail>(`/api/memory/items/${id}`);
  const [editing, setEditing] = useState(false);
  const [content, setContent] = useState("");
  const [reason, setReason] = useState("");
  const [confirm, setConfirm] = useState(false);
  async function apply(body: Record<string, unknown>) {
    try {
      await patch(`/api/memory/items/${id}`, body);
      toast("Recuerdo actualizado", "ok");
      reload();
      setEditing(false);
    } catch (err) {
      toast(
        err instanceof ApiError && err.status === 403
          ? "Solo administradores o propietarios del área pueden revisar la memoria"
          : (err as Error).message,
        "err",
      );
    }
  }
  return (
    <Modal title={data?.title ?? "Recuerdo"} onClose={onClose} wide>
      {!data ? (
        <Spinner />
      ) : (
        <div className="grid cols-2">
          <div className="stack">
            <div className="row">
              <Badge tone="info">{LAYER_LABEL[data.layer]}</Badge>
              <Badge tone="muted">{data.kind}</Badge>
              <Badge tone={VALIDATION_TONE[data.validation]}>{VALIDATION_LABEL[data.validation]}</Badge>
              <Badge tone={data.status === "active" ? "ok" : "muted"}>
                {label(MEMORY_STATUS, data.status)}
              </Badge>
            </div>
            {editing ? (
              <>
                <textarea
                  value={content}
                  onChange={(e) => setContent(e.target.value)}
                  style={{ minHeight: 140 }}
                  aria-label="Contenido corregido"
                />
                <input
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  placeholder="Motivo de la corrección"
                  aria-label="Motivo"
                />
                <div className="row">
                  <button className="primary" onClick={() => apply({ content, reason })}>
                    Guardar corrección
                  </button>
                  <button className="ghost" onClick={() => setEditing(false)}>
                    Cancelar
                  </button>
                </div>
              </>
            ) : (
              <div className="code-box">{data.content}</div>
            )}
            <dl className="kv">
              <dt>Fuente</dt>
              <dd>
                {sourceLink(data) ? (
                  <Link to={sourceLink(data)!}>{data.source_ref.title ?? data.source_type}</Link>
                ) : (
                  data.source_type
                )}
                {data.source_ref.section && (
                  <span className="small muted"> · «{data.source_ref.section}»</span>
                )}
                {data.source_ref.page && <span className="small muted"> · pág. {data.source_ref.page}</span>}
              </dd>
              <dt>Confianza</dt>
              <dd>{data.confidence.toFixed(2)}</dd>
              <dt>Creado</dt>
              <dd>{formatDateTime(data.created_at)}</dd>
              <dt>Vigencia</dt>
              <dd>{data.valid_until ? `hasta ${formatDateTime(data.valid_until)}` : "sin caducidad"}</dd>
            </dl>
            <div className="row">
              <button
                className="small"
                onClick={() => {
                  setContent(data.content);
                  setEditing(true);
                }}
              >
                Corregir
              </button>
              {data.validation !== "confirmed" && (
                <button
                  className="small"
                  onClick={() => apply({ validation: "confirmed", reason: "Validado por revisión" })}
                >
                  Confirmar
                </button>
              )}
              {data.validation !== "rejected" && (
                <button
                  className="small"
                  onClick={() => apply({ validation: "rejected", reason: "Rechazado en revisión" })}
                >
                  Rechazar
                </button>
              )}
              {data.status === "active" ? (
                <button
                  className="small"
                  onClick={() => apply({ status: "archived", reason: "Archivado en revisión" })}
                >
                  Archivar
                </button>
              ) : (
                <button
                  className="small"
                  onClick={() =>
                    apply({ status: "active", clear_valid_until: true, reason: "Reactivado en revisión" })
                  }
                >
                  Reactivar
                </button>
              )}
              {me.user.role === "admin" && (
                <button className="small danger" onClick={() => setConfirm(true)}>
                  Eliminar
                </button>
              )}
            </div>
          </div>
          <div className="stack">
            <h3>Relaciones</h3>
            {data.relations.length === 0 && <p className="muted small">Ninguna.</p>}
            <ul className="list">
              {data.relations.map((r) => (
                <li key={r.id} className="small">
                  <Badge tone={r.relation === "contradicts" ? "conflict" : "info"}>{r.relation}</Badge>{" "}
                  {r.other.title}{" "}
                  <span className="faint">
                    ({r.detail}){r.resolved ? " · resuelta" : ""}
                  </span>
                </li>
              ))}
            </ul>
            <h3>Historial</h3>
            <ul className="list">
              {data.history.map((h, i) => (
                <li key={i} className="small">
                  <strong>
                    v{h.version} · {h.change}
                  </strong>{" "}
                  <span className="muted">
                    {h.actor ?? "—"} · {formatDateTime(h.ts)}
                  </span>
                  {h.reason && <div className="faint">{h.reason}</div>}
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
      {confirm && (
        <ConfirmDialog
          title="Eliminar recuerdo"
          message="Se eliminará de la memoria; su historial conserva una copia del contenido anterior."
          danger
          confirmLabel="Eliminar"
          onClose={() => setConfirm(false)}
          onConfirm={async () => {
            await del(`/api/memory/items/${id}`);
            onClose();
          }}
        />
      )}
    </Modal>
  );
}

interface Contradiction {
  id: string;
  detail: string;
  created_at: string;
  a: {
    id: string;
    title: string;
    content: string;
    source_ref: Record<string, string>;
    source_type: string;
    created_at: string;
  };
  b: {
    id: string;
    title: string;
    content: string;
    source_ref: Record<string, string>;
    source_type: string;
    created_at: string;
  };
}

function ContradictionsTab() {
  const { data, reload } = useApi<Contradiction[]>("/api/memory/contradictions", {
    refreshOn: ["memory.changed"],
  });
  const [notes, setNotes] = useState<Record<string, string>>({});
  if (!data) return <Spinner />;
  if (!data.length) return <EmptyState title="No hay contradicciones pendientes" />;
  async function resolve(c: Contradiction, archive: string | null) {
    try {
      await post(`/api/memory/contradictions/${c.id}/resolve`, {
        note: notes[c.id] || "Revisado",
        archive_item_id: archive,
      });
      toast("Contradicción resuelta", "ok");
      reload();
    } catch (err) {
      toast((err as Error).message, "err");
    }
  }
  return (
    <div className="stack">
      {data.map((c) => (
        <div key={c.id} className="panel">
          <p className="small muted">
            Detectada {timeAgo(c.created_at)}. WADIMA no elige: decida qué dato es válido.
          </p>
          <div className="grid cols-2">
            {[c.a, c.b].map((side) => (
              <div key={side.id} className="panel" style={{ margin: 0 }}>
                <strong>{side.title}</strong>
                <div className="small muted">
                  {sourceLink(side) ? (
                    <Link to={sourceLink(side)!}>{side.source_ref.title ?? side.source_type}</Link>
                  ) : (
                    side.source_type
                  )}{" "}
                  · {formatDateTime(side.created_at)}
                </div>
                <button
                  className="small"
                  style={{ marginTop: 6 }}
                  onClick={() => resolve(c, side.id === c.a.id ? c.b.id : c.a.id)}
                >
                  Este es el válido (archivar el otro)
                </button>
              </div>
            ))}
          </div>
          <div className="row" style={{ marginTop: 8 }}>
            <input
              value={notes[c.id] ?? ""}
              onChange={(e) => setNotes({ ...notes, [c.id]: e.target.value })}
              placeholder="Nota de resolución (contexto)"
              aria-label="Nota de resolución"
              style={{ flex: 1 }}
            />
            <button className="small" onClick={() => resolve(c, null)}>
              Ambos válidos (contextos distintos)
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
