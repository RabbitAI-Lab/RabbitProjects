/** Sprint-7 UI 补口轮 parity（C.144~C.149）——入口与管理面字段级断言。
 *  ADR-0010 ③：断言由附录 C 清单行生成；每条带 `// C.x <行摘要>` 出处。
 *  守卫：attachGuards（console + 网络 ≥400 全记；本组表面无预期 4xx）。 */
import { expect, test, type Page } from "@playwright/test";

import { attachGuards, HTTP } from "./no-console-errors";

const WS = "workspace";

async function loginDemo(page: Page) {
  await page.context().clearCookies();
  await page.goto("/login");
  await page.getByRole("button", { name: /一键进入演示账号/ }).click();
  await page.waitForFunction(() => /\/projects$/.test(location.pathname), null, { timeout: 15_000 });
}

async function apiCall(page: Page, method: string, path: string, data?: unknown) {
  const cookies = await page.context().cookies();
  const csrf = cookies.find((c) => c.name === "csrftoken")?.value ?? "";
  const r = await page.request.fetch(`http://localhost:8000/api/v1${path}`, {
    method,
    headers: { "Content-Type": "application/json", ...(csrf ? { "X-CSRFToken": csrf } : {}) },
    data: data === undefined ? undefined : JSON.stringify(data),
  });
  return { status: r.status(), body: await r.json().catch(() => null) };
}

async function createProject(page: Page, tag: string) {
  // 标识符 ≤5 字符（tag 3 位 + 随机 2 位）；跨 run 残留撞 409 则换号重试
  for (let i = 0; i < 8; i += 1) {
    const ident = `${tag}${String(Math.floor(Math.random() * 90) + 10)}`.slice(0, 5);
    const r = await apiCall(page, "POST", `/workspaces/${WS}/projects/`,
      { name: `S7UI ${tag}`, identifier: ident });
    if (r.status === HTTP.CREATED) return r.body.data.id as string;
  }
  throw new Error(`建项目 ${tag} 重试耗尽`);
}

