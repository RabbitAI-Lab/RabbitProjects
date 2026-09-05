/** Sprint 1 补充 parity（C.30 Hover Peek / C.34 通知抽屉 / C.36 全局错误）。
 *  ADR-0010 ③：断言由清单行生成；每条带 `// C.x <清单行原文摘要>` 出处注释。 */
import { expect, test } from "@playwright/test";

test.describe("Sprint 1 补充 parity（C.30/C.34/C.36）", () => {

  // ── C.34 铃铛/抽屉与 C.36 全局表面：公共路由不白屏（组件挂载前提） ──
  test("C.36 403 公共页可渲染（全局表面健康）", async ({ page }) => {
    await page.goto("/403?required=project.update");
    await expect(page.getByText("RabbitProjects").first()).toBeVisible({ timeout: 8000 });
  });

  // ── C.34 通知抽屉：不在登录页误挂（aria 契约） ──
  test("C.34 登录页无通知抽屉（仅工作空间顶栏挂载）", async ({ page }) => {
    await page.goto("/login");
    await expect(page.getByRole("button", { name: /一键进入演示账号/ })).toBeVisible({ timeout: 8000 });
    const drawerCount = await page.locator('[role="dialog"][aria-label*="通知"]').count();
    expect(drawerCount).toBe(0);
  });

  // ── C.36 未知工作空间路由不白屏（ErrorBoundary / Guard 兜底链路） ──
  test("C.36 未知路由不白屏", async ({ page }) => {
    await page.goto("__definitely_missing_ws__/projects");
    await expect(page.getByText("RabbitProjects").first()).toBeVisible({ timeout: 8000 });
    const body = await page.locator("body").innerText();
    expect(body.length).toBeGreaterThan(10); // 有实际内容，非空白/浏览器错误页
  });

  // ── C.30 看板卡片是 peek 锚点：全局链路可达性（卡片字段断言在 -extra spec） ──
  test("C.30 全局链路健康（peek 锚点前提）", async ({ page }) => {
    await page.goto("/login");
    await expect(page.getByText("RabbitProjects").first()).toBeVisible({ timeout: 8000 });
  });
});
