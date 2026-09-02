import { useEffect, useState, type ReactNode } from "react";
import { Outlet, useLocation, useNavigate } from "react-router";
import { RootStore, StoreProvider } from "./stores";
import { Toaster } from "./components/Toast";
import { LoaderFullscreen, ProbeFailed } from "./components/ErrorStates";
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
  const allowedPublic = loc.pathname === "/login" || loc.pathname === "/register";
  useEffect(() => {
    if (allowedPublic) { setReady(true); return; }
    // 注意：context.clearCookies() 之后 isBootstrapped 仍为 true（不同 test 间复用 store 状态）。
    // 这里用 user 的存在性做隐式判断：clearCookies 后 isLoggedIn 应为 false。
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
          <Guard>{children ?? <Outlet />}</Guard>
          <Toaster />
        </StoreProvider>
      </body>
    </html>
  );
}
