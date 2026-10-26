// Cada módulo en escritorio y móvil: sin errores de JavaScript o consola y sin desbordes horizontales.
// Las capturas quedan en test-results/visual para la revisión visual.
import { expect, test } from "@playwright/test";
import { loginAndEnter } from "./helpers";

const OUT = process.env.VISUAL_OUT ?? "test-results/visual";
const PAGES = [
  ["oficina", "/"],
  ["operaciones", "/ops"],
  ["proyectos", "/projects"],
  ["biblioteca", "/library"],
  ["memoria", "/memory"],
  ["agentes", "/agents"],
  ["reuniones", "/meetings"],
  ["studio", "/studio"],
  ["conexiones", "/connections"],
  ["areas", "/areas"],
  ["seguridad", "/security"],
  ["configuracion", "/settings"],
  ["admin", "/admin"],
  ["control-horario", "/time"],
  ["totales", "/time?tab=totals"],
  ["configuracion-ia", "/ai"],
  ["asistente", "/setup"],
];

for (const [label, width, height] of [
  ["escritorio", 1440, 900],
  ["movil", 390, 844],
] as const) {
  test(`módulos sin errores ni desbordes (${label})`, async ({ page }) => {
    const errors: string[] = [];
    await page.setViewportSize({ width, height });
    await loginAndEnter(page, "socio1"); // antes de entrar, GET /api/auth/me responde 401 (esperado)
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
    for (const [name, path] of PAGES) {
      await page.goto(path);
      await page.waitForLoadState("networkidle");
      await page.waitForTimeout(400);
      const overflow = Number(
        await page.evaluate(
          "Math.max(document.documentElement.scrollWidth - document.documentElement.clientWidth, " +
            "(m => m.scrollWidth - m.clientWidth)(document.querySelector('main.content')))",
        ),
      );
      if (overflow > 1) errors.push(`${name}: desborde horizontal de ${overflow}px`);
      await page.screenshot({ path: `${OUT}/${label}-${name}.png`, fullPage: false });
    }
    expect(errors, errors.join("\n")).toEqual([]);
  });
}

test("menú plegable en móvil", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await loginAndEnter(page, "socio1");
  const nav = page.getByRole("complementary", { name: "Navegación principal" });
  await expect(nav).toBeHidden();
  await page.getByRole("button", { name: "Menú" }).click();
  await expect(nav).toBeVisible();
  await nav.getByRole("link", { name: "Biblioteca" }).click();
  await expect(page.getByRole("heading", { name: "Biblioteca documental" })).toBeVisible();
  await expect(nav).toBeHidden();
});
