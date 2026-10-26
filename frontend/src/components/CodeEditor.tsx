import {
  autocompletion,
  closeBrackets,
  closeBracketsKeymap,
  completionKeymap,
} from "@codemirror/autocomplete";
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import {
  bracketMatching,
  foldGutter,
  foldKeymap,
  indentOnInput,
  syntaxHighlighting,
  defaultHighlightStyle,
} from "@codemirror/language";
import { linter, lintGutter, type Diagnostic } from "@codemirror/lint";
import { highlightSelectionMatches, searchKeymap } from "@codemirror/search";
import { Compartment, EditorState, type Extension } from "@codemirror/state";
import { oneDark } from "@codemirror/theme-one-dark";
import {
  drawSelection,
  EditorView,
  highlightActiveLine,
  highlightActiveLineGutter,
  keymap,
  lineNumbers,
} from "@codemirror/view";
import { useEffect, useRef } from "react";
import { languageFor } from "../lib/languages";

/** Validación ligera en el cliente para JSON (los errores de compilación reales llegan del servidor). */
function jsonLinter(view: EditorView): Diagnostic[] {
  const text = view.state.doc.toString();
  if (!text.trim()) return [];
  try {
    JSON.parse(text);
    return [];
  } catch (err) {
    const m = /position (\d+)/.exec((err as Error).message);
    const pos = m ? Math.min(Number(m[1]), text.length) : 0;
    return [
      { from: pos, to: Math.min(pos + 1, text.length), severity: "error", message: (err as Error).message },
    ];
  }
}

interface Props {
  value: string;
  path: string;
  onChange?: (value: string) => void;
  onSave?: () => void;
  readOnly?: boolean;
  /** Clave que fuerza recrear el estado (cambio de archivo). */
  docKey?: string;
}

export function CodeEditor({ value, path, onChange, onSave, readOnly = false, docKey }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const onChangeRef = useRef(onChange);
  const onSaveRef = useRef(onSave);
  const readOnlyComp = useRef(new Compartment());
  useEffect(() => {
    onChangeRef.current = onChange;
    onSaveRef.current = onSave;
  });

  useEffect(() => {
    const extensions: Extension[] = [
      lineNumbers(),
      highlightActiveLineGutter(),
      foldGutter(),
      drawSelection(),
      history(),
      indentOnInput(),
      bracketMatching(),
      closeBrackets(),
      autocompletion(),
      highlightActiveLine(),
      highlightSelectionMatches(),
      syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
      lintGutter(),
      oneDark,
      EditorView.lineWrapping,
      languageFor(path),
      readOnlyComp.current.of(EditorState.readOnly.of(readOnly)),
      keymap.of([
        {
          key: "Mod-s",
          preventDefault: true,
          run: () => {
            onSaveRef.current?.();
            return true;
          },
        },
        indentWithTab,
        ...closeBracketsKeymap,
        ...defaultKeymap,
        ...searchKeymap,
        ...historyKeymap,
        ...foldKeymap,
        ...completionKeymap,
      ]),
      EditorView.updateListener.of((u) => {
        if (u.docChanged) onChangeRef.current?.(u.state.doc.toString());
      }),
    ];
    if (path.endsWith(".json")) extensions.push(linter(jsonLinter));
    const v = new EditorView({
      parent: host.current!,
      state: EditorState.create({ doc: value, extensions }),
    });
    view.current = v;
    return () => {
      v.destroy();
      view.current = null;
    };
    // Se recrea solo al cambiar de documento; los cambios de `value` externos se aplican abajo.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docKey ?? path]);

  useEffect(() => {
    const v = view.current;
    if (v && v.state.doc.toString() !== value) {
      v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: value } });
    }
  }, [value]);

  useEffect(() => {
    view.current?.dispatch({ effects: readOnlyComp.current.reconfigure(EditorState.readOnly.of(readOnly)) });
  }, [readOnly]);

  return <div className="cm-host" ref={host} />;
}
