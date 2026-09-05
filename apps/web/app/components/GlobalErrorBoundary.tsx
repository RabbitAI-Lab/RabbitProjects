/** 全局错误呈现组件（C.36 · INFRA-004 §3.4 + ADR-0011 #10/#12 定稿）。
 *
 *  - 三分支：404（compass）/ 403（lock）/ 500（server-crash）；按 code 分支
 *  - 路由级 + 请求级共用：本文件导出 `ErrorState` 组件（请求级空态页）+ `GlobalErrorBoundary` 类（路由级 / 渲染错误）
 *  - 500 时显示「追踪号 01JBX3K9 · 复制」行（request_id 前 8 位 + 完整 ULID 复制）
 *  - 双按钮「← 返回」「返回工作台」
 *  - 居中最大宽 480px、h1、正常文档流、按钮可 Tab
 *
 *  INFRA-004 §3.7 无障碍：红边框 + 图标 + 文字三重通道；标题 role="alert"。
 *
 *  ADR-0010 教训：本组件是「错误空态页」单一视觉；不使用 document mousedown。
 */
import { Component, type ErrorInfo, type ReactNode } from "react";
import { useNavigate, useRouteError, isRouteErrorResponse } from "react-router";
import { toast } from "./Toast";

export type ErrorVariant = "not_found" | "forbidden" | "server";

export interface ErrorStateProps {
  // exactOptionalPropertyTypes: true —— 显式并入 undefined，否则传 undefined 即 TS2375
  variant?: ErrorVariant | undefined;
  title?: string | undefined;
  message?: string | undefined;
  /** INFRA-004 错误码（决定 variant 默认值） */
  code?: string | undefined;
  /** INFRA-004 追踪号（500 分支显示） */
  requestId?: string | undefined;
  /** 重试按钮（仅请求级使用；路由级 500 重试 = location.reload） */
  onRetry?: () => void;
}

const NOT_FOUND_TITLE = "页面走丢了";
const NOT_FOUND_SUB = "你访问的内容不存在、已删除，或你没有访问权限";
const FORBIDDEN_TITLE = "没有访问权限";
const FORBIDDEN_SUB = "联系项目管理员为你开通权限后再试";
const SERVER_TITLE = "服务暂时不可用";
const SERVER_SUB = "请稍后重试；若持续出现，请凭追踪号反馈";

function variantFromCode(code: string | undefined): ErrorVariant {
  if (!code) return "not_found";
  const c = code.toUpperCase();
  if (c === "RESOURCE_NOT_FOUND") return "not_found";
  if (c === "PERM_DENIED" || c === "PERM_ROLE_INSUFFICIENT" || c === "PERM_PROJECT_ADMIN_REQUIRED" || c === "PERM_WORKSPACE_ADMIN_REQUIRED" || c === "PERM_WORKSPACE_OWNER_REQUIRED") return "forbidden";
  if (c.startsWith("SERVER_")) return "server";
  return "not_found";
}

function copyText(s: string, msg = "已复制"): void {
  navigator.clipboard?.writeText(s).then(() => toast(msg, "info"), () => toast("复制失败", "error"));
}

/** 内联 SVG（取 lucide 形态：compass / lock / server-crash） */
function VariantIcon({ variant }: { variant: ErrorVariant }) {
  const common = { width: 96, height: 96, viewBox: "0 0 24 24", fill: "none", stroke: "#d4d4d4", strokeWidth: 1.6, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true };
  if (variant === "forbidden") {
    return (
      <svg {...common}>
        <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
        <path d="M7 11V7a5 5 0 0 1 10 0v4" />
      </svg>
    );
  }
  if (variant === "server") {
    return (
      <svg {...common}>
        <path d="M3 12a9 9 0 1 0 9-9" />
        <path d="M3 12h6l1.5-3 2 6 2-3H21" />
      </svg>
    );
  }
  // not_found (compass)
  return (
    <svg {...common}>
      <circle cx="12" cy="12" r="10" />
      <polygon points="16.24 7.76 14.12 14.12 7.76 16.24 9.88 9.88 16.24 7.76" />
    </svg>
  );
}

