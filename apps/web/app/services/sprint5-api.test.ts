/** Sprint-5 域服务 URL 契约测试（前端半边跨语言契约——与后端 api-full-coverage
 * + sprint-5-flow 的路径模板互锁）。
 *
 * axios adapter 替换为捕获桩：逐方法断言 method / URL / body 形状。
 * 全部走信封 success 形态（axios.ts 拦截器会解包 data——见 axios.test.ts）。 */
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../components/Toast", () => ({ toast: vi.fn() }));
vi.mock("./permissions-revalidator", () => ({ triggerPermissionsRevalidate: vi.fn() }));

const { api } = await import("./axios");
const {
  GovernanceAPI, LifecycleAPI, MemberAdminAPI, ProjectStatsAPI, WebhookAPI,
  GithubIntegrationAPI,
} = await import("./api");

type Call = { method: string; url: string; body?: unknown; params?: Record<string, unknown> };

/** axios 序列化后的 body 是 JSON 串——比较前解包。 */
const parseBody = (c: Call | undefined): unknown =>
  c ? (typeof c.body === "string" ? JSON.parse(c.body) : c.body) : undefined;
let calls: Call[] = [];

(api.defaults as { adapter?: unknown }).adapter = (async (cfg: {
  method?: string; url?: string; data?: unknown; params?: Record<string, unknown>;
}) => {
  calls.push({ method: cfg.method ?? "", url: cfg.url ?? "", body: cfg.data, ...(cfg.params ? { params: cfg.params } : {}) });
  return { data: { status: "success", data: { ok: 1 } }, status: 200,
           statusText: "OK", headers: {}, config: cfg };
}) as unknown as typeof api.defaults.adapter;

afterEach(() => { calls = []; });

const WS = "acme", PID = "11111111-111-1111-1111-111111111111";

describe("AUTH-006 MemberAdminAPI（§4.4）", () => {
  it("bulkRole / disable / enable / 项目批量", async () => {
    await MemberAdminAPI.bulkRole(WS, { user_ids: ["u1"], role: 10 });
    await MemberAdminAPI.disable(WS, "m1");
    await MemberAdminAPI.enable(WS, "m1");
    await MemberAdminAPI.bulkRoleProject(WS, PID, { member_ids: ["u1"], role: 5 });
    expect(calls.map((c) => `${c.method} ${c.url}`)).toEqual([
      `post workspaces/${WS}/members/bulk-role/`,
      `post workspaces/${WS}/members/m1/disable/`,
      `post workspaces/${WS}/members/m1/enable/`,
      `post workspaces/${WS}/projects/${PID}/members/bulk-role/`,
    ]);
    expect(parseBody(calls[0]!) as Record<string, unknown>).toEqual({ user_ids: ["u1"], role: 10 });
  });
});

describe("TEAM-003 GovernanceAPI（§4.2）", () => {
  it("归档/恢复/标签 CRUD/状态模板/活跃度", async () => {
    await GovernanceAPI.archive(WS);
    await GovernanceAPI.restore(WS);
    await GovernanceAPI.createLabel(WS, { name: "bug", color: "#E5484D" });
    await GovernanceAPI.updateLabel(WS, "l1", { name: "bug2" });
    await GovernanceAPI.deleteLabel(WS, "l1");
    await GovernanceAPI.getDefaultStates(WS);
    await GovernanceAPI.putDefaultStates(WS, { groups: [] });
    await GovernanceAPI.activityStats(WS, 30);
    expect(calls.map((c) => `${c.method} ${c.url}`)).toEqual([
      `post workspaces/${WS}/archive/`,
      `post workspaces/${WS}/restore/`,
      `post workspaces/${WS}/labels/`,
      `patch workspaces/${WS}/labels/l1/`,
      `delete workspaces/${WS}/labels/l1/`,
      `get workspaces/${WS}/default-states/`,
      `put workspaces/${WS}/default-states/`,
      `get workspaces/${WS}/activity-stats/`,
    ]);
    expect(calls[7]!.params).toMatchObject({ days: 30 });
  });
});

