/** 骨架占位首页 —— Sprint 0 Day 6-9 按 AUTH-002/TEAM-001/PROJ-001 §3 替换为真实路由树。 */
export function meta() {
  return [{ title: "RabbitProjects" }];
}

export default function Home() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-3">
      <h1 className="text-2xl font-semibold">🐰 RabbitProjects</h1>
      <p className="text-sm text-neutral-500">@rp/web · Sprint 0 骨架就绪（INFRA-001）</p>
    </main>
  );
}
