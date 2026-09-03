import axios, { type AxiosInstance } from "axios";
import { API_BASE_URL } from "../config";
import { toast } from "../components/Toast";

/** 统一 axios 实例（INFRA-001 §4.11：业务组件不得直连 axios，统一经 services/ 层）。
 *  - withCredentials 携带 session cookie
 *  - CSRF 双提交（X-CSRFToken）
 *  - 响应解包（status=true 时取 data，否则弹错）
 *  - 强制尾斜杠（api-conventions §2.2） */
function csrf(): string {
  const m = document.cookie.match(/csrftoken=([^;]+)/);
  return m?.[1] ? decodeURIComponent(m[1]) : "";
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
    const status = err.response?.status;
    const envelopeBody = err.response?.data;
    const code = envelopeBody?.meta?.code;
    // 信封错误统一解包：前端拿到 meta.message / err.code，而非 axios 的
    // "Request failed with status code N"（登录 401、注册 409、项目 409 等场景）
    const friendly: Error & { code?: string; meta?: unknown } = new Error(
      envelopeBody?.meta?.message ?? err.message ?? "请求失败",
    );
    friendly.code = code;
    friendly.meta = envelopeBody?.meta;
    err.friendly = friendly;
    if (status === 401 && !location.pathname.startsWith("/login")) {
      if (code === "AUTH_ACCOUNT_DISABLED") {
        // 会话中被禁用（AUTH-002 §3.3）：toast.error + 落地常驻 Alert（经 ?disabled=1）
        toast("账号已被禁用，请联系管理员", "error");
        location.href = "/login?disabled=1";
      } else {
        // 会话过期（AUTH-002 §3.3）：info 级 toast（去重见 Toast 系统）+ next 回跳
        toast("登录已过期，请重新登录");
        location.href = "/login?next=" + encodeURIComponent(location.pathname);
      }
    } else if (status === 429) {
      toast("请求过于频繁，请稍后再再试", "error");
    } else if (status !== undefined && status >= 500) {
      toast("服务器开小差了，请稍后重试", "error");
    }
    return Promise.reject(friendly);
  },
);

export async function getCsrf(): Promise<string> {
  const r = await api.get<{ csrf_token: string }>("auth/csrf-token/");
  return (r as any).data.csrf_token;
}
