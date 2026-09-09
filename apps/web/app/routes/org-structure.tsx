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
          className={`flex items-center gap-2 rounded px-2 py-1.5 cursor-pointer ${selected === d.id ? "bg-blue-50" : "hover:bg-neutral-100"}`}
          style={{ paddingLeft: 8 + depth * 20 }}
          onClick={() => setSelected(selected === d.id ? null : d.id)}
        >
          <span className="text-sm">{d.name}</span>
          <span className="text-xs text-neutral-400">
            {d.member_count ?? 0}/{d.with_descendants_member_count ?? 0}
          </span>
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
        <aside className="w-64 shrink-0 border-r bg-white p-3">
          <div className="mb-2 text-xs font-medium text-neutral-400">
            部门（直属/含子级）· 未分配 {unassigned} / 共 {total}
          </div>
          {renderTree(null)}
          <div className="mt-3 border-t pt-3">
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
              <div className="mt-2 flex gap-2">
                <button
                  onClick={() => setGrantOpen(true)}
                  className="flex-1 rounded bg-emerald-600 py-1.5 text-xs text-white"
                  data-sb-scope="org-grant-open"
                >
                  按部门授权
                </button>
                <button
                  onClick={() => removeDept(selected)}
                  className="rounded border border-red-200 px-2 py-1.5 text-xs text-red-600"
                >
                  删除
                </button>
              </div>
            )}
          </div>
        </aside>
        <main className="flex-1 overflow-auto p-4">
          <div className="mb-3 flex items-center gap-3">
            <h1 className="text-base font-semibold">成员归属</h1>
            <select
              value={filterDept ?? ""}
              onChange={(e) => setFilterDept(e.target.value || null)}
              className="rounded border px-2 py-1 text-sm"
              data-sb-scope="org-member-filter"
            >
              <option value="">全部成员</option>
              <option value="__unassigned__">未分配</option>
              {depts.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select>
            {filterDept && (
              <label className="flex items-center gap-1 text-xs text-neutral-500">
                <input type="checkbox" checked={withDesc}
                       onChange={(e) => setWithDesc(e.target.checked)} />
                含子部门
              </label>
            )}
          </div>
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-neutral-400">
              <tr><th className="py-1.5">成员</th><th>部门</th><th>岗位</th><th>操作</th></tr>
            </thead>
            <tbody>
              {shownMembers.map((m) => (
                <tr key={m.id} className="border-t">
                  <td className="py-1.5">{m.user.display_name}</td>
                  <td>
                    <select
                      value={filterDept === "__unassigned__" ? "" : (m.department_id ?? "")}
                      onChange={(e) => assignDept(m.id, e.target.value || null)}
                      className="rounded border px-1.5 py-0.5 text-xs"
                      data-sb-scope="org-member-dept"
                    >
                      <option value="">未分配</option>
                      {depts.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                    </select>
                  </td>
                  <td className="text-xs text-neutral-500">{m.company_role ?? "—"}</td>
                  <td className="text-xs text-neutral-400">{deptName(m.department_id)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!shownMembers.length && (
            <div className="py-10 text-center text-sm text-neutral-400">
              该部门暂无成员——用「按部门授权」批量加入项目
            </div>
          )}
        </main>
      </div>

      {grantOpen && selected && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
          <div className="w-[420px] rounded-lg bg-white p-5 shadow-xl" data-sb-scope="org-grant-dialog">
            <h2 className="mb-3 text-sm font-semibold">
              按部门授权 · {deptName(selected)}
            </h2>
            <div className="space-y-2 text-sm">
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
              {grantPreview && (
                <div className="rounded bg-neutral-50 p-2 text-xs" data-sb-scope="org-grant-preview">
                  预览：新增 {String(grantPreview.added?.length ?? 0)} ·
                  调整 {String(grantPreview.role_changed?.length ?? 0)} ·
                  跳过 {String(grantPreview.skipped?.length ?? 0)} ·
                  不变 {String(grantPreview.unchanged?.length ?? 0)}
                </div>
              )}
            </div>
            <div className="mt-4 flex justify-end gap-2">
              <button onClick={() => { setGrantOpen(false); setGrantPreview(null); }}
                      className="rounded border px-3 py-1.5 text-sm">取消</button>
              <button onClick={previewGrant} disabled={!grantProj}
                      className="rounded border px-3 py-1.5 text-sm" data-sb-scope="org-grant-preview-btn">预览</button>
              <button onClick={execGrant} disabled={!grantProj}
                      className="rounded bg-blue-600 px-3 py-1.5 text-sm text-white" data-sb-scope="org-grant-submit">
                执行授权
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
