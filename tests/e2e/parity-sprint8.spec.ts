/** Sprint-8 三新页 parity（AUTH-007 组织 / AUTH-008 角色 / AUTH-010 审计）
 *  —— 断言由附录 C.150~C.152 清单行生成（ADR-0010 ③），每条带出处注释。
 *
 *  覆盖：
 *    C.150 组织管理页（树/新建/成员归属列/批量授权弹窗）｜
 *    C.151 角色页（模板采用/矩阵勾选/挂接/我的权限）｜
 *    C.152 审计页（筛选条/事件表/详情抽屉/导出对话框）
 *
 *  纪律：登录走 UI（用户入口铁律）；每 test 先 clearCookies；行为断言三件套；
 *  console+net guard；造数走页面共享 cookie 的 API（s5 同款 apiCall）。
 */
import { test, expect, type Page } from "@playwright/test";
import { attachGuards } from "./no-console-errors";

const API_ORIGIN = process.env.E2E_BASE_URL ?? "http://localhost:3001";
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
  const r = await page.request.fetch(`${API_ORIGIN}${path}`, {
    method,
    headers: { "Content-Type": "application/json", ...(csrf ? { "X-CSRFToken": csrf } : {}) },
    data: data === undefined ? undefined : JSON.stringify(data),
  });
  let body: Record<string, any> | null = null;
  try { body = (await r.json()) as Record<string, any>; } catch { /* 非 JSON */ }
  return { status: r.status(), body };
}

