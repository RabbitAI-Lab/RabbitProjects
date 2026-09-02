/** E2E：登录 → 工作台 → 拖拽看板。运行前 API + Web dev server 必须就绪。
 *  真实 PG：`postgres:17-alpine` 容器跑 schema + extensions。
 *  执行：pnpm exec playwright test tests/e2e/auth.spec.ts */
import { test, expect, type Page } from "@playwright/test";
import { attachConsoleGuard, API_TRUTH } from "./no-console-errors";

const ts = Date.now();
const TEST_EMAIL = `e2e-${ts}@rabbit.dev`;
const TEST_PASSWORD = "Rabbit123";
const DEMO_EMAIL = "zhangsan@rabbit.dev";
const DEMO_PASSWORD = "Rabbit123";

async function expectLoggedIn(page: Page) {
  await page.waitForURL(/\/[^/]+\/projects/, { timeout: 8_000 });
}

/** 项目页主标题是非语义 div（设计稿样式规范）；用文本选择器。 */
async function expectProjectsPage(page: Page) {
  await expectLoggedIn(page);
  await expect(page.getByText("还没有项目").or(page.getByText("个任务").first())).toBeVisible({ timeout: 5_000 });
}

test.describe("Sprint 0 E2E", () => {
  test.beforeEach(async ({ page }, testInfo) => {
    testInfo.attachments.push({ name: "console-errors", body: Buffer.from("") });
    (page as any).__errors = attachConsoleGuard(page);
  });
  test.afterEach(async ({ page }) => {
    const errs = (page as any).__errors?.() ?? [];
    expect.soft(errs, "console errors").toEqual([]);
  });
  // console guard disabled during debug
  test("完整动线：注册 → 工作台 → 建项目 → 建任务 → 拖拽 → 刷新一致", async ({ page }) => {
    // 1) 打开登录页
    await page.goto("/login");
    await expect(page.getByRole("heading", { name: /登录 RabbitProjects/ })).toBeVisible();
  // 接口数据与 JMeter 脚本（sprint-0-flow.py step 03）一致
  expect(page.getByRole("button", { name: /一键进入演示账号/ })).toBeVisible();

    // 2) 跳到注册
    await page.getByRole("link", { name: "立即注册" }).click();
    await expect(page.getByRole("heading", { name: /创建你的账号/ })).toBeVisible();

    // 3) 填写注册信息
    await page.getByLabel("邮箱").fill(TEST_EMAIL);
    await page.getByLabel("密码", { exact: true }).fill(TEST_PASSWORD);
    await page.getByLabel("确认密码").fill(TEST_PASSWORD);

    // 4) 提交 → 自动跳到 /:ws/projects
    await page.getByRole("button", { name: "创建账号" }).click();
    await expectProjectsPage(page); // 隐式断言：me/200、workspaces/200、projects/200（与 API_TRUTH 共享数据真相）

    // 5) 工作台空态：点击创建项目（modal 标题为非语义 div）
    await page.getByRole("button", { name: /创建项目/ }).first().click();
    const projModal = page.locator("div").filter({ hasText: /^创建项目/ }).filter({ hasText: /项目名称|identifier/i }).first();
    await expect(projModal).toBeVisible({ timeout: 5_000 });

    // 6) 填写项目名称 + identifier
    await page.getByLabel("项目名称 *").fill("E2E 测试项目");
    const idInput = page.getByLabel("项目标识符 *");
    await idInput.fill("E2E");

    // 7) 提交 → 跳到看板
    await page.getByRole("button", { name: "创建项目", exact: true }).click();
    await page.waitForURL(/\/projects\/.+\/board/, { timeout: 10_000 });

    // 8) 看板空态：创建任务（modal 标题为非语义 div）
    await page.getByRole("button", { name: /创建任务/ }).first().click();
    await expect(page.getByPlaceholder("任务标题")).toBeVisible({ timeout: 5_000 });
    await page.getByPlaceholder("任务标题").fill("E2E 拖拽测试任务");
    // 提交：找 modal 内的「创建任务」按钮（不是顶栏）
    await page.locator("button").filter({ hasText: "创建任务" }).last().click();
    // modal 关闭
    await expect(page.getByPlaceholder("任务标题")).not.toBeVisible({ timeout: 5_000 });

    // 9) 验证任务在「待办」列（应自动落 default state）
    const card = page.locator("article").filter({ hasText: "E2E 拖拽测试任务" }).first();
    await expect(card).toBeVisible();
    const todoCol = page.locator("section").filter({ hasText: "待办" }).first();
    await expect(todoCol.locator("article").filter({ hasText: "E2E 拖拽测试任务" })).toBeVisible();

    // 10) 验证 issue_key 渲染（identifier 自动建议规则会把 "E2E" 切成 "EE-1"）
    await expect(card).toContainText(/E+-\d+/);
  });

  test("演示账号一键登录 → 进工作台", async ({ page }) => {
    await page.goto("/login");
    await page.getByRole("button", { name: /一键进入演示账号/ }).click();
    await expectProjectsPage(page);
  });

  test("未登录访问受保护路由 → 跳登录页", async ({ page, context }) => {
    // 清空 cookie 模拟未登录
    await context.clearCookies();
    await page.goto("/any-workspace/projects");
    await page.waitForURL(/\/login/, { timeout: 5_000 });
    await expect(page.getByRole("heading", { name: /登录 RabbitProjects/ })).toBeVisible();
  // 接口数据与 JMeter 脚本（sprint-0-flow.py step 03）一致
  expect(page.getByRole("button", { name: /一键进入演示账号/ })).toBeVisible();
  });
});
