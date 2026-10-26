import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { del, post } from "../api/client";
import type { ActivityItem, UserPublic } from "../api/types";
import { timeAgo } from "../lib/format";
import { toast } from "../lib/toast";
import { useApi } from "../lib/useApi";
import { useSession } from "../store/session";
import { Spinner } from "./ui";

const KIND_ICON: Record<string, string> = {
  "task.created": "＋",
  "task.started": "▶",
  "task.completed": "✔",
  "task.blocked": "⛔",
  "note.created": "✎",
  "note.updated": "✎",
  "document.added": "▥",
  "document.converted": "▥",
  "document.error": "⚠",
  "decision.approved": "⚖",
  "decision.created": "⚖",
  "comment.created": "💬",
  "agent.result": "✦",
  "ai.result": "✦",
  "sync.error": "⚠",
};

function refLink(item: ActivityItem): string | null {
  if (item.ref_type === "note" && item.ref_id) return `/notes/${item.ref_id}`;
  if (item.ref_type === "document" && item.ref_id) return `/library/${item.ref_id}`;
  if (item.project_id) return `/projects/${item.project_id}`;
  return null;
}

export function ActivityFeed({ query = "", limit = 30 }: { query?: string; limit?: number }) {
  const { data, error } = useApi<ActivityItem[]>(`/api/activity?limit=${limit}${query ? `&${query}` : ""}`, {
    refreshOn: ["activity.new"],
  });
  if (error) return <div className="alert err">{error}</div>;
  if (!data) return <Spinner />;
  if (!data.length) return <p className="muted">No hay actividad registrada todavía.</p>;
  return (
    <ul className="list">
      {data.map((a) => {
        const link = refLink(a);
        return (
          <li key={a.id} className="row" style={{ alignItems: "flex-start", flexWrap: "nowrap" }}>
            <span className="pixel" style={{ width: 18, textAlign: "center" }} aria-hidden="true">
              {KIND_ICON[a.kind] ?? "•"}
            </span>
            <span style={{ flex: 1, minWidth: 0 }}>
              <strong>{a.actor_name}</strong> {link ? <Link to={link}>{a.summary}</Link> : a.summary}
              <span className="small faint" style={{ display: "block" }}>
                {timeAgo(a.ts)} · {a.kind}
              </span>
            </span>
          </li>
        );
      })}
    </ul>
  );
}

interface CommentRow {
  id: string;
  body: string;
  author: UserPublic | null;
  created_at: string;
}

export function Comments({ targetType, targetId }: { targetType: string; targetId: string }) {
  const me = useSession((s) => s.me)!;
  const { data, reload } = useApi<CommentRow[]>(
    `/api/comments?target_type=${targetType}&target_id=${targetId}`,
    {
      refreshOn: ["comment.changed"],
      eventFilter: (ev) => ev.target_id === targetId,
    },
  );
  const [body, setBody] = useState("");
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!body.trim()) return;
    try {
      await post("/api/comments", { target_type: targetType, target_id: targetId, body });
      setBody("");
      reload();
    } catch (err) {
      toast((err as Error).message, "err");
    }
  }
  return (
    <div className="stack">
      {!data && <Spinner />}
      {data?.length === 0 && <p className="muted small">Sin comentarios.</p>}
      <ul className="list">
        {data?.map((c) => (
          <li key={c.id}>
            <div className="row">
              <strong>{c.author?.display_name ?? "—"}</strong>
              <span className="small faint">{timeAgo(c.created_at)}</span>
              {c.author?.id === me.user.id && (
                <button
                  className="link small right"
                  onClick={async () => {
                    await del(`/api/comments/${c.id}`);
                    reload();
                  }}
                >
                  borrar
                </button>
              )}
            </div>
            <div style={{ whiteSpace: "pre-wrap" }}>{c.body}</div>
          </li>
        ))}
      </ul>
      <form onSubmit={submit} className="stack">
        <textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          placeholder="Escriba un comentario…"
          aria-label="Nuevo comentario"
          style={{ minHeight: 60 }}
        />
        <div>
          <button className="small primary" disabled={!body.trim()}>
            Comentar
          </button>
        </div>
      </form>
    </div>
  );
}