export function ErrorState({ variant, title, message, code, requestId, onRetry }: ErrorStateProps) {
  const nav = useNavigate();
  const v = variant ?? variantFromCode(code);
  const t = title ?? (v === "forbidden" ? FORBIDDEN_TITLE : v === "server" ? SERVER_TITLE : NOT_FOUND_TITLE);
  const m = message ?? (v === "forbidden" ? FORBIDDEN_SUB : v === "server" ? SERVER_SUB : NOT_FOUND_SUB);
  const short = requestId ? requestId.slice(0, 8) : "";
  const goHome = () => {
    // 全量重载以清掉可能的半污染 store 状态
    location.href = "/";
  };
  const goBack = () => {
    if (window.history.length > 1) nav(-1);
    else goHome();
  };
  const copyReqId = () => requestId && copyText(requestId, "已复制追踪号");

  return (
    <div className="min-h-[60vh] flex flex-col items-center justify-center px-4 py-12 text-center" data-sb-scope="err-state" data-variant={v}>
      <div className="w-full max-w-[480px] flex flex-col items-center gap-3.5 bg-white border border-neutral-200 rounded-xl shadow-sm px-6 py-10">
        <VariantIcon variant={v} />
        <h1 role="alert" tabIndex={-1} className="text-[20px] font-semibold text-neutral-900 outline-none mt-2">
          {t}
        </h1>
        <p className="text-[14px] text-neutral-500 max-w-[480px] leading-[1.6]">{m}</p>

        {v === "server" && requestId && (
          <div className="mt-2 flex items-center gap-1.5 text-[12px] text-neutral-500" data-sb-scope="err-request-id">
            <span>追踪号</span>
            <code className="font-mono text-neutral-700">{short}</code>
            <button type="button" onClick={copyReqId} aria-label="复制追踪号" className="inline-flex items-center gap-1 px-1.5 h-6 rounded-md hover:bg-neutral-100 text-brand-600">
              📋 复制
            </button>
          </div>
        )}

        <div className="mt-3 flex items-center gap-2.5">
          <button
            type="button"
            onClick={goBack}
            data-sb-scope="err-back"
            className="inline-flex h-[36px] items-center px-4 bg-white border border-neutral-300 text-neutral-700 rounded-md hover:bg-neutral-50"
          >
            ← 返回
          </button>
          <button
            type="button"
            onClick={goHome}
            data-sb-scope="err-home"
            className="inline-flex h-[36px] items-center px-4 bg-brand-500 text-white rounded-md font-medium hover:bg-brand-600"
          >
            返回工作台
          </button>
          {onRetry && (
            <button
              type="button"
              onClick={onRetry}
              data-sb-scope="err-retry"
              className="inline-flex h-[36px] items-center px-4 bg-white border border-neutral-300 text-neutral-700 rounded-md hover:bg-neutral-50"
            >
              重试
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

/** React Router 路由级 ErrorBoundary（接 useRouteError）。
 *  挂载点：routes.ts 的 `errorElement`（DEFERRED — routes 由其他 agent 持有）。 */
export function RouteErrorBoundary() {
  const err = useRouteError();
  if (isRouteErrorResponse(err)) {
    const status = err.status;
    const code = (err.data as { code?: string } | undefined)?.code;
    const requestId = (err.data as { request_id?: string } | undefined)?.request_id;
    const message = (err.data as { message?: string } | undefined)?.message ?? err.statusText;
    if (status === 404) return <ErrorState code={code ?? "RESOURCE_NOT_FOUND"} message={message} requestId={requestId} />;
    if (status === 403) return <ErrorState code={code ?? "PERM_DENIED"} message={message} requestId={requestId} />;
    if (status >= 500) return <ErrorState code={code ?? "SERVER_ERROR"} message={message} requestId={requestId} />;
  }
  // 兜底：未知异常
  const e = err as { request_id?: string; message?: string; code?: string } | null;
  return <ErrorState variant="server" code={e?.code} message={e?.message} requestId={e?.request_id} />;
}

interface GlobalErrorBoundaryProps {
  children: ReactNode;
}

/** React 渲染错误兜底（componentDidCatch）。
 *  挂载点：root.tsx 的 `<GlobalErrorBoundary>{children}</GlobalErrorBoundary>`。 */
export class GlobalErrorBoundary extends Component<GlobalErrorBoundaryProps, { error: Error | null }> {
  override state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error): { error: Error } {
    return { error };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    // 结构化日志（占位：INFRA-004 §3.6 反馈 Modal 提交时再接 Sentry）
    // eslint-disable-next-line no-console
    console.error("[GlobalErrorBoundary]", error.message, info.componentStack);
  }

  private retry = (): void => {
    this.setState({ error: null });
  };

  override render(): ReactNode {
    if (this.state.error) {
      const e = this.state.error as Error & { request_id?: string; code?: string };
      return <ErrorState code={e.code} message={e.message} requestId={e.request_id} onRetry={this.retry} />;
    }
    return this.props.children;
  }
}
