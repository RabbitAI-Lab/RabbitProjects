/** 守卫拦截补齐对话框（WF-004 §3.1）——error.details 按 guard 键分区渲染：
 * 字段区（required_fields/estimate_required，meta 驱动控件）+ 阻塞区
 * （blocker_completed，任务链接清单）+ 角色区仅 Toast（视图层分流）。
 *
 * BR-05 单请求闭环：guard_payload 携带补齐值重发 transitions/。 */
import { useState } from "react";

import { WorkflowAPI } from "../../services/api";
import type { ApiError } from "../../services/axios";

export interface GuardItem {
  field: string;
  code: string;
  message: string;
  guard: string;
  meta?: { label?: string; type?: string; options?: Array<{ label: string; value: string }> };
  blockers?: Array<{ id: string; issue_key: string; name: string; state_group: string }>;
  required_roles?: string[];
}

/** 拦截响应判定（error.code ∈ 四守卫主码集）。 */
export function isGuardBlocked(code: string | undefined): boolean {
  return code === "VALIDATION_REQUIRED_FIELD_MISSING"
    || code === "VALIDATION_ESTIMATE_REQUIRED"
    || code === "RESOURCE_TRANSITION_BLOCKED";
}

export function GuardDialog({ ws, projectId, issueId, transitionId, toStateId, transitionName, failures, members = [], onClose, onDone }: {
  ws: string;
  projectId: string;
  issueId: string;
  transitionId: string;
  toStateId: string;
  transitionName: string;
  failures: GuardItem[];
  members?: Array<{ user: { id: string; display_name: string } }>;
  onClose: () => void;
  onDone: () => void;
}) {
  const fieldItems = failures.filter((f) => f.guard === "required_fields" || f.guard === "estimate_required");
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [retryErrors, setRetryErrors] = useState<GuardItem[] | null>(null);

  async function submit() {
    setBusy(true);
    setRetryErrors(null);
    try {
      const payload: Record<string, unknown> = { to_state_id: toStateId, transition_id: transitionId };
      // estimate 数字 / assignees 数组（后端 _apply_guard_payload 按 M2M id 列表处理——标量会被拆成单字符）/ 其余字符串
      for (const [k, v] of Object.entries(values)) {
        const val = k === "estimate_minutes" ? Number(v)
          : k === "assignees" ? (v ? [v] : [])
          : v;
        payload.guard_payload = { ...(payload.guard_payload as object), [k]: val };
      }
      // to_state_id 由调用方在 open 时注入（简化：守卫对话框不重复持有目标态）
      await WorkflowAPI.execute(ws, projectId, issueId, payload as Parameters<typeof WorkflowAPI.execute>[3]);
      onDone();
      onClose();
    } catch (e) {
      // axios 层 reject 的是 friendly ApiError（code/details 顶层字段——services/axios.ts §解包）
      const err = e as ApiError;
      if (err.details && isGuardBlocked(err.code)) {
        setRetryErrors(err.details as unknown as GuardItem[]); // 新一轮缺口（§2.6 循环补齐——全量协议收敛）
      } else {
        setRetryErrors([{ field: "error", code: "ERROR", message: "重试失败，请刷新", guard: "error" }]);
      }
    } finally {
      setBusy(false);
    }
  }

  const shown = retryErrors ?? failures;
  const shownFields = shown.filter((f) => f.guard === "required_fields" || f.guard === "estimate_required");
  const shownBlockers = shown.find((f) => f.guard === "blocker_completed");

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/25" data-sb-scope="guard-dialog">
      <div className="w-[440px] bg-white rounded-xl shadow-2xl flex flex-col max-h-[80vh]">
        <header className="px-4 py-3 border-b border-neutral-200">
          <h2 className="text-sm font-semibold text-neutral-800">无法完成「{transitionName}」——请补齐以下内容</h2>
        </header>
        <div className="flex-1 overflow-auto p-4 space-y-4">
          {shownFields.length > 0 && (
            <section data-sb-scope="guard-fields">
              <div className="text-xs text-neutral-400 mb-2">缺少必填字段（{shownFields.length}）</div>
              <div className="space-y-2">
                {shownFields.map((f, i) => (
                  <label key={i} className="block text-sm" data-field={f.field}>
                    <span className="text-neutral-600">{f.meta?.label ?? f.field}</span>
                    {f.field === "estimate_minutes" ? (
                      <input type="number" min={1} value={values[f.field] ?? ""}
                        onChange={(e) => setValues((v) => ({ ...v, [f.field]: e.target.value }))}
                        className="mt-1 w-full border border-neutral-200 rounded-md px-2 h-9" placeholder="分钟" />
                    ) : f.meta?.options ? (
                      <select value={values[f.field] ?? ""}
                        onChange={(e) => setValues((v) => ({ ...v, [f.field]: e.target.value }))}
                        className="mt-1 w-full border border-neutral-200 rounded-md px-2 h-9">
                        <option value="">请选择</option>
                        {f.meta.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                      </select>
                    ) : f.field === "assignees" ? (
                      <select value={values[f.field] ?? ""}
                        onChange={(e) => setValues((v) => ({ ...v, [f.field]: e.target.value }))}
                        className="mt-1 w-full border border-neutral-200 rounded-md px-2 h-9">
                        <option value="">请选择负责人</option>
                        {members.map((m) => (
                          <option key={m.user.id} value={m.user.id}>{m.user.display_name}</option>
                        ))}
                      </select>
                    ) : f.field === "target_date" ? (
                      <input type="date" value={values[f.field] ?? ""}
                        onChange={(e) => setValues((v) => ({ ...v, [f.field]: e.target.value }))}
                        className="mt-1 w-full border border-neutral-200 rounded-md px-2 h-9" />
                    ) : (
                      <input type="text" value={values[f.field] ?? ""}
                        onChange={(e) => setValues((v) => ({ ...v, [f.field]: e.target.value }))}
                        className="mt-1 w-full border border-neutral-200 rounded-md px-2 h-9" />
                    )}
                  </label>
                ))}
              </div>
            </section>
          )}
          {shownBlockers?.blockers && shownBlockers.blockers.length > 0 && (
            <section data-sb-scope="guard-blockers">
              <div className="text-xs text-neutral-400 mb-2">前置任务未完成（{shownBlockers.blockers.length}）</div>
              <div className="space-y-1">
                {shownBlockers.blockers.map((b) => (
                  <div key={b.id} className="flex items-center gap-2 text-sm border border-neutral-100 rounded px-2 py-1.5">
                    <span className="font-medium text-neutral-700">{b.issue_key}</span>
                    <span className="text-neutral-500 flex-1 truncate">{b.name}</span>
                    <span className="text-xs text-neutral-400">{b.state_group}</span>
                  </div>
                ))}
              </div>
              <div className="text-xs text-neutral-400 mt-1.5">完成或取消前置后重试；管理员可强制流转。</div>
            </section>
          )}
        </div>
        <footer className="px-4 py-3 border-t border-neutral-200 flex justify-end gap-2">
          <button type="button" onClick={onClose}
            className="px-3 h-8 rounded-md border border-neutral-200 text-sm text-neutral-600 hover:bg-neutral-50">
            取消
          </button>
          <button type="button" disabled={busy || fieldItems.length === 0} onClick={() => void submit()}
            className="px-3 h-8 rounded-md bg-brand-600 text-white text-sm hover:bg-brand-700 disabled:opacity-50">
            补齐并流转
          </button>
        </footer>
      </div>
    </div>
  );
}
