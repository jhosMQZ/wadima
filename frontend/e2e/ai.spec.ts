// Configuración de IA: opciones oficiales, token MCP y una conexión MCP real por HTTP (Streamable HTTP + JSON-RPC,
// el mismo protocolo que usa Claude Code). La conexión solo aparece como activa tras una petición real.
import { expect, test } from "@playwright/test";
import { loginAndEnter } from "./helpers";

const BASE = "http://127.0.0.1:8010";

async function mcp(token: string, method: string, params: Record<string, unknown>, id = 1) {
  const res = await fetch(`${BASE}/mcp`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      Accept: "application/json, text/event-stream",
      "MCP-Protocol-Version": "2025-06-18",
      "User-Agent": "e2e-mcp-client",
    },
    body: JSON.stringify({ jsonrpc: "2.0", id, method, params }),
  });
  const text = await res.text();
  return { status: res.status, body: text ? JSON.parse(text) : null };
}

async function tool(token: string, name: string, args: Record<string, unknown> = {}) {
  const r = await mcp(token, "tools/call", { name, arguments: args }, 2);
  expect(r.status, JSON.stringify(r.body)).toBe(200);
  const result = r.body.result;
  return result.structuredContent?.result ?? result.structuredContent;
}

test("opciones de IA, token MCP, conexión real verificada y cola de tareas", async ({ page }) => {
  await loginAndEnter(page, "socio2");
  await page.goto("/ai");
  const options = page.getByTestId("ai-options");
  await expect(options).toContainText("Suscripción Claude Pro/Max");
  await expect(options).toContainText("API de Anthropic");
  await expect(options).toContainText("no incluye");
  const panel = page.getByTestId("mcp-panel");
  await expect(panel).toContainText("sin configurar");

  await panel.getByRole("button", { name: "Crear token" }).click();
  const created = page.getByTestId("mcp-token-created");
  await expect(created).toContainText("claude mcp add --transport http wadima http://127.0.0.1:8010/mcp");
  const command = await created.locator("pre").innerText();
  const token = /Bearer (wmcp_[A-Za-z0-9_-]+)/.exec(command)![1];
  await expect(panel).toContainText("token creado, ningún cliente lo ha usado aún");

  // Sin token o con un token falso, el servidor MCP rechaza la petición.
  expect((await mcp("wmcp_falso", "tools/list", {})).status).toBe(401);

  // Un cliente MCP real se conecta: inicialización y llamada a una herramienta.
  const init = await mcp(token, "initialize", {
    protocolVersion: "2025-06-18",
    capabilities: {},
    clientInfo: { name: "claude-code", version: "e2e" },
  });
  expect(init.status).toBe(200);
  expect(init.body.result.serverInfo.name).toBe("WADIMA");
  const me = await tool(token, "whoami");
  expect(me.usuario).toBe("socio2");
  await expect(panel).toContainText("conectado (petición real recibida)");
  await expect(panel).toContainText("claude-code e2e");

  // Encargar una tarea a su Claude; el cliente MCP la recoge y entrega el resultado.
  const tasks = page.getByTestId("mcp-tasks");
  await tasks.getByLabel("Encargo").fill("Resume las prioridades de la semana");
  await tasks.getByRole("button", { name: "Encargar" }).click();
  await expect(tasks).toContainText("en cola");
  const pending = await tool(token, "list_pending_tasks");
  expect(pending).toHaveLength(1);
  const detail = await tool(token, "get_task", { task_id: pending[0].task_id });
  expect(detail.encargo).toBe("Resume las prioridades de la semana");
  await tool(token, "submit_result", {
    task_id: pending[0].task_id,
    output: "## Prioridades\n\n1. Demo E2E",
  });
  await expect(tasks).toContainText("completada");
  await tasks.getByRole("link", { name: "Ver resultado" }).click();
  await expect(page.getByText("vía su suscripción de Claude (MCP)")).toBeVisible();
  await expect(page.getByText("Demo E2E")).toBeVisible();

  // Revocar el token lo invalida al instante.
  await page.goto("/ai");
  await page.getByTestId("mcp-panel").getByRole("button", { name: "Revocar" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Revocar" }).click();
  await expect(page.getByTestId("mcp-panel")).toContainText("sin configurar");
  expect((await mcp(token, "tools/list", {})).status).toBe(401);
});

test("asignar una tarea de proyecto a la IA de otro socio y verla en la oficina", async ({
  page,
  browser,
}) => {
  // Bruno conecta su Claude Code (petición MCP real con su token).
  const ctx = await browser.newContext();
  const bruno = await ctx.newPage();
  await loginAndEnter(bruno, "socio2");
  await bruno.goto("/ai");
  await bruno.getByTestId("mcp-panel").getByRole("button", { name: "Crear token" }).click();
  const command = await bruno.getByTestId("mcp-token-created").locator("pre").innerText();
  const token = /Bearer (wmcp_[A-Za-z0-9_-]+)/.exec(command)![1];
  await mcp(token, "initialize", {
    protocolVersion: "2025-06-18",
    capabilities: {},
    clientInfo: { name: "claude-code", version: "e2e" },
  });

  // Ana crea una tarea y se la asigna a «Claude de Bruno».
  await loginAndEnter(page, "socio1");
  await page.goto("/projects");
  await page.getByRole("button", { name: "Nuevo proyecto" }).click();
  await page.getByRole("dialog").getByLabel("Nombre").fill("Campaña IA");
  await page.getByRole("dialog").getByRole("button", { name: /Crear/ }).click();
  await expect(page.getByRole("heading", { name: "Campaña IA" })).toBeVisible();
  await page.getByLabel("Título de la tarea").fill("Escribir el texto de bienvenida");
  await page.getByLabel("Asignar a").selectOption({ label: "🤖 Claude de Bruno (E2E)" });
  await page.getByRole("button", { name: "Añadir" }).click();
  await expect(page.getByTestId("task-ai")).toContainText("Claude de Bruno (E2E) · en cola");

  // En la oficina, el Claude de Bruno aparece en el laboratorio de IA con la tarea en cola.
  await page.goto("/");
  await expect(page.getByTestId("claude-tag-socio2")).toHaveAttribute("data-state", "waiting");
  await expect(page.getByTestId("claude-tag-socio2")).toHaveAttribute("title", /1 en cola/);
  await page.getByTestId("claude-tag-socio2").click();
  await expect(page.getByTestId("claude-panel")).toContainText("Suscripción de Claude");
  await expect(page.getByTestId("claude-panel")).toContainText("claude-code e2e");
  await page.getByTestId("room-ai").click();
  await page.getByRole("button", { name: "Acercar" }).click();
  await expect(page.getByTestId("claude-tag-socio2")).toContainText("1 en cola");
  await page.waitForTimeout(700); // fin de la animación de zoom
  await page.screenshot({ path: "test-results/visual/escritorio-laboratorio-ia.png" });

  // El Claude de Bruno la recoge y entrega el resultado.
  const pending = await tool(token, "list_pending_tasks");
  expect(pending[0].tarea_del_proyecto).toBe("Escribir el texto de bienvenida");
  await tool(token, "get_task", { task_id: pending[0].task_id });
  await expect(page.getByTestId("claude-tag-socio2")).toHaveAttribute("data-state", "working");
  await tool(token, "submit_result", { task_id: pending[0].task_id, output: "¡Bienvenidos a la campaña!" });
  await expect(page.getByTestId("claude-tag-socio2")).toHaveAttribute("data-state", "idle");

  // La tarea queda terminada con el resultado como comentario.
  await page.goBack();
  await expect(page.getByTestId("task-ai")).toContainText("entregada");
  await page.getByRole("button", { name: "Escribir el texto de bienvenida" }).click();
  await expect(page.getByRole("dialog")).toContainText("Claude de Bruno (E2E)");
  await expect(page.getByRole("dialog")).toContainText("¡Bienvenidos a la campaña!");
  await ctx.close();
});

test("otras APIs de IA: una tarjeta por proveedor y validación local de la clave", async ({ page }) => {
  await loginAndEnter(page, "socio3");
  await page.goto("/ai");
  const options = page.getByTestId("ai-options");
  await expect(options).toContainText("Otras APIs");
  await expect(options).toContainText("DeepSeek");
  for (const id of ["openai_api", "gemini_api", "deepseek_api", "openai_compatible"]) {
    await expect(page.getByTestId(`api-${id}`)).toContainText("sin configurar");
  }
  // Una clave de Anthropic en la tarjeta de OpenAI se rechaza antes de llamar a ningún servicio
  const openai = page.getByTestId("api-openai_api");
  await openai.getByLabel("API key de OpenAI").fill("sk-ant-api03-esta-clave-es-de-anthropic-XXXX");
  await openai.getByRole("button", { name: "Guardar y verificar" }).click();
  await expect(openai).toContainText("Esa clave es de Anthropic");
  // Una API compatible no puede apuntar a la red interna
  const compat = page.getByTestId("api-openai_compatible");
  await compat.getByLabel("Dirección de la API").fill("https://192.168.1.10/v1");
  await compat.getByLabel("API key de API compatible con OpenAI").fill("clave-de-prueba");
  await compat.getByRole("button", { name: "Guardar y verificar" }).click();
  await expect(compat).toContainText("red interna");
  await expect(page.getByTestId("api-openai_compatible")).toContainText("sin configurar");
  // Sin ninguna IA verificada, el laboratorio lo explica
  await page.goto("/agents");
  await expect(page.getByText("Conecte su propia cuenta de Claude u otra IA")).toBeVisible();
});
