import { expect, test } from "@playwright/test";
import { login, loginAndEnter, newPartnerPage } from "./helpers";

test("acceso y oficina compacta con estados reales", async ({ page, browser }) => {
  await loginAndEnter(page, "socio1");
  const canvas = page.getByTestId("office-canvas").locator("canvas");
  await expect(canvas).toBeVisible();
  // Siete salas con etiquetas legibles (HTML sobre el mapa) que abren su panel.
  for (const key of ["ops", "dev", "library", "ai", "meeting", "admin", "servers"])
    await expect(page.getByTestId(`room-${key}`)).toBeVisible();
  // El listado accesible refleja a los tres socios; solo socio1 está conectado.
  const people = page.getByRole("complementary").getByRole("button", { name: /\(E2E\)/ });
  await expect(people).toHaveCount(4); // los tres socios + la cuenta "nuevo" de pruebas
  await expect(page.getByRole("button", { name: /Ana \(E2E\).*Conectado/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /Bruno \(E2E\).*Desconectado/ })).toBeVisible();

  // Un segundo socio se conecta en otro navegador: la presencia cambia en tiempo real.
  const bruno = await newPartnerPage(browser, "socio2");
  await expect(page.getByRole("button", { name: /Bruno \(E2E\).*Conectado/ })).toBeVisible();
  await bruno.getByLabel("Mi estado declarado").selectOption("meeting");
  await expect(page.getByRole("button", { name: /Bruno \(E2E\).*En reunión/ })).toBeVisible();

  // Panel lateral de una persona con información real
  await page
    .getByRole("button", { name: /Bruno \(E2E\)/ })
    .first()
    .click();
  const panel = page.getByTestId("person-panel");
  await expect(panel.getByRole("heading", { name: "Bruno (E2E)" })).toBeVisible();
  await expect(panel.getByText("Última actividad")).toBeVisible();
  await expect(panel.getByTestId("person-clock-in")).toHaveText("sin entrada registrada");
  // La etiqueta del personaje en el mapa también abre el panel.
  await page.getByTestId("tag-socio1").click();
  await expect(panel.getByRole("heading", { name: "Ana (E2E)" })).toBeVisible();

  // Selección de sala desde la etiqueta del mapa y apertura del módulo
  await page.getByTestId("room-library").click();
  await expect(
    page.getByTestId("room-panel").getByRole("heading", { name: "Biblioteca documental" }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Abrir módulo" })).toBeVisible();
  // La sala de servidores muestra el estado real de cada integración.
  await page.getByTestId("room-servers").click();
  await expect(page.getByTestId("room-panel").getByText("Google Drive", { exact: true })).toBeVisible();
  await expect(page.getByTestId("room-panel").getByText("Sin credenciales de Google")).toBeVisible();
  await page.screenshot({ path: "test-results/office.png", fullPage: false });
  await bruno.context().close();
});

test("clic en el mapa abre el panel de la sala", async ({ page }) => {
  await loginAndEnter(page, "socio1");
  const canvas = page.getByTestId("office-canvas").locator("canvas");
  await expect(canvas).toBeVisible();
  const box = (await canvas.boundingBox())!;
  // Centro de la sala de reuniones (casillas 27-38 × 10-17 de un mapa de 40×27)
  await page.mouse.click(box.x + box.width * (33 / 40), box.y + box.height * (14.5 / 27));
  await expect(
    page.getByTestId("room-panel").getByRole("heading", { name: "Sala de reuniones" }),
  ).toBeVisible();
  // Doble clic abre el módulo de la sala
  await page.mouse.dblclick(box.x + box.width * (7 / 40), box.y + box.height * (22.5 / 27));
  await expect(page).toHaveURL(/\/time$/);
});

test("la oficina en reposo no consume CPU de forma continua", async ({ page }) => {
  await loginAndEnter(page, "socio1");
  await expect(page.getByTestId("office-canvas").locator("canvas")).toBeVisible();
  await page.waitForTimeout(3000); // termina la animación de llegada
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Performance.enable");
  const metric = async () =>
    ((await cdp.send("Performance.getMetrics")).metrics.find((m) => m.name === "TaskDuration")?.value ??
      0) as number;
  const before = await metric();
  await page.waitForTimeout(5000);
  const busy = (await metric()) - before;
  // Con el bucle dormido solo quedan animaciones discretas (≈2 cuadros por segundo).
  expect(busy / 5, `ocupación del hilo principal: ${((busy / 5) * 100).toFixed(1)} %`).toBeLessThan(0.15);
});

test("usuario nuevo debe cambiar la contraseña antes de entrar", async ({ page }) => {
  await login(page, "nuevo");
  await expect(page.getByRole("heading", { name: "Cambie su contraseña" })).toBeVisible();
});

test("credenciales erróneas", async ({ page }) => {
  await login(page, "socio1", "incorrecta");
  await expect(page.getByRole("alert")).toContainText("incorrectos");
});
