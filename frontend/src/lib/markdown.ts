import DOMPurify from "dompurify";
import { marked } from "marked";

marked.setOptions({ gfm: true, breaks: false });

// Los enlaces se abren en otra pestaña sin acceso a window.opener.
DOMPurify.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName === "A") {
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", "noopener noreferrer");
  }
});

/** Markdown -> HTML saneado (contenido no confiable: documentos, notas, salidas de IA). */
export function renderMarkdown(source: string): string {
  const html = marked.parse(source ?? "", { async: false }) as string;
  return DOMPurify.sanitize(html, { USE_PROFILES: { html: true }, FORBID_TAGS: ["style", "form", "input"] });
}
