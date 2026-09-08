/** 工作流列表页（WF-001 §3.1 / WF-005 §3——C.144，2026-09-09 入口补口轮）。
 *
 *  画布此前只能贴 URL 直达——本页作为项目域「工作流」落点：
 *  列表（草稿/已发布 vN/已归档）+ 新建草稿 + 进画布 + 归档；
 *  发布/保存仍在画布页顶栏（§3.3 发布错误面板）。 */
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { ProjectAPI, WorkflowAPI, unwrap } from "../services/api";
import { Topbar } from "../components/Topbar";
import { ProjectSidebar } from "../components/ProjectSidebar";
import { toast } from "../components/Toast";
import type { ApiError } from "../services/axios";

type WfRow = { id: string; name: string; issue_type_id: string | null; status: string;
  version: number; state_count: number; transition_count: number; updated_at: string };

const STATUS_BADGE: Record<string, { text: string; cls: string }> = {
  draft: { text: "草稿", cls: "bg-amber-50 text-amber-700" },
  published: { text: "", cls: "bg-emerald-50 text-emerald-700" },
  archived: { text: "已归档", cls: "bg-neutral-100 text-neutral-500" },
};

export default function WorkflowsListPage() {
  const { workspaceSlug: ws, projectId } = useParams();
  const nav = useNavigate();
  const [rows, setRows] = useState<WfRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [projName, setProjName] = useState("…");
  const [projIdentifier, setProjIdentifier] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!ws || !projectId) return;
    try {
      const r = await WorkflowAPI.list(ws, projectId);
      setRows(unwrap<WfRow[]>(r) ?? []);
    } catch {
      toast("加载工作流失败", "error");
    } finally {
      setLoading(false);
    }
  }, [ws, projectId]);

  // oxlint-disable-next-line react/set-state-in-effect -- 服务端 loader（SWR 前的手写范式，同 approvals.tsx 基线）
  useEffect(() => { load(); }, [load]);
  // oxlint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!ws || !projectId) return;
    ProjectAPI.detail(ws, projectId).then((r) => {
      const d = (r as unknown as { data: { name?: string; identifier?: string } }).data;
      setProjName(d?.name ?? "…");
      setProjIdentifier(d?.identifier ?? "");
    }).catch(() => {});
  }, [ws, projectId]);

  async function createDraft() {
    if (!ws || !projectId || !newName.trim()) return;
    setBusy(true);
    try {
      const r = await WorkflowAPI.create(ws, projectId, { name: newName.trim() });
      const d = (r as unknown as { data: { id: string } }).data;
      toast("草稿已创建——去画布搭流程", "ok");
      nav(`/${ws}/projects/${projectId}/workflows/${d.id}/canvas`);
    } catch (e) {
      toast((e as ApiError)?.message ?? "创建失败", "error");
    } finally {
      setBusy(false);
      setCreateOpen(false);
      setNewName("");
    }
  }

  async function archive(id: string, name: string) {
    if (!ws || !projectId) return;
    if (!window.confirm(`归档「${name}」后该项目该类型回退到兜底状态流，确认归档？`)) return;
    try {
      await WorkflowAPI.archive(ws, projectId, id);
      toast("已归档", "ok");
      await load();
    } catch (e) {
      toast((e as ApiError)?.message ?? "归档失败", "error");
    }
  }

  return (
    <div className="flex flex-col h-screen bg-neutral-50">
      <Topbar />
      <div className="flex flex-1 min-h-0">
        <ProjectSidebar projectName={projName} identifier={projIdentifier} />
        <main className="flex-1 min-w-0 flex flex-col" data-sb-scope="workflows-list">
          <header className="h-[56px] border-b border-neutral-200 bg-white px-5 flex items-center gap-3 shrink-0">
            <h1 className="text-[15px] font-semibold">工作流</h1>
            <span className="text-[12px] text-neutral-500">{projIdentifier}</span>
            <span className="text-[12px] text-neutral-400">状态机草稿/发布/归档管理（WF-001 §3.1）</span>
            <div className="flex-1" />
            <Link to={`/${ws}/workflow-templates`}
              className="h-[34px] px-3.5 inline-flex items-center border border-neutral-300 rounded-md text-[13px] hover:bg-neutral-50"
              data-sb-scope="goto-templates">从模板库下发</Link>
            <button type="button" data-sb-scope="wf-create-btn"
              onClick={() => setCreateOpen(true)}
              className="h-[34px] px-3.5 inline-flex items-center gap-1.5 bg-brand-500 text-white rounded-md text-[13px] font-medium hover:bg-brand-600">＋ 新建草稿</button>
          </header>
          <div className="flex-1 overflow-auto p-5">
            {loading ? <div className="text-sm text-neutral-400">加载中…</div> : rows.length === 0 ? (
              <div className="text-sm text-neutral-400 py-16 text-center">
                还没有工作流——「新建草稿」从空白开始，或「从模板库下发」套用预设四套
              </div>
            ) : (
              <table className="w-full bg-white border border-neutral-200 rounded-lg text-sm">
                <thead>
                  <tr className="text-left text-[11px] uppercase tracking-wider text-neutral-400 border-b border-neutral-200">
                    <th className="px-4 py-2.5 font-semibold">名称</th>
                    <th className="px-4 py-2.5 font-semibold">状态</th>
                    <th className="px-4 py-2.5 font-semibold">状态数</th>
                    <th className="px-4 py-2.5 font-semibold">流转边</th>
                    <th className="px-4 py-2.5 font-semibold">更新时间</th>
                    <th className="px-4 py-2.5 font-semibold text-right">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((w) => (
                    <tr key={w.id} data-sb-scope="wf-row" data-wf-id={w.id} className="border-b border-neutral-100 last:border-0">
                      <td className="px-4 py-3 font-medium text-neutral-800">{w.name}</td>
                      <td className="px-4 py-3">
                        <span className={`px-1.5 py-0.5 rounded text-xs ${STATUS_BADGE[w.status]?.cls ?? "bg-neutral-100 text-neutral-500"}`}
                          data-sb-scope="wf-status">
                          {w.status === "published" ? `已发布 v${w.version}` : STATUS_BADGE[w.status]?.text ?? w.status}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-neutral-600">{w.state_count}</td>
                      <td className="px-4 py-3 text-neutral-600">{w.transition_count}</td>
                      <td className="px-4 py-3 text-neutral-500 text-[12px]">{new Date(w.updated_at).toLocaleString("zh-CN", { hour12: false })}</td>
                      <td className="px-4 py-3 text-right space-x-2">
                        <Link to={`/${ws}/projects/${projectId}/workflows/${w.id}/canvas`}
                          className="px-2.5 h-7 inline-flex items-center rounded border border-neutral-200 text-[12.5px] hover:bg-neutral-50"
                          data-sb-scope="wf-open-canvas">进画布</Link>
                        {w.status !== "archived" && (
                          <button type="button" data-sb-scope="wf-archive-btn"
                            onClick={() => void archive(w.id, w.name)}
                            className="px-2.5 h-7 rounded border border-rose-200 text-rose-600 text-[12.5px] hover:bg-rose-50">归档</button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </main>
      </div>

      {createOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/25" data-sb-scope="wf-create-dialog">
          <div className="w-[420px] bg-white rounded-xl shadow-2xl p-5">
            <h2 className="text-sm font-semibold mb-3">新建工作流草稿</h2>
            <input type="text" value={newName} onChange={(e) => setNewName(e.target.value)}
              placeholder="如：缺陷处理流" autoFocus
              className="w-full h-9 border border-neutral-300 rounded-md px-3 text-sm" />
            <p className="text-[12px] text-neutral-400 mt-2">创建后进入画布搭建状态与流转边；发布前经服务端校验（§3.3）。</p>
            <div className="flex justify-end gap-2 mt-4">
              <button type="button" onClick={() => setCreateOpen(false)}
                className="h-8 px-3 rounded-md border border-neutral-200 text-sm">取消</button>
              <button type="button" disabled={busy || !newName.trim()} onClick={() => void createDraft()}
                className="h-8 px-3 rounded-md bg-brand-600 text-white text-sm disabled:opacity-50">创建并进画布</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
