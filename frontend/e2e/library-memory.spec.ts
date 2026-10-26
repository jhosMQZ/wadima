import { expect, test } from "@playwright/test";
import { loginAndEnter } from "./helpers";

const DOC = `# Manual de operaciones

## Datos clave

Presupuesto: 9000 EUR
Responsable: Bruno

## Procedimiento de copia de seguridad

1. Detener los servicios.
2. Ejecutar la copia de la base de datos.
3. Verificar el archivo generado.
`;

test("subida, conversión, visores, búsqueda y memoria con referencias", async ({ page }) => {
  await loginAndEnter(page, "socio1");
  await page.getByRole("link", { name: "Biblioteca" }).click();
  await expect(page.getByRole("button", { name: "Elegir archivos" })).toBeEnabled();
  await page
    .getByTestId("file-input")
    .setInputFiles({ name: "manual.md", mimeType: "text/markdown", buffer: Buffer.from(DOC) });
  await expect(page.getByText("subido")).toBeVisible();
  const row = page.getByRole("row", { name: /manual\.md/ });
  await expect(row.getByText("indexado")).toBeVisible({ timeout: 30000 });
  await row.getByRole("link", { name: "manual.md" }).click();

  await page.getByRole("tab", { name: "Markdown" }).click();
  await expect(page.getByRole("heading", { name: "Manual de operaciones" })).toBeVisible();
  await page.getByRole("tab", { name: "Diferencias" }).click();
  await expect(page.getByLabel("Diferencias")).toBeVisible();
  await page.getByRole("tab", { name: "Fragmentos" }).click();
  await expect(page.getByText(/fragmentos · ~/)).toBeVisible();
  await page.getByRole("tab", { name: "Informe de extracción" }).click();
  await expect(page.getByText("Sin problemas detectados.")).toBeVisible();

  // Búsqueda de texto completo
  await page.getByRole("navigation").getByRole("link", { name: "Biblioteca" }).click();
  await page.getByLabel("Buscar en documentos").fill("copia de seguridad");
  await page.getByRole("button", { name: "Buscar" }).click();
  await expect(page.locator("mark").first()).toBeVisible();

  // Memoria compartida (el Memory Agent procesa en segundo plano)
  await page.getByRole("link", { name: "Memoria compartida" }).click();
  await page.getByLabel("Consulta").fill("presupuesto responsable");
  await expect
    .poll(
      async () => {
        await page.getByRole("button", { name: "Consultar" }).click();
        await page.waitForTimeout(800);
        return page.getByRole("link", { name: /Presupuesto: 9000 EUR|manual\.md/ }).count();
      },
      { timeout: 40000, intervals: [1500] },
    )
    .toBeGreaterThan(0);
  await page.getByRole("tab", { name: "Explorar y revisar" }).click();
  await expect(page.getByRole("button", { name: "Presupuesto: 9000 EUR" })).toBeVisible({ timeout: 20000 });
  await page.getByRole("button", { name: "Presupuesto: 9000 EUR" }).click();
  await expect(page.getByRole("dialog").getByText("inferido")).toBeVisible();
});

test("conexiones: Drive sin credenciales y Claude sin cuenta no aparecen como conectados", async ({
  page,
}) => {
  await loginAndEnter(page, "socio1");
  const nav = page.getByRole("complementary", { name: "Navegación principal" });
  await nav.getByRole("link", { name: "Servidores e integraciones" }).click();
  await expect(page.getByText("Falta configurar las credenciales OAuth de Google")).toBeVisible();
  await expect(page.getByRole("button", { name: "Conectar Google Drive" })).toBeDisabled();
  await expect(page.getByRole("link", { name: /Drive no configurado/ })).toBeVisible();
  await page.getByRole("tab", { name: "Claude" }).click();
  await expect(page.getByText("sin configurar").first()).toBeVisible();
  await nav.getByRole("link", { name: "Laboratorio de IA" }).click();
  await expect(page.getByText("Conecte su propia cuenta de Claude")).toBeVisible();
});
