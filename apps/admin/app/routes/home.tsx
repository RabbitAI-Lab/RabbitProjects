import type { Route } from "./+types/home";
export function meta({}: Route.MetaArgs) { return [{ title: "God Mode · RabbitProjects" }]; }
export default function Home() {
  return <main className="flex min-h-screen items-center justify-center gap-3 bg-neutral-50"><h1 className="text-2xl font-semibold">🐰 God Mode</h1><p className="text-sm text-neutral-500">@rp/admin · P0 骨架（业务 Sprint 1+）</p></main>;
}
