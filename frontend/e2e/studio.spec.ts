import { expect, test } from "@playwright/test";
import { loginAndEnter } from "./helpers";

test("Studio: editar, ver la vista previa en vivo, commit y consola", async ({ page }) => {
  await loginAndEnter(page, "socio1");
  await page
    .getByRole("complementary", { name: "Navegación principal" })
    .getByRole("link", { name: "Desarrollo" })
    .click();
  await page.getByRole("button", { name: "Nuevo proyecto de código" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Nombre").fill("Sitio E2E");
  await dialog.getByLabel("Plantilla", { exact: true }).selectOption("static");
  await dialog.getByRole("button", { name: "Crear" }).click();
  await expect(page.getByRole("heading", { name: "Sitio E2E" })).toBeVisible();

  // Vista previa servida desde el origen separado (puerto de preview) dentro del iframe
  const frame = page.frameLocator('[data-testid="preview-frame"]');
  await expect(frame.getByRole("heading", { name: "Sitio estático de WADIMA" })).toBeVisible({
    timeout: 15000,
  });

  // Edición con autoguardado: la vista previa se recarga sola
  await page.getByRole("button", { name: "index.html" }).click();
  const editor = page.locator(".cm-content");
  await expect(editor).toContainText("Sitio estático de WADIMA");
  await page.getByText("Sitio estático de WADIMA").first().dblclick();
  await editor.click();
  await page.keyboard.press("Control+f");
  await page.keyboard.press("Escape");
  // Reemplazar el título escribiendo al final del documento un nuevo encabezado visible
  await editor.click();
  await page.keyboard.press("Control+End");
  await page.keyboard.type('\n<p id="e2e">Editado desde Playwright</p>');
  await expect(page.getByText("Guardado", { exact: true })).toBeVisible({ timeout: 15000 });
  await expect(frame.locator("#e2e")).toHaveText("Editado desde Playwright", { timeout: 15000 });

  // Git: commit con autoría
  await page.getByRole("tab", { name: "Git" }).click();
  await page.getByLabel("Mensaje del commit").fill("Párrafo añadido en E2E");
  await page.getByRole("button", { name: "Commit" }).click();
  await expect(page.getByText("Párrafo añadido en E2E")).toBeVisible();
  await expect(page.getByText(/Ana \(E2E\) ·/).first()).toBeVisible();

  // Consola restringida con salida en vivo
  await page.getByRole("tab", { name: "Consola" }).click();
  await page.getByLabel("Comando").fill("node -e \"console.log('hola consola')\"");
  await page.getByRole("button", { name: "Ejecutar" }).click();
  await expect(page.getByTestId("console-output")).toContainText("hola consola", { timeout: 20000 });
  await page.getByLabel("Comando").fill("cmd /c dir");
  await page.getByRole("button", { name: "Ejecutar" }).click();
  await expect(page.getByText(/Programa no permitido/)).toBeVisible();
});
