/** 团队成员设置页（C.15 · TEAM-002 §3.1）。
 *  - 页头：成员 + 计数 + ＋ 邀请成员（PermissionGate workspace.member.invite 包裹）
 *  - 筛选条：搜索 300ms 防抖 + 角色下拉
 *  - 待接受邀请面板：Collapsible 折叠区
 *  - 成员表：五列（成员 / 邮箱 / 角色 / 加入时间 / 操作）
 *  - 角色徽章：所有者 #8B5CF6 / 管理员 #3B82F6 / 成员 #6B7280（圆点 + 文字）
 *  - 角色行内下拉 + 操作菜单（按权限显示）
 *  - 危险区域：转让所有权（仅 OWNER）
 *  - 加载 / 空 / 失败态
 *  - ADR-0011 #18：侧栏「团队设置」已点亮（Sidebar.tsx）
 *  - 守卫：PermissionRouteGuard workspace.member.read，无权降级只读 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams, useSearchParams } from "react-router";
import { Topbar } from "../components/Topbar";
import { Sidebar } from "../components/Sidebar";
import { toast } from "../components/Toast";
import { PermissionGate, usePermission } from "../components/PermissionGate";
import { PermissionRouteGuard } from "../components/PermissionRouteGuard";
import { ConfirmRemoveModal } from "../components/ConfirmRemoveModal";
import { TransferOwnershipModal, filterTransferCandidates } from "../components/TransferOwnershipModal";
import { InviteMemberModal } from "../components/InviteMemberModal";
import { useStores } from "../stores";
import { WorkspaceAPI, WorkspaceMemberAPI, unwrap } from "../services/api";
import { WorkspaceRole } from "../stores/permission";
import type { WorkspaceInvite, WorkspaceMember, WorkspaceSummary } from "@rp/types";

const WS_ROLE: Record<number, { n: string; c: string }> = {
  [WorkspaceRole.OWNER]:  { n: "所有者", c: "#8B5CF6" },
  [WorkspaceRole.ADMIN]:  { n: "管理员", c: "#3B82F6" },
  [WorkspaceRole.MEMBER]: { n: "成员",   c: "#6B7280" },
  [WorkspaceRole.GUEST]:  { n: "访客",   c: "#9CA3AF" },
};

/** 角色查表（全函数）。role 来自 API，运行时可能是矩阵之外的整数，
 *  故不收窄键类型，而是统一走兜底——既满足 noUncheckedIndexedAccess，
 *  也符合「外部数据不可信」的实际。 */
const WS_ROLE_FALLBACK = { n: "成员", c: "#6B7280" } as const;
const wsRole = (v: number) => WS_ROLE[v] ?? WS_ROLE_FALLBACK;

type LoadState = "loading" | "ready" | "error" | "empty";

export default function TeamMembersPage() {
  return (
    <PermissionRouteGuard permission="workspace.member.read" scope="workspace">
      <TeamMembersInner />
    </PermissionRouteGuard>
  );
}

