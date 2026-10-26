import { expect, test } from "@playwright/test";
import { loginAndEnter, newPartnerPage } from "./helpers";

test("proyecto, tareas, notas con autosave y conflicto entre dos socios", async ({ page, browser }) => {
  await loginAndEnter(page, "socio1");
  await page.getByRole("link", { name: "Proyectos" }).click();
  await page.getByRole("button", { name: "Nuevo proyecto" }).click();
  await page.getByLabel("Nombre").fill("Portal E2E");
  await page.getByLabel("Descripción").fill("Proyecto creado por la prueba E2E");
  await page.getByRole("button", { name: "Crear proyecto" }).click();
  await expect(page.getByRole("heading", { name: /Portal E2E/ })).toBeVisible();

  // Tarea y cambio de estado
  await page.getByLabel("Título de la tarea").fill("Preparar maqueta");
  await page.getByRole("button", { name: "Añadir" }).click();
  await expect(page.getByRole("button", { name: "Preparar maqueta" })).toBeVisible();
  await page.getByLabel("Estado de Preparar maqueta").selectOption("in_progress");
  await page.getByRole("tab", { name: "Actividad" }).click();
  await expect(page.getByText("Inició la tarea «Preparar maqueta»")).toBeVisible();

  // Documento Markdown con autosave
  await page.getByRole("tab", { name: "Documentos" }).click();
  await page.getByLabel("Título del documento").fill("Especificación E2E");
  await page.getByRole("button", { name: "Crear documento" }).click();
  const editor = page.locator(".cm-content");
  await expect(editor).toBeVisible();
  await editor.click();
  await page.keyboard.press("Control+End");
  await page.keyboard.type("Primera línea de socio1\n");
  await expect(page.getByTestId("save-state")).toHaveText("Guardado", { timeout: 15000 });
  const url = page.url();

  // Segundo socio abre el mismo documento
  const bruno = await newPartnerPage(browser, "socio2");
  await bruno.goto(url);
  await expect(bruno.locator(".cm-content")).toContainText("Primera línea de socio1");

  // Ambos editan la misma línea casi a la vez -> conflicto para el segundo
  await bruno.locator(".cm-content").click();
  await bruno.keyboard.press("Control+End");
  await bruno.keyboard.type("Texto de Bruno");
  await editor.click();
  await page.keyboard.press("Control+End");
  await page.keyboard.type("Texto de Ana");
  await expect(page.getByTestId("save-state")).toHaveText(/Guardado|Conflicto/, { timeout: 15000 });
  await expect(bruno.getByTestId("save-state")).toHaveText(/Guardado|Conflicto/, { timeout: 15000 });
  const states = [
    await page.getByTestId("save-state").textContent(),
    await bruno.getByTestId("save-state").textContent(),
  ];
  expect(states).toContain("Conflicto de edición");
  const loser = states[0] === "Conflicto de edición" ? page : bruno;
  await expect(loser.getByRole("dialog", { name: "Conflicto de edición" })).toBeVisible();
  await loser.getByRole("button", { name: "Guardar mi versión" }).click();
  await expect(loser.getByTestId("save-state")).toHaveText("Guardado", { timeout: 15000 });

  // Historial con varias versiones
  await page.getByRole("button", { name: "Historial" }).click();
  await expect(
    page.getByRole("dialog", { name: "Historial de versiones" }).getByRole("button", { name: "v1" }),
  ).toBeVisible();
  await bruno.context().close();
});
