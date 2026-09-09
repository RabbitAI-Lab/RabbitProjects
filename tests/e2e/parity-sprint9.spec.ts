/** Sprint-9 新面 parity（PROJ-004 项目集 / RPT-003 迭代与报表 / RPT-004 健康
 *  度与负载 / FILE-005 Wiki / GANTT-003 关键路径）——断言由冻结原型
 *  sprint-9-hifi-prototype.html 表面映射表生成（ADR-0010 ③），每条带出处注释。
 *
 *  纪律：登录走 UI（用户入口铁律）；每 test 先 clearCookies；新页 spec 必断
 *  导航骨架在场 + 关键内容非空（S8 三缺陷教训）；console+net guard；造数走
 *  页面共享 cookie 的 API（s5/s8 同款 apiCall）；afterAll 清数（坑 22）。
 */
import { test, expect, type Page } from "@playwright/test";
import { attachGuards } from "./no-console-errors";

const API_ORIGIN = process.env.E2E_BASE_URL ?? "http://localhost:3001";
const WS = "workspace";
const TAG = "S9E2E";

async function loginDemo(page: Page) {
  await page.context().clearCookies();
  await page.goto("/login");
  await page.getByRole("button", { name: /一键进入演示账号/ }).click();
  await page.waitForFunction(() => /\/projects$/.test(location.pathname), null, { timeout: 15_000 });
}

async function apiCall(page: Page, method: string, path: string, data?: unknown) {
  const cookies = await page.context().cookies();
  const csrf = cookies.find((c) => c.name === "csrftoken")?.value ?? "";
  const r = await page.request.fetch(`${API_ORIGIN}${path}`, {
    method,
    headers: { "Content-Type": "application/json", ...(csrf ? { "X-CSRFToken": csrf } : {}) },
    data: data === undefined ? undefined : JSON.stringify(data),
  });
  let body: Record<string, any> | null = null;
  try { body = (await r.json()) as Record<string, any>; } catch { /* 非 JSON */ }
  return { status: r.status(), body };
}

type Proj = { id: string; name: string; identifier: string };

