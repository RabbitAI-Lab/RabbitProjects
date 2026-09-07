import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router";
import { Topbar } from "../components/Topbar";
import { ProjectSidebar } from "../components/ProjectSidebar";
import { LifecycleAPI, ProjectAPI, type ProjectStatus } from "../services/api";
import { toast } from "../components/Toast";
import { LabelsAdminModal } from "./labels-admin";

export default function ProjectSettings() {
  const { workspaceSlug, projectId } = useParams<{ workspaceSlug: string; projectId: string }>();
  const nav = useNavigate();
  const [loaded, setLoaded] = useState(false);
  const [showLabels, setShowLabels] = useState(false);
  const [name, setName] = useState("");
  const [identifier, setIdentifier] = useState("");
  const [description, setDescription] = useState("");
  const [confirm, setConfirm] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [saved, setSaved] = useState(false);
  // ── Sprint-5（PROJ-003 §3.2 / C.129）：生命周期区块 ──
  const [status, setStatus] = useState<ProjectStatus>("active");
  const [logs, setLogs] = useState<Array<{ id: string; from_status: string; to_status: string;
    operator: { display_name: string } | null; reason: string; transitioned_at: string }>>([]);
  const [closeWizard, setCloseWizard] = useState<{ openCount: number } | null>(null);
  const [closeName, setCloseName] = useState("");

  /** 用户已改动过表单 —— 异步回写不得再覆盖。 */
  const dirtyRef = useRef(false);

  // 加载项目详情回显（修复：原版不拉数据，空字段 + PATCH 空 name 会清空项目名）
  useEffect(() => {
    let cancelled = false;
    ProjectAPI.detail(workspaceSlug!, projectId!)
      .then((r) => {
        if (cancelled) return;
        setLoaded(true);
        // 竞态防护（INT-D2 实测）：详情响应可能晚于用户首次输入到达，
        // 无条件 setName 会把刚改好的项目名悄悄回滚成服务端旧值 ——
        // 结果「保存」提交的是旧名字，删除确认框也永远匹配不上。
        if (dirtyRef.current) return;
        const p = (r as any).data;
        setName(p.name ?? ""); setIdentifier(p.identifier ?? ""); setDescription(p.description ?? "");
        if (p.status) setStatus(p.status);
        LifecycleAPI.statusLogs(workspaceSlug!, projectId!).then((lr) =>
          setLogs(((lr as any).data ?? []) as typeof logs)).catch(() => {});
      })
      .catch(() => { if (!cancelled) setLoaded(true); });
    return () => { cancelled = true; };
  }, [workspaceSlug, projectId]);

  async function save() {
    await ProjectAPI.patch(workspaceSlug!, projectId!, { name, description });
    setSaved(true); setTimeout(() => setSaved(false), 1800);
  }

  async function doDelete() {
    await ProjectAPI.delete(workspaceSlug!, projectId!);
    nav(`/${workspaceSlug}/projects`);
  }

  return (
    <div className="flex flex-col h-screen">
      <Topbar />
      <div className="flex flex-1 min-h-0">
        <ProjectSidebar projectName={name || "…"} identifier={identifier} />
        <main className="flex-1 min-w-0 overflow-y-auto p-5">
          {!loaded ? <div className="text-sm text-neutral-500">加载中…</div> : (
          <div className="max-w-[640px]">
            <div className="bg-white border border-neutral-200 rounded-lg p-5 mb-4">
              <div className="text-[15px] font-semibold mb-3.5">基本信息</div>
              <div className="flex items-center gap-3 mb-3.5">
                <label htmlFor="st-name" className="w-20 text-[13px] text-neutral-500 shrink-0">项目名称</label>
                <input id="st-name" className="flex-1 h-9 border border-neutral-300 rounded-md px-2.5" value={name} onChange={(e) => { dirtyRef.current = true; setName(e.target.value); }} />
              </div>
              <div className="flex items-center gap-3 mb-3.5">
                <label htmlFor="st-id" className="w-20 text-[13px] text-neutral-500 shrink-0">项目标识符</label>
                <input id="st-id" className="w-[130px] h-9 border border-neutral-300 rounded-md px-2.5 font-mono shrink-0" disabled value={identifier} />
                <span className="text-xs text-neutral-500 inline-flex items-center gap-1">🔒 创建后不可修改</span>
              </div>
              <div className="flex items-center gap-3 mb-3.5">
                <label htmlFor="st-desc" className="w-20 text-[13px] text-neutral-500 shrink-0">项目描述</label>
                <textarea id="st-desc" className="flex-1 border border-neutral-300 rounded-md p-2 text-sm" rows={3} value={description} onChange={(e) => { dirtyRef.current = true; setDescription(e.target.value); }} />
              </div>
              <div className="text-right flex items-center justify-end gap-2">
                {saved && <span className="text-xs text-emerald-600 inline-flex items-center gap-1">✓ 已保存</span>}
                <button onClick={save} disabled={!name.trim()} className="h-[34px] px-3.5 bg-brand-500 text-white rounded-md hover:bg-brand-600 disabled:opacity-50">保存更改</button>
              </div>
            </div>

            {/* C.26 标签管理入口（ADR-0011 #13：项目设置页入口之一） */}
            <div className="bg-white border border-neutral-200 rounded-lg p-5 mb-4">
              <div className="flex items-center justify-between mb-1.5">
                <div>
                  <div className="text-[15px] font-semibold">项目标签</div>
                  <div className="text-[12px] text-neutral-500 mt-0.5">为该项目配置任务可用的标签集合（如 紧急 / 阻塞 / 后端）。</div>
                </div>
                {/* ADR-0011 #13：标签管理是 modal，不单独成页。
                    原实现 Link 到未注册的 `settings/labels` 路由 → 点进去 404。 */}
                <button type="button" onClick={() => setShowLabels(true)}
                        data-sb-scope="settings-open-labels"
                        className="h-[30px] px-3 bg-brand-500 text-white rounded-md text-[13px] inline-flex items-center hover:bg-brand-600">
                  管理标签
                </button>
              </div>
              <div className="text-[12px] text-neutral-400 mt-2">点上方「管理标签」打开 720px 弹窗（颜色 / 名称 / 停用 / 引用计数）。</div>
            </div>

            {/* C.129 项目生命周期（PROJ-003 §3.2 / §2.2 守卫矩阵） */}
            <div className="bg-white border border-neutral-200 rounded-lg p-5 mb-4" data-sb-scope="lifecycle-block">
              <div className="text-[15px] font-semibold mb-1">生命周期</div>
              <div className="text-[12px] text-neutral-500 mb-3">closed 为终态（重开 = 副本）；archived 只读（恢复归 PROJ_ADMIN）。</div>
              <LifecycleSection status={status} logs={logs}
                onTransition={async (to, force, reason) => {
                  try {
                    const r = await LifecycleAPI.transition(workspaceSlug!, projectId!, { to_status: to, ...(force !== undefined ? { force } : {}), ...(reason !== undefined ? { reason } : {}) });
                    const d = (r as any).data;
                    setStatus(d.status);
                    if (d.affected_issues) toast(`已${CN[to]}——${d.affected_issues} 个开放任务批量取消`, "ok");
                    else if (!d.idempotent) toast(`已${CN[to]}`, "ok");
                    LifecycleAPI.statusLogs(workspaceSlug!, projectId!).then((lr) => setLogs((lr as any).data ?? []));
                    return true;
                  } catch (e) {
                    const err = (e as { response?: { data?: { error?: { details?: Array<{ code: string; open_count?: number }> } } } });
                    const det = err?.response?.data?.error?.details?.[0];
                    if (det?.code === "OPEN_ISSUES") {
                      setCloseWizard({ openCount: det.open_count ?? 0 });
                      return false;
                    }
                    toast(e instanceof Error ? e.message : "转换失败", "error");
                    return false;
                  }
                }}
                onDuplicate={async () => {
                  try {
                    const r = await LifecycleAPI.duplicate(workspaceSlug!, projectId!);
                    const d = (r as any).data;
                    toast("已创建 draft 副本（四件套与成员已复制）", "ok");
                    if (d?.id) nav(`/${workspaceSlug}/projects/${d.id}/settings`);
                  } catch { toast("副本重开失败", "error"); }
                }} />
            </div>

            {closeWizard && (
              <div className="fixed inset-0 z-50 bg-black/30 flex items-center justify-center p-4" role="dialog" aria-modal="true">
                <div className="bg-white rounded-xl p-6 w-[480px] max-w-full" data-sb-scope="close-wizard">
                  <div className="text-[15px] font-semibold mb-3">关闭项目</div>
                  <p className="text-[13px] text-neutral-600 mb-4">
                    还有 <b className="text-red-600">{closeWizard.openCount}</b> 个开放任务。
                    可先去处理，或强制关闭——开放任务将批量迁入「已取消」。
                  </p>
                  {closeWizard.openCount > 0 && (
                    <>
                      <div className="text-[12.5px] text-neutral-500 mb-1.5">输入项目名 <b>{name}</b> 确认强制关闭：</div>
                      <input value={closeName} onChange={(e) => setCloseName(e.target.value)} aria-label="确认项目名"
                        className="w-full h-9 border border-neutral-300 rounded-md px-2.5 mb-4" />
                    </>
                  )}
                  <div className="flex justify-end gap-2">
                    <button onClick={() => { setCloseWizard(null); setCloseName(""); }}
                      className="h-9 px-4 rounded-md border border-neutral-300 text-[13px]">先去处理</button>
                    <button disabled={closeName !== name}
                      onClick={async () => {
                        const r = await LifecycleAPI.transition(workspaceSlug!, projectId!, {
                          to_status: "closed", force: true, reason: "强制关闭（向导确认）" });
                        setStatus((r as any).data.status);
                        setCloseWizard(null); setCloseName("");
                        toast("已关闭（终态——重开走副本）", "ok");
                      }}
                      className="h-9 px-4 rounded-md bg-red-600 text-white text-[13px] disabled:opacity-50">强制关闭</button>
                  </div>
                </div>
              </div>
            )}

            <div className="bg-white border border-red-300 rounded-lg p-5">
              <div className="text-[15px] font-semibold mb-3.5 text-red-700">⚠️ 危险区域</div>
              <div className="text-xs text-neutral-500 mb-3">删除项目将同时删除其下全部任务，此操作不可在界面恢复。</div>
              <div className="text-right"><button onClick={() => setDeleting(true)} className="h-[34px] px-3.5 bg-red-500 text-white rounded-md hover:bg-red-600">删除项目</button></div>
            </div>
            {deleting && (
              <div className="fixed inset-0 bg-black/30 flex items-center justify-center p-4 z-50">
                <div className="bg-white rounded-xl shadow-lg w-[480px] p-6">
                  <div className="flex items-center justify-between mb-[18px]"><div className="text-base font-semibold">删除项目</div><button onClick={() => { setDeleting(false); setConfirm(""); }}>✕</button></div>
                  <div className="flex items-center gap-2 text-[13px] px-3 py-2 bg-red-50 text-red-700 rounded-md mb-3">⚠ 此操作不可恢复。输入项目名称 <b>{name}</b> 以确认。</div>
                  <input className="w-full h-9 border border-neutral-300 rounded-md px-2.5 mb-3" placeholder={name} value={confirm} onChange={(e) => setConfirm(e.target.value)} />
                  <div className="flex justify-end gap-2.5">
                    <button onClick={() => { setDeleting(false); setConfirm(""); }} className="h-[34px] px-3.5 bg-white border border-neutral-300 rounded-md">取消</button>
                    <button disabled={confirm !== name} onClick={doDelete} className="h-[34px] px-3.5 bg-red-500 text-white rounded-md disabled:opacity-50">删除项目</button>
                  </div>
                </div>
              </div>
            )}
          </div>
          )}
        </main>
      </div>
      {showLabels && workspaceSlug && projectId && (
        <LabelsAdminModal workspaceSlug={workspaceSlug} projectId={projectId} onClose={() => setShowLabels(false)} />
      )}
    </div>
  );
}


