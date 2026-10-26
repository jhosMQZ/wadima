import { diffLines, diffWordsWithSpace } from "diff";
import { useMemo } from "react";

export function DiffView({
  before,
  after,
  words = false,
}: {
  before: string;
  after: string;
  words?: boolean;
}) {
  const parts = useMemo(
    () => (words ? diffWordsWithSpace(before ?? "", after ?? "") : diffLines(before ?? "", after ?? "")),
    [before, after, words],
  );
  const stats = useMemo(() => {
    let add = 0;
    let del = 0;
    for (const p of parts) {
      if (p.added) add += p.count ?? 0;
      else if (p.removed) del += p.count ?? 0;
    }
    return { add, del };
  }, [parts]);
  return (
    <div>
      <div className="small muted" style={{ marginBottom: 4 }}>
        +{stats.add} / −{stats.del} {words ? "palabras" : "líneas"}
      </div>
      <div className="diff" aria-label="Diferencias">
        {parts.map((p, i) => (
          <span key={i} className={p.added ? "add" : p.removed ? "del" : undefined}>
            {p.value}
          </span>
        ))}
      </div>
    </div>
  );
}
