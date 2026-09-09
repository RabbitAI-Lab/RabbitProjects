/** 服务层契约单测（sprint-5 §6 前端核心路径 ≥70% 门槛基建）。
 *
 * 被测对象：api.ts 的纯逻辑导出（unwrap / 游标解析 / 事件组常量 / 服务 URL 契约）。
 * 行为断言（点击→请求→回读）由 Playwright parity spec 承载——此处不重复。 */
import { beforeEach, describe, expect, it, vi } from "vitest";

/* Sprint-9 URL 契约面（真调用——funcs 覆盖按执行计）：
   mock ./axios 的 api 实例，逐方法调用并断言命中路径/动词。 */
const calls: Array<{ method: string; url: string }> = [];
vi.mock("./axios", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./axios")>();
  const stub = new Proxy({}, {
    get(_t, verb: string) {
      return (url: string, ...rest: unknown[]) => {
        calls.push({ method: verb, url });
        const maybeParams = rest.find((x) => x && typeof x === "object" && "params" in (x as object));
        void maybeParams;
        return Promise.resolve({ data: { status: "success", data: null, meta: {} } });
      };
    },
  }) as unknown as typeof actual.api;
  return { ...actual, api: stub };
});

const expectHit = (method: string, urlFragment: string) => {
  const hit = calls.find((c) => c.method === method && c.url.includes(urlFragment));
  if (!hit) throw new Error(`未命中 ${method.toUpperCase()} ${urlFragment}（实收 ${calls.length} 次）`);
};
beforeEach(() => { calls.length = 0; });
import {
  ActivityStreamAPI,
  CriticalPathAPI,
  CycleAPI,
  PortfolioAPI,
  ReportAPI,
  WikiAPI,
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

describe("Sprint-9 域服务 URL 契约（RPT-003/004、PROJ-004、FILE-005、GANTT-003）", () => {
  it("真调用覆盖：动词 × 路径模板（funcs 执行体，路径字面量由后端 flow 跨语言守护）", async () => {
    await CycleAPI.list("ws", "p1");
    expectHit("get", "projects/p1/cycles/");
    await CycleAPI.complete("ws", "p1", "c1", "next");
    expectHit("post", "cycles/c1/complete/");
    await CycleAPI.setIssues("ws", "p1", "c1", ["a"]);
    expectHit("put", "cycles/c1/issues/");
    await CycleAPI.burndown("ws", "p1", "c1");
    expectHit("get", "cycles/c1/burndown/");
    await ReportAPI.velocity("ws", "p1");
    expectHit("get", "reports/velocity/");
    await ReportAPI.cfd("ws", "p1", "2026-09-01", "2026-09-30");
    expectHit("get", "reports/cumulative-flow/");
    await ReportAPI.healthDrilldown("ws", "p1", "overdue");
    expectHit("get", "reports/health/drilldown/");
    await ReportAPI.healthTrend("ws", "p1");
    expectHit("get", "reports/health/trend/");
    await ReportAPI.patchHealthConfig("ws", "p1", {});
    expectHit("patch", "reports/health/config/");
    await ReportAPI.workload("ws", "p1", "a", "b");
    expectHit("get", "reports/workload/");
    await ReportAPI.workloadExport("ws", "p1", "a", "b");
    expectHit("get", "reports/workload/export/");
    await ReportAPI.exportStatus("ws", "t1");
    expectHit("get", "exports/t1/");
    await PortfolioAPI.tree("ws");
    expectHit("get", "portfolios/");
    await PortfolioAPI.summary("ws", "f1");
    expectHit("get", "portfolios/f1/summary/");
    await PortfolioAPI.mountProject("ws", "f1", "p1");
    expectHit("post", "portfolios/f1/projects/");
    await PortfolioAPI.unmountProject("ws", "f1", "p1");
    expectHit("delete", "portfolios/f1/projects/p1/");
    await PortfolioAPI.createMilestone("ws", "f1", {});
    expectHit("post", "portfolios/f1/milestones/");
    await PortfolioAPI.patchMilestone("ws", "f1", "m1", {});
    expectHit("patch", "milestones/m1/");
    await PortfolioAPI.dependencyGraph("ws", "f1");
    expectHit("get", "dependency-graph/");
    await WikiAPI.spaces("ws", "p1");
    expectHit("get", "wiki/spaces/");
    await WikiAPI.space("ws", "s1");
    expectHit("get", "wiki/spaces/s1/");
    await WikiAPI.saveDraft("ws", "pg1", { doc: 1 });
    expectHit("patch", "pages/pg1/draft/");
    await WikiAPI.publish("ws", "pg1", null, "s");
    expectHit("post", "pages/pg1/publish/");
    await WikiAPI.version("ws", "pg1", "v1");
    expectHit("get", "versions/v1/");
    await WikiAPI.rollback("ws", "pg1", "v1");
    expectHit("post", "pages/pg1/rollback/");
    await WikiAPI.trash("ws", "s1");
    expectHit("get", "pages/trash/");
    await WikiAPI.restorePage("ws", "pg1");
    expectHit("post", "pages/pg1/restore/");
    await WikiAPI.search("ws", "q", "p1");
    expectHit("get", "wiki/search/");
    await CriticalPathAPI.rows("ws", "p1", "a", "b");
    expectHit("get", "gantt/critical-path/");
    await CriticalPathAPI.recompute("ws", "p1");
    expectHit("post", "critical-path/recompute/");
    await CriticalPathAPI.patchConfig("ws", "p1", {});
    expectHit("patch", "gantt/cpm-config/");
  });
  it("六个 Sprint-9 服务面方法齐全（方法存在性 = 路径模板冻结，跨语言契约由后端 flow 守护）", () => {
    for (const [svc, methods] of [
      [CycleAPI, ["list", "create", "detail", "patch", "start", "complete", "setIssues", "burndown"]],
      [ReportAPI, ["config", "patchConfig", "velocity", "cfd", "health", "healthDrilldown",
                   "healthTrend", "healthConfig", "patchHealthConfig", "workload", "workloadExport", "exportStatus"]],
      [PortfolioAPI, ["tree", "create", "patch", "summary", "mountProject", "unmountProject",
                      "milestones", "createMilestone", "patchMilestone", "dependencyGraph"]],
      [WikiAPI, ["spaces", "createSpace", "space", "patchSpace", "page", "createPage", "patchPage",
                 "saveDraft", "publish", "versions", "version", "rollback", "trash", "deletePage",
                 "restorePage", "search"]],
      [CriticalPathAPI, ["rows", "recompute", "config", "patchConfig"]],
    ] as const) {
      for (const m of methods) {
        expect(typeof (svc as Record<string, unknown>)[m]).toBe("function");
      }
    }
  });
});
