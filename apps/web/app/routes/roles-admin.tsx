/** 自定义角色管理页（AUTH-008 §3.1/§3.2——C.151，Sprint-8 R6）。
 *
 *  角色列表（assigned_count）+ 权限矩阵编辑器（catalog 42 码按域分组勾选）+
 *  内置模板采用 + 成员挂接/卸除 + 「我的权限」并集面板。 */
import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router";

import { CustomRoleAPI, ProjectMemberAPI, unwrap } from "../services/api";
import { Topbar } from "../components/Topbar";
import { ProjectSidebar } from "../components/ProjectSidebar";
import { toast } from "../components/Toast";

type Role = {
  id: string; name: string; description: string;
  permissions: string[]; is_builtin_template: boolean;
  assigned_count?: number;
};
type CatalogGroup = { domain: string; codes: { code: string }[] };
type Member = { id: string; user: { display_name: string }; role: number };
type Assignment = { id: string; role_id: string; role_name: string };

const TEMPLATES = [
  { key: "qa_engineer", n: "测试工程师" },
  { key: "external_collab", n: "外包协作" },
  { key: "stakeholder_readonly", n: "只读干系人" },
];

export default function RolesAdminPage() {
  const { workspaceSlug: ws, projectId } = useParams();
  const [roles, setRoles] = useState<Role[]>([]);
  const [catalog, setCatalog] = useState<CatalogGroup[]>([]);
  const [members, setMembers] = useState<Member[]>([]);
  const [selected, setSelected] = useState<Role | null>(null);
  const [draftPerms, setDraftPerms] = useState<Set<string>>(new Set());
  const [editing, setEditing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [memberAssign, setMemberAssign] = useState<Record<string, Assignment[]>>({});
  const [effective, setEffective] = useState<Record<string, unknown> | null>(null);
  const [projName, setProjName] = useState("");
  const [projIdentifier, setProjIdentifier] = useState("");

  const load = useCallback(async () => {
    if (!ws || !projectId) return;
    try {
      const [r, c, m] = await Promise.all([
        CustomRoleAPI.list(ws, projectId),
        CustomRoleAPI.catalog(ws, projectId).catch(() => null),
        ProjectMemberAPI.list(ws, projectId).catch(() => null),
      ]);
      setRoles(unwrap<Role[]>(r) ?? []);
      const cat = (c as unknown as { data?: { groups?: CatalogGroup[] } } | null)?.data;
      setCatalog(cat?.groups ?? []);
      setMembers(unwrap<Member[]>(m as unknown) ?? []);
    } catch {
      toast("加载角色失败", "error");
    } finally {
      setLoading(false);
    }
  }, [ws, projectId]);

  // oxlint-disable-next-line react/set-state-in-effect -- 服务端 loader（同 approvals.tsx 基线）
  useEffect(() => { load(); }, [load]);
  // oxlint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!ws || !projectId) return;
    import("../services/api").then(({ ProjectAPI }) =>
      ProjectAPI.detail(ws!, projectId!).then((r) => {
        const d = (r as unknown as { data?: { name?: string; identifier?: string } }).data;
        setProjName(d?.name ?? ""); setProjIdentifier(d?.identifier ?? "");
      }).catch(() => {}));
  }, [ws, projectId]);

  async function openMember(memberId: string) {
    if (!ws || !projectId) return;
    if (memberAssign[memberId]) return;
    try {
      const [a, e] = await Promise.all([
        CustomRoleAPI.assignments(ws, projectId, memberId),
        CustomRoleAPI.effective(ws, projectId, memberId).catch(() => null),
      ]);
      setMemberAssign((prev) => ({
        ...prev, [memberId]: unwrap<Assignment[]>(a) ?? [],
      }));
      if (e) setEffective(unwrap<Record<string, unknown>>(e as unknown));
    } catch {
      toast("加载挂接失败", "error");
    }
  }

  async function createFromTemplate(key: string) {
    if (!ws || !projectId) return;
    try {
      await CustomRoleAPI.create(ws, projectId, { template_key: key });
      toast("模板角色已创建（可调整权限码）");
      load();
    } catch (e) {
      toast((e as { message?: string })?.message ?? "创建失败", "error");
    }
  }

  async function createBlank() {
    if (!ws || !projectId) return;
    const name = prompt("新角色名称（≤40 字）");
    if (!name) return;
    try {
      await CustomRoleAPI.create(ws, projectId, { name, permissions: ["issue.read"] });
      load();
    } catch (e) {
      toast((e as { message?: string })?.message ?? "创建失败（重名/超上限）", "error");
    }
  }

  async function savePerms() {
    if (!ws || !projectId || !selected) return;
    try {
      await CustomRoleAPI.patch(ws, projectId, selected.id, {
        permissions: Array.from(draftPerms).sort(),
      });
      toast("权限码已保存（全部挂接者即时生效）");
      setEditing(false);
      load();
    } catch (e) {
      toast((e as { message?: string })?.message ?? "保存失败", "error");
    }
  }

  async function removeRole(role: Role) {
    if (!ws || !projectId || !confirm(`删除角色「${role.name}」？（须先卸除全部挂接）`)) return;
    try {
      await CustomRoleAPI.delete(ws, projectId, role.id);
      toast("角色已删除");
      setSelected(null);
      load();
    } catch (e) {
      toast((e as { message?: string })?.message ?? "有挂接或引用不可删除", "error");
    }
  }

  async function toggleAssign(memberId: string, role: Role, on: boolean) {
    if (!ws || !projectId) return;
    try {
      if (on) {
        await CustomRoleAPI.assign(ws, projectId, memberId, role.id);
      } else {
        await CustomRoleAPI.revoke(ws, projectId, memberId, role.id);
      }
      const a = await CustomRoleAPI.assignments(ws, projectId, memberId);
      setMemberAssign((prev) => ({ ...prev, [memberId]: unwrap<Assignment[]>(a) ?? [] }));
    } catch (e) {
      toast((e as { message?: string })?.message ?? "挂接失败（访客天花板或权限）", "error");
    }
  }

  const catalogCount = catalog.reduce((n, g) => n + g.codes.length, 0);

  if (loading) return <div className="p-10 text-sm text-neutral-400">加载角色…</div>;

  return (
    <div className="flex h-screen flex-col">
      <Topbar />
      <div className="flex flex-1 overflow-hidden">
        <ProjectSidebar projectName={projName} identifier={projIdentifier} />
        <main className="flex-1 overflow-auto p-4">
          <div className="mb-3 flex items-center justify-between">
            <h1 className="text-base font-semibold">自定义角色（目录 {catalogCount} 码）</h1>
            <div className="flex gap-2">
              {TEMPLATES.map((t) => (
                <button key={t.key} onClick={() => createFromTemplate(t.key)}
                        className="rounded border px-2.5 py-1 text-xs" data-sb-scope="role-template">
                  采用「{t.n}」
                </button>
              ))}
              <button onClick={createBlank}
                      className="rounded bg-blue-600 px-3 py-1 text-xs text-white" data-sb-scope="role-new">
                新建角色
              </button>
            </div>
          </div>

          <div className="grid grid-cols-[280px_1fr] gap-4">
            {/* 角色列表 */}
            <div className="rounded-lg border bg-white" data-sb-scope="role-list">
              {roles.map((r) => (
                <button key={r.id}
                        onClick={() => { setSelected(r); setDraftPerms(new Set(r.permissions)); setEditing(false); }}
                        className={`block w-full border-b px-3 py-2 text-left text-sm last:border-0 ${selected?.id === r.id ? "bg-blue-50" : "hover:bg-neutral-50"}`}>
                  <div className="flex justify-between">
                    <span>{r.name}</span>
                    <span className="text-xs text-neutral-400">{r.assigned_count ?? 0} 人挂接</span>
                  </div>
                  <div className="text-xs text-neutral-400">{r.permissions.length} 码</div>
                </button>
              ))}
              {!roles.length && (
                <div className="p-6 text-center text-xs text-neutral-400">
                  尚无自定义角色——从内置模板开始
                </div>
              )}
            </div>

            {/* 矩阵编辑器 */}
            <div className="rounded-lg border bg-white p-3" data-sb-scope="role-matrix">
              {selected ? (
                <>
                  <div className="mb-2 flex items-center justify-between">
                    <div>
                      <span className="text-sm font-medium">{selected.name}</span>
                      <span className="ml-2 text-xs text-neutral-400">{selected.description || "—"}</span>
                    </div>
                    <div className="flex gap-2">
                      {!editing ? (
                        <button onClick={() => setEditing(true)}
                                className="rounded border px-2.5 py-1 text-xs" data-sb-scope="role-edit">
                          编辑权限
                        </button>
                      ) : (
                        <button onClick={savePerms}
                                className="rounded bg-blue-600 px-2.5 py-1 text-xs text-white" data-sb-scope="role-save">
                          保存
                        </button>
                      )}
                      <button onClick={() => removeRole(selected)}
                              className="rounded border border-red-200 px-2.5 py-1 text-xs text-red-600">
                        删除
                      </button>
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-x-4 md:grid-cols-3">
                    {catalog.map((g) => (
                      <div key={g.domain} className="mb-2">
                        <div className="mb-1 text-xs font-medium text-neutral-500">{g.domain}</div>
                        {g.codes.map((c) => {
                          const on = draftPerms.has(c.code);
                          return (
                            <label key={c.code}
                                   className={`flex items-center gap-1.5 py-0.5 text-xs ${editing ? "cursor-pointer" : "opacity-80"}`}>
                              <input type="checkbox" checked={on} disabled={!editing}
                                     onChange={(e) => {
                                       const next = new Set(draftPerms);
                                       if (e.target.checked) next.add(c.code);
                                       else next.delete(c.code);
                                       setDraftPerms(next);
                                     }}
                                     data-sb-scope="role-perm-check" />
                              <span className={on ? "font-medium" : "text-neutral-500"}>{c.code}</span>
                            </label>
                          );
                        })}
                      </div>
                    ))}
                  </div>
                </>
              ) : (
                <div className="p-8 text-center text-sm text-neutral-400">
                  选择左侧角色查看权限矩阵
                </div>
              )}
            </div>
          </div>

          {/* 成员挂接 */}
          <div className="mt-4 rounded-lg border bg-white">
            <div className="border-b px-3 py-2 text-sm font-medium">成员挂接</div>
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-neutral-400">
                <tr><th className="px-3 py-1.5">成员</th><th>已挂角色</th><th>操作</th></tr>
              </thead>
              <tbody>
                {members.map((m) => (
                  <tr key={m.id} className="border-t">
                    <td className="px-3 py-1.5">{m.user.display_name}</td>
                    <td className="text-xs">
                      {(memberAssign[m.id] ?? []).map((a) => a.role_name).join("、") || "—"}
                    </td>
                    <td className="px-3">
                      <button onClick={() => openMember(m.id)}
                              className="text-xs text-blue-600" data-sb-scope="role-assign-open">
                        {memberAssign[m.id] ? "刷新" : "挂接…"}
                      </button>
                      {(memberAssign[m.id] ?? []).map((a) => (
                        <button key={a.id} onClick={() => toggleAssign(m.id, selected!, false)}
                                className="ml-2 text-xs text-red-500" title={selected?.name}>
                          卸 {a.role_name}
                        </button>
                      ))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {selected && members.length > 0 && (
              <div className="border-t px-3 py-2">
                <button onClick={() => toggleAssign(members[0]!.id, selected, true)}
                        className="text-xs text-emerald-600" data-sb-scope="role-assign-first">
                  把「{selected.name}」挂给首位成员
                </button>
              </div>
            )}
          </div>

          {effective && (
            <div className="mt-4 rounded-lg border bg-neutral-50 p-3 text-xs" data-sb-scope="role-effective">
              <span className="font-medium">我的权限（并集 {String((effective.permissions as string[])?.length ?? 0)} 码）：</span>
              <span className="text-neutral-500">
                {((effective.permissions as string[]) ?? []).slice(0, 20).join(" · ")}…
              </span>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
