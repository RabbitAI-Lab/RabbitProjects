/** 骨架占位首页 —— 业务页面按各功能文档 §3 交付。 */
export function meta() {
  return [{ title: "God Mode · RabbitProjects" }];
}

export default function Home() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-3">
      <h1 className="text-2xl font-semibold">🐰 God Mode · RabbitProjects</h1>
      <p className="text-sm text-neutral-500">@rp/admin · God Mode 实例管理（P0 骨架）</p>
    </main>
  );
}
