import axios, { type AxiosInstance } from "axios";
import { API_BASE_URL } from "../config";

/** 统一 axios 实例（INFRA-001 §4.11：业务组件不得直连 axios，统一经 services/ 层）。
 *  - withCredentials 携带 session cookie
 *  - CSRF 双提交（X-CSRFToken）
 *  - 响应解包（status=true 时取 data，否则弹错）
 *  - 强制尾斜杠（api-conventions §2.2） */
function csrf(): string {
  const m = document.cookie.match(/csrftoken=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

export const api: AxiosInstance = axios.create({
  baseURL: API_BASE_URL,
  withCredentials: true,
  timeout: 15000,
  headers: { "Content-Type": "application/json" },
});

api.interceptors.request.use((cfg) => {
  const safe = cfg.method?.toLowerCase() !== "get";
  if (safe) cfg.headers["X-CSRFToken"] = csrf();
  // 强制尾斜杠
  if (cfg.url && !cfg.url.endsWith("/")) cfg.url += "/";
  return cfg;
});

api.interceptors.response.use(
  (r) => {
    const body = r.data;
    if (body && typeof body === "object" && "status" in body) {
      if (body.status) return { ...r, data: body.data, meta: body.meta ?? null };
      const err: Error & { code?: string; meta?: unknown } = new Error(body?.meta?.message ?? "request failed");
      err.code = body?.meta?.code;
      err.meta = body?.meta;
      throw err;
    }
    return r;
  },
  (err) => {
    // 401：会话过期
    if (err.response?.status === 401 && !location.pathname.startsWith("/login")) {
      location.href = "/login?next=" + encodeURIComponent(location.pathname);
    }
    return Promise.reject(err);
  },
);

export async function getCsrf(): Promise<string> {
  const r = await api.get<{ csrf_token: string }>("auth/csrf-token/");
  return (r as any).data.csrf_token;
}
