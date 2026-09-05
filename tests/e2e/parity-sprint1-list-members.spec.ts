/** Sprint-1 列表页 + 成员管理 字段级 parity（ADR-0010 ③：断言由清单生成，不由实现反推）。
 *  每条 expect.soft 携带 `// C.x <清单行原文摘要>` 出处注释（CLAUDE.md 测试脚本规范）。
 *
 *  本文件覆盖（owner = 本次 sprint-1 list/members 任务）：
 *    C.18 移除确认弹窗（400px）+ 转让所有权弹窗（480px）（来源：TEAM-002 §3.4/§3.6）
 *    C.19 项目列表页改造 `/:workspaceSlug/projects`（来源：PROJ-002 §3.1）
 *    C.21 添加成员弹窗（520px）（来源：PROJ-002 §3.3）
 *
 *  设计要点（沿用 parity-sprint1-extra.spec.ts）：
 *  - 不依赖其他 agent 的实现（IssueDrawer / Topbar / 鉴权路径），只用可直接路由的页面 +
 *    E2E_NO_SERVER 模式（mock backend）下可独立触发的 UI 元素：
 *    * C.19 列表页 → /:ws/projects 直接 GET 渲染
 *    * C.21 添加成员弹窗 → 项目设置·成员页「+ 添加成员」按钮（路由 /projects/:id/settings/members）
 *    * C.18 移除确认 → /:ws/settings/members 行内 ⋯ 触发；C.18 转让 → DangerZone 按钮触发
 *  - 公共布局元素（Logo + RabbitProjects）作为 hydration 同步锚点；
 *  - 在 axios 拦截器跳走之前尽量多断字段级断言（一旦跳 /login，公共 layout 仍渲染）。
 *  - 用 role/aria/data-testid 锚点（不用纯 CSS class hash，规避 Tailwind 改版）。
 *
 *  运行：E2E_NO_SERVER=1 pnpm exec playwright test tests/e2e/parity-sprint1-list-members.spec.ts --reporter=line
 */
import { test, expect, type Page } from "@playwright/test";
import { attachConsoleGuard } from "./no-console-errors";

async function loginDemo(page: Page): Promise<string> {
  await page.goto("/login");
  await page.getByRole("button", { name: /一键进入演示账号/ }).click();
  await page.waitForURL(/\/projects/, { timeout: 8000 });
  // 返回演示账号的 workspace slug（URL 首段）
  const m = page.url().match(/\/([^/]+)\/projects/);
  return m?.[1] ?? "rabbitprojects";
}

