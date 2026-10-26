import { useMemo } from "react";
import { renderMarkdown } from "../lib/markdown";

export function Markdown({ source, className = "" }: { source: string; className?: string }) {
  const html = useMemo(() => renderMarkdown(source), [source]);
  return <div className={`md ${className}`} dangerouslySetInnerHTML={{ __html: html }} />;
}
