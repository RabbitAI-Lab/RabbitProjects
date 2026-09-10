/** 工作流模板库（WF-005 §3——C.145，2026-09-09 入口补口轮）。
 *
 *  WS 级页面：预设四套浏览 + 两步下发向导（第一步预演回显 BR-05 状态映射，
 *  第二步 confirm 实例化为项目工作流草稿并引导进画布）。 */
import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router";

import { ProjectAPI, TemplateAPI, unwrap } from "../services/api";
import { toast } from "../components/Toast";
import type { ApiError } from "../services/axios";

type Tpl = { id: string; name: string; description: string; is_builtin: boolean;
  status: string; version: number; state_count: number; updated_at: string };
type ProjOpt = { id: string; name: string; identifier: string };

export default function WorkflowTemplatesPage() {
  const { workspaceSlug: ws } = useParams();
  const nav = useNavigate();
  const [rows, setRows] = useState<Tpl[]>([]);
  const [projects, setProjects] = useState<ProjOpt[]>([]);
  const [loading, setLoading] = useState(true);
  // 下发向导状态
  const [dist, setDist] = useState<{ tpl: Tpl; step: 1 | 2; projectId: string; preview: Record<string, unknown> | null } | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!ws) return;
    try {
      const [t, p] = await Promise.all([
        TemplateAPI.list(ws),
        ProjectAPI.listByWs(ws, {}),
      ]);
      setRows(unwrap<Tpl[]>(t) ?? []);
      setProjects(unwrap<ProjOpt[]>(p) ?? []);
    } catch {
      toast("加载模板库失败", "error");
    } finally {
      setLoading(false);
    }
  }, [ws]);

  // oxlint-disable-next-line react/set-state-in-effect -- 服务端 loader（同 approvals.tsx 基线）
  useEffect(() => { load(); }, [load]);

  /** 第一步：预演（confirm=false，回显状态映射/冲突）。 */
  async function startDist(tpl: Tpl) {
    setDist({ tpl, step: 1, projectId: projects[0]?.id ?? "", preview: null });
  }
  async function previewDist() {
    if (!ws || !dist?.tpl || !dist.projectId) return;
    setBusy(true);
    try {
      const r = await TemplateAPI.distribute(ws, dist.projectId, dist.tpl.id, false);
      setDist({ ...dist, step: 2, preview: (r as unknown as { data: Record<string, unknown> }).data });
    } catch (e) {
      toast((e as ApiError)?.message ?? "预演失败", "error");
    } finally {
      setBusy(false);
    }
  }
  /** 第二步：confirm 实例化 → 引导进画布。 */
  async function confirmDist() {
    if (!ws || !dist?.tpl || !dist.projectId) return;
    setBusy(true);
    try {
      const r = await TemplateAPI.distribute(ws, dist.projectId, dist.tpl.id, true);
      const d = (r as unknown as { data: { workflow_id?: string; id?: string } }).data;
      const wid = d?.workflow_id ?? d?.id;
      toast("模板已实例化为项目工作流草稿", "ok");
      setDist(null);
      if (wid) nav(`/${ws}/projects/${dist.projectId}/workflows/${wid}/canvas`);
    } catch (e) {
      toast((e as ApiError)?.message ?? "下发失败", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex-1 flex flex-col bg-neutral-50" data-sb-scope="template-library">
      <header className="h-12 border-b border-neutral-200 bg-white px-4 flex items-center gap-3">
        <h1 className="text-sm font-semibold text-neutral-800">工作流模板库</h1>
        <span className="text-xs text-neutral-400">预设四套 · 两步下发（WF-005 §3）</span>
      </header>
      <div className="flex-1 overflow-auto p-5">
        {loading ? <div className="text-sm text-neutral-400">加载中…</div> : (
          <div className="grid grid-cols-2 gap-4 max-w-[980px]">
            {rows.map((t) => (
              <div key={t.id} data-sb-scope="tpl-row" data-tpl-id={t.id}
                className="bg-white border border-neutral-200 rounded-lg p-4 flex flex-col gap-2">
                <div className="flex items-center gap-2">
                  <span className="text-[15px] font-semibold text-neutral-800">{t.name}</span>
                  {t.is_builtin && <span className="px-1.5 py-0.5 rounded text-[11px] bg-brand-50 text-brand-600">预设</span>}
                  <span className="text-[11px] text-neutral-400">v{t.version} · {t.state_count} 态</span>
                </div>
                <p className="text-[13px] text-neutral-500 flex-1">{t.description}</p>
                <div className="flex justify-end">
                  <button type="button" data-sb-scope="tpl-distribute-btn"
                    onClick={() => void startDist(t)}
                    className="h-8 px-3.5 rounded-md bg-brand-600 text-white text-[13px] hover:bg-brand-700">下发到项目…</button>
                </div>
              </div>
            ))}
            {rows.length === 0 && <div className="text-sm text-neutral-400 col-span-2 py-12 text-center">模板库为空（GET 幂等补种未生效？）</div>}
          </div>
        )}
      </div>

      {dist && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/25" data-sb-scope="dist-dialog">
          <div className="w-[560px] bg-white rounded-xl shadow-2xl p-5">
            <h2 className="text-sm font-semibold mb-1">下发「{dist.tpl.name}」</h2>
            <p className="text-[12px] text-neutral-400 mb-4">
              {dist.step === 1 ? "第 1 步 · 选择目标项目并预演" : "第 2 步 · 确认实例化（BR-05 状态映射如下）"}
            </p>
            {dist.step === 1 ? (
              <>
                <label className="block text-[13px] text-neutral-600 mb-3">
                  目标项目
                  <select value={dist.projectId} onChange={(e) => setDist({ ...dist, projectId: e.target.value })}
                    className="mt-1 w-full h-9 border border-neutral-300 rounded-md px-2 text-sm" data-sb-scope="dist-project-select">
                    <option value="">请选择</option>
                    {projects.map((p) => <option key={p.id} value={p.id}>{p.identifier} · {p.name}</option>)}
                  </select>
                </label>
                <div className="flex justify-end gap-2">
                  <button type="button" onClick={() => setDist(null)} className="h-8 px-3 rounded-md border border-neutral-200 text-sm">取消</button>
                  <button type="button" disabled={busy || !dist.projectId} data-sb-scope="dist-preview-btn"
                    onClick={() => void previewDist()}
                    className="h-8 px-3 rounded-md bg-brand-600 text-white text-sm disabled:opacity-50">预演</button>
                </div>
              </>
            ) : (
              <>
                <pre className="text-[12px] bg-neutral-50 border border-neutral-200 rounded-md p-3 max-h-[280px] overflow-auto whitespace-pre-wrap"
                  data-sb-scope="dist-preview">{JSON.stringify(dist.preview, null, 2)}</pre>
                <div className="flex justify-end gap-2 mt-4">
                  <button type="button" onClick={() => setDist(null)} className="h-8 px-3 rounded-md border border-neutral-200 text-sm">取消</button>
                  <button type="button" disabled={busy} data-sb-scope="dist-confirm-btn"
                    onClick={() => void confirmDist()}
                    className="h-8 px-3 rounded-md bg-brand-600 text-white text-sm disabled:opacity-50">确认下发（生成草稿）</button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
