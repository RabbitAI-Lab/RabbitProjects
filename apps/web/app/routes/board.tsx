import { useEffect, useMemo, useState } from "react";
import { useParams, useSearchParams } from "react-router";
import { Topbar } from "../components/Topbar";
import { ProjectSidebar } from "../components/ProjectSidebar";
import { IssueDrawer as SharedDrawer } from "../components/IssueDrawer";
import { PeekPopover, usePeekHover, type PeekIssue } from "../components/PeekPopover";
import { NewTaskModal } from "../components/NewTaskModal";
import { AvatarStack, BlockedCompleteDialog, blockersFromError, type BlockerItem } from "../components/issue-dialogs";
import { IssueAPI, ProjectAPI, ProjectMemberAPI, RelationAPI, unwrap, type RelationRow } from "../services/api";
import type { ApiError } from "../services/axios";
import { useStores } from "../stores";
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

/** 看板拖拽 → 409 拦截演出（T005 §3.3 / §4.4.2）：卡片 300ms 弹回 + 列头 shake 一次 + M-BLOCKED。 */
const BOARD_CSS = `
@keyframes boardshake{0%,100%{transform:translateX(0)}20%{transform:translateX(-4px)}40%{transform:translateX(4px)}60%{transform:translateX(-3px)}80%{transform:translateX(3px)}}
section.bcol.shaking{animation:boardshake .4s ease}
@keyframes boardbounce{0%{transform:scale(.95)}60%{transform:translateX(8px)}100%{transform:translateX(0)}}
article.bcard.bounce-back{animation:boardbounce .3s cubic-bezier(.2,.9,.3,1.1)}
`;

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

  // ── Sprint-2（TASK-005 §3.4 ⛔ / TASK-007 §3.3 执行人堆叠）──
  const stores = useStores();
  const isAdmin = stores.permission.effectiveProjectRole(projectId, workspaceSlug) >= 20;
  /** C.51 成员表（卡片头像堆叠姓名解析；assignee_ids → 头像首字母） */
  const [members, setMembers] = useState<Array<{ id: string; user: { id: string; display_name: string } }>>([]);
  /** C.45 被阻塞集合：?blocked=true 服务端口径（is_blocked_by 且前置未完成/取消） */
  const [blockedIds, setBlockedIds] = useState<Set<string>>(new Set());
  /** ⛔ 角标 tooltip：阻塞项前 3 个 key + 等 N 项（hover 时按需拉 relations） */
  const [blockedTip, setBlockedTip] = useState<Record<string, string>>({});
  /** C.44 M-BLOCKED 对话框（拖拽 409 RESOURCE_TRANSITION_BLOCKED 入口） */
  const [blockedDlg, setBlockedDlg] = useState<{ issueName: string; blockers: BlockerItem[]; stateId: string; issueId: string } | null>(null);

  const nameOf = useMemo(() => (uid: string) => members.find((m) => m.user.id === uid)?.user.display_name ?? "…", [members]);

  async function load() {
    setLoading(true);
    try {
      const [sRes, iRes, pRes, bRes] = await Promise.all([
        // ★ 必须带 include_cancelled=1：states 端点默认 exclude(group=cancelled)，
        // 不传则「已取消」列拿不到 state id → move() 在 `if (!col?.id) return` 处静默空转，
        // 表现为「拖过去没反应 / 回弹」（sprint-1 验收缺陷）。
        ProjectAPI.states(workspaceSlug!, projectId!, { include_cancelled: "1" }),
        IssueAPI.list(workspaceSlug!, projectId!, { ordering: "sort_order", per_page: 100 }),
        ProjectAPI.detail(workspaceSlug!, projectId!),
        // C.45：被阻塞集合（服务端 Exists 口径；两请求并行，失败不影响主列表）
        IssueAPI.list(workspaceSlug!, projectId!, { blocked: true, per_page: 100 }).catch(() => null),
      ]);
      const projData = (pRes as unknown as { data: { name?: string; identifier?: string; current_user_role?: number } | null }).data;
      setProjName(projData?.name ?? "…");
      setProjIdentifier(projData?.identifier ?? "");
      const states = (sRes as unknown as { data: Array<{ id: string; name: string; group: string; is_default?: boolean }> }).data;
      const issues = (iRes as unknown as { data: Issue[] }).data;
      if (bRes) {
        const bIssues = (bRes as unknown as { data: Issue[] }).data ?? [];
        setBlockedIds(new Set(bIssues.map((i) => i.id)));
      }
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

  // C.51 成员表
  useEffect(() => {
    ProjectMemberAPI.list(workspaceSlug!, projectId!, { per_page: 100 })
      .then((r) => setMembers(unwrap<typeof members>(r) ?? []))
      .catch(() => {});
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceSlug, projectId]);

  // ?peekIssue URL 同步（TASK-001 §3.3）：卡片/行点击 push 参数；关闭/back 移除即关抽屉
  const [sp, setSp] = useSearchParams();
  const peekId = sp.get("peekIssue");
  function openPeek(id: string) { setSp((prev) => { const n = new URLSearchParams(prev); n.set("peekIssue", id); return n; }, { preventScrollReset: true }); }
  function closePeek() { setSp((prev) => { const n = new URLSearchParams(prev); n.delete("peekIssue"); return n; }, { preventScrollReset: true }); }

  /** C.45 ⛔ 角标 tooltip：hover 按需拉 relations（列阻塞项前 3 个 +「等 N 项」）。 */
  async function loadBlockedTip(issueId: string) {
    if (blockedTip[issueId]) return;
    try {
      const r = await RelationAPI.list(workspaceSlug!, projectId!, issueId);
      const rows = (unwrap<RelationRow[]>(r) ?? [])
        .filter((x) => x.relation_type === "is_blocked_by"
          && x.related_issue.state_group !== "completed" && x.related_issue.state_group !== "cancelled");
      const keys = rows.map((x) => x.related_issue.issue_key);
      setBlockedTip((cur) => ({
        ...cur,
        [issueId]: keys.length > 3 ? `${keys.slice(0, 3).join("、")} 等 ${keys.length} 项` : keys.join("、"),
      }));
    } catch { /* tooltip 降级为通用文案 */ }
  }

  /** 拖拽弹回演出：卡片 300ms 弹回原列 + 列头 shake 一次（§4.4.2）。 */
  function bounceBack(issueId: string, fromGroup: string) {
    const card = document.querySelector(`article[data-card-id="${issueId}"]`);
    card?.classList.add("bounce-back");
    setTimeout(() => card?.classList.remove("bounce-back"), 320);
    const col = document.querySelector(`section[data-col="${fromGroup}"]`);
    col?.classList.remove("shaking");
    if (col) void (col as HTMLElement).offsetWidth; // 重启动画
    col?.classList.add("shaking");
    setTimeout(() => col?.classList.remove("shaking"), 450);
  }

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
    } catch (e: unknown) {
      const err = e as ApiError;
      const it = cols.flatMap((c) => c.issues).find((x) => x.id === id);
      // C.44 触发：409 RESOURCE_TRANSITION_BLOCKED → 弹回 + shake + M-BLOCKED 对话框
      if (err?.code === "RESOURCE_TRANSITION_BLOCKED" && it) {
        bounceBack(id, it.state_group ?? "unstarted");
        setBlockedDlg({ issueName: it.name, blockers: blockersFromError(err), stateId: col.id, issueId: id });
        await load();
        return;
      }
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
      <style>{BOARD_CSS}</style>
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
              // C.45 列头计数：「进行中 · 5（2 被阻塞）」次级文本（amber）
              const blockedN = visibleIssues.filter((i) => blockedIds.has(i.id)).length;
              return (
                <section key={n.group} data-col={n.group} aria-label={`${n.label}列`} className={`w-[280px] shrink-0 flex flex-col bg-neutral-100 rounded-lg max-h-full transition ${dragId && col?.issues.some((i) => i.id === dragId) ? "" : ""}`}
                  onDragOver={(e) => { e.preventDefault(); }}
                  onDrop={async (e) => { e.preventDefault(); if (dragId) { await move(dragId, n.group); setDragId(null); } }}>
                  <div className="h-11 flex items-center gap-2 px-3 shrink-0">
                    <span className="dot w-1.5 h-1.5 rounded-full" style={{ background: { unstarted: "#9ca3af", started: "#3b82f6", completed: "#10b981", cancelled: "#f87171" }[n.group] }} />
                    <span className="text-[13px] font-medium">{n.label}</span>
                    <span className="ml-auto font-mono text-xs text-neutral-400">{visibleIssues.length}</span>
                  </div>
                  {/* C.45 被阻塞列头计数（仅进行中列；amber 次级文本） */}
                  {n.group === "started" && blockedN > 0 && (
                    <div className="px-3 pb-1.5 text-[11px] text-amber-700 shrink-0" data-sb-scope="board-blocked-sub">
                      {visibleIssues.length} 个任务（{blockedN} 被阻塞）
                    </div>
                  )}
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
                    {visibleIssues.length ? visibleIssues.map((it) => {
                      const blocked = blockedIds.has(it.id);
                      const tip = blockedTip[it.id] ?? "被未完成前置任务阻塞";
                      const assigneeNames = (it.assignee_ids ?? []).map(nameOf);
                      return (
                        <article key={it.id} data-card-id={it.id} data-sb-scope="board-card" draggable tabIndex={0}
                          className={`bcard relative bg-white border border-neutral-200 rounded-md p-2.5 pl-3.5 shadow-sm cursor-grab hover:shadow-md hover:border-neutral-300 transition ${dragId === it.id ? "opacity-40 scale-[0.98]" : ""} ${it.state_group === "cancelled" ? "opacity-60" : ""}`}
                          onClick={() => openPeek(it.id)}
                          {...bind(it as unknown as PeekIssue)}
                          onMouseEnter={(e) => {
                            if (blocked) void loadBlockedTip(it.id);
                            bind(it as unknown as PeekIssue).onMouseEnter?.(e);
                          }}
                          onDragStart={(e) => { setDragId(it.id); e.dataTransfer.setData("text/plain", it.id); }}
                          onDragEnd={() => setDragId(null)}>
                          <div className="absolute left-0 top-2 bottom-2 w-[3px] rounded" style={{ background: { backlog: "#a1a1aa", unstarted: "#9ca3af", started: "#3b82f6", completed: "#10b981", cancelled: "#f87171" }[it.state_group] }} />
                          {/* C.45 看板 ⛔ 角标：被未完成前置阻塞的卡片右上角 ⛔(amber)；tooltip 列阻塞项前 3 个 +「等 N 项」 */}
                          {blocked && (
                            <span className="absolute top-1.5 right-1.5 text-amber-500 text-[13px]" title={tip}
                              role="img" aria-label={`被未完成前置任务阻塞：${tip}`} data-sb-scope="board-blocked-badge">⛔</span>
                          )}
                          <div className="text-[13px] text-neutral-900 line-clamp-3 pr-4">{it.name}</div>
                          <div className="flex items-center justify-between mt-2 text-xs">
                            <span className="font-mono text-neutral-400">{it.issue_key}</span>
                            {/* C.51 看板执行人：卡片头像堆叠（前叠 2 + 计数）；未指派左下虚线人形占位 */}
                            <span className="flex items-center gap-1.5 text-neutral-500">
                              {it.target_date && <span className="font-mono text-[11px]">{new Date(it.target_date).getMonth() + 1}-{new Date(it.target_date).getDate()}</span>}
                              {assigneeNames.length > 0
                                ? <AvatarStack names={assigneeNames} size={20} />
                                : <span className="inline-flex text-neutral-300 border border-dashed border-neutral-300 rounded-full w-5 h-5 items-center justify-center" title="未指派" aria-label="未指派" data-sb-scope="board-unassigned">
                                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
                                  </span>}
                            </span>
                          </div>
                        </article>
                      );
                    }) : <div className="border-2 border-dashed border-neutral-300 rounded-md py-4 text-center text-xs text-neutral-400">将任务拖拽到这里</div>}
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

          {/* C.44 M-BLOCKED 完成被拦截对话框（看板拖拽入口；管理员可强制完成 force=true + comment） */}
          {blockedDlg && (
            <BlockedCompleteDialog
              issueName={blockedDlg.issueName}
              blockers={blockedDlg.blockers}
              isAdmin={isAdmin}
              onClose={() => setBlockedDlg(null)}
              onForce={async (comment) => {
                try {
                  await IssueAPI.patch(workspaceSlug!, projectId!, blockedDlg.issueId, { state_id: blockedDlg.stateId, force: true, comment });
                  toast("已强制完成（管理员通道）· 已记录说明", "warning");
                  setBlockedDlg(null);
                  await load();
                } catch (e: unknown) {
                  const err = e as ApiError;
                  toast(err?.details?.[0]?.message ?? err?.message ?? "强制完成失败", "error");
                }
              }}
              onJump={(b) => {
                const hit = cols.flatMap((c) => c.issues).find((x) => x.issue_key === b.issue_key);
                if (hit) openPeek(hit.id);
              }}
            />
          )}
        </main>
      </div>
    </div>
  );
}
