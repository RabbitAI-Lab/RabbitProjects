/** admin 运维台 API 助手（同源 /api/v1；会话凭据随行；信封解包）。 */
export const API_BASE = "/api/v1";

export interface Envelope<T> {
  status: "success" | "error";
  data?: T;
  meta?: Record<string, unknown>;
  error?: { code: string; message: string; details?: Array<{ field: string; code: string; message: string }> };
}

export async function api<T>(method: string, path: string, body?: unknown): Promise<Envelope<T>> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const csrf = document.cookie.match(/csrftoken=([A-Za-z0-9]+)/)?.[1];
  if (csrf) headers["X-CSRFToken"] = csrf;
  const init: RequestInit = { method, headers, credentials: "include" };
  if (body !== undefined) init.body = JSON.stringify(body);
  const resp = await fetch(API_BASE + path, init);
  try { return (await resp.json()) as Envelope<T>; }
  catch { return { status: "error", error: { code: "NETWORK", message: `HTTP ${resp.status}` } }; }
}
