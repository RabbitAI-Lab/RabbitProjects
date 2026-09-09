/** useIssueRemoteFlash 单测（COLLAB-004 §3.3，Sprint-9 补 TC-COVER-004）。
 *
 *  BR-07 乱序免疫（version 旧于等于本地忽略）/ BR-08 自操作不回显 /
 *  brief 匹配透出与 4s 自动消隐 / issue_id 不匹配忽略。 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { liveEventBus } from "@rp/shared-state";
import type { IssueUpdatedPayload, LiveEnvelope } from "@rp/types";
import { useIssueRemoteFlash } from "./useIssueRemoteFlash";

function emit(p: Partial<IssueUpdatedPayload>) {
  liveEventBus.emit("issue.updated", {
    type: "event", event: "issue.updated",
    payload: { issue_id: "i1", version: "v2", brief: "字段更新", actor_id: null, ...p },
  } as unknown as LiveEnvelope);
}

describe("useIssueRemoteFlash（BR-07/08）", () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); });

  it("他人更新且 version 更新 → 透出 brief 并 4s 消隐", () => {
    const { result } = renderHook(() => useIssueRemoteFlash("i1", "v1", "me"));
    act(() => emit({ actor_id: "someone", version: "v2", brief: "状态变更" }));
    expect(result.current.remoteBrief).toBe("状态变更");
    act(() => { vi.advanceTimersByTime(4_100); });
    expect(result.current.remoteBrief).toBeNull();
  });

  it("BR-08：自己的操作不回显", () => {
    const { result } = renderHook(() => useIssueRemoteFlash("i1", "v1", "me"));
    act(() => emit({ actor_id: "me", version: "v9", brief: "自己" }));
    expect(result.current.remoteBrief).toBeNull();
  });

  it("BR-07：version 旧于等于本地忽略（乱序免疫）", () => {
    const { result } = renderHook(() => useIssueRemoteFlash("i1", "v2", "me"));
    act(() => emit({ actor_id: "other", version: "v2", brief: "旧" }));
    expect(result.current.remoteBrief).toBeNull();
    act(() => emit({ actor_id: "other", version: "v1", brief: "更旧" }));
    expect(result.current.remoteBrief).toBeNull();
  });

  it("issue_id 不匹配忽略；clear 手动清空", () => {
    const { result } = renderHook(() => useIssueRemoteFlash("i1", null, "me"));
    act(() => emit({ issue_id: "i2", version: "v2", brief: "别的任务" }));
    expect(result.current.remoteBrief).toBeNull();
    act(() => emit({ version: "v2", brief: "命中" }));
    expect(result.current.remoteBrief).toBe("命中");
    act(() => result.current.clear());
    expect(result.current.remoteBrief).toBeNull();
  });
});
