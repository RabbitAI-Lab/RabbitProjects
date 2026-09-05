import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "react-router";
import { Topbar } from "../components/Topbar";
import { ProjectSidebar } from "../components/ProjectSidebar";
import { IssueDrawer as SharedDrawer } from "../components/IssueDrawer";
import { PeekPopover, usePeekHover, type PeekIssue } from "../components/PeekPopover";
import { NewTaskModal } from "../components/NewTaskModal";
import { IssueAPI, ProjectAPI } from "../services/api";
import { toast } from "../components/Toast";
import { LabelsAdminModal } from "./labels-admin";
import type { Issue } from "@rp/types";

interface Col { id: string | null; name: string; group: string; issues: Issue[]; }

/** C.29 第四列「已取消」：BOARD-002 §3.1 起四态全开（取消列固定 280px）。 */
const COL_NAMES = [
  { group: "unstarted", label: "待办" },
  { group: "started", label: "进行中" },
  { group: "completed", label: "已完成" },
  { group: "cancelled", label: "已取消" },
];

export default function Board() {
  const { workspaceSlug, projectId } = useParams<{ workspaceSlug: string; projectId: string }>();
  const [cols, setCols] = useState<Col[]>([]);
  const [, setLoading] = useState(true);
  const [dragId, setDragId] = useState<string | null>(null);
  // C.30 Hover Peek：hover ≥400ms 触发，拖拽/触摸不弹
  const { peek, bind, close: closeHoverPeek } = usePeekHover();
  const [showTaskModal, setShowTaskModal] = useState(false);
  const [quickCol, setQuickCol] = useState<string | null>(null);
  const [projName, setProjName] = useState("…");
  const [projIdentifier, setProjIdentifier] = useState("");
  const [search, setSearch] = useState("");
  const [showLabelsAdmin, setShowLabelsAdmin] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const [sRes, iRes, pRes] = await Promise.all([
        // ★ 必须带 include_cancelled=1：states 端点默认 exclude(group=cancelled)，
        // 不传则「已取消」列拿不到 state id → move() 在 `if (!col?.id) return` 处静默空转，
        // 表现为「拖过去没反应 / 回弹」（sprint-1 验收缺陷）。
        ProjectAPI.states(workspaceSlug!, projectId!, { include_cancelled: "1" }),
        IssueAPI.list(workspaceSlug!, projectId!, { ordering: "sort_order", per_page: 100 }),
        ProjectAPI.detail(workspaceSlug!, projectId!),
      ]);
      const projData = (pRes as unknown as { data: { name?: string; identifier?: string } | null }).data;
      setProjName(projData?.name ?? "…");
      setProjIdentifier(projData?.identifier ?? "");
      const states = (sRes as unknown as { data: Array<{ id: string; name: string; group: string; is_default?: boolean }> }).data;
      const issues = (iRes as unknown as { data: Issue[] }).data;
      const map = new Map<string | null, Col>();
      for (const n of COL_NAMES) map.set(n.label, { id: null, name: n.label, group: n.group, issues: [] });
      // 占位状态：使用服务端返回的第一个匹配 group 的 state id
      for (const s of states) {
        const col = [...map.values()].find((c) => c.group === s.group);
        if (col && !col.id) col.id = s.id;
      }
      for (const it of issues) {
        const s = states.find((x) => x.id === it.state_id);
        if (!s) continue;
        const col = [...map.values()].find((c) => c.group === s.group);
        if (col) col.issues.push(it);
      }
      setCols([...map.values()]);
    } catch { /* 鉴权失败由拦截器统一处理 */ } finally { setLoading(false); }
  }

  useEffect(() => {
    const handle = setTimeout(() => { void load(); }, 0);
    return () => clearTimeout(handle);
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceSlug, projectId]);
  // ?peekIssue URL 同步（TASK-001 §3.3）：卡片/行点击 push 参数；关闭/back 移除即关抽屉
  const [sp, setSp] = useSearchParams();
  const peekId = sp.get("peekIssue");
  function openPeek(id: string) { setSp((prev) => { const n = new URLSearchParams(prev); n.set("peekIssue", id); return n; }, { preventScrollReset: true }); }
  function closePeek() { setSp((prev) => { const n = new URLSearchParams(prev); n.delete("peekIssue"); return n; }, { preventScrollReset: true }); }

  async function move(id: string, targetGroup: string) {
    const col = cols.find((c) => c.group === targetGroup);
    if (!col?.id) {
      // 静默 return 会让「拖不进去」看起来像被后端拒绝；显式告知缺状态（BOARD-002 §3.3）
      toast(`「${col?.name ?? targetGroup}」列缺少可用状态，请联系项目管理员创建`, "error");
      return;
    }
    const last = col.issues[col.issues.length - 1];
    const sort = last ? last.sort_order + 65535 : 65535;
    // 记录源列（拖拽失败时红环反馈，BOARD-001 §3.3）
    const srcCol = cols.find((c) => c.issues.some((x) => x.id === id));
    try {
      await IssueAPI.patch(workspaceSlug!, projectId!, id, { state_id: col.id, sort_order: sort });
      // BOARD-002 §3.3 / E2E-3：拖入已取消必须给「可拖回恢复」的可见反馈
      if (targetGroup === "cancelled") toast("已取消，可拖回其他列恢复");
      await load();
    } catch {
      if (srcCol) {
        const el = document.querySelector(`[data-col="${srcCol.group}"]`);
        el?.classList.add("ring-2", "ring-red-300");
        setTimeout(() => el?.classList.remove("ring-2", "ring-red-300"), 400);
      }
      toast("移动失败，已回滚到原位置", "error");
      await load();
    }
  }

  return (
    <div className="flex flex-col h-screen">
      <Topbar />
      <div className="flex flex-1 min-h-0">
        <ProjectSidebar projectName={projName} identifier={projIdentifier} />
        <main className="flex-1 min-w-0 flex flex-col">
          {/* 视图条（C.29 视图名 + 创建任务按钮） */}
          <div className="h-[56px] border-b border-neutral-200 flex items-center gap-2 px-5 bg-white">
            <span className="text-[15px] font-semibold">看板</span>
            <span className="text-[12px] text-neutral-500 ml-1">{projIdentifier}</span>
            <button onClick={() => setShowTaskModal(true)} className="ml-auto inline-flex h-[34px] items-center gap-1.5 px-3.5 bg-brand-500 text-white rounded-md font-medium">+ 创建任务</button>
          </div>
          {/* 筛选工具条（C.29 §3.1：sticky 52px + 搜索框 + 管理标签入口） */}
          <div className="h-[52px] border-b border-neutral-200 sticky top-0 z-20 bg-white/95 backdrop-blur flex items-center gap-3 px-5">
            <div className="flex items-center gap-1.5 h-8 border border-neutral-300 rounded-md px-2.5 bg-white focus-within:border-brand-500 w-[240px]">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-neutral-400"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
              <input
                aria-label="搜索任务"
                placeholder="搜索任务…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="flex-1 h-7 outline-none text-[13px] bg-transparent"
              />
            </div>
            <button
              onClick={() => setShowLabelsAdmin(true)}
              data-sb-scope="board-open-labels"
              className="h-8 px-2.5 inline-flex items-center gap-1.5 border border-neutral-300 rounded-md text-[13px] hover:bg-neutral-50"
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M20.59 13.41 13.41 20.59a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><path d="M7 7h.01"/></svg>
              管理标签
            </button>
            <div className="ml-auto text-[12px] text-neutral-400">四列固定 · 280px</div>
          </div>
          <div className="flex-1 flex gap-4 overflow-x-auto p-4 min-h-0">
            {COL_NAMES.map((n) => {
              const col = cols.find((c) => c.group === n.group);
              const visibleIssues = col?.issues.filter((i) => !search.trim() || i.name.toLowerCase().includes(search.trim().toLowerCase()) || i.issue_key.toLowerCase().includes(search.trim().toLowerCase())) ?? [];
              return (
                <section key={n.group} data-col={n.group} aria-label={`${n.label}列`} className={`w-[280px] shrink-0 flex flex-col bg-neutral-100 rounded-lg max-h-full transition ${dragId && col?.issues.some((i) => i.id === dragId) ? "" : ""}`}
                  onDragOver={(e) => { e.preventDefault(); }}
                  onDrop={async (e) => { e.preventDefault(); if (dragId) { await move(dragId, n.group); setDragId(null); } }}>
                  <div className="h-11 flex items-center gap-2 px-3 shrink-0">
                    <span className="dot w-1.5 h-1.5 rounded-full" style={{ background: { unstarted: "#9ca3af", started: "#3b82f6", completed: "#10b981", cancelled: "#f87171" }[n.group] }} />
                    <span className="text-[13px] font-medium">{n.label}</span>
                    <span className="ml-auto font-mono text-xs text-neutral-400">{visibleIssues.length}</span>
                  </div>
                  <div className="flex-1 overflow-y-auto p-2 flex flex-col gap-2 min-h-[56px]">
                    {quickCol === n.group && (
                      <div className="flex flex-col gap-1">
                        <input autoFocus id={`qi-${n.group}`} className="h-[34px] border border-brand-500 rounded-md px-2.5 text-[13px] focus:outline-none focus:ring-[3px] focus:ring-brand-50 bg-white"
                          placeholder="输入任务标题…"
                          onKeyDown={async (e) => {
                            if (e.key === "Enter") {
                              const v = (e.target as HTMLInputElement).value.trim();
                              if (!v) return;
                              setQuickCol(null);
                              if (col?.id) { await IssueAPI.create(workspaceSlug!, projectId!, { name: v, state_id: col.id }); await load(); }
                              // 与 move() 同款静默失败：列还没拿到 state id（states 未回来）时
                              // 什么都不做，用户以为回车没生效。给出可见反馈。
                              else toast("该列状态尚未就绪，请稍后重试", "error");
                            }
                            if (e.key === "Escape") setQuickCol(null);
                          }}
                          onBlur={(e) => { if (!e.target.value.trim()) setQuickCol(null); }} />
                        <div className="text-[11px] text-neutral-400 px-1">回车创建 · Esc 取消</div>
                      </div>
                    )}
                    {visibleIssues.length ? visibleIssues.map((it) => (
                      <article key={it.id} draggable tabIndex={0} className={`bg-white border border-neutral-200 rounded-md p-2.5 pl-3.5 shadow-sm cursor-grab relative hover:shadow-md hover:border-neutral-300 transition ${dragId === it.id ? "opacity-40 scale-[0.98]" : ""} ${it.state_group === "cancelled" ? "opacity-60" : ""}`}
                        onClick={() => openPeek(it.id)}
                        {...bind(it as unknown as PeekIssue)}
                        onDragStart={(e) => { setDragId(it.id); e.dataTransfer.setData("text/plain", it.id); }}
                        onDragEnd={() => setDragId(null)}>
                        <div className="absolute left-0 top-2 bottom-2 w-[3px] rounded" style={{ background: { backlog: "#a1a1aa", unstarted: "#9ca3af", started: "#3b82f6", completed: "#10b981", cancelled: "#f87171" }[it.state_group] }} />
                        <div className="text-[13px] text-neutral-900 line-clamp-3">{it.name}</div>
                        <div className="flex items-center justify-between mt-2 text-xs">
                          <span className="font-mono text-neutral-400">{it.issue_key}</span>
                          <span className="flex items-center gap-1.5 text-neutral-500">
                            {it.assignee && <span className="w-5 h-5 rounded-full text-white text-[10px] font-semibold flex items-center justify-center" style={{ background: ["#3b82f6","#10b981","#f59e0b"][(it.assignee.name?.charCodeAt(0) ?? 0) % 3] }}>{it.assignee.name.slice(0, 1)}</span>}
                            {it.target_date && <span className="font-mono text-[11px]">{new Date(it.target_date).getMonth() + 1}-{new Date(it.target_date).getDate()}</span>}
                          </span>
                        </div>
                      </article>
                    )) : <div className="border-2 border-dashed border-neutral-300 rounded-md py-4 text-center text-xs text-neutral-400">将任务拖拽到这里</div>}
                  </div>
                  {/* 列还没拿到 state id（states 未回来）时禁用：否则填完标题回车会命中
                      `if (col?.id)` 的空分支，用户输入无声丢失 —— 列内快速创建的静默失败。
                      禁用后 Playwright 的 click 会自动等到可用，测试也不必与首屏加载赛跑。 */}
                  <button onClick={() => setQuickCol(n.group)} disabled={!col?.id}
                    title={col?.id ? undefined : "该列状态尚未就绪"}
                    className="h-8 mt-1.5 flex items-center gap-1.5 px-2.5 rounded-md text-[13px] text-neutral-400 hover:bg-neutral-200/60 disabled:opacity-40 disabled:cursor-not-allowed w-full shrink-0">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 5v14M5 12h14"/></svg>添加任务
                  </button>
                </section>
              );
            })}
          </div>
          <PeekPopover peek={peek} onClose={closeHoverPeek} />
          {peekId && <SharedDrawer issueId={peekId} slug={workspaceSlug!} projectId={projectId!} onClose={() => { closePeek(); load(); }} onChanged={() => load()} />}
          {showTaskModal && <NewTaskModal slug={workspaceSlug!} projectId={projectId!} projectName={projName} onClose={() => setShowTaskModal(false)} onCreated={() => load()} />}
          {showLabelsAdmin && workspaceSlug && projectId && (
            <LabelsAdminModal workspaceSlug={workspaceSlug} projectId={projectId} onClose={() => setShowLabelsAdmin(false)} />
          )}
        </main>
      </div>
    </div>
  );
}
