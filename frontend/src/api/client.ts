// Cliente HTTP: cookies de sesión httpOnly + cabecera CSRF en métodos que modifican estado.

export class ApiError extends Error {
  status: number;
  detail: unknown;
  /** Cuerpo completo de la respuesta de error (p. ej. la jornada existente en un 409). */
  body: unknown;
  constructor(status: number, detail: unknown, body?: unknown) {
    super(typeof detail === "string" ? detail : detailMessage(detail) || `Error ${status}`);
    this.status = status;
    this.detail = detail;
    this.body = body;
  }
}

function detailMessage(detail: unknown): string {
  if (!detail) return "";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) =>
        d && typeof d === "object" && "msg" in d ? String((d as { msg: unknown }).msg) : String(d),
      )
      .join("; ");
  }
  if (typeof detail === "object" && detail !== null && "message" in detail) {
    return String((detail as { message: unknown }).message);
  }
  return JSON.stringify(detail);
}

let csrfToken = "";
let onUnauthorized: (() => void) | null = null;

export function setCsrfToken(token: string) {
  csrfToken = token;
}

export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}

type Options = { method?: string; body?: unknown; form?: FormData; signal?: AbortSignal; raw?: boolean };

export async function api<T = unknown>(path: string, opts: Options = {}): Promise<T> {
  const method = opts.method ?? (opts.body !== undefined || opts.form ? "POST" : "GET");
  const headers: Record<string, string> = { Accept: "application/json" };
  let body: BodyInit | undefined;
  if (opts.form) {
    body = opts.form;
  } else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }
  if (!["GET", "HEAD"].includes(method) && csrfToken) headers["X-CSRF-Token"] = csrfToken;
  let res: Response;
  try {
    res = await fetch(path, { method, headers, body, credentials: "same-origin", signal: opts.signal });
  } catch (err) {
    if ((err as Error).name === "AbortError") throw err;
    throw new ApiError(0, "No se pudo contactar con el servidor WADIMA");
  }
  if (res.status === 401 && !path.startsWith("/api/auth/login")) onUnauthorized?.();
  if (opts.raw) {
    if (!res.ok) throw await apiError(res);
    return res as unknown as T;
  }
  if (!res.ok) throw await apiError(res);
  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("content-type") ?? "";
  return (ct.includes("application/json") ? res.json() : res.text()) as Promise<T>;
}

async function apiError(res: Response): Promise<ApiError> {
  try {
    const data = await res.json();
    return new ApiError(res.status, data?.detail ?? data, data);
  } catch {
    return new ApiError(res.status, res.statusText);
  }
}

export const get = <T>(path: string, signal?: AbortSignal) => api<T>(path, { signal });
export const post = <T>(path: string, body?: unknown) => api<T>(path, { method: "POST", body: body ?? {} });
export const put = <T>(path: string, body?: unknown) => api<T>(path, { method: "PUT", body: body ?? {} });
export const patch = <T>(path: string, body?: unknown) => api<T>(path, { method: "PATCH", body: body ?? {} });
export const del = <T>(path: string, body?: unknown) => api<T>(path, { method: "DELETE", body });
