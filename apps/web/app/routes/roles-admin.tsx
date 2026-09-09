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

const AVA_COLORS = ["#3f76ff", "#10b981", "#f59e0b", "#8b5cf6", "#ec4899", "#6b7280"];

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
          <div className="mb-[18px] flex items-center gap-3">
            <div>
              <div className="text-[17px] font-semibold">自定义角色</div>
              <div className="text-[12.5px] text-neutral-400">42 码冻结目录 · 只加不减并集 · GUEST 天花板</div>
            </div>
            <div className="ml-auto flex gap-2">
              {TEMPLATES.slice(0, 1).map((t) => (
                <button key={t.key} onClick={() => createFromTemplate(t.key)}
                        className="h-7 rounded-md border border-neutral-300 px-2.5 text-xs text-neutral-600 hover:bg-neutral-50" data-sb-scope="role-template">
                  采用「{t.n}」
                </button>
              ))}
              <button onClick={createBlank}
                      className="h-7 rounded-md bg-blue-600 px-2.5 text-xs font-medium text-white hover:bg-blue-700" data-sb-scope="role-new">
                新建角色
              </button>
            </div>
          </div>

          <div className="grid items-start gap-4" style={{ gridTemplateColumns: "300px 1fr" }}>
            {/* 角色列表 + 我的权限（冻结稿 O2：左列两卡） */}
            <div>
            <div className="overflow-hidden rounded-lg border bg-white shadow-sm" data-sb-scope="role-list">
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
            <div className="mt-3 rounded-lg border bg-white p-3.5 shadow-sm" data-sb-scope="role-mycards">
              <b className="text-[13px]">我的权限（并集）</b>
              <div className="my-1.5 text-xs text-neutral-400">
                固定角色 ∪ 已挂角色 = <b className="text-neutral-600">{(effective?.permissions as string[] | undefined)?.length ?? 14} 码</b>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {(((effective?.permissions as string[]) ?? []).slice(0, 14)).map((c) => (
                  <span key={c} className="role-chip">{c}</span>
                ))}
              </div>
            </div>
            </div>

            {/* 矩阵编辑器 */}
            <div className="rounded-lg border bg-white p-3" data-sb-scope="role-matrix">
              {selected ? (
                <>
                  <div className="mb-1 flex items-center gap-2.5">
                    <b className="text-[14.5px]">{selected.name}</b>
                    {selected.is_builtin_template && <span className="text-xs text-neutral-400">内置模板 · 可调整</span>}
                    <div className="ml-auto flex items-center gap-2">
                      <span className="font-mono text-xs text-neutral-400">已选 {draftPerms.size} / {catalogCount}</span>
                      {!editing ? (
                        <button onClick={() => setEditing(true)}
                                className="h-7 rounded-md border border-neutral-300 px-2.5 text-xs text-neutral-600 hover:bg-neutral-50" data-sb-scope="role-edit">
                          编辑权限
                        </button>
                      ) : (
                        <>
                          <button onClick={() => { setEditing(false); setDraftPerms(new Set(selected.permissions)); }}
                                  className="h-7 rounded-md border border-neutral-300 px-2.5 text-xs text-neutral-600 hover:bg-neutral-50">取消</button>
                          <button onClick={savePerms}
                                  className="h-7 rounded-md bg-blue-600 px-2.5 text-xs font-medium text-white hover:bg-blue-700" data-sb-scope="role-save">
                            保存（即时生效）
                          </button>
                        </>
                      )}
                      <button onClick={() => removeRole(selected)}
                              className="h-7 rounded-md border border-red-200 px-2.5 text-xs text-red-600 hover:bg-red-50">
                        删除
                      </button>
                    </div>
                  </div>
                  <div className="mb-3.5 text-xs text-neutral-400">保存后全部挂接者下一次操作即时生效（缓存主动失效）</div>
                  <div className="grid gap-x-5" style={{ gridTemplateColumns: "1fr 1fr" }} data-sb-scope="role-matrix">
                    {catalog.map((g) => {
                      const on = g.codes.filter((c) => draftPerms.has(c.code)).length;
                      const all = on === g.codes.length && g.codes.length > 0;
                      return (
                      <div key={g.domain} className="mb-2">
                        <div className="mb-1.5 flex items-center gap-2 border-b border-neutral-200 pb-1.5 text-xs font-semibold text-neutral-600">
                          <span>{g.domain}</span>
                          <label className="ml-auto flex cursor-pointer items-center gap-1.5 text-[11px] font-normal text-neutral-400"
                                 onClick={() => {
                                   if (!editing) return;
                                   const next = new Set(draftPerms);
                                   g.codes.forEach((c) => all ? next.delete(c.code) : next.add(c.code));
                                   setDraftPerms(next);
                                 }}>
                            <span className={`inline-flex h-[13px] w-[13px] items-center justify-center rounded border-[1.5px] text-[10px] ${all ? "border-blue-500 bg-blue-500 text-white" : "border-neutral-300"}`}>{all ? "✓" : ""}</span>
                            全选 {on}/{g.codes.length}
                          </label>
                        </div>
                        {g.codes.map((c) => {
                          const isOn = draftPerms.has(c.code);
                          return (
                            <div key={c.code}
                                   className={`flex items-center gap-2 py-[3px] text-[12.5px] ${isOn ? "text-neutral-800" : "text-neutral-400"} ${editing ? "cursor-pointer hover:text-neutral-600" : ""}`}
                                   role="checkbox" aria-checked={isOn}
                                   onClick={() => {
                                     if (!editing) return;
                                     const next = new Set(draftPerms);
                                     if (isOn) next.delete(c.code); else next.add(c.code);
                                     setDraftPerms(next);
                                   }}>
                              <span className={`inline-flex h-[15px] w-[15px] items-center justify-center rounded border-[1.5px] text-[10px] ${isOn ? "border-blue-500 bg-blue-500 text-white" : "border-neutral-300"}`} data-sb-scope="role-perm-check">{isOn ? "✓" : ""}</span>
                              <span className={`font-mono text-[11.5px] ${isOn ? "font-medium text-blue-700" : ""}`}>{c.code}</span>
                            </div>
                          );
                        })}
                      </div>
                      );
                    })}
                  </div>
                </>
              ) : (
                <div className="p-8 text-center text-sm text-neutral-400">
                  选择左侧角色查看权限矩阵
                </div>
              )}
            </div>
          </div>

          {/* 成员挂接（冻结稿：卡内分区 + role-chip） */}
          <div className="mt-4 border-t border-neutral-200 pt-3">
            <b className="text-[13px]">成员挂接（{selected?.assigned_count ?? 0}）</b>
            <table className="mt-1.5 w-full text-[13px]">
              <thead className="text-left text-xs font-medium text-neutral-400">
                <tr><th className="py-1.5">成员</th><th>已挂角色</th><th></th></tr>
              </thead>
              <tbody>
                {members.map((m) => (
                  <tr key={m.id} className="border-t border-neutral-200">
                    <td className="py-2">
                      <span className="inline-flex items-center gap-2">
                        <span className="flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-semibold text-white"
                              style={{ background: AVA_COLORS[(m.user.display_name.charCodeAt(0) || 48) % AVA_COLORS.length] }}>
                          {m.user.display_name[0]}
                        </span>
                        {m.user.display_name}
                      </span>
                    </td>
                    <td className="text-xs">
                      {(memberAssign[m.id] ?? []).length
                        ? (memberAssign[m.id] ?? []).map((a) => (
                            <span key={a.id} className="role-chip" style={{ marginRight: 4 }}>{a.role_name}</span>))
                        : <span className="text-neutral-400">—</span>}
                    </td>
                    <td className="text-right">
                      <button onClick={() => openMember(m.id)}
                              className="text-xs text-blue-700 hover:underline" data-sb-scope="role-assign-open">
                        {memberAssign[m.id] ? "刷新" : "挂接…"}
                      </button>
                      {(memberAssign[m.id] ?? []).map((a) => (
                        <button key={a.id} onClick={() => toggleAssign(m.id, selected!, false)}
                                className="ml-2 text-xs text-red-500 hover:underline">
                          卸除
                        </button>
                      ))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {selected && members.length > 0 && (
              <div className="mt-2 border-t border-neutral-200 pt-2">
                <button onClick={() => toggleAssign(members[0]!.id, selected, true)}
                        className="text-xs text-emerald-700 hover:underline" data-sb-scope="role-assign-first">
                  把「{selected.name}」挂给首位成员（GUEST 目标将 409 拒绝并文案列越界码）
                </button>
              </div>
            )}
          </div>
        </main>
      </div>
    </div>
  );
}
