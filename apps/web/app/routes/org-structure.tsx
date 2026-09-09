/** 组织管理页（AUTH-007 §3.1——C.150，Sprint-8 R6）。
 *
 *  部门树（平铺数据前端组树，§6.4）+ 新建/改名/删除 + 成员列表（部门列/
 *  过滤含子部门）+ 按部门批量授权弹窗（预览计数 → 执行）。 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "react-router";

import { DepartmentAPI, ProjectAPI, WorkspaceMemberAPI, unwrap } from "../services/api";
import { Topbar } from "../components/Topbar";
import { toast } from "../components/Toast";

type Dept = {
  id: string; parent_id: string | null; name: string;
  member_count?: number; with_descendants_member_count?: number;
  path: string;
};
type Member = {
  id: string; user: { id: string; display_name: string; email: string };
  department_id: string | null; company_role: string | null; role: number;
};
type ProjectLite = { id: string; name: string };

const WS_ROLE_LABEL: Record<number, string> = { 20: "所有者", 15: "管理员", 10: "成员", 5: "访客" };
const AVA_COLORS = ["#3f76ff", "#10b981", "#f59e0b", "#8b5cf6", "#ec4899", "#6b7280"];
const avatarColor = (n: string) => AVA_COLORS[(n.charCodeAt(0) || 48) % AVA_COLORS.length];

const ROLE_OPTS = [
  { v: 5, n: "查看者" }, { v: 10, n: "评论者" },
  { v: 15, n: "协作者" }, { v: 20, n: "管理员" },
];

export default function OrgStructurePage() {
  const { workspaceSlug: ws } = useParams();
  const [depts, setDepts] = useState<Dept[]>([]);
  const [members, setMembers] = useState<Member[]>([]);
  const [projects, setProjects] = useState<ProjectLite[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [filterDept, setFilterDept] = useState<string | null>(null);
  const [withDesc, setWithDesc] = useState(true);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [grantOpen, setGrantOpen] = useState(false);
  const [grantProj, setGrantProj] = useState("");
  const [grantRole, setGrantRole] = useState(15);
  const [grantPreview, setGrantPreview] = useState<{ added?: unknown[]; role_changed?: unknown[]; skipped?: unknown[]; unchanged?: unknown[] } | null>(null);

  const load = useCallback(async () => {
    if (!ws) return;
    try {
      const [d, m, p] = await Promise.all([
        DepartmentAPI.list(ws, true),
        WorkspaceMemberAPI.list(ws, { per_page: 100 }).catch(() => null),
        ProjectAPI.listByWs(ws, { status: "all" }).catch(() => null),
      ]);
      setDepts(unwrap<Dept[]>(d) ?? []);
      setMembers(unwrap<Member[]>(m as unknown) ?? []);
      setProjects(unwrap<ProjectLite[]>(p as unknown) ?? []);
    } catch {
      toast("加载组织架构失败", "error");
    } finally {
      setLoading(false);
    }
  }, [ws]);

  // oxlint-disable-next-line react/set-state-in-effect -- 服务端 loader（同 approvals.tsx 基线）
  useEffect(() => { load(); }, [load]);

  const childrenOf = useMemo(() => {
    const map = new Map<string | null, Dept[]>();
    for (const d of depts) {
      const list = map.get(d.parent_id) ?? [];
      list.push(d);
      map.set(d.parent_id, list);
    }
    return map;
  }, [depts]);

  const deptName = (id: string | null) =>
    id ? (depts.find((d) => d.id === id)?.name ?? id) : "未分配";

  const subtreeIds = (id: string): string[] => {
    const out = [id];
    for (const c of childrenOf.get(id) ?? []) out.push(...subtreeIds(c.id));
    return out;
  };

  const shownMembers = useMemo(() => {
    if (!filterDept) return members;
    const ids = new Set(withDesc ? subtreeIds(filterDept) : [filterDept]);
    return members.filter((m) => m.department_id && ids.has(m.department_id));
    // subtreeIds 是模块内稳定递归（不随渲染重建），childrenOf 已含于其闭包
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [members, filterDept, withDesc]);

  const unassigned = members.filter((m) => !m.department_id).length;
  const total = members.length;

  async function createDept() {
    if (!ws || !newName.trim()) return;
    setCreating(true);
    try {
      await DepartmentAPI.create(ws, {
        name: newName.trim(),
        parent_id: selected,
      });
      toast("部门已创建");
      setNewName("");
      load();
    } catch {
      toast("创建失败（同级重名或超 6 层）", "error");
    } finally {
      setCreating(false);
    }
  }

  async function removeDept(id: string) {
    if (!ws || !confirm("删除该空部门？")) return;
    try {
      await DepartmentAPI.delete(ws, id);
      toast("部门已删除");
      if (selected === id) setSelected(null);
      load();
    } catch (e) {
      toast((e as { message?: string })?.message ?? "非空部门不可删除", "error");
    }
  }

  async function assignDept(memberId: string, deptId: string | null) {
    if (!ws) return;
    try {
      await WorkspaceMemberAPI.patch(ws, memberId, { department_id: deptId } as never);
      load();
    } catch {
      toast("归属变更失败", "error");
    }
  }

  async function previewGrant() {
    if (!ws || !selected || !grantProj) return;
    try {
      const r = await DepartmentAPI.grantPreview(ws, selected, {
        project_id: grantProj, role: grantRole,
        with_descendants: true,
      });
      setGrantPreview(unwrap<{ added?: string[]; role_changed?: string[]; skipped?: unknown[]; unchanged?: string[] }>(r as unknown) ?? null);
    } catch {
      toast("预览失败", "error");
    }
  }

  async function execGrant() {
    if (!ws || !selected || !grantProj) return;
    try {
      const r = await DepartmentAPI.grant(ws, selected, {
        project_id: grantProj, role: grantRole, with_descendants: true,
      });
      const d = unwrap<{ added?: string[]; skipped?: unknown[] }>(r) ?? {};
      toast(`授权完成：新增 ${d.added?.length ?? 0} 人，跳过 ${d.skipped?.length ?? 0} 人`);
      setGrantOpen(false);
      setGrantPreview(null);
    } catch (e) {
      toast((e as { message?: string })?.message ?? "授权失败", "error");
    }
  }

  function renderTree(parent: string | null, depth = 0): React.ReactNode {
    return (childrenOf.get(parent) ?? []).map((d) => (
      <div key={d.id}>
        <div
          className={`tree-node${selected === d.id ? " sel" : ""}`}
          role="treeitem"
          aria-selected={selected === d.id}
          style={{ paddingLeft: 8 + depth * 18 }}
          onClick={() => setSelected(selected === d.id ? null : d.id)}
          data-sb-scope="org-tree-node"
        >
          {depth < 2
            ? <span style={{ fontSize: 9, color: "#a3a3a3" }}>▾</span>
            : <span style={{ fontSize: 9 }}>·</span>}
          <span className="text-[13.5px]">{d.name}</span>
          <span className="cnt">{d.member_count ?? 0}/{d.with_descendants_member_count ?? 0}</span>
        </div>
        {renderTree(d.id, depth + 1)}
      </div>
    ));
  }

  if (loading) return <div className="p-10 text-sm text-neutral-400">加载组织架构…</div>;

  return (
    <div className="flex h-screen flex-col">
      <Topbar />
      <div className="flex flex-1 overflow-hidden">
        <main className="flex-1 overflow-auto p-4">
        <div className="mx-auto max-w-[1240px] px-2 py-4">
        <div className="mb-[18px] flex items-center gap-3">
          <div>
            <div className="text-[17px] font-semibold">组织架构</div>
            <div className="text-[12.5px] text-neutral-400">部门树与成员归属 · 按部门批量授权（快照展开）</div>
          </div>
          <div className="ml-auto flex gap-2">
            <button className="h-7 rounded-md border border-neutral-300 px-2.5 text-xs text-neutral-600 hover:bg-neutral-50">导出</button>
            <button onClick={() => selected && setGrantOpen(true)} disabled={!selected}
                    className="h-7 rounded-md bg-blue-600 px-2.5 text-xs font-medium text-white disabled:opacity-50"
                    data-sb-scope="org-grant-open">按部门授权</button>
          </div>
        </div>
        <div className="grid items-start gap-4" style={{ gridTemplateColumns: "280px 1fr" }}>
        <aside className="rounded-lg border bg-white p-2.5 shadow-sm">
          <div className="px-2 pb-2 pt-1 text-xs text-neutral-400">部门 · 直属/含子级</div>
          <div role="tree">{renderTree(null)}
            <div className="tree-node" style={{ color: "#a3a3a3" }}>
              <span style={{ fontSize: 9 }}>·</span><span>未分配</span>
              <span className="cnt">{unassigned}</span>
            </div>
          </div>
          <div className="mt-3 border-t border-neutral-200 px-2.5 py-2 text-[11.5px] leading-relaxed text-neutral-400">
            全体 {total} = Σ部门直属 {total - unassigned} + 未分配 {unassigned}
            <div style={{ color: "#a3a3a3" }}>（恒等式实时对账，AUTH-007 §2.2）</div>
          </div>
          <div className="mt-1 border-t border-neutral-200 pt-2.5">
            <input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder={selected ? `在「${deptName(selected)}」下新建…` : "新建根部门…"}
              className="mb-2 w-full rounded border px-2 py-1.5 text-sm"
              data-sb-scope="org-new-name"
            />
            <button
              onClick={createDept}
              disabled={creating || !newName.trim()}
              className="w-full rounded bg-blue-600 py-1.5 text-sm text-white disabled:opacity-50"
              data-sb-scope="org-new-submit"
            >
              新建部门
            </button>
            {selected && (
              <button onClick={() => removeDept(selected)}
                      className="mt-2 h-7 w-full rounded-md border border-red-200 text-[11.5px] text-red-600 hover:bg-red-50">
                删除选中部门（须为空部门）
              </button>
            )}
          </div>
        </aside>
        <div className="overflow-hidden rounded-lg border bg-white shadow-sm">
          <div className="flex items-center gap-2.5 border-b border-neutral-200 px-3.5 py-3">
            <b className="text-[13.5px]">成员归属</b>
            <select
              value={filterDept ?? ""}
              onChange={(e) => setFilterDept(e.target.value || null)}
              className="h-[30px] w-[150px] rounded-md border border-neutral-300 px-1.5 text-[12.5px]"
              data-sb-scope="org-member-filter"
            >
              <option value="">全部成员（{members.length}）</option>
              <option value="__unassigned__">未分配（{unassigned}）</option>
              {depts.map((d) => <option key={d.id} value={d.id}>{d.name}（{d.member_count ?? 0}）</option>)}
            </select>
            {filterDept && (
              <label className="flex cursor-pointer items-center gap-1.5 text-xs text-neutral-400">
                <input type="checkbox" checked={withDesc}
                       onChange={(e) => setWithDesc(e.target.checked)} />
                含子部门
              </label>
            )}
            {selected && (
              <button onClick={() => removeDept(selected)}
                      className="ml-2 h-6 rounded-md border border-red-200 px-2 text-[11px] text-red-600 hover:bg-red-50">
                删除选中部门
              </button>
            )}
            <span className="ml-auto text-xs text-neutral-400">{shownMembers.length} 人</span>
          </div>
          <table className="w-full text-[13px]">
            <thead className="text-left text-xs font-medium text-neutral-400">
              <tr><th className="px-3 py-2">成员</th><th className="w-[190px]">部门</th><th>岗位</th><th>空间角色</th></tr>
            </thead>
            <tbody>
              {shownMembers.map((m) => (
                <tr key={m.id} className="border-t border-neutral-200 hover:bg-neutral-50">
                  <td className="px-3 py-2">
                    <span className="inline-flex items-center gap-2">
                      <span className="flex h-7 w-7 items-center justify-center rounded-full text-xs font-semibold text-white"
                            style={{ background: avatarColor(m.user.display_name) }}>
                        {m.user.display_name[0]}
                      </span>
                      {m.user.display_name}
                    </span>
                  </td>
                  <td className="px-3 py-2">
                    <select
                      value={m.department_id ?? ""}
                      onChange={(e) => assignDept(m.id, e.target.value || null)}
                      className="h-7 rounded-md border border-neutral-300 px-1.5 text-[12.5px]"
                      data-sb-scope="org-member-dept"
                    >
                      <option value="">未分配</option>
                      {depts.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                    </select>
                  </td>
                  <td className="text-xs text-neutral-400">{m.company_role ?? "—"}</td>
                  <td className={m.role === 5 ? "text-[13px] text-amber-700" : "text-[13px]"}>
                    {WS_ROLE_LABEL[m.role as keyof typeof WS_ROLE_LABEL] ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!shownMembers.length && (
            <div className="p-9 text-center">
              <div className="text-[26px]">🏢</div>
              <div className="mt-2 text-xs text-neutral-400">该部门暂无成员——用「按部门授权」批量加入项目</div>
            </div>
          )}
        </div>
        </div>
        </div>
        </main>
      </div>

      {grantOpen && selected && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-[2px]">
          <div className="w-[520px] max-w-full animate-[pop_.18s_cubic-bezier(.2,.9,.3,1.1)] rounded-xl bg-white p-6 shadow-2xl" role="dialog" aria-label="按部门授权" data-sb-scope="org-grant-dialog">
            <div className="mb-4 flex items-center justify-between">
              <div className="text-base font-semibold">按部门授权 · {deptName(selected)}</div>
              <button className="flex h-7 w-7 items-center justify-center rounded-md text-neutral-400 hover:bg-neutral-100" onClick={() => { setGrantOpen(false); setGrantPreview(null); }}>✕</button>
            </div>
            <div className="mb-3.5 text-xs text-neutral-400">授权时刻把部门成员快照展开为项目成员（后续部门变动不影响已授权成员）</div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              <label className="block">
                <span className="text-xs text-neutral-500">目标项目</span>
                <select value={grantProj} onChange={(e) => setGrantProj(e.target.value)}
                        className="mt-1 w-full rounded border px-2 py-1.5" data-sb-scope="org-grant-project">
                  <option value="">选择项目…</option>
                  {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
              </label>
              <label className="block">
                <span className="text-xs text-neutral-500">项目角色</span>
                <select value={grantRole} onChange={(e) => setGrantRole(Number(e.target.value))}
                        className="mt-1 w-full rounded border px-2 py-1.5" data-sb-scope="org-grant-role">
                  {ROLE_OPTS.map((r) => <option key={r.v} value={r.v}>{r.n}</option>)}
                </select>
              </label>
            </div>
            <label className="mt-3 flex items-center gap-1.5 text-[13px] text-neutral-600">
              <input type="checkbox" defaultChecked />含子部门（{depts.find(d => d.id === selected)?.with_descendants_member_count ?? 0} 人）
            </label>
            {grantPreview && (
              <div className="mt-3.5 flex gap-3.5 rounded-lg bg-neutral-100 px-3 py-2.5 text-[12.5px] text-neutral-600" data-sb-scope="org-grant-preview">
                <span>预览：<b className="text-emerald-700">新增 {String(grantPreview.added?.length ?? 0)}</b></span>
                <span>调角色 {String(grantPreview.role_changed?.length ?? 0)}</span>
                <span className="text-amber-700">跳过 {String(grantPreview.skipped?.length ?? 0)}（停用/访客上限）</span>
                <span>不变 {String(grantPreview.unchanged?.length ?? 0)}</span>
              </div>
            )}
            <div className="mt-5 flex justify-end gap-2.5">
              <button onClick={() => { setGrantOpen(false); setGrantPreview(null); }}
                      className="h-8.5 rounded-md border border-neutral-300 px-3.5 text-[13px] text-neutral-600 hover:bg-neutral-50">取消</button>
              <button onClick={previewGrant} disabled={!grantProj}
                      className="h-8.5 rounded-md border border-neutral-300 px-3.5 text-[13px] text-neutral-600 hover:bg-neutral-50 disabled:opacity-50" data-sb-scope="org-grant-preview-btn">预览</button>
              <button onClick={execGrant} disabled={!grantProj}
                      className="h-8.5 rounded-md bg-blue-600 px-3.5 text-[13px] font-medium text-white hover:bg-blue-700 disabled:opacity-50" data-sb-scope="org-grant-submit">
                执行授权
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
