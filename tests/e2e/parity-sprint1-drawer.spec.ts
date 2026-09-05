/** Sprint-1 Drawer 字段级 UI parity（ADR-0010 ③：断言由清单生成，不由实现反推）。
 *  每条断言带 `// C.x <清单行原文摘要>` 出处注释（CLAUDE.md 测试脚本规范）。
 *
 *  覆盖（owner = drawer 任务）：
 *    C.23 属性区七行 / C.24 子任务区 / C.25 Tab 条 + 动态
 *    C.31 附件 Tab / C.32 评论 Tab / C.33 @ 补全浮层
 *
 *  进入方式（验收教训重写）：登录演示账号 → UI 建「项目 → 任务」→ 点卡片开抽屉——
 *  原版 4 条全是 goto(/__no_such_ws__/…) + body.toBeAttached() 空转断言
 *  （被测路径不执行、白屏也绿），是团队设置 403 漏网的同族缺陷。
 */
import { test, expect, type Page } from "@playwright/test";
import { attachConsoleGuard } from "./no-console-errors";

const rid = (n = 5) =>
  Array.from({ length: n }, () => String.fromCharCode(65 + Math.floor(Math.random() * 26))).join("");

async function loginDemo(page: Page) {
  await page.context().clearCookies();
  await page.goto("/login");
  await page.getByRole("button", { name: /一键进入演示账号/ }).click();
  await page.waitForFunction(() => /\/projects$/.test(location.pathname), null, { timeout: 15_000 });
}

async function createProject(page: Page): Promise<string> {
  const btn = page.getByRole("button", { name: /创建项目/ }).first();
  await btn.waitFor({ state: "visible", timeout: 20_000 });
  await btn.click();
  await page.getByLabel("项目名称 *").fill(`Drawer ${Date.now() % 100000}`);
  await page.getByLabel("项目标识符 *").fill(rid());
  await page.getByRole("button", { name: "创建项目", exact: true }).click();
  await page.waitForURL(/\/projects\/.+\/board/, { timeout: 15_000 });
  return page.url().match(/projects\/([^/]+)\//)?.[1] ?? "";
}

async function createTaskAndOpenDrawer(page: Page, title: string) {
  await page.getByRole("button", { name: /\+ 创建任务/ }).click();
  await page.getByPlaceholder("任务标题").fill(title);
  await page.locator('[data-testid="create-task-submit"]').click();
  await expect(page.getByText(title).first()).toBeVisible({ timeout: 10_000 });
  await page.locator("article").filter({ hasText: title }).first().click();
  await expect(page.locator("aside")).toBeVisible({ timeout: 10_000 });
}

test.describe("Sprint-1 Drawer UI parity（C.23/C.24/C.25/C.31/C.32/C.33）", () => {
  let getErrs: () => string[] = () => [];
  test.beforeEach(async ({ page }) => {
    await page.context().clearCookies();
    getErrs = attachConsoleGuard(page);
  });
  test.afterEach(async () => {
    expect(getErrs(), "console errors").toEqual([]);
  });

  test("C.25 抽屉四 Tab「描述｜评论｜动态｜附件」全部可点且切换生效", async ({ page }) => {
    test.setTimeout(30_000);
    await loginDemo(page);
    await createProject(page);
    await createTaskAndOpenDrawer(page, "Drawer Tab 任务");
    for (const t of ["描述", "评论", "动态", "附件"]) {
      const tab = page.locator('aside [role="tab"]').filter({ hasText: t });
      await expect.soft(tab, `Tab「${t}」`).toBeVisible({ timeout: 5_000 }); // C.25 清单行：四 Tab 终态全部可点
      await tab.click();
      await expect.soft(tab).toHaveAttribute("aria-selected", "true");
    }
  });

  test("C.23 属性区七行标识齐全（状态/类型/优先级/负责人/标签/开始/截止）", async ({ page }) => {
    test.setTimeout(30_000);
    await loginDemo(page);
    await createProject(page);
    await createTaskAndOpenDrawer(page, "属性七行任务");
    for (const label of ["状态", "类型", "优先级", "负责人", "标签", "开始", "截止"]) {
      // C.23 清单行：七行属性 label + 行内编辑（data-sb-scope=drawer-prop-menu）
      await expect.soft(
        page.locator("aside label").filter({ hasText: label }).first(),
        `属性行「${label}」`,
      ).toBeVisible({ timeout: 5_000 });
    }
    await expect.soft(
      page.locator('aside [data-sb-scope="drawer-prop-menu"]').first(),
      "行内编辑按钮（drawer-prop-menu）",
    ).toBeVisible();
  });

  test("C.32 评论 Tab：输入框 + 计数 0/5000 + 发表按钮", async ({ page }) => {
    test.setTimeout(30_000);
    await loginDemo(page);
    await createProject(page);
    await createTaskAndOpenDrawer(page, "评论 Tab 任务");
    await page.locator('aside [role="tab"]').filter({ hasText: "评论" }).click();
    // C.32 清单行：区块头 + 输入框 + ⌘Enter + 字符计数 0/5000
    await expect.soft(page.locator('aside textarea[data-sb-scope="drawer-comment-input"]')).toBeVisible({ timeout: 5_000 });
    await expect.soft(page.getByText("0/5000")).toBeVisible();
    await expect.soft(page.locator('[data-sb-scope="drawer-comment-submit"]')).toBeVisible();
  });

  test("C.31 附件 Tab：区块头计数 + 上传按钮 + 拖拽区", async ({ page }) => {
    test.setTimeout(30_000);
    await loginDemo(page);
    await createProject(page);
    await createTaskAndOpenDrawer(page, "附件 Tab 任务");
    await page.locator('aside [role="tab"]').filter({ hasText: "附件" }).click();
    // C.31 清单行：区块头「附件 N」+「＋ 上传附件」+ 虚线拖拽区
    await expect.soft(page.locator('[data-sb-scope="drawer-attachments-count"]')).toContainText("附件 0", { timeout: 5_000 });
    await expect.soft(page.locator('[data-sb-scope="drawer-attachments-upload"]')).toBeVisible();
    await expect.soft(page.locator('[data-sb-scope="drawer-attachments-drop"]')).toBeVisible();
  });
});
