/** E2E 补全 spec：承接 test-cases.md 中原标 Nightly/占位的用例。
 *  TC-AUTH2-007  401 拦截 → 自动跳登录页（axios 拦截器，page.on('response') 断言）
 *  TC-AUTH2-008  withCredentials：/users/me/ 请求头携带 sessionid cookie
 *  TC-AUTH2-009  响应信封解包：status=false → 前端渲染 meta.message 错误提示
 *  TC-PROJ1-007a 项目删除：confirm != name → 删除按钮 disabled（DOM 断言）
 *  运行前 API(8000) + Web(3001) 就绪：E2E_NO_SERVER=1 pnpm exec playwright test tests/e2e/coverage.spec.ts */
import { test, expect, type Page } from "@playwright/test";

const TEST_PASSWORD = "Rabbit123";

/** 每次调用生成独立邮箱（同 worker 多测试不共享注册状态） */
function freshEmail(prefix = "cov"): string {
  return `${prefix}-${Date.now()}-${Math.floor(Math.random() * 1e4)}@rabbit.dev`;
}

async function registerAndLandProjects(page: Page, email = freshEmail()) {
  await page.goto("/register");
  await page.getByLabel("邮箱").fill(email);
  await page.getByLabel("密码", { exact: true }).fill(TEST_PASSWORD);
  await page.getByLabel("确认密码").fill(TEST_PASSWORD);
  await page.getByRole("button", { name: "创建账号" }).click();
  await page.waitForURL(/\/[^/]+\/projects/, { timeout: 8_000 });
}

test.describe("覆盖补全（原 Nightly / 占位用例）", () => {
  test("TC-AUTH2-007：401/403 拦截 → 自动跳登录页", async ({ page, context }) => {
    await registerAndLandProjects(page);
    const url = page.url(); // 受保护页
    // 清 cookie 模拟会话失效，刷新受保护页 → Guard 发 /users/me/ 得 401/403（DRF SessionAuth 未认证返回 403）→ 跳 /login?next=
    await context.clearCookies();
    const sawAuthFail = page.waitForResponse(
      (r) => r.url().includes("/api/v1/users/me/") && (r.status() === 401 || r.status() === 403),
      { timeout: 10_000 },
    );
    await page.goto(url);
    await sawAuthFail;
    await page.waitForURL(/\/login/, { timeout: 5_000 });
    await expect(page.getByRole("heading", { name: /登录 RabbitProjects/ })).toBeVisible();
  });

  test("TC-AUTH2-008：withCredentials — session cookie 建立并随请求往返", async ({ page }) => {
    // 演示账号一键登录 → context 应持有 sessionid（HttpOnly，document.cookie 不可见，context.cookies 可见）
    await page.goto("/login");
    await page.getByRole("button", { name: /一键进入演示账号/ }).click();
    await page.waitForURL(/\/[^/]+\/projects/, { timeout: 8_000 });
    const cookies = await page.context().cookies();
    const session = cookies.find((c) => c.name === "sessionid");
    expect(session, "登录后应写入 sessionid cookie").toBeDefined();
    // cookie 随请求往返：刷新受保护页（Guard 发 /users/me/ 带 cookie）不再跳登录
    await page.reload();
    await page.waitForURL(/\/projects/, { timeout: 8_000 });
    await expect(page.getByRole("heading", { name: /登录/ })).not.toBeVisible();
  });

  test("TC-AUTH2-009：响应信封解包 — status=false 渲染 meta.message", async ({ page }) => {
    // mock 登录端点：HTTP 200 + 信封 status=false → axios 拦截器抛 Error(message) → 页面渲染错误提示
    await page.route("**/api/v1/auth/sign-in/", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          status: false,
          data: null,
          meta: { code: "AUTH_INVALID_CREDENTIALS", message: "邮箱或密码错误" },
        }),
      }),
    );
    await page.goto("/login");
    await page.getByLabel("邮箱").fill(freshEmail("mock"));
    await page.getByLabel("密码", { exact: true }).fill("Whatever123");
    await page.getByRole("button", { name: "登录", exact: true }).click();
    // 登录页错误提示（信封 meta.message 被解包渲染）
    await expect(page.getByText("邮箱或密码错误").first()).toBeVisible({ timeout: 5_000 });
  });

  test("TC-PROJ1-007a：项目删除 confirm != name → 按钮禁用", async ({ page }) => {
    await registerAndLandProjects(page);
    // 建项目
    await page.getByRole("button", { name: /创建项目/ }).first().click();
    await page.getByLabel("项目名称 *").fill("删除测试项目");
    await page.getByLabel("项目标识符 *").fill("DEL");
    await page.getByRole("button", { name: "创建项目", exact: true }).click();
    await page.waitForURL(/\/projects\/.+\/board/, { timeout: 10_000 });

    // 进设置页（异步加载项目详情后 danger zone 才渲染）
    await page.goto(page.url().replace(/\/board$/, "/settings"));
    const openDelete = page.locator("button", { hasText: "删除项目" }).first();
    await expect(openDelete).toBeVisible({ timeout: 10_000 });
    await openDelete.click();

    // modal：confirm 输入框是页面上第 3 个 input（设置页 name+identifier 两个 input，描述是 textarea）
    const confirmInput = page.locator("input").nth(2);
    await expect(confirmInput).toBeVisible({ timeout: 5_000 });
    await confirmInput.fill("错误的名字");
    // confirm != name（name 为空）→ modal 内确认按钮 disabled
    const confirmBtn = page.locator("button[disabled]", { hasText: "删除项目" }).last();
    await expect(confirmBtn).toBeVisible();
  });
});
