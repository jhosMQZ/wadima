// Reunión de audio real entre dos navegadores (WebRTC) con el micrófono simulado de Chrome.
import { expect, test, type Browser, type Page } from "@playwright/test";
import { loginAndEnter } from "./helpers";

test.use({
  launchOptions: {
    args: [
      "--use-fake-ui-for-media-stream",
      "--use-fake-device-for-media-stream",
      // Candidatos ICE con IP local (sin mDNS) para que los dos navegadores de la prueba se encuentren.
      "--disable-features=WebRtcHideLocalIpsWithMdns",
    ],
  },
});

// Cuenta las peticiones de micrófono para comprobar que solo se pide al activarlo.
const COUNT_GUM = `
  window.__gum = 0;
  const md = navigator.mediaDevices;
  if (md && md.getUserMedia) {
    const orig = md.getUserMedia.bind(md);
    md.getUserMedia = (c) => { window.__gum++; return orig(c); };
  }`;

async function partner(browser: Browser, user: string, script = COUNT_GUM): Promise<Page> {
  const ctx = await browser.newContext({ permissions: ["microphone"] });
  await ctx.addInitScript(script);
  const page = await ctx.newPage();
  await loginAndEnter(page, user);
  await page.goto("/meetings");
  await expect(page.getByTestId("meeting-room")).toBeVisible();
  return page;
}

const gum = (p: Page) => p.evaluate("window.__gum") as Promise<number>;

test("audio entre dos socios: entrar, micrófono, indicador de voz, silenciar y salir", async ({
  browser,
}) => {
  const ana = await partner(browser, "socio1");
  const bruno = await partner(browser, "socio2");

  await ana.getByTestId("meeting-join").click();
  await expect(ana.getByTestId("meeting-room")).toHaveAttribute("data-status", "joined");
  expect(await gum(ana)).toBe(0); // entrar no pide el micrófono
  await bruno.getByTestId("meeting-join").click();
  await expect(bruno.getByTestId("meeting-room")).toHaveAttribute("data-status", "joined");

  const brunoInAna = ana.getByTestId("peer-remote").filter({ hasText: "Bruno (E2E)" });
  const anaInBruno = bruno.getByTestId("peer-remote").filter({ hasText: "Ana (E2E)" });
  await expect(brunoInAna).toHaveAttribute("data-connection", "connected", { timeout: 20_000 });
  await expect(anaInBruno).toHaveAttribute("data-connection", "connected", { timeout: 20_000 });
  await expect(anaInBruno).toContainText("micrófono apagado");

  // Ana activa el micrófono: se pide permiso en ese momento y Bruno lo ve activo y la oye hablar.
  await ana.getByTestId("mic-toggle").click();
  await expect(ana.getByTestId("mic-toggle")).toHaveAttribute("aria-pressed", "true");
  expect(await gum(ana)).toBe(1);
  await expect(ana.getByLabel("Dispositivo de entrada")).toBeVisible();
  await expect(anaInBruno).toContainText(/micrófono activo|hablando/);
  // El micrófono simulado emite un pitido periódico: el indicador de voz se activa con el audio recibido.
  await expect(anaInBruno).toHaveClass(/speaking/, { timeout: 30_000 });

  // Volumen local y silencio
  await anaInBruno.getByLabel("Volumen de Ana (E2E)").fill("0.5");
  await ana.getByTestId("mic-toggle").click();
  await expect(anaInBruno).toContainText("micrófono apagado");

  // Presencia: los dos aparecen en reunión en la oficina.
  await ana.goto("/");
  await expect(ana.getByRole("button", { name: /Bruno \(E2E\).*En reunión/ })).toBeVisible();

  // Bruno sale sin cerrar la aplicación.
  await bruno.getByTestId("meeting-leave").click();
  await expect(bruno.getByTestId("meeting-room")).toHaveAttribute("data-status", "idle");
  await expect(bruno.getByRole("heading", { name: "Sala de reuniones" })).toBeVisible();
  await ana.context().close();
  await bruno.context().close();
});

test("permiso de micrófono denegado: error comprensible y la reunión sigue", async ({ browser }) => {
  const deny = `
    navigator.mediaDevices.getUserMedia = () =>
      Promise.reject(new DOMException("Permission denied", "NotAllowedError"));`;
  const carla = await partner(browser, "socio3", deny);
  await carla.getByTestId("meeting-join").click();
  await expect(carla.getByTestId("meeting-room")).toHaveAttribute("data-status", "joined");
  await carla.getByTestId("mic-toggle").click();
  await expect(carla.getByTestId("mic-error")).toContainText("Permiso de micrófono denegado");
  await expect(carla.getByTestId("meeting-room")).toHaveAttribute("data-status", "joined");
  await carla.getByTestId("meeting-leave").click();
  await carla.context().close();
});

test("pérdida de conexión: aviso, reconexión y vuelta a la sala", async ({ browser }) => {
  test.setTimeout(180_000);
  const ana = await partner(browser, "socio1");
  await ana.getByTestId("meeting-join").click();
  await expect(ana.getByTestId("meeting-room")).toHaveAttribute("data-status", "joined");
  await ana.context().setOffline(true);
  await expect(ana.getByTestId("meeting-room")).toHaveAttribute("data-status", "reconnecting", {
    timeout: 60_000,
  });
  await expect(ana.getByRole("alert").filter({ hasText: "Reconectando" }).first()).toBeVisible();
  await ana.context().setOffline(false);
  await expect(ana.getByTestId("meeting-room")).toHaveAttribute("data-status", "joined", { timeout: 60_000 });
  await ana.context().close();
});
