// Formateo con Prettier en el navegador; se carga bajo demanda para no aumentar el paquete inicial.

const PARSERS: Record<string, string> = {
  js: "babel",
  mjs: "babel",
  cjs: "babel",
  jsx: "babel",
  ts: "typescript",
  tsx: "typescript",
  json: "json",
  css: "css",
  scss: "scss",
  html: "html",
  htm: "html",
  vue: "vue",
  md: "markdown",
  markdown: "markdown",
  yml: "yaml",
  yaml: "yaml",
};

export function canFormat(path: string): boolean {
  return Boolean(PARSERS[path.split(".").pop()?.toLowerCase() ?? ""]);
}

export async function formatCode(path: string, source: string): Promise<string> {
  const parser = PARSERS[path.split(".").pop()?.toLowerCase() ?? ""];
  if (!parser) throw new Error("No hay formateador para este tipo de archivo");
  const [prettier, babel, estree, typescript, postcss, html, markdown, yaml] = await Promise.all([
    import("prettier/standalone"),
    import("prettier/plugins/babel"),
    import("prettier/plugins/estree"),
    import("prettier/plugins/typescript"),
    import("prettier/plugins/postcss"),
    import("prettier/plugins/html"),
    import("prettier/plugins/markdown"),
    import("prettier/plugins/yaml"),
  ]);
  return prettier.format(source, {
    parser,
    plugins: [babel, estree, typescript, postcss, html, markdown, yaml],
    printWidth: 100,
  });
}
