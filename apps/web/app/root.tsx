import { useEffect, useState, type ReactNode } from "react";
import { Outlet, useLocation, useNavigate } from "react-router";
import { RootStore, StoreProvider } from "./stores";
import { Toaster } from "./components/Toast";
import { GlobalErrorBoundary } from "./components/GlobalErrorBoundary";
import { LoaderFullscreen, ProbeFailed } from "./components/ErrorStates";
import { hasSessionProbe } from "./services/session-probe";
import "./styles/app.css";

const root = new RootStore();

/** 路由守卫：未登录访问受保护路由 → 跳 /login?next=…（AUTH-002 §3.2）。
 *  SPA 模式无 loader，守卫以 layout 级 useEffect 实现。
 *  全屏 Loader 800ms 后才显文本（INFRA-001 §3.1 / AUTH-002 §3.1 延迟逻辑）。
 *  "受保护" 路由：除 /login /register / 之外的所有路径。 */
function Guard({ children }: { children?: ReactNode }) {
  const nav = useNavigate();
  const loc = useLocation();
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  // 受保护路由白名单：登录 / 注册 / 403 公共提示页（任何权限上下文都能渲染）
  // /invite/:token 单独白名单？当前 invite-accept 走 401 时 axios 拦截器跳 /login?next=
  // 已足够覆盖；把它也加入 allowedPublic 以保证「未登录直达 /invite/bogus」能展示提示。
  const allowedPublic =
    loc.pathname === "/login" ||
    loc.pathname === "/register" ||
    loc.pathname === "/403" ||
    loc.pathname === "/labels-admin" ||
    loc.pathname.startsWith("/invite/");
  useEffect(() => {
    if (allowedPublic) { setReady(true); return; }
    // 规范 ③ 实测根因：内存态 isBootstrapped/isLoggedIn 跨 page 复用，而会话 cookie
    // 可能已被清（e2e beforeEach / 用户在他标签页退出）。此时旧逻辑直接放行 → 后续请求
    // 全部 401 → axios 拦截器整页跳 /login，页面表现为「闪 blank 再跳登录」。
    // 修复：内存态显示已登录但探针 cookie 缺失 → 视为会话失效，重置 store 重走 bootstrap。
    // 注意：不能嗅探 `sessionid`——它是 HttpOnly，document.cookie 恒读不到（见 session-probe.ts）。
    const hasSessionCookie = hasSessionProbe();
    if (root.session.isBootstrapped && root.session.isLoggedIn && !hasSessionCookie) {
      root.session.isBootstrapped = false;
      root.session.user = null;
      root.session.workspaces = [];
      root.session.currentWsSlug = null;
    }
    if (root.session.isBootstrapped && root.session.isLoggedIn) return;
    // isBootstrapped=true 但用户不存在（之前 bootstrap 后 signOut / clearCookies）→ 重置
    if (root.session.isBootstrapped && !root.session.user) {
      root.session.isBootstrapped = false;
      root.session.workspaces = [];
      root.session.currentWsSlug = null;
    }
    if (root.session.isBootstrapped && !root.session.isLoggedIn) {
      nav(`/login?next=${encodeURIComponent(loc.pathname + loc.search)}`, { replace: true });
      return;
    }
    setFailed(false);
    let cancel = false;
    const timer = setTimeout(() => { if (!cancel && !root.session.isBootstrapped) setFailed(true); }, 8000); // §3.1：8s 超时切错误态
    root.session.bootstrap().then((ok) => {
      if (cancel) return;
      clearTimeout(timer);
      if (!ok) nav(`/login?next=${encodeURIComponent(loc.pathname + loc.search)}`, { replace: true });
      setReady(true);
    }).catch(() => { if (!cancel) { clearTimeout(timer); setFailed(true); } });
    return () => { cancel = true; clearTimeout(timer); };
  }, [loc.pathname, loc.search, allowedPublic, nav]);
  if (!ready && !allowedPublic) {
    if (failed) return <ProbeFailed onRetry={() => { setFailed(false); setReady(false); root.session.isBootstrapped = false; location.reload(); }} />;
    return <LoaderFullscreen />;
  }
  return children ?? <Outlet />;
}

export default function AppLayout({ children }: { children?: ReactNode }) {
  return (
    <html lang="zh-CN">
      <head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>RabbitProjects</title>
      </head>
      <body className="min-h-screen bg-neutral-50 text-neutral-900 antialiased">
        <StoreProvider value={root}>
          {/* C.36 路由级错误兜底（INFRA-004 §3.4）：渲染异常统一走错误空态页 */}
          <GlobalErrorBoundary>
            <Guard>{children ?? <Outlet />}</Guard>
          </GlobalErrorBoundary>
          <Toaster />
        </StoreProvider>
      </body>
    </html>
  );
}
