import { expect, test, type Page } from "@playwright/test";
import { apiCall, loginAndEnter, newPartnerPage } from "./helpers";

// PNG 1×1 válido (el servidor comprueba la firma, no la extensión).
const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
  "base64",
);

async function setIdentity(page: Page, values: Record<string, string>) {
  const form = page.getByTestId("org-identity");
  for (const [label, value] of Object.entries(values))
    await form.getByLabel(label, { exact: true }).fill(value);
  await form.getByRole("button", { name: "Guardar identidad y políticas" }).click();
  await expect(page.getByText("Identidad de la organización guardada")).toBeVisible();
}

test("el nombre de la plataforma se cambia sin tocar el código y llega a todos los módulos", async ({
  page,
  browser,
}) => {
  await loginAndEnter(page, "socio1");
  const bruno = await newPartnerPage(browser, "socio2");
  await page.goto("/settings");
  await setIdentity(page, {
    "Nombre visible de la plataforma": "VANTIA",
    "Nombre de la organización (razón social)": "VANTIA GROUP",
    "Iniciales o símbolo (1–4 caracteres)": "VG",
    "Color principal (hexadecimal)": "#e4572e",
  });
  await expect(page.getByTestId("brand")).toContainText("VANTIA");
  await expect(page).toHaveTitle(/VANTIA/);
  // Otro socio lo recibe en vivo (evento org.updated), sin recargar.
  await expect(bruno.getByTestId("brand")).toContainText("VANTIA");

  // Oficina, salas, reuniones y control horario
  await page.goto("/");
  await expect(page.getByText("VANTIA GROUP: estados reales")).toBeVisible();
  await page.getByTestId("room-meeting").click();
  await expect(page.getByTestId("room-panel")).toContainText("VANTIA ·");
  await page.goto("/meetings");
  await expect(page.getByText(/VANTIA GROUP: audio en tiempo real/)).toBeVisible();
  // Documento generado: exportación del control horario con la razón social.
  const csv = await page.request.get("/api/time/export?format=csv");
  expect(await csv.text()).toContain("VANTIA GROUP · Control horario");

  // Logotipo
  await page.goto("/settings");
  await page
    .getByLabel("Archivo de logotipo")
    .setInputFiles({ name: "logo.png", mimeType: "image/png", buffer: PNG });
  await expect(page.getByTestId("brand").locator("img.brand-logo")).toHaveAttribute(
    "src",
    /\/api\/org\/logo\?v=/,
  );

  // Persiste tras recargar y se ve en la pantalla de inicio de sesión.
  await page.reload();
  await expect(page.getByTestId("brand")).toContainText("VANTIA");
  await page.getByRole("button", { name: "Cerrar sesión" }).click();
  await expect(page.getByTestId("login-platform")).toHaveText("VANTIA");
  await expect(page.getByText("VANTIA GROUP")).toBeVisible();
  // Las rutas y la API no cambian.
  expect((await page.request.get("/api/health")).status()).toBe(200);

  // Restaurar la identidad por defecto para el resto de pruebas.
  await loginAndEnter(page, "socio1");
  await apiCall(page, "DELETE", "/api/org/logo");
  await page.goto("/settings");
  await setIdentity(page, {
    "Nombre visible de la plataforma": "WADIMA",
    "Nombre de la organización (razón social)": "WADIMA INDUSTRIE",
    "Iniciales o símbolo (1–4 caracteres)": "W",
    "Color principal (hexadecimal)": "#6fd3ff",
  });
  await expect(page.getByTestId("brand")).toContainText("WADIMA");
  await bruno.context().close();
});

test("un socio que no es administrador no puede cambiar la identidad", async ({ page }) => {
  await loginAndEnter(page, "socio2");
  await page.goto("/settings");
  await expect(page.getByText("Solo un administrador puede cambiar estos datos.")).toBeVisible();
  await expect(page.getByTestId("org-identity").getByLabel("Nombre visible de la plataforma")).toBeDisabled();
  const r = await apiCall(page, "GET", "/api/org");
  const put = await apiCall(page, "PUT", "/api/org", { ...r.body, platform_name: "Pirata" });
  expect(put.status).toBe(403);
});

test("asistente de configuración: áreas predefinidas habilitables sin duplicados", async ({ page }) => {
  await loginAndEnter(page, "socio1");
  await page.goto("/setup");
  const wizard = page.getByTestId("setup-wizard");
  await expect(wizard).toContainText("Paso 1 de 7");
  await wizard.getByLabel("Descripción de la organización").fill("Tres socios, una oficina.");
  for (let i = 0; i < 4; i++) await wizard.getByRole("button", { name: "Siguiente" }).click();
  await expect(wizard).toContainText("Paso 5 de 7: Áreas");
  await wizard.getByRole("checkbox", { name: /Servidores e integraciones/ }).uncheck();
  await wizard.getByRole("button", { name: "Siguiente" }).click();
  await expect(wizard).toContainText("Google Drive");
  await wizard.getByRole("button", { name: "Siguiente" }).click();
  await expect(wizard).toContainText("Configuración de IA");
  await wizard.getByTestId("setup-finish").click();
  await expect(page.getByRole("heading", { name: /Oficina virtual|Acceso a la oficina/ })).toBeVisible();
  const nav = page.getByRole("complementary", { name: "Navegación principal" });
  await expect(nav.getByRole("link", { name: "Servidores e integraciones" })).toHaveCount(0);

  // Se vuelve a habilitar desde Áreas; no se crea un área duplicada.
  await page.goto("/areas");
  const card = page
    .getByTestId("predefined-areas")
    .locator(".template-card", { hasText: "Servidores e integraciones" });
  await expect(card).toContainText("deshabilitada");
  await card.getByRole("button", { name: "Habilitar" }).click();
  await expect(card).toContainText("habilitada");
  await expect(nav.getByRole("link", { name: "Servidores e integraciones" })).toBeVisible();
  const areas = (await apiCall(page, "GET", "/api/areas")).body.areas as { template_key: string | null }[];
  expect(areas.filter((a) => a.template_key === "servers")).toHaveLength(1);
  // La razón social sigue siendo la misma y la descripción se guardó.
  const org = (await apiCall(page, "GET", "/api/org")).body;
  expect(org.description).toBe("Tres socios, una oficina.");
});
