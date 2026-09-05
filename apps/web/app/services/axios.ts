import axios, { type AxiosInstance } from "axios";
import { API_BASE_URL } from "../config";
import { toast } from "../components/Toast";
import { triggerPermissionsRevalidate } from "./permissions-revalidator";

/** 统一 axios 实例（INFRA-001 §4.11：业务组件不得直连 axios，统一经 services/ 层）。
 *  - withCredentials 携带 session cookie
 *  - CSRF 双提交（X-CSRFToken）
 *  - 响应解包（status==="success" 时取 data，否则弹错）—— Sprint-1 INFRA-004
 *    把状态字段从布尔改成字符串，并新增嵌套的 `error.{code,message,details,request_id}`。
 *  - 强制尾斜杠（api-conventions §2.2） */
function csrf(): string {
  const m = document.cookie.match(/csrftoken=([^;]+)/);
  return m?.[1] ? decodeURIComponent(m[1]) : "";
}

export interface ApiErrorDetailsItem {
  field?: string;
  code?: string;
  message?: string;
  /** 业务自定义扩展键（如 PROJECT_IDENTIFIER_EXISTS 的 `suggestion`） */
  suggestion?: string;
  [k: string]: unknown;
}

export interface ApiErrorBody {
  code: string;
  message: string;
  details?: ApiErrorDetailsItem[];
  request_id?: string;
  doc_url?: string;
}

export interface ApiEnvelopeSuccess<T> {
  status: "success";
  data: T;
  meta?: Record<string, unknown> | null;
}

export interface ApiEnvelopeError {
  status: "error";
  error: ApiErrorBody;
}

/** 业务组件捕获的友好 Error —— 形状稳定，跨后端 schema 漂移都收敛在这层。 */
export interface ApiError extends Error {
  // exactOptionalPropertyTypes: true —— 显式并入 undefined，否则赋 undefined 即 TS2412
  code?: string | undefined;
  details?: ApiErrorDetailsItem[] | undefined;
  request_id?: string | undefined;
  meta?: ApiErrorBody | null;
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
      if (body.status === "success") {
        return { ...r, data: body.data, meta: body.meta ?? null };
      }
      // 业务层 2xx 但 status="error"（少见；预防性保留）
      const err = buildFriendlyError(body.error, "request failed");
      throw err;
    }
    return r;
  },
  (err) => {
    const status = err.response?.status;
    const envelopeBody = err.response?.data;
    const apiErr = envelopeBody?.error as ApiErrorBody | undefined;
    const code = apiErr?.code;
    // 信封错误统一解包：前端拿到 error.message / err.code / err.details，而非 axios 的
    // "Request failed with status code N"（登录 401、注册 409、项目 409 等场景）
    const friendly = buildFriendlyError(apiErr, err.message ?? "请求失败");
    err.friendly = friendly;
    if (status === 401) {
      // 与 root.tsx 的 allowedPublic 对齐：未登录访问公共提示页不应被拖到 /login。
      // 此前只看 /login 前缀，把 /403、/labels-admin（公共调试入口）、/invite/:token 拖走了。
      const isPublic = (p: string) =>
        p === "/login" || p === "/register" || p === "/403" || p === "/labels-admin" || p.startsWith("/invite/");
      if (!isPublic(location.pathname)) {
        if (code === "AUTH_ACCOUNT_DISABLED") {
          toast("账号已被禁用，请联系管理员", "error");
          location.href = "/login?disabled=1";
        } else {
          toast(apiErr?.message || "登录已过期，请重新登录");
          location.href = "/login?next=" + encodeURIComponent(location.pathname);
        }
      }
    } else if (status === 403 && code && code.startsWith("PERM_")) {
      // AUTH-005 §2.2 / §3.4：收到 PERM_* 403 → 静默触发权限快照重拉
      // （PermissionStore 注册的 revalidator 会拉 /users/me/permissions/，
      //  无 toast —— §3.4「无感」）。同一请求的后续业务由调用方自己接 Promise.reject。
      triggerPermissionsRevalidate();
    } else if (status === 429) {
      toast("请求过于频繁，请稍后重试", "error");
    } else if (status !== undefined && status >= 500) {
      toast("服务器开小差了，请稍后重试", "error");
    }
    return Promise.reject(friendly);
  },
);

function buildFriendlyError(
  apiErr: ApiErrorBody | undefined,
  fallbackMessage: string,
): ApiError {
  const friendly = new Error(
    apiErr?.message ?? fallbackMessage,
  ) as ApiError;
  friendly.code = apiErr?.code;
  friendly.details = apiErr?.details;
  friendly.request_id = apiErr?.request_id;
  // 保留旧字段名 `meta` 以兼容历史调用方（projects-list.tsx 迁移后即可移除）
  friendly.meta = apiErr ?? null;
  return friendly;
}

export async function getCsrf(): Promise<string> {
  const r = await api.get<ApiEnvelopeSuccess<{ csrf_token: string }>>("auth/csrf-token/");
  return (r as any).data.csrf_token;
}