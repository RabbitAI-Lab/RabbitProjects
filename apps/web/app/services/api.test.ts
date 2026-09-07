/** 服务层契约单测（sprint-5 §6 前端核心路径 ≥70% 门槛基建）。
 *
 * 被测对象：api.ts 的纯逻辑导出（unwrap / 游标解析 / 事件组常量 / 服务 URL 契约）。
 * 行为断言（点击→请求→回读）由 Playwright parity spec 承载——此处不重复。 */
import { describe, expect, it } from "vitest";
import {
  ActivityStreamAPI,
  GovernanceAPI,
  LifecycleAPI,
  MemberAdminAPI,
  ProjectStatsAPI,
  STREAM_EVENT_GROUPS,
  WebhookAPI,
  parseStreamCursor,
  unwrap,
} from "./api";

describe("unwrap（信封解包类型收窄）", () => {
  it("取 data 字段", () => {
    expect(unwrap<{ x: number }>({ data: { x: 1 } })).toEqual({ x: 1 });
    expect(unwrap<string[]>({ data: ["a"] })).toEqual(["a"]);
  });
});

describe("parseStreamCursor（COLLAB-003 §4.2.1 要点 2）", () => {
  it("自右向左取末段 UUID、其余整体为时间戳", () => {
    expect(parseStreamCursor("2026-09-07T01:02:03Z:abc")).toEqual({
      createdAt: "2026-09-07T01:02:03Z",
      id: "abc",
    });
  });
  it("时间戳含冒号（ISO 偏移）也不裂", () => {
    expect(parseStreamCursor("2026-09-07T01:02:03+08:00:xyz")).toEqual({
      createdAt: "2026-09-07T01:02:03+08:00",
      id: "xyz",
    });
  });
  it("无分隔符 → null", () => {
    expect(parseStreamCursor("nocolon")).toBeNull();
    expect(parseStreamCursor("")).toBeNull();
  });
});

describe("STREAM_EVENT_GROUPS（§2.3 语义组与后端 EVENT_CHOICES 同源）", () => {
  it("13 组 + Sprint-5 扩 lifecycle 第 14 组", () => {
    const keys = STREAM_EVENT_GROUPS.map((g) => g.key);
    expect(keys).toContain("comment");
    expect(keys).toContain("lifecycle");
    expect(keys.at(-1)).toBe("lifecycle");
    expect(keys).toHaveLength(14);
  });
});

describe("Sprint-5 域服务 URL 契约（端点路径模板冻结）", () => {
  // 通过实例方法 toString 无法取 URL（闭包）——改断言方法存在性与关键签名面。
  // URL 字面量本身由后端 api-full-coverage + sprint-5-flow 守护（跨语言契约）。
  it("五个 Sprint-5 服务面齐全", () => {
    for (const [svc, methods] of [
      [MemberAdminAPI, ["bulkRole", "disable", "enable", "bulkRoleProject"]],
      [GovernanceAPI, ["archive", "restore", "listLabels", "createLabel", "activityStats"]],
      [LifecycleAPI, ["transition", "statusLogs", "duplicate", "listTemplates"]],
      [ProjectStatsAPI, ["progress", "members"]],
      [WebhookAPI, ["list", "create", "ping", "deliveries", "replay"]],
      [ActivityStreamAPI, ["stream", "batchDetail"]],
    ] as const) {
      for (const m of methods) {
        expect(typeof (svc as Record<string, unknown>)[m]).toBe("function");
      }
    }
  });
});