/** 造一个排期齐全的项目（CPM/报表可用），返回项目与关键 id 集。 */
async function seedProject(page: Page): Promise<{
  proj: Proj; issues: string[]; issueKeys: string[];
}> {
  const name = `${TAG}-proj-${Date.now() % 100000}`;
  // identifier 5 字符上限且工作空间内唯一——时间片尾 3 位防残留冲突
  const r = await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/`, {
    name, identifier: `S9${String(Date.now() % 1000).padStart(3, "0")}`.slice(0, 5), description: "parity 造数" });
  const proj = r.body?.data as Proj;
  const today = new Date();
  const d = (offset: number) => new Date(today.getTime() + offset * 86400000).toISOString().slice(0, 10);
  const issues: string[] = [];
  const issueKeys: string[] = [];
  for (let i = 0; i < 4; i++) {
    const it = await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/`, {
      name: `${TAG} 任务${i}`, start_date: d(1 + i), target_date: d(3 + i * 2), estimate_minutes: 480,
    });
    issues.push(it.body?.data?.id);
    issueKeys.push(it.body?.data?.issue_key);
  }
  // 依赖链 A→B（blocks）
  await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${issues[0]}/relations/`,
    { related_issue_id: issues[1], relation_type: "blocks" });
  return { proj, issues, issueKeys };
}

async function purgeProject(page: Page, projId: string) {
  // status 为 read-only（PROJ-003 生命周期唯一入口）——清理直接软删项目行
  await apiCall(page, "DELETE", `/api/v1/workspaces/${WS}/projects/${projId}/`).catch(() => {});
}

test.describe("S9E2E · 项目集/报表/Wiki/关键路径 parity", () => {
  test("V-PORT 项目集：侧栏入口 + 组合树 + 新建 + 挂载弹窗 + 汇总三卡（PROJ-004 §3.1）", async ({ page }) => {
    const guards = attachGuards(page);
    guards.allow({ method: "POST", url: "/portfolios/", status: 409 });
    await loginDemo(page);
    // 幂等清理旧名
    const old = await apiCall(page, "GET", `/api/v1/workspaces/${WS}/portfolios/`);
    for (const node of ((old.body?.data as Array<{ id: string; name: string; children?: unknown[] }>) ?? [])
      .filter((n) => n.name.startsWith(TAG))) {
      // 先卸载挂载项目（BR-11 非空阻断）再删节点；409 无妨（两轮）
      const mounted = await apiCall(page, "GET", `/api/v1/workspaces/${WS}/projects/?per_page=100`).catch(() => null);
      for (const prj of ((mounted?.body?.data as Array<{ id: string; portfolio?: string }>) ?? [])) {
        await apiCall(page, "DELETE", `/api/v1/workspaces/${WS}/portfolios/${node.id}/projects/${prj.id}/`).catch(() => {});
      }
      await apiCall(page, "DELETE", `/api/v1/workspaces/${WS}/portfolios/${node.id}/`).catch(() => {});
    }
    // 造项目 + 组合
    const { proj } = await seedProject(page);
    const pfName = `${TAG}-组合-${Date.now() % 10000}`;
    const pf = await apiCall(page, "POST", `/api/v1/workspaces/${WS}/portfolios/`, { name: pfName });
    expect(pf.status).toBe(201);
    const pfId = pf.body?.data?.id;
    await apiCall(page, "POST", `/api/v1/workspaces/${WS}/portfolios/${pfId}/projects/`, { project_id: proj.id });

    // 用户路径：侧栏「项目集」进入
    await page.goto(`/${WS}/portfolios`);
    await expect(page.locator('[data-sb-scope="page-portfolios"]')).toBeVisible();
    await expect(page.getByRole("navigation").getByText("项目集")).toBeVisible();   // 导航骨架在场（S8 教训）
    await expect(page.locator('[data-sb-scope="pf-tree"]').getByText(pfName)).toBeVisible();
    await page.locator('[data-sb-scope="pf-tree"]').getByText(pfName).click();
    // 三卡骨架 + 权重注脚（O1）
    await expect(page.locator('[data-sb-scope="pf-card-progress"]')).toBeVisible();
    await expect(page.locator('[data-sb-scope="pf-card-resource"]')).toBeVisible();
    await expect(page.locator('[data-sb-scope="pf-card-risks"]')).toBeVisible();
    await expect(page.getByText("权重 = 未取消任务数（BR-13）")).toBeVisible();
    // 项目列表行可见（关键内容非空——S8 教训）
    await expect(page.locator('[data-sb-scope="pf-projects"] tbody tr').first()).toBeVisible();
    // 挂载弹窗可开（BR-02 文案在场）
    await page.locator('[data-sb-scope="pf-mount-modal"], [data-sb-scope="pf-projects"] button', { hasText: "挂载项目" }).first().click();
    await expect(page.getByText("一个项目至多挂载一个项目集")).toBeVisible();
    await page.keyboard.press("Escape");
    await purgeProject(page, proj.id);
  });

  test("V-CYCLE 迭代页：新建迭代 + 燃尽卡 + 结束迭代弹窗（RPT-003 §3.1）", async ({ page }) => {
    const guards = attachGuards(page);
    guards.allow({ method: "GET", url: "/burndown/", status: 404 });
    await loginDemo(page);
    const { proj } = await seedProject(page);
    await page.goto(`/${WS}/projects/${proj.id}/cycles`);
    await expect(page.locator('[data-sb-scope="page-cycles"]')).toBeVisible();
    await expect(page.locator('[data-sb-scope="nav-cycles"]')).toBeVisible();
    // 新建迭代（UI 表单 → POST）
    await page.locator('[data-sb-scope="cycles-new"]').click();
    await page.locator('[data-sb-scope="cycle-new-modal"] input:not([type="date"])').fill(`${TAG}-S1`);
    await page.locator('input[type="date"]').first().fill(new Date().toISOString().slice(0, 10));
    await page.locator('input[type="date"]').nth(1).fill(new Date(Date.now() + 13 * 86400000).toISOString().slice(0, 10));
    await page.getByRole("button", { name: "创建" }).click();
    await expect(page.locator('[data-sb-scope="cycles-list"]').getByText(`${TAG}-S1`)).toBeVisible();
    // 结束迭代弹窗（BR-04 结转/移回 + 终版快照提示）
    await page.locator('[data-sb-scope="cycle-row"]', { hasText: `${TAG}-S1` }).locator("text=开始迭代").click();
    await expect(page.locator('[data-sb-scope="cycle-row"]', { hasText: `${TAG}-S1` }).locator("text=结束迭代")).toBeVisible();
    await page.locator('[data-sb-scope="cycle-row"]', { hasText: `${TAG}-S1` }).locator("text=结束迭代").click();
    await expect(page.locator('[data-sb-scope="cycle-close-modal"]')).toBeVisible();
    await expect(page.getByText("终版快照（is_final）")).toBeVisible();
    await purgeProject(page, proj.id);
  });

  test("V-HEALTH/V-LOAD 健康度与负载：总评卡 + 四维 + 热力矩阵（RPT-004 §3.1/§3.3）", async ({ page }) => {
    const guards = attachGuards(page);
    await loginDemo(page);
    const { proj } = await seedProject(page);
    // 健康度页（beat 未跑走读侧即时补算兜底）
    await page.goto(`/${WS}/projects/${proj.id}/reports/health`);
    await expect(page.locator('[data-sb-scope="page-health"]')).toBeVisible();
    await expect(page.getByRole("navigation").getByText("健康度")).toBeVisible();
    await expect(page.locator('[data-sb-scope="health-hero"]')).toBeVisible();
    await expect(page.locator('[data-sb-scope="health-dim-overdue"]')).toBeVisible();
    await expect(page.locator('[data-sb-scope="health-dim-effort"]')).toBeVisible();
    // 负载页：窗口校验文案在场（BR-07）——周一锚定
    await page.goto(`/${WS}/projects/${proj.id}/reports/workload`);
    await expect(page.locator('[data-sb-scope="page-workload"]')).toBeVisible();
    await expect(page.getByText(/窗口周一起始 ≤12 周/)).toBeVisible();
    await expect(page.locator('[data-sb-scope="workload-export"]')).toBeVisible();
    await purgeProject(page, proj.id);
  });

  test("V-WIKI + 检索：空间/页面树/草稿发布 + 独立检索入口（FILE-005 §3.1/§3.3）", async ({ page }) => {
    const guards = attachGuards(page);
    guards.allow({ method: "POST", url: "/wiki/", status: 409 });
    await loginDemo(page);
    const { proj } = await seedProject(page);
    const space = await apiCall(page, "POST", `/api/v1/workspaces/${WS}/wiki/spaces/`,
      { project_id: proj.id, name: `${TAG}-空间-${Date.now() % 10000}` });
    expect(space.status).toBe(201);
    const spaceId = space.body?.data?.id;
    const spaceName = space.body?.data?.name;
    const pg = await apiCall(page, "POST", `/api/v1/workspaces/${WS}/wiki/pages/`,
      { space_id: spaceId, title: `${TAG} 错误码规范 ${Date.now() % 10000}`, content: null });
    expect(pg.status).toBe(201);
    const pageId = pg.body?.data?.id;
    const pageTitle = pg.body?.data?.title;
    await apiCall(page, "PATCH", `/api/v1/workspaces/${WS}/wiki/pages/${pageId}/draft/`, {
      content: { type: "doc", content: [
        { type: "paragraph", content: [{ type: "text", text: `所有接口错误码必须从注册表选取 ${TAG}` }] }] } });
    await apiCall(page, "POST", `/api/v1/workspaces/${WS}/wiki/pages/${pageId}/publish/`, { base_version_id: null });

    await page.goto(`/${WS}/projects/${proj.id}/wiki`);
    await expect(page.locator('[data-sb-scope="page-wiki"]')).toBeVisible();
    await expect(page.locator('[data-sb-scope="nav-wiki"]')).toBeVisible();
    await expect(page.locator('[data-sb-scope="wiki-tree"]').getByText(pageTitle)).toBeVisible();
    await page.locator('[data-sb-scope="wiki-tree"]').getByText(pageTitle).click();
    await expect(page.locator('[data-sb-scope="wiki-doc-head"]')).toContainText(pageTitle);
    await expect(page.locator('[data-sb-scope="wiki-doc-body"]')).toContainText("错误码");  // 发布内容渲染（非空）
    // 独立检索入口（BR-10 权限前置 + 标题 3x）
    await page.goto(`/${WS}/wiki-search`);
    await page.locator('[data-sb-scope="wiki-search-bar"] input').fill(`错误码规范 ${Date.now() % 10000}`);
    page.locator('[data-sb-scope="wiki-search-bar"] button').click();
    await expect(page.locator('[data-sb-scope="wiki-search-results"]')).toContainText(pageTitle, { timeout: 10_000 });
    // 清数（软删空间）
    await apiCall(page, "DELETE", `/api/v1/workspaces/${WS}/wiki/spaces/${spaceId}/`).catch(() => {});
    await purgeProject(page, proj.id);
  });

  test("V-GANTT/V-CPMC 关键路径：控制条开关 + 红描边 + 计划分析页（GANTT-003 §3.1/§3.2）", async ({ page }) => {
    const guards = attachGuards(page);
    await loginDemo(page);
    const { proj } = await seedProject(page);
    await page.goto(`/${WS}/projects/${proj.id}/gantt`);
    // CPM 控制条（有排期完整任务才渲染——seedProject 保证 4 条）
    await expect(page.locator('[data-sb-scope="cp-bar"]')).toBeVisible({ timeout: 10_000 });
    await page.locator('[data-sb-scope="cp-toggle"]').check();
    // 开关后关键条红描边出现（O8——有 blocks 链必有 float=0 关键）
    await expect(page.locator(".rp-gbar.crit").first()).toBeVisible({ timeout: 10_000 });
    // 计划分析页（浮动卡 + 预警配置）
    await page.locator('[data-sb-scope="cp-analyze-link"]').click();
    await expect(page.locator('[data-sb-scope="page-gantt-cpm"]')).toBeVisible();
    await expect(page.locator('[data-sb-scope="cpm-float-card"]')).toContainText("最早开始");
    await expect(page.locator('[data-sb-scope="cpm-config"]')).toContainText("浮动耗尽预警");
    await purgeProject(page, proj.id);
  });
});
