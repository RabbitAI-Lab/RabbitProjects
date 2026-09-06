/** FILE-004 §4.2 公开三端点的 space 侧薄客户端（/api/v1/public/shares/…）。
 *
 *  匿名零会话：不引 axios 会话实例——原生 fetch（same-origin 自动携 share_token
 *  cookie，BR-08 2h HMAC；unlock 的 Set-Cookie 同样落在同源）。
 *  410 = 失效四态统一码（防枚举区分）；401 = 密码错误（details 带剩余次数）；
 *  429 = (IP, slug) 防爆破锁定（Retry-After 头给等待秒数）。 */

export interface ShareFileMeta {
  name: string;
  size_bytes: number;
  type_category: "image" | "document" | "video" | "archive" | "other";
}

export interface ShareMeta {
  requires_password: boolean;
  file?: ShareFileMeta;
  permission?: "view" | "download";
  expires_at?: string | null;
}

export interface ContentDispatch {
  kind: "image" | "pdf" | "text" | "video" | "archive" | "other";
  ready: boolean;
  state?: "transcoding" | "too_large" | "unsupported" | "no_preview";
  preview_url?: string;
  poster_url?: string;
  eta_seconds?: number;
  fallback_download?: boolean;
}

interface EnvelopeError {
  code?: string;
  message?: string;
  details?: Array<{ field?: string; code?: string; message?: string }>;
}

async function readError(r: Response): Promise<EnvelopeError | null> {
  try {
    const body = (await r.json()) as { error?: EnvelopeError };
    return body?.error ?? null;
  } catch {
    return null;
  }
}

export async function fetchShareMeta(slug: string): Promise<
  | { ok: true; data: ShareMeta }
  | { ok: false; gone: true }
  | { ok: false; error: string }
> {
  try {
    const r = await fetch(`/api/v1/public/shares/${encodeURIComponent(slug)}/`, { method: "GET" });
    if (r.status === 410) return { ok: false, gone: true };
    if (!r.ok) return { ok: false, error: `HTTP ${r.status}` };
    const body = (await r.json()) as { status: string; data: ShareMeta };
    if (body?.status !== "success" || !body.data) return { ok: false, error: "响应格式异常" };
    return { ok: true, data: body.data };
  } catch {
    return { ok: false, error: "网络错误" };
  }
}

export async function unlockShare(slug: string, password: string): Promise<
  | { ok: true }
  | { ok: false; status: number; remaining?: string; retryAfterSec?: number }
> {
  try {
    const r = await fetch(`/api/v1/public/shares/${encodeURIComponent(slug)}/unlock/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    if (r.ok) return { ok: true };
    const err = await readError(r);
    const detailMsg = err?.details?.[0]?.message;
    if (r.status === 429) {
      const ra = Number(r.headers.get("Retry-After"));
      return { ok: false, status: 429, retryAfterSec: Number.isFinite(ra) && ra > 0 ? ra : 600 };
    }
    return { ok: false, status: r.status, ...(detailMsg !== undefined ? { remaining: detailMsg } : {}) };
  } catch {
    return { ok: false, status: 0, remaining: "网络错误，请重试" };
  }
}

export async function fetchShareContent(slug: string): Promise<
  | { ok: true; data: ContentDispatch; accepted: boolean }
  | { ok: false; status: number }
> {
  try {
    const r = await fetch(`/api/v1/public/shares/${encodeURIComponent(slug)}/content/`, { method: "GET" });
    if (r.status === 410) return { ok: false, status: 410 };
    if (!r.ok) return { ok: false, status: r.status };
    const body = (await r.json()) as { status: string; data: ContentDispatch };
    if (body?.status !== "success" || !body.data) return { ok: false, status: 0 };
    return { ok: true, data: body.data, accepted: r.status === 202 };
  } catch {
    return { ok: false, status: -1 };
  }
}

/** 字节人性化（与 web 域同口径）。 */
export function humanSize(n: number): string {
  let size = n;
  for (const unit of ["B", "KB", "MB", "GB", "TB"]) {
    if (size < 1024 || unit === "TB") {
      if (unit === "B") return `${Math.round(size)}B`;
      return `${(Math.round(size * 10) / 10).toString().replace(/\.0$/, "")}${unit}`;
    }
    size /= 1024;
  }
  return `${n}B`;
}

/** 过期倒计时（「29 天后过期」/「永久有效」）。 */
export function expiresInLabel(expiresAt: string | null | undefined): string {
  if (!expiresAt) return "永久有效";
  const days = Math.ceil((new Date(expiresAt).getTime() - Date.now()) / 86_400_000);
  return days <= 0 ? "已过期" : `${days} 天后过期`;
}
