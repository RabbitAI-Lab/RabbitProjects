/** 审批中心（WF-002 §3.1 / §4.6）——三 Tab：待办 / 已办 / 我发起的。
 *
 * C.140 附录 C 表面（Sprint-7 UI parity）：Tab 切换 / 行点击开抽屉 /
 * approve/reject 动作（reject 意见必填）/ 状态徽标五态。 */
import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router";

import { ApprovalAPI, type ApprovalRow } from "../services/api";

const STATUS_LABEL: Record<string, { text: string; cls: string }> = {
  pending: { text: "审批中", cls: "bg-amber-50 text-amber-700" },
  approved: { text: "已通过", cls: "bg-emerald-50 text-emerald-700" },
  rejected: { text: "已驳回", cls: "bg-rose-50 text-rose-700" },
  withdrawn: { text: "已撤回", cls: "bg-neutral-100 text-neutral-500" },
  terminated: { text: "已终止", cls: "bg-neutral-100 text-neutral-500" },
};

type Tab = "pending" | "acted" | "mine";

export default function ApprovalCenter() {
  const { workspaceSlug: ws } = useParams();
  const nav = useNavigate();
  const [tab, setTab] = useState<Tab>("pending");
  const [rows, setRows] = useState<ApprovalRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [detailId, setDetailId] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!ws) return;
    try {
      const t = tab;
      const fn = t === "pending" ? ApprovalAPI.pending
        : t === "acted" ? ApprovalAPI.acted : ApprovalAPI.mine;
      const r = await fn(ws);
      setRows(r.data ?? []);
      setError(null);
    } catch {
      setError("加载失败，请重试");
    } finally {
      setLoading(false);
    }
  }, [ws, tab]);

  // oxlint-disable-next-line react/set-state-in-effect -- 服务端数据加载（SWR 范式前的手写 loader）；
  // 规则对 async loader 链保守误报（同构 fetchAll 旧文件未触发，基线 23 项存量同类）
  useEffect(() => { load(); /* eslint-disable-line react-hooks/set-state-in-effect -- 服务端 loader 保守误报（基线存量同类 23 项） */ }, [load]);

  const tabs: Array<{ key: Tab; label: string }> = [
    { key: "pending", label: "待办" },
    { key: "acted", label: "已办" },
    { key: "mine", label: "我发起的" },
  ];

  return (
    <div className="flex-1 flex flex-col bg-neutral-50" data-sb-scope="approval-center">
      <header className="h-12 border-b border-neutral-200 bg-white px-4 flex items-center gap-4">
        <h1 className="text-sm font-semibold text-neutral-800">审批中心</h1>
        <nav className="flex gap-1" data-sb-scope="approval-tabs">
          {tabs.map((t) => (
            <button key={t.key} type="button" data-tab={t.key}
              onClick={() => setTab(t.key)}
              className={`px-3 h-7 rounded-md text-sm ${tab === t.key
                ? "bg-brand-50 text-brand-600 font-medium" : "text-neutral-600 hover:bg-neutral-100"}`}>
              {t.label}{t.key === "pending" && rows.length > 0 && tab === "pending" ? ` (${rows.length})` : ""}
            </button>
          ))}
        </nav>
      </header>

      <div className="flex-1 overflow-auto p-4" data-sb-scope="approval-list">
        {error && <div className="text-sm text-rose-600">{error}</div>}
        {loading ? (
          <div className="text-sm text-neutral-400">加载中…</div>
        ) : rows.length === 0 ? (
          <div className="text-sm text-neutral-400 py-16 text-center" data-sb-scope="approval-empty">
            {tab === "pending" ? "没有待处理的审批" : tab === "acted" ? "还没有审批记录" : "你还没有发起过审批"}
          </div>
        ) : (
          <table className="w-full bg-white rounded-lg border border-neutral-200 text-sm">
            <thead>
              <tr className="text-left text-neutral-500 border-b border-neutral-200">
                <th className="px-3 py-2 font-normal">任务</th>
                <th className="px-3 py-2 font-normal">审批流</th>
                <th className="px-3 py-2 font-normal">状态</th>
                <th className="px-3 py-2 font-normal">发起时间</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.instance_id} data-approval-id={r.instance_id}
                  onClick={() => setDetailId(r.instance_id)}
                  className="border-b border-neutral-100 last:border-0 cursor-pointer hover:bg-neutral-50">
                  <td className="px-3 py-2">
                    <span className="font-medium text-neutral-800">{r.issue.issue_key}</span>
                    <span className="ml-2 text-neutral-500">{r.issue.name}</span>
                  </td>
                  <td className="px-3 py-2 text-neutral-600">
                    {r.flow_name}
                    {r.status === "pending" && <span className="ml-1 text-neutral-400">第 {r.current_level} 级</span>}
                  </td>
                  <td className="px-3 py-2">
                    <span className={`px-1.5 py-0.5 rounded text-xs ${STATUS_LABEL[r.status]?.cls ?? ""}`}
                      data-status={r.status}>
                      {STATUS_LABEL[r.status]?.text ?? r.status}
                    </span>
                    {r.my_action && <span className="ml-1.5 text-xs text-neutral-400">我的动作：{r.my_action}</span>}
                  </td>
                  <td className="px-3 py-2 text-neutral-400">{new Date(r.created_at).toLocaleString("zh-CN")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {detailId && (
        <ApprovalDetailDrawer
          instanceId={detailId}
          projectId={rows.find((r) => r.instance_id === detailId)?.issue.project_id ?? ""}
          onClose={() => setDetailId(null)}
          onChanged={() => void load()}
          onOpenIssue={(pid, iid) => nav(`/${ws}/projects/${pid}?issue=${iid}`)}
        />
      )}
    </div>
  );
}

/** 审批详情抽屉（WF-002 §3.2）：时间线 + 动作区（approve / reject 意见必填）。 */
function ApprovalDetailDrawer({ instanceId, projectId, onClose, onChanged, onOpenIssue }: {
  instanceId: string;
  projectId: string;
  onClose: () => void;
  onChanged: () => void;
  onOpenIssue: (projectId: string, issueId: string) => void;
}) {
  const { workspaceSlug: ws } = useParams();
  const [detail, setDetail] = useState<Awaited<ReturnType<typeof ApprovalAPI.instance>>["data"] | null>(null);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => {
    if (!ws || !projectId) return;
    void ApprovalAPI.instance(ws, projectId, instanceId).then((r) => setDetail(r.data));
  }, [ws, projectId, instanceId]);

  async function act(action: "approve" | "reject") {
    if (!ws || !projectId) return;
    if (action === "reject" && !comment.trim()) {
      setActionError("驳回意见必填");
      return;
    }
    setBusy(true);
    setActionError(null);
    try {
      await ApprovalAPI.act(ws, projectId, instanceId, { action, comment });
      onChanged();
      onClose();
    } catch (e) {
      // 透出服务端真实原因（如「仅当前节点审批人可操作」403）——通用文案曾掩盖越权根因
      const err = e as { message?: string };
      setActionError(err?.message ?? "操作失败，请重试");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/20" data-sb-scope="approval-drawer"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <aside className="w-[480px] bg-white h-full flex flex-col shadow-xl">
        <header className="h-12 border-b border-neutral-200 px-4 flex items-center justify-between">
          <h2 className="text-sm font-semibold">审批详情</h2>
          <button type="button" onClick={onClose} className="text-neutral-400 hover:text-neutral-600">✕</button>
        </header>
        {!detail ? (
          <div className="p-4 text-sm text-neutral-400">加载中…</div>
        ) : (
          <>
            <div className="flex-1 overflow-auto p-4 space-y-4">
              <section>
                <div className="text-xs text-neutral-400 mb-1">任务</div>
                <button type="button" className="text-sm text-brand-600 hover:underline"
                  onClick={() => onOpenIssue(detail.issue.project_id, detail.issue.id)}>
                  {detail.issue.issue_key} · {detail.issue.name}
                </button>
              </section>
              <section>
                <div className="text-xs text-neutral-400 mb-1">审批流与状态</div>
                <div className="text-sm">
                  {detail.flow_name}
                  <span className={`ml-2 px-1.5 py-0.5 rounded text-xs ${STATUS_LABEL[detail.status]?.cls ?? ""}`}>
                    {STATUS_LABEL[detail.status]?.text ?? detail.status}
                  </span>
                  {detail.status === "pending" && <span className="ml-2 text-neutral-400">当前第 {detail.current_level} 级</span>}
                </div>
              </section>
              <section data-sb-scope="approval-records">
                <div className="text-xs text-neutral-400 mb-1">审批记录</div>
                <div className="space-y-1.5">
                  {detail.records.map((rec, i) => (
                    <div key={i} className="flex items-center gap-2 text-sm border border-neutral-100 rounded px-2 py-1.5">
                      <span className="text-neutral-400 w-10">L{rec.level}</span>
                      <span className="text-neutral-700 flex-1">{rec.approver_name}</span>
                      <span className={
                        rec.action === "approve" ? "text-emerald-600" :
                        rec.action === "reject" ? "text-rose-600" :
                        rec.action === "pending" ? "text-amber-600" : "text-neutral-400"}>
                        {rec.action === "approve" ? "通过" : rec.action === "reject" ? "驳回"
                          : rec.action === "pending" ? "待审" : "跳过"}
                      </span>
                      {rec.comment && <span className="text-xs text-neutral-400 truncate max-w-40" title={rec.comment}>{rec.comment}</span>}
                    </div>
                  ))}
                </div>
              </section>
            </div>
            {detail.status === "pending" && (
              <footer className="border-t border-neutral-200 p-3 space-y-2" data-sb-scope="approval-actions">
                <textarea value={comment} onChange={(e) => setComment(e.target.value)}
                  placeholder="审批意见（驳回必填）"
                  data-sb-scope="approval-comment"
                  className="w-full h-16 border border-neutral-200 rounded-md px-2 py-1.5 text-sm resize-none focus:outline-none focus:border-brand-400" />
                {actionError && <div className="text-xs text-rose-600">{actionError}</div>}
                <div className="flex gap-2 justify-end">
                  <button type="button" disabled={busy} data-action="reject"
                    onClick={() => void act("reject")}
                    className="px-3 h-8 rounded-md border border-rose-200 text-rose-600 text-sm hover:bg-rose-50 disabled:opacity-50">
                    驳回
                  </button>
                  <button type="button" disabled={busy} data-action="approve"
                    onClick={() => void act("approve")}
                    className="px-3 h-8 rounded-md bg-brand-600 text-white text-sm hover:bg-brand-700 disabled:opacity-50">
                    通过
                  </button>
                </div>
              </footer>
            )}
          </>
        )}
      </aside>
    </div>
  );
}
