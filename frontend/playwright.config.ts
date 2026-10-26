import { defineConfig } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// Lee las URL de la base E2E del .env local (escrito por `python manage.py db init`): la del rol propietario
// prepara el esquema y la del rol de aplicación (sin propiedad de las tablas) la usa el servidor.
function envValue(key: string): string | undefined {
  if (process.env[key]) return process.env[key];
  const env = readFileSync(resolve(import.meta.dirname, "..", ".env"), "utf-8");
  const line = env.split(/\r?\n/).find((l) => l.startsWith(`${key}=`));
  return line?.slice(key.length + 1);
}

function e2eDatabaseUrl(): string {
  const url = envValue("WADIMA_E2E_DATABASE_URL");
  if (!url) throw new Error("Falta WADIMA_E2E_DATABASE_URL en .env");
  return url;
}

const PORT = 8010;
const PREVIEW_PORT = 8011;
const backendDir = resolve(import.meta.dirname, "..", "backend");
const env = {
  ...process.env,
  WADIMA_ENV: "e2e",
  WADIMA_DATABASE_URL: envValue("WADIMA_E2E_APP_DATABASE_URL") ?? e2eDatabaseUrl(),
  WADIMA_MIGRATION_DATABASE_URL: e2eDatabaseUrl(),
  WADIMA_DATA_DIR: resolve(import.meta.dirname, "..", "var", "e2e"),
  WADIMA_MASTER_KEY: "e2e-master-key-only-for-tests",
  WADIMA_ALLOWED_ORIGINS: JSON.stringify([`http://127.0.0.1:${PORT}`, `http://127.0.0.1:${PREVIEW_PORT}`]),
  WADIMA_PUBLIC_ORIGIN: `http://127.0.0.1:${PORT}`,
  WADIMA_PREVIEW_PORT: String(PREVIEW_PORT),
  WADIMA_PREVIEW_PUBLIC_ORIGIN: `http://127.0.0.1:${PREVIEW_PORT}`,
  WADIMA_STUDIO_PORT_RANGE_START: "5400",
  WADIMA_STUDIO_PORT_RANGE_END: "5420",
} as Record<string, string>;

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    channel: "chrome",
    headless: true,
    viewport: { width: 1440, height: 900 },
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  webServer: {
    command: `uv run --project "${backendDir}" python -m wadima.e2e reset && uv run --project "${backendDir}" uvicorn wadima.main:app --host 127.0.0.1 --port ${PORT}`,
    cwd: backendDir,
    url: `http://127.0.0.1:${PORT}/api/health`,
    env,
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