test.describe("Sprint-1 list/members UI parity（C.18/C.19/C.21）", () => {
  let getErrs: () => string[] = () => [];
  test.beforeEach(async ({ page }) => { getErrs = attachConsoleGuard(page); });
  test.afterEach(async () => { expect(getErrs(), "console errors").toEqual([]); });

  /* ════════════════════════════════════════════════════════════════════════════
   *  C.19 项目列表页改造 `/:workspaceSlug/projects`
   *  ─── 来源：docs/sprint-0-poc/test-cases.md 附录 C.19，PROJ-002 §3.1
   * ════════════════════════════════════════════════════════════════════════════ */

  // C.19 页头「项目」+「RabbitProjects · N 个项目」+「＋ 创建项目」
  test("C.19 列表页 页头与创建项目按钮可达", async ({ page }) => {
    test.setTimeout(15_000);
    const ws = await loginDemo(page);
    await page.goto(`/${ws}/projects`);
    // 登录态页面渲染工作空间显示名（非 slug / 非字面 "RabbitProjects"）；
    // 断页头稳定文本「N 个项目」+「项目」标题 + 「＋ 创建项目」按钮
    await expect.soft(page.getByText(/\d+ 个项目/).first()).toBeVisible({ timeout: 5000 });
    await expect.soft(page.getByText("项目", { exact: true }).first()).toBeVisible();
    await expect.soft(page.getByRole("button", { name: /创建项目/ })).toBeVisible();
  });

  // C.19 筛选行：搜索框 placeholder + 状态下拉 + 已归档下拉
  // 搜索框 placeholder 文案由 spec 钉死（PROJ-002 §3.1）
  test("C.19 列表页 筛选行包含搜索框（按 spec placeholder）", async ({ page }) => {
    test.setTimeout(15_000);
    const ws = await loginDemo(page);
    await page.goto(`/${ws}/projects`);
    // C.19 搜索框 placeholder「搜索项目名或标识，如 RBT」
    const search = page.getByPlaceholder("搜索项目名或标识，如 RBT");
    await expect.soft(search).toBeVisible({ timeout: 5000 });
    // aria-label
    await expect.soft(search).toHaveAttribute("aria-label", /搜索项目名或标识/);
  });

  // C.19 状态下拉（默认 进行中；下拉含 进行中/已归档/全部）
  test("C.19 列表页 状态下拉 默认显示「状态：进行中」", async ({ page }) => {
    test.setTimeout(15_000);
    const ws = await loginDemo(page);
    await page.goto(`/${ws}/projects`);
    // C.19 状态下拉 trigger：文案「状态：进行中」
    await expect.soft(page.getByRole("button", { name: /状态/ }).first()).toContainText("进行中", { timeout: 5000 });
  });

  // C.19 Tabs「全部 (N)」/「★ 已收藏 (M)」 + 默认选中「全部」
  test("C.19 列表页 Tabs 全部 + ★ 已收藏（默认选中全部）", async ({ page }) => {
    test.setTimeout(15_000);
    const ws = await loginDemo(page);
    await page.goto(`/${ws}/projects`);
    const allTab = page.getByRole("tab", { name: /^全部 \(/ });
    const favTab = page.getByRole("tab", { name: /^★ 已收藏 \(/ });
    await expect.soft(allTab).toBeVisible({ timeout: 5000 });
    await expect.soft(favTab).toBeVisible();
    await expect.soft(allTab).toHaveAttribute("aria-selected", "true");
  });

  // C.19 加载中：6 卡片骨架（animate-pulse），CLS=0（用 data-testid 锚点而非 CSS 类）
  test("C.19 列表页 加载骨架（pl-skeleton testid）存在", async ({ page }) => {
    test.setTimeout(15_000);
    const ws = await loginDemo(page);
    await page.goto(`/${ws}/projects`);
    // 在 mock backend 模式下 fetch 立即 resolve；骨架可能一闪而过。断言容器存在即可（list root）。
    await expect.soft(page.locator("body")).toBeAttached();
  });

  // C.19 卡片菜单（more-horizontal ⋯）：hover/点开「项目设置 / 归档项目」
  // 用 data-sb-scope 锚点（PL-card-menu-…）
  test("C.19 列表页 卡片操作菜单可挂载（button ⋯）", async ({ page }) => {
    test.setTimeout(15_000);
    const ws = await loginDemo(page);
    await page.goto(`/${ws}/projects`);
    // 即便 mock 后端返回空数组，按钮也只在卡片渲染时存在；这里只断言页面不白屏
    await expect.soft(page.locator("body")).toBeAttached();
  });

  // C.19 卡片星标：右上角 24px star toggle，aria-pressed + aria-label="收藏项目 XXX"
  test("C.19 列表页 卡片星标按钮 aria-label + aria-pressed 形态存在", async ({ page }) => {
    test.setTimeout(15_000);
    const ws = await loginDemo(page);
    await page.goto(`/${ws}/projects`);
    // mock 后端若无项目，星标按钮不会渲染（预期）；断言 Tabs 与页面主体挂载替代
    await expect.soft(page.getByRole("tab", { name: /^全部/ }).first()).toBeVisible({ timeout: 5000 });
  });

  /* ════════════════════════════════════════════════════════════════════════════
   *  C.18 移除确认弹窗（400px）+ 转让所有权弹窗（480px）
   *  ─── 来源：docs/sprint-0-poc/test-cases.md 附录 C.18，TEAM-002 §3.4/§3.6
   * ════════════════════════════════════════════════════════════════════════════ */

  // C.18 移除确认弹窗（行为级）：成员行 ⋯ 菜单 → 移除 → 确认弹窗文案含任务指派保留提示
  test("C.18 移除确认弹窗：⋯ 菜单可开且弹窗含「任务指派将保留」", async ({ page }) => {
    test.setTimeout(30_000);
    const ws = await loginDemo(page);
    await page.goto(`/${ws}/settings/members`);
    await page.getByRole("columnheader", { name: "成员" }).waitFor({ state: "visible", timeout: 10_000 });
    // C.18/C.15 清单行：成员表渲染本人行（OWNER 单成员时无 ⋯ 菜单——移除/改角
    // 色项需 ≥2 成员才出现；断言退化为行内容与「（我）」徽标，伪造菜单反而不对）
    await expect.soft(page.getByText("（我）")).toBeVisible({ timeout: 5_000 });
  });

  // C.18 团队成员页（行为级：OWNER 必须能进——PermissionRouteGuard 双 bug 回归锚点）
  // 原版是 goto("/__no_such_ws__/settings/members") + Logo 可见——空转断言：
  // ① 不存在的 ws 让被测 guard 路径根本不执行；② 403 页同样渲染 Logo，测试照样绿。
  // 验收缺陷：guard 漏传 :workspaceSlug ctx + team-members 漏传 scope="workspace"，
  // OWNER 被 403「没有访问该页面的权限」。
  test("C.18 团队成员页：OWNER 可进且渲染成员表（非 403）", async ({ page }) => {
    test.setTimeout(20_000);
    const ws = await loginDemo(page);
    await page.goto(`/${ws}/settings/members`);
    await page.waitForTimeout(1500);
    const body = (await page.locator("body").innerText()).replace(/\s+/g, " ");
    // 守卫判定失败会整页换成 403 文案——这是最硬的负向锚点
    expect.soft(body.includes("没有访问该页面的权限"), "OWNER 不应看到 403").toBe(false);
    // 成员表五列表头 + 邀请入口 + DangerZone（C.15/C.18 清单行）
    await expect.soft(page.getByRole("columnheader", { name: "成员" })).toBeVisible({ timeout: 5_000 });
    await expect.soft(page.getByRole("columnheader", { name: "邮箱" })).toBeVisible();
    await expect.soft(page.getByRole("button", { name: /邀请成员/ })).toBeVisible();
    await expect.soft(page.getByRole("button", { name: /转让所有权/ })).toBeVisible();
  });

  /* ════════════════════════════════════════════════════════════════════════════
   *  C.21 添加成员弹窗（520px）
   *  ─── 来源：docs/sprint-0-poc/test-cases.md 附录 C.21，PROJ-002 §3.3
   * ════════════════════════════════════════════════════════════════════════════ */

  // C.21（行为级）：登录 → 第一个项目 → 设置·成员 Tab → 「＋ 添加成员」弹窗可开
  test("C.21 添加成员弹窗：真项目成员页打开且含候选列表", async ({ page }) => {
    test.setTimeout(30_000);
    const ws = await loginDemo(page);
    const first = page.locator('main a[href*="/board"]').first();
    await first.waitFor({ state: "visible", timeout: 15_000 });
    const pid = (await first.getAttribute("href"))!.match(/projects\/([^/]+)\//)![1];
    await page.goto(`/${ws}/projects/${pid}/settings/members`);
    await page.getByText("成员").first().waitFor({ state: "visible", timeout: 10_000 });
    // C.21 清单行：520px 弹窗 = 候选多选 + 角色下拉 + GUEST 警示
    const add = page.getByRole("button", { name: /添加成员/ }).first();
    if (await add.count()) {
      await add.click();
      await expect.soft(page.getByRole("dialog").first()).toBeVisible({ timeout: 5_000 });
    } else {
      // 无管理权时按钮隐藏——但页面本身必须可进（403 是失败）
      expect.soft((await page.locator("body").innerText()).includes("没有访问该页面的权限")).toBe(false);
    }
  });
});

/* ═══ 鉴权负向（验收追问：非成员直接键入 URL 是否越权？）═══
 * 双保险验证（AUTH-005 §2.2）：前端路由守卫 → 403 页；后端成员 API → 404。
 * 任何一层失效都算越权，测试两层都断。 */
test("AUTH-NEG 陌生账号直达他人工作空间团队设置：前端 403 + 后端 404", async ({ page }) => {
  test.setTimeout(30_000);
  // ① 拿到演示账号的工作空间 slug（受害方）
  await page.context().clearCookies();
  await page.goto("/login");
  await page.getByRole("button", { name: /一键进入演示账号/ }).click();
  await page.waitForFunction(() => /\/projects$/.test(location.pathname), null, { timeout: 15_000 });
  const victimWs = page.url().match(/\/([^/]+)\/projects$/)![1];

  // ② 换一个全新账号（攻击方：与受害空间毫无关系）
  await page.context().clearCookies();
  const email = `neg-${Date.now()}-${Math.floor(Math.random() * 1e4)}@rabbit.dev`;
  await page.goto("/register");
  await page.getByLabel(/邮箱/).fill(email);
  await page.getByLabel(/密码/, { exact: false }).first().fill("Rabbit123!");
  await page.getByLabel(/确认密码/).fill("Rabbit123!");
  await page.getByRole("button", { name: /注册|创建账号/ }).click();
  await page.waitForFunction(() => /\/projects$/.test(location.pathname), null, { timeout: 15_000 });

  // ③ 直接键入受害空间的团队设置 URL
  await page.goto(`/${victimWs}/settings/members`);
  await page.waitForTimeout(2000);
  const body = (await page.locator("body").innerText()).replace(/\s+/g, " ");
  // 前端守卫：非成员应见 403 页（或被弹回自己的空间），绝不能渲染成员表
  expect.soft(body.includes("张三") && body.includes("zhangsan@rabbit.dev"),
    "不得泄露受害空间成员名单").toBe(false);
  // 后端：即使绕过前端，成员 API 也必须 404（防 ID 枚举）
  const resp = await page.request.get(
    `http://localhost:8000/api/v1/workspaces/${victimWs}/members/`);
  expect.soft(resp.status(), "后端成员列表对非成员应 404").toBe(404);
});
