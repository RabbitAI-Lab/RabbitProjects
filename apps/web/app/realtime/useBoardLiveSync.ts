/**
 * 看板实时同步消费接线（COLLAB-004 §3.2 / §4.4.2）——GroupedBoard 最小触点。
 *
 *  事件 → 动作：
 *  - issue.state.changed / board.moved：目标列定向更新（GET group_by 后仅回写受影响
 *    列的 state，其余列 DOM 不重排）+ 远端卡 300ms 淡入 + 列计数 bump（§3.2）；
 *  - issue.updated：brief 命中当前分组维度才收敛该任务所在列（§4.4.2 定向 patch）；
 *  - BR-07：version（=updated_at）旧于等于本地忽略（乱序免疫）；
 *  - BR-08：自己的操作不回显（actor_id === me 过滤——live 已滤，双保险）；
 *  - §3.2 本地拖拽保护：拖拽中远端事件入队（不重排 DOM），松手后合并；
 *  - §2.3 注 2 batch_id：同批聚合为单条 Toast（「张三 批量更新了 N 个任务」）。
 */
import { useCallback, useEffect, useRef } from "react";
import { liveEventBus } from "@rp/shared-state";
import type {
  BoardMovedPayload,
  Issue,
  IssueStateChangedPayload,
  IssueUpdatedPayload,
  LiveEnvelope,
} from "@rp/types";
import { toast } from "../components/Toast";
import { useStores } from "../stores";

/** GroupedBoard 列形状（结构声明——避免循环 import）。 */
export interface LiveColMeta {
  key: string;
  issues: Issue[];
  total: number;
}

interface LiveJob {
  /** 受影响列 key；`"*"` = 全列轻收敛（断线补偿/brief 命中维度兜底）。 */
  colKeys: Set<string> | "*";
  /** 迁移实体（远端卡淡入定位）。 */
  issueId?: string | undefined;
}

export interface UseBoardLiveSyncOptions<T extends LiveColMeta> {
  groupBy: string;
  cols: T[];
  /** state 列 key → group 映射（state_id 维度才有；其它维度 undefined）。 */
  colGroupOf?: (colKey: string) => string | undefined;
  fetchGrouped: () => Promise<{ data: Record<string, { results: unknown[]; total_results: number }> } | null>;
  setCols: (updater: (cols: T[]) => T[]) => void;
  /** 当前是否有本地拖拽进行中（拖拽保护）。 */
  dragging: boolean;
  memberName: (uid: string | null) => string;
}

/** brief（字段族）→ 分组维度映射（§4.4.2 定向范围选择）。 */
const BRIEF_TO_GROUP: Record<string, string> = {
  state: "state_id",
  priority: "priority",
  assignees: "assignee_id",
  labels: "label_id",
};

