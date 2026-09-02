import { useEffect, useState, type ReactNode } from "react";
import { Outlet, useLocation, useNavigate } from "react-router";
import { RootStore, StoreProvider } from "./stores";
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
  const allowedPublic = loc.pathname === "/login" || loc.pathname === "/register";
  useEffect(() => {
    if (allowedPublic) { setReady(true); return; }
    if (root.session.isBootstrapped) {
      if (!root.session.isLoggedIn) nav(`/login?next=${encodeURIComponent(loc.pathname + loc.search)}`, { replace: true });
      return;
    }
    let cancel = false;
    root.session.bootstrap().then((ok) => {
      if (cancel) return;
      if (!ok) nav(`/login?next=${encodeURIComponent(loc.pathname + loc.search)}`, { replace: true });
      setReady(true);
    });
    return () => { cancel = true; };
  }, [loc.pathname, loc.search, allowedPublic, nav]);
  if (!ready && !allowedPublic) {
    const showText = typeof window !== "undefined" && (performance.now() > 800);
    return (
      <div className="fixed inset-0 bg-neutral-50 z-[110] flex flex-col items-center justify-center gap-3.5" role="status" aria-busy="true" aria-label="正在验证登录状态">
        <div className="text-[32px] opacity-90">🐰</div>
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="text-brand-500 animate-spin" aria-hidden>
          <path d="M21 12a9 9 0 1 1-6.22-8.56" />
        </svg>
        {showText && <div className="text-[13px] text-neutral-500">正在加载…</div>}
      </div>
    );
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
        </StoreProvider>
      </body>
    </html>
  );
}
