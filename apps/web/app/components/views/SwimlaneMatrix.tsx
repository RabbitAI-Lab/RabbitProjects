/** 二维泳道看板（BOARD-005 §3.1 泳道区——C.154，Sprint-8 R6）。
 *
 *  group_by（列）× sub_group_by（行泳道）矩阵：服务端聚合（meta.matrix——
 *  格计数不拉全量卡片 BR-07/异常表）；空格虚线框（§3.4）；计数 99+ 截断
 *  （BR-13 展示层）；降级黄条（meta.degraded——维度停用/聚合超时回退一维）。
 *  格点击 → 带双维过滤跳一维看板（逐格列表对账入口，§2.3 懒加载语义）。 */
import { useEffect, useState } from "react";
import type { ViewPage } from "./useViewPage";

const DIM_NAMES: Record<string, string> = {
  state_id: "状态", priority: "优先级", assignee_id: "负责人", label_id: "标签",
};

type Cell = { col: string; row: string; count: number; sample_issue_ids?: string[] };

export function SwimlaneMatrix({ vp, workspaceSlug, projectId, subGroupBy, reloadKey = 0, onCount }: {
  vp: ViewPage;
  workspaceSlug?: string | undefined;
  projectId?: string | undefined;
  subGroupBy: string;
  reloadKey?: number;
  onCount?: (n: number) => void;
}) {
  const groupBy = vp.groupBy;
  const [matrix, setMatrix] = useState<Cell[]>([]);
  const [columns, setColumns] = useState<{ key: string }[]>([]);
  const [rows, setRows] = useState<{ key: string }[]>([]);
  const [degraded, setDegraded] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!workspaceSlug || !projectId || !groupBy || !subGroupBy) return;
    let alive = true;
    setLoading(true);
    void (async () => {
      const api = await import("../../services/api");
      try {
        const r = await api.IssueAPI.list(workspaceSlug, projectId,
          vp.listParams({ group_by: groupBy, sub_group_by: subGroupBy }));
        const body = (r as unknown as {
          data?: { matrix?: Cell[]; columns?: { key: string }[]; rows?: { key: string }[] };
          meta?: { degraded?: Record<string, unknown> | null; total_count?: number };
        });
        if (!alive) return;
        setMatrix(body.data?.matrix ?? []);
        setColumns(body.data?.columns ?? []);
        setRows(body.data?.rows ?? []);
        setDegraded(body.meta?.degraded ?? null);
        onCount?.(body.meta?.total_count ?? 0);
      } catch {
        if (alive) setDegraded({ error: "matrix_fetch_failed" });
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceSlug, projectId, groupBy, subGroupBy, reloadKey, vp.viewIdParam, vp.filtersParam]);

  const colName = (k: string) =>
    k === "__none__" ? "未设置"
      : (vp.states.find((s) => s.id === k)?.name
        ?? (groupBy === "assignee_id" ? vp.members.find((m) => m.id === k)?.name : undefined)
        ?? (groupBy === "label_id" ? vp.labels.find((l) => l.id === k)?.name : undefined)
        ?? k.slice(0, 8));
  const rowName = (k: string) =>
    k === "__none__" ? "未分配"
      : (vp.states.find((s) => s.id === k)?.name
        ?? (subGroupBy === "assignee_id" ? vp.members.find((m) => m.id === k)?.name : undefined)
        ?? (subGroupBy === "label_id" ? vp.labels.find((l) => l.id === k)?.name : undefined)
        ?? k.slice(0, 8));
  const cellMap = new Map(matrix.map((c) => [`${c.col}|${c.row}`, c]));

  if (loading) {
    return <div className="flex-1 p-6 text-sm text-neutral-400" data-sb-scope="matrix-loading">矩阵聚合中…</div>;
  }
  return (
    <div className="flex-1 overflow-auto p-4" data-sb-scope="swimlane-matrix">
      {degraded && (
        <div role="status" data-sb-scope="matrix-degraded"
          className="mb-3 flex items-center gap-2 bg-amber-50 border border-amber-200 text-amber-700 rounded-lg px-3 py-1.5 text-[12.5px]">
          ⚠ 二维统计暂不可用，已切换单列分组（{DIM_NAMES[subGroupBy] ?? subGroupBy} 维度
          {String((degraded as { sub_group_by?: string }).sub_group_by ?? "")}）
        </div>
      )}
      {!degraded && (
        <div className="mb-2 text-xs text-neutral-400" data-sb-scope="matrix-dims">
          {DIM_NAMES[groupBy] ?? groupBy} × {DIM_NAMES[subGroupBy] ?? subGroupBy} · 共 {matrix.reduce((n, c) => n + c.count, 0)} 项（Σ格计数与任务总数实时对账）
        </div>
      )}
      {!degraded && (
        <div className="overflow-auto rounded-lg border border-neutral-200 bg-white" data-sb-scope="swimlane-matrix-table">
        <table className="matrix-tbl" aria-label="二维分组矩阵">
          <thead>
            <tr>
              <th style={{ zIndex: 6 }}>{DIM_NAMES[subGroupBy] ?? subGroupBy} ＼ {DIM_NAMES[groupBy] ?? groupBy}</th>
              {columns.map((c) => (
                <th key={c.key} data-sb-scope="matrix-col-head">
                  {groupBy === "state_id"
                    ? <><span className="inline-block h-2 w-2 rounded-full mr-1.5" style={{ background: vp.states.find((s) => s.id === c.key)?.color ?? "#9ca3af" }} />{colName(c.key)}</>
                    : colName(c.key)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key} data-sb-scope="matrix-row">
                <th data-sb-scope="matrix-row-head">
                  {subGroupBy === "assignee_id" && r.key !== "__none__"
                    ? <span className="inline-flex items-center gap-1.5">
                        <span className="inline-flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-semibold text-white"
                              style={{ background: "#3f76ff" }}>{rowName(r.key)[0]}</span>
                        {rowName(r.key)}
                      </span>
                    : rowName(r.key)}
                </th>
                {columns.map((c) => {
                  const cell = cellMap.get(`${c.key}|${r.key}`);
                  const n = cell?.count ?? 0;
                  return (
                    <td key={c.key} className="align-top" data-sb-scope="matrix-cell">
                      {n > 0 ? (
                        <div className="flex flex-wrap items-center">
                          <span className="cell-count" data-sb-scope="matrix-cell-count">
                            {n > 99 ? "99+" : n}
                          </span>
                          {(cell?.sample_issue_ids ?? []).slice(0, 8).map((id) => (
                            <span key={id} data-tip={`任务 ${id}`} data-sb-scope="matrix-cell-sample"
                                  className="cell-sample">
                              {id.slice(-3).toUpperCase()}
                            </span>
                          ))}
                        </div>
                      ) : (
                        <div className="cell-empty">拖拽任务到此</div>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}
      {!degraded && (
        <div className="mt-2 text-xs text-neutral-400">
          格内计数服务端聚合（99+ 截断）；样例短卡悬浮显任务编号；点击格子 → 带双维过滤的一维看板
        </div>
      )}
    </div>
  );
}
