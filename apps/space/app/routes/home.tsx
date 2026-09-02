/** 骨架占位首页 —— 业务页面按各功能文档 §3 交付。 */
export function meta() {
  return [{ title: "公开空间 · RabbitProjects" }];
}

export default function Home() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-3">
      <h1 className="text-2xl font-semibold">🐰 公开空间 · RabbitProjects</h1>
      <p className="text-sm text-neutral-500">@rp/space · 对外公开空间（P0 骨架）</p>
    </main>
  );
}
