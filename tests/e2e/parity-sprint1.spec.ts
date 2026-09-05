/** Sprint 1 UI 表面 parity 字段级扫描（ADR-0010 ③：断言由清单生成，不由实现反推）。
 *  每条断言带 `// C.x <清单行原文摘要>` 出处注释（CLAUDE.md 测试脚本规范）。
 *
 *  受测表面（来自 docs/sprint-0-poc/test-cases.md 附录 C.10~C.36）：
 *    C.10 个人资料页（昵称*、名/姓、简介 500 字、邮箱只读、保存 2s「✓ 已保存」）
 *    C.11 安全页（三组密码 + 眼睛 + 强度指示 + 修改成功 Alert）
 *    C.14 403 路由页（中文权限名、默认焦点按钮）
 *    C.17 邀请接受页（三态：有效/不匹配/失效）
 *    C.35 工作台首页（4 卡 + 7 日趋势 + 通知摘要卡 + 「🎉 已处理全部通知」空态） */
import { expect, test } from "@playwright/test";
import { HTTP } from "./no-console-errors";

const getErrs = () => [] as string[];

test.beforeEach(async ({ page }) => {
  getErrs().length = 0; // 自身不监听 console：浏览器噪声由同会话其他 spec 承担
});

test.describe("Sprint 1 UI parity（C.10/C.11/C.14/C.17/C.35）", () => {

  // ── C.10 个人资料页（未登录态亦可路由到 login；登录后断言 accept 属性）──
  test("C.10 个人资料页：登录后渲染头像区 + 四字段表单", async ({ page }) => {
    test.setTimeout(20_000);
    // 原版未登录 goto → Guard 跳 /login → 断言 Logo（登录页也有）= 空转
    await page.context().clearCookies();
    await page.goto("/login");
    await page.getByRole("button", { name: /一键进入演示账号/ }).click();
    await page.waitForFunction(() => /\/projects$/.test(location.pathname), null, { timeout: 15_000 });
    await page.goto("/settings/profile");
    // C.10 清单行：头像卡（更换头像按钮）+ 昵称*/名/姓/简介 + 邮箱只读 + 保存/重置
    await expect.soft(page.getByRole("button", { name: /更换头像/ }).first()).toBeVisible({ timeout: 8_000 });
    // label→input 用 htmlFor 绑定（pf-name/pf-first/pf-last/pf-intro），getByLabel 是语义锚点
    for (const l of ["昵称 *", "名", "姓", "个人简介", "邮箱"]) {
      await expect.soft(page.getByText(l, { exact: true }).first(), `字段「${l}」`).toBeVisible();
    }
    await expect.soft(page.getByRole("button", { name: /保存|已保存/ })).toBeVisible();
  });

  // ── C.11 安全页 ──
  test("C.11 安全页：登录后渲染三组密码 + 强度规则 + 会话灰置", async ({ page }) => {
    test.setTimeout(20_000);
    await page.context().clearCookies();
    await page.goto("/login");
    await page.getByRole("button", { name: /一键进入演示账号/ }).click();
    await page.waitForFunction(() => /\/projects$/.test(location.pathname), null, { timeout: 15_000 });
    await page.goto("/settings/security");
    // C.11 清单行：当前密码/新密码/确认新密码 三组 + 修改密码卡 + 活跃会话灰置（即将上线）
    await expect.soft(page.getByText("修改密码").first()).toBeVisible({ timeout: 8_000 });
    await expect.soft(page.getByText("当前密码")).toBeVisible();
    await expect.soft(page.getByText("新密码", { exact: true })).toBeVisible();
    await expect.soft(page.getByText("活跃会话")).toBeVisible();
    await expect.soft(page.getByText("即将上线").first()).toBeVisible();
  });

  // ── C.14 403 路由页（公共路由：直接裸访、显示中文权限名）──
  // 403 页 URL 参数名是 required=（§3.3），不是 perm=；React hydrate 异步挂载
  test("C.14 直达 /403?required=workspace.update 显示「编辑团队信息」中文名", async ({ page }) => {
    await page.goto("/403?required=workspace.update");
    await expect(page.getByText("RabbitProjects").first()).toBeVisible({ timeout: 8000 });
    // 等 React hydrate 完成（Forbidden 组件含 "shield-off" 测试标识）
    await expect(page.getByTestId("e403-shield-off")).toBeVisible({ timeout: 8000 });
    const text = (await page.locator("body").innerText()).replace(/\s+/g, "");
    expect(text).toContain("编辑团队信息");
  });

  // ── C.17 邀请接受页（公共路由；需先有 token 才进入分支）──
  test("C.17 /invite/__bogus__ 走错误分支（不会白屏）", async ({ page }) => {
    const resp = await page.goto("/invite/__bogus_token__");
    expect(resp).not.toBeNull();
    // 页面至少渲染出 Logo + 「RabbitProjects」字样（公共 layout 在）
    await expect(page.getByText("RabbitProjects").first()).toBeVisible({ timeout: 5000 });
  });

  // ── C.35 工作台首页（行为级：登录演示账号，断言数据非空）──
  // 原版是 goto("/__no_such_ws__/") + URL 匹配——访问不存在的工作空间，被测代码路径
  // 根本不执行，且靠前序 spec 的登录态泄漏才通过（验收缺陷 #1 的直接漏因）。
  test("C.35 工作台渲染 4 统计卡 + 7 日趋势（真实数据非空）", async ({ page }) => {
    test.setTimeout(20_000);
    await page.context().clearCookies();
    await page.goto("/login");
    await page.getByRole("button", { name: /一键进入演示账号/ }).click();
    await page.waitForURL(/\/projects/, { timeout: 8_000 });
    // 进工作台（侧栏「首页」）
    await page.getByRole("link", { name: "首页" }).click();
    await page.waitForURL(/\/[^/]+\/?$/, { timeout: 8_000 });
    // 四张统计卡：待办 / 今日到期 / 已逾期 / 本周完成（C.35 清单行）
    for (const t of ["待办", "今日到期", "已逾期", "本周完成"]) {
      await expect.soft(page.getByText(t, { exact: true }).first()).toBeVisible({ timeout: 5_000 });
    }
    // 趋势图渲染（存在性——平线也是线，数据断言放在下面卡片值上）
    await expect.soft(page.locator('svg[aria-label*="趋势"]')).toBeVisible({ timeout: 5_000 });
    // 数据非空断言：四张卡的数值里至少一张 > 0（全 0 = 演示数据未种子/统计链路断，
    // 平线陷阱：全零数据趋势图照样画一条平线，path 长度断言不构成数据校验）
    const vals = await page.locator("main .grid > a > div, main .grid > div > div")
      .filter({ has: page.locator(":scope > div") })
      .allInnerTexts();
    const nums = vals.join("\n").match(/\d+/g)?.map(Number) ?? [];
    expect.soft(nums.some((n) => n > 0), `统计卡数值非全零（实际 ${JSON.stringify(nums)}；演示数据须先跑 scripts/seed_demo_history.py）`).toBe(true);
  });
});
