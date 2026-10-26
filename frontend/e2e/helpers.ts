import { expect, type Browser, type BrowserContextOptions, type Page } from "@playwright/test";

export const PASSWORD = process.env.WADIMA_E2E_PASSWORD ?? "E2e-Prueba-2026!";

export async function login(page: Page, username: string, password = PASSWORD) {
  await page.goto("/");
  await page.getByLabel("Usuario").fill(username);
  await page.getByLabel("Contraseña").fill(password);
  await page.getByRole("button", { name: "Entrar", exact: true }).click();
}

/**
 * Pasa la pantalla de acceso a la oficina. Con `clockIn` registra la entrada; sin él entra sin registrarla (o sin
 * tocar una jornada que ya estuviera abierta o pendiente de otro test).
 */
export async function enterOffice(page: Page, { clockIn = false } = {}) {
  const gate = page.getByTestId("office-gate");
  const heading = page.getByRole("heading", { name: "Oficina virtual" });
  await expect(gate.or(heading)).toBeVisible();
  if (await gate.isVisible()) {
    const register = page.getByTestId("clock-in");
    if (clockIn && (await register.isVisible())) {
      await register.click();
      await expect(page.getByTestId("clock-in-confirmation")).toBeVisible();
      await page.getByRole("button", { name: "Entrar a la oficina" }).click();
    } else {
      await page
        .getByRole("button", {
          name: /Ver la oficina sin registrar entrada|Entrar sin cambiar nada|Entrar a la oficina/,
        })
        .click();
    }
  }
  await expect(heading).toBeVisible();
}

export async function loginAndEnter(page: Page, username: string, opts: { clockIn?: boolean } = {}) {
  await login(page, username);
  await enterOffice(page, opts);
}

export async function newPartnerPage(
  browser: Browser,
  username: string,
  opts: { clockIn?: boolean; context?: BrowserContextOptions } = {},
) {
  const context = await browser.newContext(opts.context);
  const page = await context.newPage();
  await loginAndEnter(page, username, opts);
  return page;
}

/** Llamada a la API con la sesión y el token CSRF de la página. */
export async function apiCall(
  page: Page,
  method: string,
  path: string,
  body?: unknown,
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
): Promise<{ status: number; body: any }> {
  return page.evaluate(
    async ([m, p, b]) => {
      const me = (await (await fetch("/api/auth/me")).json()) as { csrf_token: string };
      const res = await fetch(p as string, {
        method: m as string,
        headers: { "Content-Type": "application/json", "X-CSRF-Token": me.csrf_token },
        body: b === undefined ? undefined : JSON.stringify(b),
      });
      return { status: res.status, body: await res.json().catch(() => null) };
    },
    [method, path, body] as const,
  );
}
