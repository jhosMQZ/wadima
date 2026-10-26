import { expect, test } from "@playwright/test";
import { apiCall, enterOffice, login, loginAndEnter } from "./helpers";

test("entrada y salida con la hora del servidor, robot, historial, aprobación y exportación", async ({
  page,
  browser,
}) => {
  test.setTimeout(150_000); // dos navegadores y dos recorridos completos del robot
  await loginAndEnter(page, "socio1"); // Ana observa la oficina
  const ana = page;

  // Bruno tiene el reloj del equipo mal (año 2020): el registro oficial usa la hora del servidor.
  const ctx = await browser.newContext();
  const bruno = await ctx.newPage();
  await bruno.clock.setFixedTime(new Date("2020-01-01T08:00:00Z"));
  await login(bruno, "socio2");
  await expect(bruno.getByTestId("office-gate")).toBeVisible();
  await expect(bruno.getByTestId("server-clock")).toBeVisible();
  await bruno.getByTestId("clock-in").click();
  await expect(bruno.getByTestId("clock-in-confirmation")).toContainText("Entrada registrada a las");
  const status = (await apiCall(bruno, "GET", "/api/time/status")).body;
  const registered = new Date(status.open.clock_in_at).getTime();
  expect(Math.abs(registered - Date.now())).toBeLessThan(120_000); // hora real del servidor, no 2020
  await bruno.getByRole("button", { name: "Entrar a la oficina" }).click();
  await expect(bruno.getByTestId("clock-widget")).toContainText("En jornada");

  // El robot de la oficina de Ana representa el registro (evento real ya guardado).
  await expect(ana.getByTestId("robot-bubble")).toContainText("Bruno", { timeout: 15_000 });
  await expect(ana.getByTestId("robot-log")).toContainText("entrada de Bruno (E2E)");
  await ana
    .getByRole("button", { name: /Bruno \(E2E\)/ })
    .first()
    .click();
  await expect(ana.getByTestId("person-clock-in")).not.toHaveText("sin entrada registrada");

  // Un segundo intento (doble clic, otra pestaña) no crea otra jornada.
  const dup = await apiCall(bruno, "POST", "/api/time/clock-in", {});
  expect(dup.status).toBe(409);
  expect(dup.body.entry.id).toBe(status.open.id);

  // Salida desde el reloj de la barra superior, con confirmación y duración.
  await bruno.getByTestId("clock-widget").getByRole("button", { name: "Registrar salida" }).click();
  await bruno.getByRole("dialog").getByRole("button", { name: "Registrar salida" }).click();
  await expect(bruno.getByText(/Salida registrada a las .* duración/)).toBeVisible();
  await expect(bruno.getByTestId("clock-widget")).toContainText("Sin entrada");
  await expect(ana.getByTestId("robot-log")).toContainText("salida de Bruno (E2E)", { timeout: 15_000 });

  // Historial, aprobación por otro socio y eventos verificables.
  await ana.goto("/time?tab=history");
  const row = ana.getByTestId("time-entries").getByRole("row", { name: /Bruno \(E2E\)/ });
  await expect(row).toContainText("Cerrada");
  await row.getByRole("button", { name: "Aprobar" }).click();
  await ana.getByRole("dialog").getByRole("button", { name: "Aprobar jornada" }).click();
  await expect(row).toContainText("Aprobada");
  await ana.goto("/time?tab=events");
  await expect(ana.getByTestId("time-events")).toContainText("entrada");
  await expect(ana.getByTestId("time-events")).toContainText("aprobación");
  await ana.goto("/time?tab=totals");
  await expect(ana.getByTestId("time-totals")).toContainText("Registradas");
  const csvHref = await ana.getByRole("link", { name: "Exportar CSV" }).getAttribute("href");
  const csv = await ana.request.get(csvHref!);
  expect(csv.headers()["content-type"]).toContain("text/csv");
  expect(await csv.text()).toContain("Bruno (E2E)");
  const xlsx = await ana.request.get(
    (await ana.getByRole("link", { name: "Exportar Excel" }).getAttribute("href"))!,
  );
  expect(xlsx.headers()["content-type"]).toContain("spreadsheetml");
  // El historial verificable también está en el panel de administración.
  await ana.goto("/admin");
  await ana.getByRole("tab", { name: "Control horario" }).click();
  await expect(ana.getByTestId("admin-time")).toContainText("Bruno (E2E)");
  await expect(ana.getByTestId("admin-time")).toContainText("aprobación");
  await ctx.close();
});

test("salida declarada (olvido) y revisión por un aprobador", async ({ page, browser }) => {
  const ctx = await browser.newContext();
  const carla = await ctx.newPage();
  await loginAndEnter(carla, "socio3", { clockIn: true });
  await carla.goto("/time");
  await carla.getByTestId("my-day").getByRole("button", { name: "Declarar hora real de salida" }).click();
  const dialog = carla.getByRole("dialog");
  await dialog.getByLabel("Motivo").fill("Olvidé registrar la salida");
  await dialog.getByRole("button", { name: "Declarar salida" }).click();
  await expect(carla.getByText(/Salida declarada a las/)).toBeVisible();

  await loginAndEnter(page, "socio1");
  await page.goto("/time?tab=review");
  const row = page.getByTestId("time-entries").getByRole("row", { name: /Carla \(E2E\)/ });
  await expect(row).toContainText("Declarada por el socio");
  await row.getByRole("button", { name: "Aprobar" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Aprobar jornada" }).click();
  await expect(page.getByText("Jornada aprobada")).toBeVisible();
  await ctx.close();
});

test("cerrar sesión con la jornada abierta pregunta si registrar la salida", async ({ page }) => {
  await login(page, "socio3");
  await enterOffice(page, { clockIn: true });
  await expect(page.getByTestId("clock-widget")).toContainText("En jornada");
  await page.getByRole("button", { name: "Cerrar sesión" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Registrar salida y cerrar sesión" }).click();
  await expect(page.getByLabel("Usuario")).toBeVisible();
  await login(page, "socio3");
  await expect(page.getByTestId("office-gate")).toBeVisible(); // sin jornada abierta: vuelve a la pantalla de acceso
});
