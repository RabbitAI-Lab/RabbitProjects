import type { ReactNode } from "react";
import { Outlet } from "react-router";
import "./styles/app.css";

export default function AppLayout({ children }: { children?: ReactNode }) {
  return (
    <html lang="zh-CN">
      <head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>God Mode · RabbitProjects</title>
      </head>
      <body className="min-h-screen bg-neutral-50 text-neutral-900 antialiased">
        {children ?? <Outlet />}
      </body>
    </html>
  );
}
