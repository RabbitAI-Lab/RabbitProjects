/** 全局过渡态/错误态（AUTH-002 §3.1/§3.4 + AUTH-003 §3.2 共用组件）。 */

export function LoaderFullscreen() {
  return (
    <div className="fixed inset-0 bg-neutral-50 z-[110] flex flex-col items-center justify-center gap-3.5" role="status" aria-busy="true" aria-label="正在验证登录状态">
      <div className="text-[32px] opacity-90">🐰</div>
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="text-brand-500 animate-spin"><path d="M21 12a9 9 0 1 1-6.22-8.56" /></svg>
    </div>
  );
}

export function ProbeFailed({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-24 text-neutral-500">
      <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="#d4d4d4" strokeWidth="2"><path d="M12 20h.01"/><path d="M2 8.82a15 15 0 0 1 20 0"/><path d="M5 12.859a10 10 0 0 1 14 0"/><path d="M8.5 16.429a5 5 0 0 1 7 0"/></svg>
      <div className="text-[15px] font-semibold text-neutral-700">加载失败</div>
      <div className="text-[13px]">无法连接服务器，请检查网络后重试</div>
      <button onClick={onRetry} className="mt-1 h-[34px] px-3.5 bg-brand-500 text-white rounded-md font-medium hover:bg-brand-600">重试</button>
    </div>
  );
}

/** 404/无权空态：保留导航上下文由父级布局提供（AUTH-003 §3.2 仅内容区替换）。 */
export function NotFoundState() {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-24 text-neutral-500">
      <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="#d4d4d4" strokeWidth="2"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>
      <div className="text-[15px] font-semibold text-neutral-700">内容不存在或你没有访问权限</div>
      <div className="text-[13px]">请确认链接是否正确，或联系项目管理员邀请你加入</div>
      <a href="#/ws-projects" onClick={(e) => { e.preventDefault(); location.href = "/"; }} className="mt-1 h-[34px] px-3.5 inline-flex items-center border border-neutral-300 rounded-md text-neutral-700 hover:bg-neutral-50">返回工作台</a>
    </div>
  );
}
