import { css } from "@codemirror/lang-css";
import { html } from "@codemirror/lang-html";
import { javascript } from "@codemirror/lang-javascript";
import { json } from "@codemirror/lang-json";
import { markdown } from "@codemirror/lang-markdown";
import { python } from "@codemirror/lang-python";
import { sql } from "@codemirror/lang-sql";
import { xml } from "@codemirror/lang-xml";
import { yaml } from "@codemirror/lang-yaml";
import { StreamLanguage } from "@codemirror/language";
import { c, cpp, csharp, java } from "@codemirror/legacy-modes/mode/clike";
import { go } from "@codemirror/legacy-modes/mode/go";
import { powerShell } from "@codemirror/legacy-modes/mode/powershell";
import { rust } from "@codemirror/legacy-modes/mode/rust";
import { shell } from "@codemirror/legacy-modes/mode/shell";
import type { Extension } from "@codemirror/state";

export function languageFor(path: string): Extension {
  const ext = path.split(".").pop()?.toLowerCase() ?? "";
  switch (ext) {
    case "js":
    case "mjs":
    case "cjs":
      return javascript();
    case "jsx":
      return javascript({ jsx: true });
    case "ts":
    case "mts":
      return javascript({ typescript: true });
    case "tsx":
      return javascript({ jsx: true, typescript: true });
    case "html":
    case "htm":
    case "vue":
      return html();
    case "css":
    case "scss":
      return css();
    case "json":
      return json();
    case "md":
    case "markdown":
      return markdown();
    case "py":
      return python();
    case "sql":
      return sql();
    case "xml":
    case "svg":
      return xml();
    case "yml":
    case "yaml":
      return yaml();
    case "java":
      return StreamLanguage.define(java);
    case "cs":
      return StreamLanguage.define(csharp);
    case "c":
    case "h":
      return StreamLanguage.define(c);
    case "cpp":
    case "cc":
    case "hpp":
      return StreamLanguage.define(cpp);
    case "go":
      return StreamLanguage.define(go);
    case "rs":
      return StreamLanguage.define(rust);
    case "sh":
    case "bash":
      return StreamLanguage.define(shell);
    case "ps1":
    case "psm1":
      return StreamLanguage.define(powerShell);
    default:
      return [];
  }
}