test.describe("S8E2E · 组织/角色/审计 parity", () => {
  /** 幂等清理：上次失败运行可能残留同名部门（活行）——软删 404 无妨。 */
  async function purgeDepts(page: Page) {
    // 两轮循环：首轮子级先删后父级才空（列表序不保证深序）
    for (let round = 0; round < 2; round++) {
      const r = await apiCall(page, "GET", `/api/v1/workspaces/${WS}/departments/`);
      const alive = ((r.body?.data as Array<{ id: string; name: string }>) ?? [])
        .filter((d) => d.name.startsWith("S8E2E "));
      for (const d of alive) {
        await apiCall(page, "DELETE", `/api/v1/workspaces/${WS}/departments/${d.id}/`)
          .catch(() => {});
      }
      if (!alive.length) break;
    }
  }
  test("C.150 组织管理页：部门树 + 新建 + 成员归属 + 批量授权弹窗（AUTH-007 §3.1/§3.2）", async ({ page }) => {
    const guards = attachGuards(page);
    // allow：同级重名 409（BR-02 负向被测）与清理 DELETE 409（幂等两轮）
    guards.allow({ method: "POST", url: "/departments/", status: 409 });
    guards.allow({ method: "DELETE", url: "/departments/", status: 409 });
    await loginDemo(page);
    await purgeDepts(page);
    // 造数：两部门 + 挂一成员（API）
    const d1 = await apiCall(page, "POST", `/api/v1/workspaces/${WS}/departments/`,
      { name: "S8E2E 研发中心" });
    expect(d1.status, JSON.stringify(d1.body)).toBe(201);
    const deptId = d1.body!.data.department.id as string;
    const d2 = await apiCall(page, "POST", `/api/v1/workspaces/${WS}/departments/`,
      { name: "S8E2E 平台组", parent_id: deptId });
    expect(d2.status).toBe(201);
    const childId = d2.body!.data.department.id as string;

    // 用户入口：WS 侧栏「组织」→ 组织管理页
    await page.goto(`/${WS}/projects`);
    await page.getByRole("link", { name: "组织" }).first().click();
    await page.waitForURL(/\/org$/);
    // 树与计数（C.150 树行；作用域限定侧栏树——成员表下拉含同名 option）
    const tree = page.locator("aside").first();
    await expect(tree.getByText("S8E2E 研发中心")).toBeVisible();
    await expect(tree.getByText("S8E2E 平台组")).toBeVisible();
    // 新建部门（C.150 新建行）：选中父部门后在同级建同名 → 结构化报错（BR-02）
    await tree.getByText("S8E2E 研发中心").click();  // 选中父级（此后新建挂在它下）
    await page.locator('[data-sb-scope="org-new-name"]').fill("S8E2E 平台组");
    await page.locator('[data-sb-scope="org-new-submit"]').click();
    await expect(page.getByText(/创建失败|已存在/).first()).toBeVisible();
    // 成员归属列（C.150 成员表行）
    await expect(page.locator('[data-sb-scope="org-member-dept"]').first()).toBeVisible();
    // 批量授权弹窗（C.150 弹窗行）：父部门已选中 → 授权开 → 预览
    await page.locator('[data-sb-scope="org-grant-open"]').click();
    await expect(page.locator('[data-sb-scope="org-grant-dialog"]')).toBeVisible();
    await page.locator('[data-sb-scope="org-grant-project"]').selectOption({ index: 1 });
    await page.locator('[data-sb-scope="org-grant-preview-btn"]').click();
    await expect(page.locator('[data-sb-scope="org-grant-preview"]')).toBeVisible();
    // 清理（API 软删子部门→父部门）
    await apiCall(page, "DELETE", `/api/v1/workspaces/${WS}/departments/${childId}/`);
    await apiCall(page, "DELETE", `/api/v1/workspaces/${WS}/departments/${deptId}/`);
    expect(guards.report() ?? []).toEqual([]);
  });

  test("C.151 角色页：模板采用 + 矩阵勾选 + 挂接 + 我的权限（AUTH-008 §3.1/§3.2/§3.3）", async ({ page }) => {
    const guards = attachGuards(page);
    await loginDemo(page);
    // 造项目随机重试（5 位随机标识符防跨 run 撞 409——对方会话同款教训）
    let pid = "";
    for (let i = 0; i < 5 && !pid; i++) {
      const suffix = Math.random().toString(36).slice(2, 7).toUpperCase();
      const r = await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/`,
        { name: `S8E2E 角色 ${suffix}`, identifier: suffix });
      if (r.status === 201) pid = r.body!.data.id as string;
    }
    expect(pid, "造项目失败（5 次随机重试后仍撞）").toBeTruthy();

    // 用户入口：项目内侧栏「角色」
    await page.goto(`/${WS}/projects/${pid}/issues`);
    await page.getByRole("link", { name: "角色" }).first().click();
    await page.waitForURL(/\/roles$/);
    // 模板采用（C.151 列表行）
    await page.locator('[data-sb-scope="role-template"]').first().click();
    await expect(page.getByText("测试工程师").first()).toBeVisible();
    // 矩阵（C.151 编辑器行）：先选中角色 → 目录码可见 + 勾选交互
    await page.locator('[data-sb-scope="role-list"] button', { hasText: "测试工程师" }).click();
    await expect(page.getByText("issue", { exact: true })).toBeVisible();
    await page.locator('[data-sb-scope="role-edit"]').click();
    const firstCheck = page.locator('[data-sb-scope="role-perm-check"]').first();
    const before = await firstCheck.isChecked();
    await firstCheck.click();
    await page.locator('[data-sb-scope="role-save"]').click();
    await expect(page.getByText("权限码已保存")).toBeVisible();
    // 挂接面（C.151 挂接行）+ 我的权限（C.151 面板行）
    await expect(page.getByText("成员挂接")).toBeVisible();
    await page.locator('[data-sb-scope="role-assign-open"]').first().click();
    await expect(page.locator('[data-sb-scope="role-effective"]')).toBeVisible();
    expect(guards.report() ?? []).toEqual([]);
  });

  test("C.152 审计页：筛选 + 事件表 + 详情抽屉 + 导出对话框（AUTH-010 §3.1/§3.2）", async ({ page }) => {
    const guards = attachGuards(page);
    guards.allow({ method: "POST", url: "/departments/", status: 409 });
    await loginDemo(page);
    await purgeDepts(page);  // 幂等清残留（上轮失败可能留活行致 409 无事件）
    // 造审计事件（部门创建经 R4 管道落库——worker 消费后可见；先造再开页）
    await apiCall(page, "POST", `/api/v1/workspaces/${WS}/departments/`,
      { name: "S8E2E 审计触点" });

    // 用户入口：WS 侧栏「审计」
    await page.goto(`/${WS}/projects`);
    await page.getByRole("link", { name: "审计" }).first().click();
    await page.waitForURL(/\/audit-logs$/);
    // 筛选条（C.152 筛选行）
    await expect(page.locator('[data-sb-scope="audit-filter-cat"]')).toBeVisible();
    await expect(page.locator('[data-sb-scope="audit-filter-search"]')).toBeVisible();
    // 事件表（C.152 表行）——worker 消费有延迟，轮询等待 department 事件出现
    await expect(
      page.locator('[data-sb-scope="audit-rows"]').getByText("department", { exact: true }).first(),
    ).toBeVisible({ timeout: 30_000 });
    // 详情抽屉（C.152 抽屉行）
    await page.locator('[data-sb-scope="audit-rows"] tr').first().click();
    await expect(page.locator('[data-sb-scope="audit-detail"]')).toBeVisible();
    await expect(page.getByText("event_key").first()).toBeVisible();
    // 导出对话框（C.152 导出行）——密码确认 UI（不真导出，断言对话框与确认禁用态）
    await page.locator('[data-sb-scope="audit-export-open"]').click();
    await expect(page.locator('[data-sb-scope="audit-export-dialog"]')).toBeVisible();
    await expect(page.locator('[data-sb-scope="audit-export-confirm"]')).toBeDisabled();
    await page.keyboard.press("Escape");
    expect(guards.report() ?? []).toEqual([]);
  });
});
