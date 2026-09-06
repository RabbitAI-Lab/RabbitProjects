/**
 * 延期概览条（C.109 / GANTT-002 §3.2）：⚠ 逾期 N 个任务 · 最长逾期 M 天 · 按人分布
 * （张三(3)…）+ [查看 ▾] 明细（编号/标题/逾期天数/执行人，点击跳行）。
 *  - role=status（§3.4）；无逾期整条隐藏；限流角标（429 处理，§4.2.1 要点 4）；
 *  - 明细 20 截断尾提示「已展示前 20 条，完整清单见任务列表 overdue 筛选」；
 *    统计三数字仍为完整集口径（§2.3——服务端聚合，前端不自算）。
 */
import { observer } from "mobx-react-lite";
import type { GanttStore } from "../../stores/gantt";

export const OverdueBar = observer(function OverdueBar({
  store, memberName, onJump,
}: {
  store: GanttStore;
  memberName: (uid: string) => string;
  onJump: (issueId: string) => void;
}) {
  const od = store.overdue;
  if (!od || od.overdue_count === 0) return null;
  return (
    <>
      <div className="flex items-center gap-2 px-5 py-1.5 bg-amber-50 border-b border-amber-200 text-amber-800 text-[13px] shrink-0"
        role="status" data-sb-scope="gantt-overdue-bar" aria-live="polite">
        <span aria-hidden="true">⚠</span> 逾期 <b data-sb-scope="gantt-overdue-count">{od.overdue_count}</b> 个任务 · 最长逾期 <b>{od.max_overdue_days}</b> 天 ·
        <span className="flex items-center gap-1 flex-wrap" data-sb-scope="gantt-overdue-by">
          {od.by_assignee.map((a) => (
            <span key={a.assignee_id} className="bg-white border border-amber-200 rounded-full px-2 text-[12px]">
              {a.display_name || memberName(a.assignee_id)}({a.count})
            </span>
          ))}
        </span>
        {store.overdueThrottled && (
          <span className="text-[11px] text-amber-600" title="概览聚合端点限流 10 次/分钟，数字为最近一次成功结果">⏳ 概览刷新受限流</span>
        )}
        <button type="button" className="ml-auto text-brand-600 hover:underline" data-sb-scope="gantt-overdue-toggle"
          onClick={() => { store.overdueOpen = !store.overdueOpen; }}>
          {store.overdueOpen ? "收起 ▴" : "查看 ▾"}
        </button>
      </div>
      {store.overdueOpen && (
        <div className="max-h-[220px] overflow-auto border-b border-neutral-200 bg-white shrink-0" role="list" data-sb-scope="gantt-overdue-list">
          {od.items.map((it) => (
            <div key={it.id} role="listitem"
              className="flex items-center gap-2 px-5 py-1.5 text-[12.5px] border-b border-dashed border-neutral-200">
              <span className="badge-id">{it.issue_key}</span>
              <span className="flex-1 min-w-0 truncate">{it.name}</span>
              <span className="text-red-500">逾期 {it.overdue_days} 天</span>
              <span className="text-neutral-400">{it.assignee_ids.map(memberName).join("、") || "未指派"}</span>
              <button type="button" className="text-brand-600 hover:underline" onClick={() => onJump(it.id)}>跳转</button>
            </div>
          ))}
          <div className="px-5 py-1.5 text-[11.5px] text-neutral-400">
            已展示前 {Math.min(od.items.length, 20)} 条{od.items_truncated ? "，完整清单见任务列表 overdue 筛选" : ""}——统计三数字为完整集口径
          </div>
        </div>
      )}
    </>
  );
});
