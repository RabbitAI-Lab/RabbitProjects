/**
 * 任务详情实时提示（COLLAB-004 §3.3）：issue.updated（brief 匹配打开字段区）→
 * 该区轻闪（150ms 背景 pulse）+「已更新」角标。
 *
 *  - BR-07：version（=updated_at）旧于等于本地忽略（乱序免疫）；
 *  - BR-08：自己的操作不回显（actor_id === me；live 已滤，双保险）；
 *  - 提示不自动刷正文（推送是提示不是数据源）；角标 4s 自动消隐。
 */
import { useEffect, useRef, useState } from "react";
import { liveEventBus } from "@rp/shared-state";
import type { IssueUpdatedPayload, LiveEnvelope } from "@rp/types";

export function useIssueRemoteFlash(issueId: string, localVersion: string | null, myUserId: string | null): {
  remoteBrief: string | null;
  clear: () => void;
} {
  const [remoteBrief, setRemoteBrief] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const versionRef = useRef<string | null>(localVersion);
  useEffect(() => {
    versionRef.current = localVersion;
  }, [localVersion]);

  useEffect(() => {
    const off = liveEventBus.on("issue.updated", (env: LiveEnvelope) => {
      const p = env.payload as unknown as IssueUpdatedPayload;
      if (p?.issue_id !== issueId) return;
      if (p.actor_id === myUserId) return; // BR-08
      if (p.version && versionRef.current && p.version <= versionRef.current) return; // BR-07
      setRemoteBrief(p.brief || "updated");
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => setRemoteBrief(null), 4_000);
    });
    return () => {
      off();
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [issueId, myUserId]);

  return { remoteBrief, clear: () => setRemoteBrief(null) };
}