export function useBoardLiveSync<T extends LiveColMeta>(opts: UseBoardLiveSyncOptions<T>): { flush: () => void } {
  const { groupBy, cols, colGroupOf, fetchGrouped, setCols, dragging, memberName } = opts;
  const stores = useStores();
  const meId = stores.session.user?.id ?? null;
  const draggingRef = useRef(dragging);
  draggingRef.current = dragging;
  /** cols 快照（事件回调闭包读最新列，避免重复订阅）。 */
  const colsRef = useRef(cols);
  colsRef.current = cols;
  /** 本地版本表（issueId → updated_at，BR-07 比对基准），随 cols 派生。 */
  const versionsRef = useRef<Map<string, string>>(new Map());
  /** 列排序版本表（board.moved 的 column_version 比对，§2.3 注 3：旧于等于本地忽略——
   *  防同列并发拖拽的乱序重排；无服务端基线，首见恒放行）。 */
  const columnVersionsRef = useRef<Map<string, string>>(new Map());
  const queueRef = useRef<LiveJob[]>([]);
  const batchRef = useRef<Map<string, { count: number; actorId: string | null }>>(new Map());

  const rebuildVersions = useCallback(() => {
    const m = new Map<string, string>();
    for (const c of colsRef.current) for (const i of c.issues) if (i.updated_at) m.set(i.id, i.updated_at);
    versionsRef.current = m;
  }, []);

  const isStale = useCallback((issueId: string, version: string | undefined): boolean => {
    if (!version) return false;
    rebuildVersions();
    const local = versionsRef.current.get(issueId);
    if (local == null) return false;
    // 数值化比较：事件侧 version 为 UTC ISO（+00:00），REST updated_at 为本地时区
    // （+08:00）——字符串比较跨时区恒判 stale（Phase 5 验收发现的 P1 缺陷）
    const tRemote = Date.parse(version);
    const tLocal = Date.parse(local);
    if (Number.isNaN(tRemote) || Number.isNaN(tLocal)) return version <= local; // 解析失败退字符串
    return tRemote <= tLocal;
  }, [rebuildVersions]);

  const applyJob = useCallback(async (job: LiveJob) => {
    const env = await fetchGrouped();
    if (!env) return;
    setCols((cur) =>
      cur.map((c): T => {
        if (job.colKeys !== "*" && !job.colKeys.has(c.key)) return c;
        const bucket = env.data?.[c.key] ?? { results: [] as Issue[], total_results: 0 };
        return { ...c, issues: (bucket.results as Issue[]) ?? [], total: bucket.total_results ?? 0 };
      }),
    );
    // §3.2 远端卡片 300ms 淡入 + 列计数 bump（不自动滚动视口）
    const targetKeys = job.colKeys === "*" ? [] : [...job.colKeys];
    setTimeout(() => {
      if (job.issueId) {
        const card = document.querySelector(`article[data-card-id="${job.issueId}"]`);
        card?.classList.add("rp-remote-in");
        setTimeout(() => card?.classList.remove("rp-remote-in"), 350);
      }
      for (const k of targetKeys) {
        const cnt = document.querySelector(`[data-count="${k}"]`);
        if (cnt) {
          cnt.classList.add("rp-count-bump");
          setTimeout(() => cnt.classList.remove("rp-count-bump"), 600);
        }
      }
    }, 60);
  }, [fetchGrouped, setCols]);

  const enqueue = useCallback((job: LiveJob) => {
    if (draggingRef.current) {
      // §3.2 本地拖拽保护：只更新数据不重排 DOM（入队，松手后合并）
      queueRef.current.push(job);
      return;
    }
    void applyJob(job);
  }, [applyJob]);

  /** 松手合并：列集去重后一次应用。 */
  const flush = useCallback(() => {
    if (queueRef.current.length === 0) return;
    const merged = new Set<string>();
    let star = false;
    let issueId: string | undefined;
    for (const j of queueRef.current) {
      if (j.colKeys === "*") star = true;
      else for (const k of j.colKeys) merged.add(k);
      issueId = issueId ?? j.issueId;
    }
    queueRef.current = [];
    void applyJob({ colKeys: star ? "*" : merged, issueId });
  }, [applyJob]);

  /** batch_id 聚合 Toast（§2.3 注 2：同批逐实体事件 → 单条提示）。 */
  const aggregateBatch = useCallback((batchId: number | undefined, actorId: string | null) => {
    if (batchId == null) return;
    const key = String(batchId);
    const cur = batchRef.current.get(key) ?? { count: 0, actorId };
    cur.count += 1;
    batchRef.current.set(key, cur);
    setTimeout(() => {
      const final = batchRef.current.get(key);
      batchRef.current.delete(key);
      if (final && final.count > 0) {
        toast(`${memberName(final.actorId)} 批量更新了 ${final.count} 个任务（batch）`, "info");
      }
    }, 800);
  }, [memberName]);

  useEffect(() => {
    /** state 类事件 → 受影响列（state_id 维度 = from/to group 命中的列；其它维度 = 含该任务的列）。 */
    const colsForStateChange = (from: string | null, to: string | null, issueId: string): LiveJob["colKeys"] => {
      if (groupBy === "state_id" && colGroupOf) {
        const keys = new Set<string>();
        for (const c of colsRef.current) {
          const g = colGroupOf(c.key);
          if (g === from || g === to) keys.add(c.key);
        }
        return keys;
      }
      const keys = new Set<string>();
      for (const c of colsRef.current) if (c.issues.some((i) => i.id === issueId)) keys.add(c.key);
      return keys;
    };

    const onState = (env: LiveEnvelope) => {
      const p = env.payload as unknown as IssueStateChangedPayload;
      if (!p?.issue_id || p.actor_id === meId) return; // BR-08
      if (isStale(p.issue_id, p.version)) return; // BR-07
      aggregateBatch(p.batch_id, p.actor_id ?? null);
      enqueue({ colKeys: colsForStateChange(p.from_group ?? null, p.to_group ?? null, p.issue_id), issueId: p.issue_id });
    };
    const onMoved = (env: LiveEnvelope) => {
      const p = env.payload as unknown as BoardMovedPayload;
      if (!p?.issue_id || p.actor_id === meId) return;
      // §2.3 注 3：column_version 列粒度版本比对（旧于等于本地已应用版本 → 忽略）
      if (p.to_group && p.column_version) {
        const seen = columnVersionsRef.current.get(p.to_group);
        if (seen && p.column_version <= seen) return;
        columnVersionsRef.current.set(p.to_group, p.column_version);
      }
      enqueue({ colKeys: colsForStateChange(p.from_group ?? null, p.to_group ?? null, p.issue_id), issueId: p.issue_id });
    };
    const onUpdated = (env: LiveEnvelope) => {
      const p = env.payload as unknown as IssueUpdatedPayload;
      if (!p?.issue_id || p.actor_id === meId) return;
      if (isStale(p.issue_id, p.version)) return;
      aggregateBatch(p.batch_id, p.actor_id ?? null);
      // brief 命中当前分组维度才需要列收敛；其它域（标题/描述等）不动看板布局
      const dim = BRIEF_TO_GROUP[p.brief] ?? (p.brief?.startsWith("cf_") ? p.brief : undefined);
      if (dim === groupBy) {
        const keys = new Set<string>();
        for (const c of colsRef.current) if (c.issues.some((i) => i.id === p.issue_id)) keys.add(c.key);
        enqueue({ colKeys: keys, issueId: p.issue_id });
      }
    };
    const onReconnected = () => {
      void applyJob({ colKeys: "*" }); // 断线窗口补偿（§4.4.1）：全列轻收敛
    };

    const offs = [
      liveEventBus.on("issue.state.changed", onState),
      liveEventBus.on("board.moved", onMoved),
      liveEventBus.on("issue.updated", onUpdated),
    ];
    window.addEventListener("rp:live-reconnected", onReconnected);
    return () => {
      offs.forEach((off) => off());
      window.removeEventListener("rp:live-reconnected", onReconnected);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [groupBy, meId, isStale, enqueue, applyJob, aggregateBatch, colGroupOf]);

  return { flush };
}
