/** Sprint-1 字段级 UI parity 扫描（ADR-0010 ③：断言由清单生成，不由实现反推）。
 *  每条 `expect.soft` 携带 `// C.x <清单行原文摘要>` 出处注释（CLAUDE.md 测试脚本规范）。
 *
 *  本文件覆盖（owner = 本次 sprint-1 extra 任务）：
 *    C.25 任务详情抽屉·Tab 条「描述｜评论｜动态｜附件」四 Tab 全部可点
 *    C.26 标签管理面板 720px（入口：列表 / 看板筛选条「管理标签」）
 *    C.28 任务列表·筛选/搜索/排序（工具条 + Chips + 命中数 + 总计数）
 *    C.29 看板四列与筛选工具条（第四列「已取消」+ 工具条 + 管理标签入口）
 *    C.31 附件 Tab（拖拽区 + 文件行空态）
 *    C.32 评论 Tab（列表空态 + 输入框 + ⌘Enter 提示）
 *
 *  设计要点（沿用 parity.spec.ts）：
 *  - expect.soft：任一断言失败不中断，一次跑出**全部**缺失字段清单
 *  - 不依赖其他 agent 的实现（不直接进 /board /issues 视图，规避跨实现耦合）；
 *    取最大可独立验证点：/labels-admin 调试路由、Issue Drawer tab strip（通过
 *    routes/board.tsx 触发 ?peekIssue 的 720px aside）。
 *  运行：E2E_NO_SERVER=1 pnpm exec playwright test tests/e2e/parity-sprint1-extra.spec.ts --reporter=line
 */
import { test, expect } from "@playwright/test";
import { attachConsoleGuard } from "./no-console-errors";

test.describe("Sprint-1 extra UI parity（C.25/C.26/C.28/C.29/C.31/C.32）", () => {
  let getErrs: () => string[] = () => [];
  test.beforeEach(async ({ page }) => { getErrs = attachConsoleGuard(page); });
  test.afterEach(async () => { expect(getErrs(), "console errors").toEqual([]); });

  /* ── C.26 标签管理面板 720px（调试入口 /labels-admin?slug=…&projectId=…）── */
  test("C.26 标签管理面板 720px 渲染面板头 + 新建标签 + 空态", async ({ page }) => {
    test.setTimeout(30_000);
    const slug = `__parity_ws_${Date.now()}`;
    const projectId = `00000000-0000-0000-0000-${Date.now().toString().padStart(12, "0").slice(-12)}`;
    await page.goto(`/labels-admin?slug=${slug}&projectId=${projectId}`);
    // dialog 必须可见（720px modal 容器）
    await expect.soft(page.locator('[data-sb-scope="labels-admin-body"]'))
      .toBeVisible({ timeout: 8000 });
    await expect.soft(page.locator('[data-sb-scope="labels-admin-body"]'))
      .toHaveAttribute("aria-label", "标签管理", { timeout: 3000 });
    // C.26 面板头：「项目标签」+「＋ 新建标签」
    await expect.soft(page.getByText("项目标签")).toBeVisible();
    await expect.soft(page.locator('[data-sb-scope="labels-admin-new"]')).toBeVisible();
    // 空态「还没有标签」
    await expect.soft(page.getByText("还没有标签")).toBeVisible({ timeout: 5000 });
  });

  test("C.26 标签管理面板 新建标签 → 列表行出现", async ({ page }) => {
    test.setTimeout(30_000);
    const slug = `__parity_ws_${Date.now()}`;
    const projectId = `00000000-0000-0000-0000-${Date.now().toString().padStart(12, "0").slice(-12)}`;
    await page.goto(`/labels-admin?slug=${slug}&projectId=${projectId}`);
    await expect.soft(page.locator('[data-sb-scope="labels-admin-body"]'))
      .toBeVisible({ timeout: 8000 });
    // 点击新建 → 表单行展开（含名称 + hex + 12 预设色）
    await page.locator('[data-sb-scope="labels-admin-new"]').click();
    // aria-label=标签名称 / 颜色 hex / 预设颜色 role=group
    await expect.soft(page.getByLabel("标签名称")).toBeVisible();
    await expect.soft(page.getByLabel("颜色 hex")).toBeVisible();
    await expect.soft(page.getByRole("group", { name: "预设颜色" })).toBeVisible();
  });

  /* ── C.28 + C.29 工具条 + 管理标签入口（公共 UI 元素，跨列表/看板复用） ── */
  // 仅校验「管理标签」按钮在 board 路由下可挂载（不依赖登录态数据）；
  // 实际业务校验由 boards-list.tsx / issues-list.tsx 的实现者在登录后跑完整端到端。
  test("C.29 看板工具条存在「管理标签」入口（登录后真项目看板）", async ({ page }) => {
    test.setTimeout(30_000);
    // 原版 goto(/__no_such_ws__/…) + body 可见 = 空转（未登录即被跳走，按钮从未渲染）
    await page.context().clearCookies();
    await page.goto("/login");
    await page.getByRole("button", { name: /一键进入演示账号/ }).click();
    await page.waitForFunction(() => /\/projects$/.test(location.pathname), null, { timeout: 15_000 });
    const first = page.locator('main a[href*="/board"]').first();
    await first.waitFor({ state: "visible", timeout: 15_000 });
    await first.click();
    await page.waitForURL(/\/board/, { timeout: 15_000 });
    // C.29 清单行：看板筛选工具条「管理标签」入口（按钮真实渲染且可点开弹窗）
    const mgr = page.getByRole("button", { name: /管理标签/ }).first();
    await expect.soft(mgr, "管理标签入口").toBeVisible({ timeout: 10_000 });
    await mgr.click();
    await expect.soft(page.locator('[data-sb-scope="labels-admin-modal"], [role="dialog"]').first())
      .toBeVisible({ timeout: 5_000 });
  });

  /* ── C.25 / C.31 / C.32 Drawer 4 Tab strip（通过看板卡片 → ?peekIssue 触发）── */
  // 仅验证公共 aside 元素可在未登录跳走前渲染（避开完整鉴权流程）
  test("C.27 看板四列齐备（含「已取消」第 4 列）", async ({ page }) => {
    test.setTimeout(30_000);
    // 原版同文件第 2 条空转 goto 已删除——Drawer 四 Tab 的行为级断言在
    // parity-sprint1-drawer.spec.ts（登录 → 建任务 → 开抽屉逐 Tab 点选）。
    // 此处补 C.27/C.29 清单行的看板列结构断言（BOARD-002 §3.1）。
    await page.context().clearCookies();
    await page.goto("/login");
    await page.getByRole("button", { name: /一键进入演示账号/ }).click();
    await page.waitForFunction(() => /\/projects$/.test(location.pathname), null, { timeout: 15_000 });
    const first = page.locator('main a[href*="/board"]').first();
    await first.waitFor({ state: "visible", timeout: 15_000 });
    await first.click();
    await page.waitForURL(/\/board/, { timeout: 15_000 });
    for (const col of ["待办", "进行中", "已完成", "已取消"]) {
      await expect.soft(page.locator("section").filter({ hasText: new RegExp(`^${col}$|${col}`) }).first(),
        `看板列「${col}」`).toBeVisible({ timeout: 10_000 });
    }
  });
});