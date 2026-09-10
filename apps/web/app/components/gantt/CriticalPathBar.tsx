/** 关键路径控制条（GANTT-003 §3.1——冻结原型 V-GANTT，Sprint-9）。
 *
 *  开关（关键路径高亮）+ 图例 + 外部约束计数 + 计划分析（浮动卡/预警配置）入口；
 *  CPM 行经 CriticalPathAPI 拉取，criticalIds 上抛甘特条渲染（O8 红描边）。 */
import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router";

import { CriticalPathAPI, unwrap } from "../../services/api";

type CpmRow = { id: string; issue_key: string; float_days: number; is_critical: boolean; has_external_preds: boolean };

export function CriticalPathBar({ onCriticalChange }: {
  onCriticalChange: (ids: Set<string>, on: boolean) => void;
}) {
  const { workspaceSlug: ws, projectId } = useParams();
  const [rows, setRows] = useState<CpmRow[]>([]);
  const [on, setOn] = useState(false);

  const load = useCallback(async () => {
    if (!ws || !projectId) return;
    try {
      const data = unwrap<{ rows: CpmRow[] }>(await CriticalPathAPI.rows(ws, projectId));
      setRows(data?.rows ?? []);
    } catch { /* 限流（10/min）/失败：保持空态（60s 窗口自恢复） */ }
  }, [ws, projectId]);

  // oxlint-disable-next-line react/set-state-in-effect -- 服务端 loader（org-structure 基线）
  useEffect(() => { load(); }, [load]);

  const criticalIds = new Set(rows.filter((r) => r.is_critical).map((r) => r.id));
  const externalN = rows.filter((r) => r.has_external_preds).length;

  useEffect(() => {
    onCriticalChange(on ? criticalIds : new Set<string>(), on && criticalIds.size > 0);
  }, [on, rows]); // eslint-disable-line react-hooks/exhaustive-deps -- criticalIds 由 rows 派生

  if (rows.length === 0) return null;   // 无 CPM 数据（排期不全）不渲染

  return (
    <div className="flex items-center gap-3 border-b border-neutral-200 bg-white px-4 py-2 text-[12px]" data-sb-scope="cp-bar">
      <label className="flex cursor-pointer items-center gap-1.5 text-neutral-700">
        <input type="checkbox" className="h-[15px] w-[15px] accent-brand-500" checked={on}
          onChange={(e) => setOn(e.target.checked)} data-sb-scope="cp-toggle" />
        关键路径
      </label>
      <span className="text-neutral-400">关键 {criticalIds.size} · 总 {rows.length}</span>
      {externalN > 0 && (
        <span className="rounded-full bg-amber-50 px-2 py-0.5 text-[11px] text-amber-700" title="外部前置不进 CPM（BR-03 软策略）">
          外部约束 {externalN}
        </span>
      )}
      <span className="flex items-center gap-1.5 text-neutral-400">
        <span className="inline-block h-[9px] w-3.5 rounded-sm border-2 border-red-500 bg-red-100" />关键（红描边）
        {on && <><span className="inline-block h-[9px] w-3.5 rounded-sm bg-neutral-200" />非关键降淡</>}
      </span>
      <Link to={`/${ws}/projects/${projectId}/gantt/cpm`} className="ml-auto text-brand-600 hover:underline" data-sb-scope="cp-analyze-link">
        计划分析（浮动时间 / 预警配置）▸
      </Link>
    </div>
  );
}
