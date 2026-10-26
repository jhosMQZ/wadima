import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { post } from "../api/client";
import { timeAgo } from "../lib/format";
import { useApi } from "../lib/useApi";
import { useBrand } from "../store/brand";

interface NotificationList {
  unread: number;
  items: {
    id: string;
    kind: string;
    title: string;
    body: string;
    link: string;
    created_at: string;
    read_at: string | null;
  }[];
}

export function NotificationsBell() {
  const { data, reload } = useApi<NotificationList>("/api/notifications", {
    refreshOn: ["notification.new"],
  });
  const [open, setOpen] = useState(false);
  const platform = useBrand((s) => s.org.platform_name);
  const unread = data?.unread ?? 0;
  useEffect(() => {
    document.title = unread > 0 ? `(${unread}) ${platform}` : platform;
  }, [unread, platform]);
  return (
    <div style={{ position: "relative" }}>
      <button
        className="small ghost"
        aria-label={`Notificaciones (${unread} sin leer)`}
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        🔔{unread > 0 && <span className="badge warn">{unread}</span>}
      </button>
      {open && (
        <div
          className="panel"
          style={{
            position: "absolute",
            right: 0,
            top: 34,
            width: "min(340px, 92vw)",
            zIndex: 40,
            maxHeight: 420,
            overflow: "auto",
          }}
        >
          <div className="panel-title">
            <h3>Notificaciones de {platform}</h3>
            {unread > 0 && (
              <button
                className="small"
                onClick={async () => {
                  await post("/api/notifications/read");
                  reload(true);
                }}
              >
                Marcar leídas
              </button>
            )}
          </div>
          {!data?.items.length && <p className="muted">No hay notificaciones.</p>}
          <ul className="list">
            {data?.items.map((n) => (
              <li key={n.id} style={{ opacity: n.read_at ? 0.6 : 1 }}>
                <div className="row">
                  <strong style={{ flex: 1 }}>
                    {n.link ? (
                      <Link to={n.link} onClick={() => setOpen(false)}>
                        {n.title}
                      </Link>
                    ) : (
                      n.title
                    )}
                  </strong>
                  <span className="small faint">{timeAgo(n.created_at)}</span>
                </div>
                {n.body && <div className="small muted">{n.body}</div>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