const CN: Record<ProjectStatus, string> = {
  draft: "转为草稿", active: "启用", archived: "归档", closed: "关闭",
};
const STATUS_STYLE: Record<ProjectStatus, string> = {
  draft: "bg-neutral-100 text-neutral-600",
  active: "bg-green-50 text-green-700",
  archived: "bg-amber-50 text-amber-700",
  closed: "bg-neutral-800 text-white",
};

/** C.129 生命周期区块（状态徽章 + 允许目标按钮 + 状态历史）。 */
function LifecycleSection({ status, logs, onTransition, onDuplicate }: {
  status: ProjectStatus;
  logs: Array<{ id: string; from_status: string; to_status: string;
                operator: { display_name: string } | null; reason: string; transitioned_at: string }>;
  onTransition: (to: ProjectStatus, force?: boolean, reason?: string) => Promise<boolean>;
  onDuplicate: () => Promise<void>;
}) {
  const allowed: ProjectStatus[] =
    status === "draft" ? ["active"]
    : status === "active" ? ["archived", "closed"]
    : status === "archived" ? ["active", "closed"]
    : [];
  return (
    <div>
      <div className="flex items-center gap-3 mb-3">
        <span className={`px-2.5 py-1 rounded-full text-[12.5px] ${STATUS_STYLE[status]}`} data-sb-scope="lifecycle-status">
          {status === "closed" ? "🔒 " : ""}{CN[status]}
        </span>
        <div className="flex gap-2">
          {allowed.map((to) => (
            <button key={to} data-sb-scope={`lifecycle-to-${to}`}
              onClick={() => void onTransition(to)}
              className={`h-8 px-3 rounded-md text-[12.5px] border ${
                to === "closed" ? "border-red-300 text-red-600 hover:bg-red-50"
                : "border-neutral-300 text-neutral-700 hover:bg-neutral-50"}`}>
              {to === "active" ? (status === "draft" ? "启用项目" : "恢复项目") : CN[to]}
            </button>
          ))}
          {status === "closed" && (
            <button onClick={() => void onDuplicate()} data-sb-scope="lifecycle-duplicate"
              className="h-8 px-3 rounded-md border border-brand-300 text-brand-600 hover:bg-brand-50 text-[12.5px]">
              重开为副本
            </button>
          )}
        </div>
      </div>
      {logs.length > 0 && (
        <ul className="border-t border-neutral-100 pt-2 text-[12.5px] text-neutral-600 space-y-1">
          {logs.slice(0, 6).map((l) => (
            <li key={l.id} className="flex items-center gap-2">
              <span className="font-mono text-neutral-400">{l.transitioned_at.slice(0, 16).replace("T", " ")}</span>
              <span>{l.from_status || "创建"} → {l.to_status}</span>
              {l.operator && <span className="text-neutral-400">（{l.operator.display_name}）</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