function TeamMembersInner() {
  const { workspaceSlug } = useParams<{ workspaceSlug: string }>();
  const { session } = useStores();
  const [params, setParams] = useSearchParams();

  const [ws, setWs] = useState<WorkspaceSummary | null>(null);
  const [members, setMembers] = useState<WorkspaceMember[]>([]);
  const [invites, setInvites] = useState<WorkspaceInvite[]>([]);
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [search, setSearch] = useState(params.get("search") ?? "");
  const [searchInput, setSearchInput] = useState(search);
  const [roleFilter, setRoleFilter] = useState<"all" | "admins" | "members">(
    (params.get("role") as "all" | "admins" | "members") ?? "all",
  );
  const [pendingOpen, setPendingOpen] = useState(true);
  const [actionMenuFor, setActionMenuFor] = useState<string | null>(null);
  const [roleMenuFor, setRoleMenuFor] = useState<string | null>(null);
  const [removeTarget, setRemoveTarget] = useState<WorkspaceMember | null>(null);
  const [transferOpen, setTransferOpen] = useState(false);
  const [inviteOpen, setInviteOpen] = useState(false);

  const canInvite = usePermission("workspace.member.invite", "workspace");
  const canManage = usePermission("workspace.member.manage", "workspace");
  const canTransfer = usePermission("workspace.transfer", "workspace");
  const canRemove = usePermission("workspace.member.remove", "workspace");
  const currentUserId = session.user?.id;
  const myMember = useMemo(
    () => members.find((m) => m.user.id === currentUserId) ?? null,
    [members, currentUserId],
  );

  const fetchAll = useCallback(async () => {
    if (!workspaceSlug) return;
    setLoadState("loading");
    try {
      const [wRes, mRes, iRes] = await Promise.all([
        WorkspaceAPI.list(),
        WorkspaceMemberAPI.list(workspaceSlug, { expand: "user", per_page: 100 }),
        canManage ? WorkspaceMemberAPI.invitations(workspaceSlug) : Promise.resolve(null),
      ]);
      const wsList = unwrap<WorkspaceSummary[]>(wRes);
      setWs(wsList.find((w) => w.slug === workspaceSlug) ?? null);
      setMembers((unwrap<WorkspaceMember[]>(mRes)) ?? []);
      if (iRes) setInvites((unwrap<WorkspaceInvite[]>(iRes)) ?? []);
      setLoadState("ready");
    } catch {
      setLoadState("error");
    }
  }, [workspaceSlug, canManage]);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  // 300ms 防抖 → URL 同步（CLAUDE.md：search 同步 URL）
  useEffect(() => {
    const t = setTimeout(() => {
      if (search === searchInput) return;
      setSearch(searchInput);
      const p = new URLSearchParams(params);
      if (searchInput) p.set("search", searchInput); else p.delete("search");
      setParams(p, { replace: true });
    }, 300);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchInput]);

  // ?open=invite 直接打开邀请弹窗（来自「去邀请成员」深链）
  useEffect(() => {
    if (params.get("open") === "invite" && canInvite) setInviteOpen(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canInvite]);

  // 筛选
  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return members
      .filter((m) => !q || (m.user.display_name + m.user.email).toLowerCase().includes(q))
      .filter((m) => {
        if (roleFilter === "admins") return m.role === WorkspaceRole.OWNER || m.role === WorkspaceRole.ADMIN;
        if (roleFilter === "members") return m.role === WorkspaceRole.MEMBER;
        return true;
      });
  }, [members, search, roleFilter]);

  const reloadAfterMutation = useCallback(async () => {
    if (!workspaceSlug) return;
    try {
      const [mRes, iRes] = await Promise.all([
        WorkspaceMemberAPI.list(workspaceSlug, { expand: "user", per_page: 100 }),
        canManage ? WorkspaceMemberAPI.invitations(workspaceSlug) : Promise.resolve(null),
      ]);
      setMembers((unwrap<WorkspaceMember[]>(mRes)) ?? []);
      if (iRes) setInvites((unwrap<WorkspaceInvite[]>(iRes)) ?? []);
    } catch { /* ignore */ }
  }, [workspaceSlug, canManage]);

  const onChangeRole = async (m: WorkspaceMember, newRole: number) => {
    if (m.role === WorkspaceRole.OWNER) return;
    if (newRole === WorkspaceRole.OWNER) { toast("所有者仅能通过转让所有权产生", "error"); return; }
    if (m.user.id === currentUserId) { toast("不能修改自己的角色", "error"); return; }
    const snap = { ...m };
    setMembers((cur) => cur.map((x) => x.id === m.id ? { ...x, role: newRole } : x));
    try {
      const r = await WorkspaceMemberAPI.patch(workspaceSlug!, m.id, { role: newRole });
      setMembers((cur) => cur.map((x) => x.id === m.id ? unwrap(r) : x));
      toast(`已将 ${m.user.display_name} 调整为 ${wsRole(newRole).n}`);
    } catch (e) {
      setMembers((cur) => cur.map((x) => x.id === m.id ? snap : x));
      toast(e instanceof Error ? e.message : "调整失败", "error");
    }
  };

  const onRemoveConfirm = async () => {
    if (!removeTarget) return;
    const target = removeTarget;
    try {
      await WorkspaceMemberAPI.remove(workspaceSlug!, target.id);
      setMembers((cur) => cur.filter((m) => m.id !== target.id));
      toast(`已将 ${target.user.display_name} 移出 ${ws?.name ?? ""}`);
      setRemoveTarget(null);
    } catch (e) {
      toast(e instanceof Error ? e.message : "移除失败", "error");
    }
  };

  const onTransfer = async (payload: { new_owner_member_id: string; confirm_name: string }) => {
    try {
      const r = await WorkspaceMemberAPI.transfer(workspaceSlug!, payload);
      const newOwner = unwrap<{ new_owner?: { display_name: string } }>(r).new_owner;
      toast(`所有权已转让给 ${newOwner?.display_name ?? "新所有者"}`);
      setTransferOpen(false);
      await reloadAfterMutation();
    } catch (e) {
      toast(e instanceof Error ? e.message : "转让失败", "error");
    }
  };

  const onRevokeInvite = async (inv: WorkspaceInvite) => {
    try {
      await WorkspaceMemberAPI.revokeInvite(workspaceSlug!, inv.id);
      setInvites((cur) => cur.filter((x) => x.id !== inv.id));
      toast("邀请已撤销");
    } catch (e) {
      toast(e instanceof Error ? e.message : "撤销失败", "error");
    }
  };

  return (
    <div className="flex flex-col h-screen">
      <Topbar />
      <div className="flex flex-1 min-h-0">
        <Sidebar workspaceSlug={workspaceSlug!} />
        <main className="flex-1 min-w-0 overflow-y-auto px-6 py-5">
          {loadState === "loading" && <MembersSkeleton />}
          {loadState === "error" && <ErrorState onRetry={fetchAll} />}
          {loadState !== "loading" && loadState !== "error" && (
            <div className="max-w-[900px]">
              {/* 页头 */}
              <div className="flex items-center gap-3 mb-5">
                <div>
                  <div className="text-lg font-semibold">成员</div>
                  <div className="text-[13px] text-neutral-500">{members.length} 名成员</div>
                </div>
                {canInvite && (
                  <PermissionGate permission="workspace.member.invite" scope="workspace" mode="hide">
                    <button
                      onClick={() => setInviteOpen(true)}
                      data-testid="open-invite"
                      className="ml-auto h-[34px] px-3.5 bg-brand-500 text-white rounded-md font-medium hover:bg-brand-600 inline-flex items-center gap-1.5"
                    >
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 11h-6M19 8v6"/></svg>
                      邀请成员
                    </button>
                  </PermissionGate>
                )}
              </div>

              {/* 筛选条 */}
              <div className="flex items-center gap-2.5 mb-4">
                <div className="relative w-[260px]">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="absolute left-2.5 top-1/2 -translate-y-1/2 text-neutral-400"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
                  <input
                    type="search"
                    aria-label="搜索成员"
                    placeholder="搜索昵称或邮箱…"
                    value={searchInput}
                    onChange={(e) => setSearchInput(e.target.value)}
                    className="w-full h-9 pl-8 pr-2.5 border border-neutral-300 rounded-md bg-white text-[13px] focus:outline-none focus:border-brand-500 focus:ring-[3px] focus:ring-brand-50"
                  />
                </div>
                <RoleFilter value={roleFilter} onChange={(v) => {
                  setRoleFilter(v);
                  const p = new URLSearchParams(params);
                  if (v === "all") p.delete("role"); else p.set("role", v);
                  setParams(p, { replace: true });
                }} />
              </div>

              {/* 待接受邀请面板（仅 ADMIN+） */}
              {canManage && invites.length > 0 && (
                <div className="mb-4 border border-neutral-200 rounded-md bg-white overflow-hidden">
                  <button
                    onClick={() => setPendingOpen((v) => !v)}
                    aria-expanded={pendingOpen}
                    className="w-full flex items-center gap-2 px-3.5 py-2.5 text-left hover:bg-neutral-50"
                  >
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={`transition-transform text-neutral-500 ${pendingOpen ? "rotate-90" : ""}`}><path d="m9 18 6-6-6-6"/></svg>
                    <span className="text-[13px] font-medium">待接受邀请 ({invites.length})</span>
                  </button>
                  {pendingOpen && (
                    <ul className="divide-y divide-neutral-100 border-t border-neutral-100">
                      {invites.map((iv) => (
                        <li key={iv.id} className="flex items-center gap-3 px-3.5 py-2 text-[13px]">
                          <span className="font-mono text-[12px] text-neutral-600">{maskEmail(iv.email)}</span>
                          <span className="text-[12px] text-neutral-500">· {wsRole(iv.role).n}</span>
                          <span className="ml-auto flex items-center gap-1.5">
                            <button
                              onClick={() => toast("已重发邮件（演示）")}
                              className="h-7 px-2 border border-neutral-300 rounded text-[12px] hover:bg-neutral-50"
                            >重发邮件</button>
                            <button
                              onClick={() => onRevokeInvite(iv)}
                              className="h-7 px-2 border border-neutral-300 rounded text-[12px] hover:bg-neutral-50"
                            >撤销</button>
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}

              {/* 成员表 */}
              <div className="border border-neutral-200 rounded-md bg-white overflow-hidden">
                {filtered.length === 0 && search ? (
                  <div className="flex flex-col items-center gap-3 py-12 text-neutral-500">
                    <svg width="52" height="52" viewBox="0 0 24 24" fill="none" stroke="#d4d4d4" strokeWidth="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/><path d="M8 11h6"/></svg>
                    <div className="text-[15px] font-semibold text-neutral-700">未找到匹配的成员</div>
                    <button
                      onClick={() => { setSearchInput(""); setSearch(""); }}
                      className="h-[34px] px-3.5 border border-neutral-300 rounded-md text-neutral-700 hover:bg-neutral-50"
                    >清除搜索</button>
                  </div>
                ) : (
                  <table className="w-full text-[13px]">
                    <thead className="bg-neutral-50 text-neutral-500 text-[12px]">
                      <tr>
                        <th scope="col" className="text-left font-medium px-3.5 py-2 w-[200px]">成员</th>
                        <th scope="col" className="text-left font-medium px-3.5 py-2">邮箱</th>
                        <th scope="col" className="text-left font-medium px-3.5 py-2 w-[140px]">角色</th>
                        <th scope="col" className="text-left font-medium px-3.5 py-2 w-[140px]">加入时间</th>
                        <th scope="col" className="text-left font-medium px-3.5 py-2 w-[80px]">操作 ⋯</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-neutral-100">
                      {filtered.map((m) => {
                        const isMe = m.user.id === currentUserId;
                        const isOwner = m.role === WorkspaceRole.OWNER;
                        const canEditRole = canManage && !isOwner && !isMe && m.role < (myMember?.role ?? 0);
                        const canRemoveThis = canRemove && !isOwner && !isMe && m.role < (myMember?.role ?? 0);
                        return (
                          <tr key={m.id} data-mem={m.user.display_name}>
                            <td className="px-3.5 py-2.5">
                              <span className="inline-flex items-center gap-2">
                                <span
                                  className="w-6 h-6 rounded-md text-white text-xs font-semibold flex items-center justify-center shrink-0"
                                  style={{ background: hashColor(m.user.id) }}
                                  aria-hidden="true"
                                >{m.user.display_name.slice(0, 1)}</span>
                                <span className="truncate max-w-[140px]">{m.user.display_name}{isMe && <span className="text-neutral-400">（我）</span>}</span>
                              </span>
                            </td>
                            <td className="px-3.5 py-2.5 font-mono text-[12px] text-neutral-600">{m.user.email}</td>
                            <td className="px-3.5 py-2.5">
                              {canEditRole ? (
                                <RoleDropdown member={m} open={roleMenuFor === m.id} onToggle={() => setRoleMenuFor(roleMenuFor === m.id ? null : m.id)} onClose={() => setRoleMenuFor(null)} onPick={(r) => onChangeRole(m, r)} />
                              ) : (
                                <RoleBadge value={m.role} />
                              )}
                            </td>
                            <td className="px-3.5 py-2.5 font-mono text-[12px] text-neutral-600">{formatJoinDate(m.joined_at)}</td>
                            <td className="px-3.5 py-2.5">
                              {isOwner ? (
                                <span className="text-[12px] text-neutral-400">（无）</span>
                              ) : (
                                <ActionMenu
                                  open={actionMenuFor === m.id}
                                  onToggle={() => setActionMenuFor(actionMenuFor === m.id ? null : m.id)}
                                  onClose={() => setActionMenuFor(null)}
                                  canEdit={canEditRole}
                                  canRemove={canRemoveThis}
                                  onAdjust={() => { setActionMenuFor(null); setRoleMenuFor(m.id); }}
                                  onRemove={() => { setActionMenuFor(null); setRemoveTarget(m); }}
                                />
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                )}
              </div>

              {/* 危险区域（仅 OWNER） */}
              {canTransfer && ws && (
                <div className="mt-4 border border-red-200 bg-red-50 rounded-md p-4">
                  <div className="text-[15px] font-semibold text-red-700 mb-1.5">⚠️ 危险区域</div>
                  <p className="text-[13px] text-red-700 mb-3">转让所有权后你将成为管理员，且不可自助撤销。</p>
                  <div className="text-right">
                    <button
                      onClick={() => setTransferOpen(true)}
                      className="h-[34px] px-3.5 bg-red-500 text-white rounded-md hover:bg-red-600"
                    >转让所有权</button>
                  </div>
                </div>
              )}
            </div>
          )}
        </main>
      </div>

      {removeTarget && (
        <ConfirmRemoveModal
          subject={{ name: removeTarget.user.display_name, email: removeTarget.user.email }}
          workspaceName={ws?.name ?? "工作空间"}
          cascadeText="该成员将同时被移出 N 个项目的成员名单；其名下任务指派将保留并以「已移出成员」展示。"
          onConfirm={onRemoveConfirm}
          onClose={() => setRemoveTarget(null)}
        />
      )}
      {transferOpen && ws && (
        <TransferOwnershipModal
          workspaceName={ws.name}
          candidates={filterTransferCandidates(members, currentUserId)}
          onConfirm={onTransfer}
          onClose={() => setTransferOpen(false)}
        />
      )}
      {inviteOpen && ws && (
        <InviteMemberModal
          workspaceSlug={workspaceSlug!}
          workspaceName={ws.name}
          onClose={() => setInviteOpen(false)}
          onInvited={reloadAfterMutation}
        />
      )}
    </div>
  );
}

function MembersSkeleton() {
  return (
    <div className="max-w-[900px]">
      <div className="flex items-center gap-3 mb-5">
        <div>
          <div className="h-6 w-20 bg-neutral-100 rounded animate-pulse" />
          <div className="h-3 w-32 bg-neutral-100 rounded mt-2 animate-pulse" />
        </div>
        <div className="ml-auto h-[34px] w-24 bg-neutral-100 rounded animate-pulse" />
      </div>
      <div className="border border-neutral-200 rounded-md bg-white">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="flex items-center gap-3 px-3.5 py-2.5 border-b border-neutral-100 last:border-0">
            <div className="h-6 w-32 bg-neutral-100 rounded animate-pulse" />
            <div className="h-3 w-40 bg-neutral-100 rounded flex-1 animate-pulse" />
            <div className="h-5 w-16 bg-neutral-100 rounded animate-pulse" />
            <div className="h-3 w-20 bg-neutral-100 rounded animate-pulse" />
            <div className="h-5 w-10 bg-neutral-100 rounded animate-pulse" />
          </div>
        ))}
      </div>
    </div>
  );
}

function ErrorState({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="flex flex-col items-center gap-3 py-16 text-neutral-500">
      <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#ef4444" strokeWidth="2"><circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/></svg>
      <div className="text-[15px] font-semibold text-neutral-700">加载失败</div>
      <div className="text-[13px]">网络异常，请稍后重试</div>
      <button onClick={onRetry} className="mt-1 h-[34px] px-3.5 border border-neutral-300 rounded-md text-neutral-700 hover:bg-neutral-50">重试</button>
    </div>
  );
}

function RoleFilter({ value, onChange }: { value: "all" | "admins" | "members"; onChange: (v: "all" | "admins" | "members") => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as HTMLElement | null;
      if (t?.closest('[data-sb-scope="role-filter"]')) return;
      setOpen(false);
    };
    document.addEventListener("mousedown", onDown, true);
    return () => document.removeEventListener("mousedown", onDown, true);
  }, [open]);
  const labels = { all: "全部", admins: "管理员+所有者", members: "成员" } as const;
  return (
    <div className="relative" ref={ref} data-sb-scope="role-filter">
      <button
        data-sb-scope="role-filter"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className="h-9 px-2.5 border border-neutral-300 rounded-md bg-white text-[13px] hover:bg-neutral-50 inline-flex items-center gap-1.5"
      >
        {labels[value]}
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-neutral-400"><path d="m6 9 6 6 6-6"/></svg>
      </button>
      {open && (
        <div
          data-sb-scope="role-filter"
          role="listbox"
          className="absolute top-[calc(100%+4px)] left-0 w-[180px] bg-white border border-neutral-200 rounded-md shadow-lg py-1 z-10"
        >
          {(["all", "admins", "members"] as const).map((k) => (
            <button
              key={k}
              role="option"
              aria-selected={value === k}
              onClick={() => { onChange(k); setOpen(false); }}
              className={`w-full px-2.5 py-1.5 flex items-center justify-between text-left text-[13px] hover:bg-neutral-50 ${value === k ? "bg-brand-50" : ""}`}
            >
              {labels[k]}
              {value === k && <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M20 6 9 17l-5-5"/></svg>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function RoleDropdown({
  member, open, onToggle, onClose, onPick,
}: {
  member: WorkspaceMember;
  open: boolean;
  onToggle: () => void;
  onClose: () => void;
  onPick: (r: number) => void;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as HTMLElement | null;
      if (t?.closest(`[data-mem-role="${member.id}"]`)) return;
      onClose();
    };
    document.addEventListener("mousedown", onDown, true);
    return () => document.removeEventListener("mousedown", onDown, true);
  }, [open, onClose, member.id]);
  // 选项 = 非 OWNER 的两个等级
  const opts: number[] = [WorkspaceRole.ADMIN, WorkspaceRole.MEMBER].filter((r) => r !== member.role);
  return (
    <div className="relative" ref={ref} data-mem-role={member.id}>
      <button
        onClick={onToggle}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`调整 ${member.user.display_name} 的角色`}
        className="inline-flex items-center gap-1 hover:opacity-80"
      >
        <RoleBadge value={member.role} />
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-neutral-400"><path d="m6 9 6 6 6-6"/></svg>
      </button>
      {open && (
        <div className="absolute top-[calc(100%+4px)] left-0 min-w-[140px] bg-white border border-neutral-200 rounded-md shadow-lg py-1 z-10">
          <div className="px-2.5 py-1 text-[11px] text-neutral-400">调整角色（层级保护过滤）</div>
          {opts.map((r) => (
            <button
              key={r}
              onClick={() => { onPick(r); onClose(); }}
              className="w-full px-2.5 py-1.5 flex items-center gap-2 text-left text-[13px] hover:bg-neutral-50"
            >
              <RoleBadge value={r} />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function ActionMenu({
  open, onToggle, onClose, canEdit, canRemove, onAdjust, onRemove,
}: {
  open: boolean;
  onToggle: () => void;
  onClose: () => void;
  canEdit: boolean;
  canRemove: boolean;
  onAdjust: () => void;
  onRemove: () => void;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as HTMLElement | null;
      if (t?.closest('[data-sb-scope="row-actions"]')) return;
      onClose();
    };
    document.addEventListener("mousedown", onDown, true);
    return () => document.removeEventListener("mousedown", onDown, true);
  }, [open, onClose]);
  if (!canEdit && !canRemove) return <span className="text-[12px] text-neutral-400">（无）</span>;
  return (
    <div className="relative" ref={ref} data-sb-scope="row-actions">
      <button
        data-sb-scope="row-actions"
        onClick={onToggle}
        aria-label="更多操作"
        aria-haspopup="menu"
        className="w-7 h-7 inline-flex items-center justify-center text-neutral-500 hover:text-neutral-900 hover:bg-neutral-100 rounded"
      >⋯</button>
      {open && (
        <div
          data-sb-scope="row-actions"
          role="menu"
          className="absolute top-[calc(100%+2px)] right-0 w-[140px] bg-white border border-neutral-200 rounded-md shadow-lg py-1 z-10"
        >
          {canEdit && (
            <button
              role="menuitem"
              onClick={() => { onClose(); onAdjust(); }}
              className="w-full px-2.5 py-1.5 text-left text-[13px] hover:bg-neutral-50 inline-flex items-center gap-2"
            >调整角色</button>
          )}
          {canRemove && (
            <button
              role="menuitem"
              onClick={() => { onClose(); onRemove(); }}
              className="w-full px-2.5 py-1.5 text-left text-[13px] text-red-600 hover:bg-red-50 inline-flex items-center gap-2"
            >移除</button>
          )}
        </div>
      )}
    </div>
  );
}

function RoleBadge({ value }: { value: number }) {
  const m = wsRole(value);
  return (
    <span
      className="inline-flex items-center gap-1.5 px-1.5 h-6 rounded text-[12px] font-medium"
      style={{ background: `${m.c}1a`, color: m.c }}
    >
      <span className="w-1.5 h-1.5 rounded-full" style={{ background: m.c }} />
      {m.n}
    </span>
  );
}

function hashColor(id: string) {
  return ["#3b82f6", "#10b981", "#f59e0b", "#8b5cf6", "#ec4899"][(id?.charCodeAt(0) ?? 48) % 5];
}

function formatJoinDate(iso: string) {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "—";
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${m}-${day} ${hh}:${mm}`;
}

function maskEmail(email: string) {
  // 用 slice 而非下标：noUncheckedIndexedAccess 下 s[i] 是 string | undefined
  const at = email.indexOf("@");
  if (at <= 0) return "***";
  const local = email.slice(0, at);
  const domain = email.slice(at + 1);
  if (local.length <= 2) return `${local.slice(0, 1)}***@${domain}`;
  return `${local.slice(0, 1)}***${local.slice(-1)}@${domain}`;
}
