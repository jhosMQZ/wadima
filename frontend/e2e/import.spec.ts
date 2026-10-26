// Trabajar con archivos propios: importar una carpeta como proyecto de Studio, subir más archivos, subir una
// carpeta de documentos a la Biblioteca, y zoom de la oficina al entrar en una sala.
import { expect, test } from "@playwright/test";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { apiCall, loginAndEnter } from "./helpers";

function makeTree(files: Record<string, string>): string {
  const root = mkdtempSync(join(tmpdir(), "wadima-e2e-"));
  for (const [rel, content] of Object.entries(files)) {
    const full = join(root, ...rel.split("/"));
    mkdirSync(join(full, ".."), { recursive: true });
    writeFileSync(full, content);
  }
  return root;
}

test("importar una carpeta propia como proyecto de Studio y subir más archivos", async ({ page }) => {
  const tmp = makeTree({
    "mi-web/index.html": "<!doctype html><h1 id='t'>Mi web propia</h1>",
    "mi-web/css/estilo.css": "h1 { color: red; }",
    "mi-web/node_modules/pesado/index.js": "// no se sube",
  });
  try {
    await loginAndEnter(page, "socio1");
    await page.goto("/studio");
    await page.getByRole("button", { name: "Nuevo proyecto de código" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Importar una carpeta de mi equipo").check();
    await dialog.getByTestId("import-folder").setInputFiles(join(tmp, "mi-web"));
    await expect(dialog).toContainText("2 archivo(s) para subir");
    await expect(dialog).toContainText("1 omitidos");
    await expect(dialog.getByLabel("Nombre")).toHaveValue("mi-web");
    await dialog.getByRole("button", { name: "Crear e importar" }).click();
    await expect(page.getByRole("heading", { name: "mi-web" })).toBeVisible();
    await expect(page.getByText("2 archivo(s) subidos")).toBeVisible();
    const tree = page.locator(".file-tree");
    await expect(tree).toContainText("index.html");
    await expect(tree).toContainText("estilo.css");
    await expect(tree).not.toContainText("node_modules");
    // Se detectó una web estática y quedó una versión inicial en Git.
    const id = page.url().split("/").at(-1)!;
    const log = await apiCall(page, "GET", `/api/studio/projects/${id}/git/log`);
    expect(log.body[0].message).toBe("Importar proyecto «mi-web»");
    const project = await apiCall(page, "GET", `/api/studio/projects/${id}`);
    expect(project.body.template).toBe("static");

    // Subir más archivos a una subcarpeta del proyecto ya creado.
    await page.getByRole("button", { name: "Subir", exact: true }).click();
    const upload = page.getByTestId("studio-upload");
    await upload.getByTestId("upload-files").setInputFiles({
      name: "notas.md",
      mimeType: "text/markdown",
      buffer: Buffer.from("# Notas del proyecto\n"),
    });
    await upload.getByLabel("Carpeta de destino dentro del proyecto (vacío = raíz)").fill("docs");
    await upload.getByRole("button", { name: "Subir", exact: true }).click();
    await expect(upload).toContainText("1 archivo(s) subidos");
    await upload.getByRole("button", { name: "Cerrar" }).click();
    await expect(tree).toContainText("notas.md");
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
});

test("subir una carpeta de documentos a la Biblioteca", async ({ page }) => {
  const tmp = makeTree({
    "Documentacion/contratos/acuerdo.md": "# Acuerdo de socios\n\nVigente desde 2026.\n",
    "Documentacion/manual.txt": "Manual de uso interno",
    "Documentacion/instalador.exe": "MZ binario no admitido",
  });
  try {
    await loginAndEnter(page, "socio1");
    await page.goto("/library");
    await page.getByLabel("Área").selectOption({ label: "General" });
    await page.getByTestId("folder-input").setInputFiles(join(tmp, "Documentacion"));
    await expect(page.getByTestId("batch-status")).toContainText("Terminado: 2 procesados", {
      timeout: 30_000,
    });
    await expect(page.getByTestId("batch-status")).toContainText("2 subidos · 1 omitidos · 0 con error");
    const docs = await apiCall(page, "GET", "/api/library/documents");
    const acuerdo = (docs.body as { title: string; tags: string[] }[]).find((d) =>
      d.title.startsWith("acuerdo"),
    );
    expect(acuerdo?.tags).toEqual(expect.arrayContaining(["documentacion", "contratos"]));
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
});

test("la oficina se acerca a la sala a la que va el socio", async ({ page }) => {
  await loginAndEnter(page, "socio1");
  await expect(page.getByTestId("zoomed-room")).toHaveCount(0); // al entrar: vista completa
  await page.getByTestId("room-meeting").click();
  await page.getByRole("button", { name: "Ir con mi avatar" }).click();
  await expect(page.getByTestId("zoomed-room")).toHaveText("Viendo: Sala de reuniones");
  await page.waitForTimeout(1500); // termina la animación y el avatar llega a la sala
  await page.screenshot({ path: "test-results/visual/escritorio-oficina-zoom.png" });
  // La etiqueta de la sala sigue visible y dentro del mapa con el zoom aplicado.
  const stage = (await page.getByTestId("office-canvas").boundingBox())!;
  const label = (await page.getByTestId("room-meeting").boundingBox())!;
  expect(label.x).toBeGreaterThanOrEqual(stage.x);
  expect(label.y).toBeGreaterThanOrEqual(stage.y);
  expect(label.x).toBeLessThan(stage.x + stage.width / 3); // la sala ocupa ahora casi todo el mapa
  await page.getByTestId("reset-view").click();
  await expect(page.getByTestId("zoomed-room")).toHaveCount(0);
  // Se puede desactivar
  await page.getByTestId("auto-zoom").uncheck();
  await page.getByTestId("room-library").click();
  await page.getByRole("button", { name: "Ir con mi avatar" }).click();
  await page.waitForTimeout(800);
  await expect(page.getByTestId("zoomed-room")).toHaveCount(0);
  await page.getByTestId("auto-zoom").check();
});
