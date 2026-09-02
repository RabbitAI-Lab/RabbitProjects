import { type ReactNode } from "react";
import { Outlet } from "react-router";
import { RootStore, StoreProvider } from "./stores";
import "./styles/app.css";

const root = new RootStore();

export default function AppLayout({ children }: { children?: ReactNode }) {
  return (
    <html lang="zh-CN">
      <head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>RabbitProjects</title>
      </head>
      <body className="min-h-screen bg-neutral-50 text-neutral-900 antialiased">
        <StoreProvider value={root}>{children ?? <Outlet />}</StoreProvider>
      </body>
    </html>
  );
}