test.describe("Sprint-7 UI 补口（C.144~C.149 · 入口与管理面）", () => {
  let getErrs: ReturnType<typeof attachGuards> | undefined;
  test.beforeEach(async ({ page }) => {
    await page.context().clearCookies();
    getErrs = attachGuards(page);
  });
  test.afterEach(async () => {
    expect.soft(getErrs?.report() ?? [], "console/net errors").toEqual([]);
  });

  test("C.144 工作流列表：侧栏入口 + 行/徽标 + 新建弹层 + 模板入口", async ({ page }) => {
    test.setTimeout(60_000);
    await loginDemo(page);
    const pid = await createProject(page, "W7A");
    // 直达列表页（侧栏入口在 board 页同断言）
    await page.goto(`/${WS}/projects/${pid}/workflows`);
    await expect(page.getByRole("heading", { name: "工作流" })).toBeVisible({ timeout: 10_000 });
    // C.144 空态：还没有工作流文案（新项目零配置）
    await expect(page.getByText("还没有工作流")).toBeVisible();
    // 新建弹层：名称输入 + 取消（不真建，防脏数据）
    await page.locator('[data-sb-scope="wf-create-btn"]').click();
    await expect(page.locator('[data-sb-scope="wf-create-dialog"]')).toBeVisible();
    await page.locator('[data-sb-scope="wf-create-dialog"] input').fill("冒烟草稿");
    await page.locator('[data-sb-scope="wf-create-dialog"] button', { hasText: "取消" }).click();
    // 模板库入口（C.145 联动）
    await expect(page.locator('[data-sb-scope="goto-templates"]')).toBeVisible();
    // 项目侧栏新入口：工作流/工时/自动化/审计四项
    await page.goto(`/${WS}/projects/${pid}/board`);
    const nav = page.getByRole("navigation");
    await expect(nav.getByRole("link", { name: "工作流", exact: true })).toBeVisible();
    await expect(nav.getByRole("link", { name: "工时", exact: true })).toBeVisible();
    await expect(nav.getByRole("link", { name: "自动化", exact: true })).toBeVisible();
    await expect(nav.getByRole("link", { name: "审计", exact: true })).toBeVisible();
  });

  test("C.145 模板库：预设四套卡片 + 两步下发向导（预演→取消）", async ({ page }) => {
    test.setTimeout(60_000);
    await loginDemo(page);
    // 先备好目标项目（下拉选项在页面加载时拉取——后建会缺项）
    const pid = await createProject(page, "W7B");
    await page.goto(`/${WS}/workflow-templates`);
    await expect(page.getByRole("heading", { name: "工作流模板库" })).toBeVisible({ timeout: 10_000 });
    // C.145 预设四套（幂等补种）
    const cards = page.locator('[data-sb-scope="tpl-row"]');
    await expect(cards.first()).toBeVisible();
    expect(await cards.count()).toBeGreaterThanOrEqual(4);
    // 两步下发：第一步选项目 → 预演（BR-05 映射回显）→ 取消（不实例化，防脏数据）
    await cards.first().locator('[data-sb-scope="tpl-distribute-btn"]').click();
    const dialog = page.locator('[data-sb-scope="dist-dialog"]');
    await expect(dialog).toBeVisible();
    const select = dialog.locator('[data-sb-scope="dist-project-select"]');
    await expect(select).toBeVisible();
    await select.selectOption(pid);
    await dialog.locator('[data-sb-scope="dist-preview-btn"]').click();
    await expect(dialog.locator('[data-sb-scope="dist-preview"]')).toBeVisible({ timeout: 10_000 });
    await dialog.getByRole("button", { name: "取消" }).click();
  });

  test("C.146 自动化规则：列表/启停开关 + Dry Run 弹层 + 运行日志 Tab", async ({ page }) => {
    test.setTimeout(60_000);
    await loginDemo(page);
    const pid = await createProject(page, "W7C");
    const rule = await apiCall(page, "POST", `/workspaces/${WS}/projects/${pid}/automation-rules/`, {
      name: "S7UI 冒烟规则", trigger: { type: "state_changed", config: { to_group: "started" } },
      conditions: [], actions: [{ type: "set_field", config: { field: "priority", value: "high" } }],
    });
    expect(rule.status).toBe(HTTP.CREATED);
    const iss = await apiCall(page, "POST", `/workspaces/${WS}/projects/${pid}/issues/`, { name: "S7UI 样本" });
    expect(iss.status).toBe(HTTP.CREATED);

    await page.goto(`/${WS}/projects/${pid}/automation`);
    await expect(page.getByRole("heading", { name: "自动化" })).toBeVisible({ timeout: 10_000 });
    // C.146 列表行 + 启停开关（PATCH is_active）
    const row = page.locator('[data-sb-scope="rule-row"]').first();
    await expect(row).toBeVisible();
    await expect(row).toContainText("S7UI 冒烟规则");
    await expect(row).toContainText("设置字段");
    // Dry Run 弹层：搜任务 → 选中 → 结果 JSON（0 写预演）
    await row.locator('[data-sb-scope="rule-dryrun-btn"]').click();
    const dry = page.locator('[data-sb-scope="dryrun-dialog"]');
    await expect(dry).toBeVisible();
    await dry.locator("input").fill("S7UI 样本");
    await dry.getByRole("button", { name: /S7UI 样本/ }).click();
    await expect(dry.locator('[data-sb-scope="dryrun-result"]')).toBeVisible({ timeout: 10_000 });
    await dry.getByRole("button", { name: "关闭" }).click();
    // 运行日志 Tab（空态文案也是断言面）
    await page.locator('[data-sb-scope="automation-tabs"] [data-tab="runs"]').click();
    await expect(page.locator('[data-sb-scope="runs-table"]')).toBeVisible();
  });

  test("C.147 画布边配置：审批流下拉 + 四类守卫编辑器", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const pid = await createProject(page, "W7D");
    const wf = await apiCall(page, "POST", `/workspaces/${WS}/projects/${pid}/workflows/`, { name: "冒烟流" });
    expect(wf.status).toBe(HTTP.CREATED);
    const states = await apiCall(page, "GET", `/workspaces/${WS}/projects/${pid}/states/`);
    const s = states.body.data as Array<{ id: string; name: string }>;
    const graph = await apiCall(page, "PUT", `/workspaces/${WS}/projects/${pid}/workflows/${wf.body.data.id}/graph/`, {
      states: [
        { id: "n1", state_id: s[0].id, is_initial: true, layout_x: 80, layout_y: 200, field_locks: [] },
        { id: "n2", state_id: s[1].id, is_initial: false, layout_x: 360, layout_y: 200, field_locks: [] },
      ],
      transitions: [
        { id: "e1", from_state_id: "n1", to_state_id: "n2", name: "冒烟边", guards: [], side_effects: [], approval_flow_id: null, sort_order: 100 },
      ],
    }, );
    expect(graph.status, JSON.stringify(graph.body).slice(0, 200)).toBe(HTTP.OK);

    await page.goto(`/${WS}/projects/${pid}/workflows/${wf.body.data.id}/canvas`);
    await page.locator(".react-flow__edge").first().click({ force: true, timeout: 10_000 });
    const aside = page.locator('[data-sb-scope="edge-config"]');
    await expect(aside).toBeVisible({ timeout: 5_000 });
    // C.147 审批流下拉 + 四类守卫控件在场（required_fields 勾选展开字段 chips）
    await expect(aside.locator('[data-sb-scope="edge-approval-select"]')).toBeVisible();
    await expect(aside.locator('[data-sb-scope="guard-required-fields"]')).toBeVisible();
    await expect(aside.locator('[data-sb-scope="guard-estimate"]')).toBeVisible();
    await expect(aside.locator('[data-sb-scope="guard-blocker"]')).toBeVisible();
    await expect(aside.locator('[data-sb-scope="guard-role"]')).toBeVisible();
    await aside.locator('[data-sb-scope="guard-required-fields"]').check();
    await expect(aside.locator('[data-sb-scope="guard-rf-fields"]')).toBeVisible();
    // 不保存——冒烟只断言配置面（防脏数据）
  });

  test("C.148 审计页：完整性徽标 + 事件列表 + 导出按钮", async ({ page }) => {
    test.setTimeout(60_000);
    await loginDemo(page);
    const pid = await createProject(page, "W7E");
    await page.goto(`/${WS}/projects/${pid}/audit`);
    await expect(page.getByRole("heading", { name: "留痕审计" })).toBeVisible({ timeout: 10_000 });
    // C.148 空链也是有效链（0 事件）
    await expect(page.locator('[data-sb-scope="audit-verify-badge"]')).toContainText(/链完整/);
    await expect(page.locator('[data-sb-scope="audit-events"]')).toBeVisible();
    await expect(page.locator('[data-sb-scope="audit-export-btn"]')).toBeVisible();
  });

  test("C.149 字段权限矩阵：菜单入口 + 矩阵网格（勾选即配置面）", async ({ page }) => {
    test.setTimeout(60_000);
    await loginDemo(page);
    const pid = await createProject(page, "W7F");
    const cf = await apiCall(page, "POST", `/workspaces/${WS}/projects/${pid}/issue-properties/`, {
      name: "冒烟字段", field_key: "cf_smoke", field_type: "text",
    });
    expect(cf.status).toBe(HTTP.CREATED);
    await page.goto(`/${WS}/projects/${pid}/settings/fields`);
    const row = page.locator("tr").filter({ hasText: "冒烟字段" }).first();
    await expect(row).toBeVisible({ timeout: 10_000 });
    await row.locator('[data-sb-scope="field-row-menu"]').click();
    await row.locator('[data-sb-scope="field-menu-perm"]').click();
    // C.149 矩阵：四角色 × 三列勾选网格 + 保存钮（BR-08/BR-17 服务端校验）
    const modal = page.locator('[data-sb-scope="perm-matrix"]');
    await expect(modal).toBeVisible();
    await expect(modal.getByText("管理员", { exact: true })).toBeVisible();
    await expect(modal.getByText("只读", { exact: true })).toBeVisible();
    const cells = modal.locator('[data-sb-scope="perm-cell"]');
    expect(await cells.count()).toBeGreaterThanOrEqual(12);
    await modal.locator('[data-sb-scope="perm-save-btn"]').click();
    await expect(modal).toBeHidden({ timeout: 5_000 });
  });
});
