import { expect, test, type Page } from "@playwright/test";

async function loginDemo(page: Page) {
  await page.context().clearCookies();
  await page.goto("/login");
  await page.getByRole("button", { name: /一键进入演示账号/ }).click();
  await page.waitForFunction(() => /\/projects$/.test(location.pathname), null,
    { timeout: 15_000 });
}

/** RPT-001 前端补齐（C.35 个人工作台）：我的任务页四卡/趋势/列表/筛选。 */
test.describe("我的任务（RPT-001 · C.35）", () => {
  test.setTimeout(60_000);

  test("RPT001-1 菜单可点 + 四统计卡 + 待办列表非空", async ({ page }) => {
    await loginDemo(page);
    await expect(page.locator("nav a").filter({ hasText: "我的任务" })).toBeVisible();
    await page.locator("nav a").filter({ hasText: "我的任务" }).click();
    await expect(page).toHaveURL(/\/my-tasks$/);
    await expect(page.locator('[data-sb-scope="my-tasks-card"]')).toHaveCount(4);
    await expect(page.locator('[data-sb-scope="my-tasks-card"]').first()).toContainText("待办");
    await expect(page.locator('[data-sb-scope="my-tasks-rows"] tr').first()).toBeVisible();
  });

  test("RPT001-2 趋势恒 7 柱 + 卡片点击筛选联动", async ({ page }) => {
    await loginDemo(page);
    await page.goto("/workspace/my-tasks");
    await page.waitForTimeout(2500);
    const bars = page.locator('[data-sb-scope="my-tasks-trend"] > div');
    await expect(bars).toHaveCount(7);                     // BR-10 补零恒 7 点
    await page.locator('[data-sb-scope="my-tasks-card"]').first().click();
    await page.waitForTimeout(600);
    await expect(page.locator('[data-sb-scope="my-tasks-rows"]')).toBeVisible();
  });
});