describe("PROJ-003 LifecycleAPI（§4.2）", () => {
  it("转换/历史/副本/模板列表", async () => {
    await LifecycleAPI.transition(WS, PID, { to_status: "closed", force: true });
    await LifecycleAPI.statusLogs(WS, PID);
    await LifecycleAPI.duplicate(WS, PID);
    await LifecycleAPI.listTemplates(WS);
    expect(calls.map((c) => `${c.method} ${c.url}`)).toEqual([
      `post workspaces/${WS}/projects/${PID}/transitions/`,
      `get workspaces/${WS}/projects/${PID}/status-logs/`,
      `post workspaces/${WS}/projects/${PID}/duplicate/`,
      `get workspaces/${WS}/project-templates/`,
    ]);
    expect(parseBody(calls[0]!) as Record<string, unknown>).toEqual({ to_status: "closed", force: true });
  });
});

describe("RPT-002 ProjectStatsAPI（§4.2）", () => {
  it("progress（days/tz 参数）/ members（role/order_by）", async () => {
    await ProjectStatsAPI.progress(WS, PID, { days: 30, tz: "Asia/Shanghai" });
    await ProjectStatsAPI.members(WS, PID, { role: "PROJ_ADMIN", order_by: "-open_count" });
    expect(calls[0]).toMatchObject({
      method: "get", url: `workspaces/${WS}/projects/${PID}/stats/`,
      params: { days: 30, tz: "Asia/Shanghai" },
    });
    expect(calls[1]).toMatchObject({
      method: "get", url: `workspaces/${WS}/projects/${PID}/stats/members/`,
      params: { role: "PROJ_ADMIN", order_by: "-open_count" },
    });
  });
});

describe("INTG-001 GithubIntegrationAPI（§4.2）", () => {
  it("安装入口/仓库/绑定 CRUD/日志", async () => {
    await GithubIntegrationAPI.installEntry(WS);
    await GithubIntegrationAPI.repositories(WS, PID, 9001);
    await GithubIntegrationAPI.createBinding(WS, PID, { repository_full_name: "a/b" });
    await GithubIntegrationAPI.updateBinding(WS, PID, "b1", { sync_status: "paused" });
    await GithubIntegrationAPI.deleteBinding(WS, PID, "b1");
    await GithubIntegrationAPI.syncLogs(WS, PID);
    expect(calls.map((c) => `${c.method} ${c.url}`)).toEqual([
      `get workspaces/${WS}/integrations/github/app/`,
      `get workspaces/${WS}/projects/${PID}/integrations/github/repositories/`,
      `post workspaces/${WS}/projects/${PID}/integrations/github/bindings/`,
      `patch workspaces/${WS}/projects/${PID}/integrations/github/bindings/b1/`,
      `delete workspaces/${WS}/projects/${PID}/integrations/github/bindings/b1/`,
      `get workspaces/${WS}/projects/${PID}/integrations/github/sync-logs/`,
    ]);
    expect(calls[1]!.params).toMatchObject({ installation_id: 9001 });
  });
});

describe("INTG-002 WebhookAPI（§4.2 九端点）", () => {
  it("CRUD/启停/ping/日志/重放全路径", async () => {
    const W = `workspaces/${WS}/projects/${PID}/webhooks/`;
    await WebhookAPI.list(WS, PID);
    await WebhookAPI.create(WS, PID, { url: "https://x/", events: ["issue.created"] });
    await WebhookAPI.update(WS, PID, "w1", { events: ["issue.updated"] });
    await WebhookAPI.disable(WS, PID, "w1");
    await WebhookAPI.enable(WS, PID, "w1");
    await WebhookAPI.remove(WS, PID, "w1");
    await WebhookAPI.ping(WS, PID, "w1");
    await WebhookAPI.deliveries(WS, PID, "w1", { status: "dead" });
    await WebhookAPI.replay(WS, PID, "w1", "d1");
    expect(calls.map((c) => `${c.method} ${c.url}`)).toEqual([
      `get ${W}`,
      `post ${W}`,
      `patch ${W}w1/`,
      `post ${W}w1/disable/`,
      `post ${W}w1/enable/`,
      `delete ${W}w1/`,
      `post ${W}w1/ping/`,
      `get ${W}w1/deliveries/`,
      `post ${W}w1/deliveries/d1/`,
    ]);
    expect(calls[7]!.params).toMatchObject({ status: "dead" });
  });
});
