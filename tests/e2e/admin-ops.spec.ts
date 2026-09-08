/**
 * admin 运维台 parity（C.134~C.136——Sprint-6 T6-05）。
 *
 * 用户入口纪律：经 API 登录 SystemAdmin 演示账号拿会话 → goto /ops*（真实
 * 路由，非深链假资源）。admin dev server 须在 3002（API 8000 经 vite proxy
 * ——见 apps/admin/vite.config.ts 的同源约定；缺 proxy 时以 API_BASE 直连）。
 */
import { test, expect } from "@playwright/test";
import { attachGuards, HTTP } from "./no-console-errors";

const ADMIN = "http://localhost:3002/god-mode";

test.describe("admin 运维台（C.134~C.136）", () => {
  const OPS_EMAIL = "ops-parity@rabbit.dev";

  test.beforeAll(async ({ playwright }) => {
    // 专用 SystemAdmin 账号：注册（幂等——已存在则 400 忽略）+ psql 授予
    //（_cleanup_s* 脚本同款 docker exec 先例；不入库数据仅 dev 栈）
    const ctx = await playwright.request.newContext({ baseURL: "http://localhost:8000" });
    const csrf = (await (await ctx.get("/api/v1/auth/csrf-token/")).json());
    await ctx.post("/api/v1/auth/sign-up/", {
      headers: { "X-CSRFToken": csrf.data.csrf_token, "Content-Type": "application/json" },
      data: { email: OPS_EMAIL, password: "Rabbit123!", display_name: "运维台" },
    });
    await ctx.dispose();
    const { execSync } = await import("node:child_process");
    execSync(
      `docker exec rp-pg psql -U rp -d rabbit_projects -tAc `
      + `"INSERT INTO system_admins(id, user_id, is_active, allowed_ip_cidrs, created_at, updated_at) `
      + `SELECT gen_random_uuid(), id, true, '[]'::jsonb, NOW(), NOW() FROM users `
      + `WHERE email='${OPS_EMAIL}' AND NOT EXISTS `
      + `(SELECT 1 FROM system_admins sa JOIN users u2 ON u2.id=sa.user_id `
      + `WHERE u2.email='${OPS_EMAIL}');"`);
  });

  let getErrs: ReturnType<typeof attachGuards> | undefined;
  test.beforeEach(async ({ page }) => {
    getErrs = attachGuards(page);
    const csrf = await page.request.get("http://localhost:8000/api/v1/auth/csrf-token/");
    const token = (await csrf.json()).data.csrf_token as string;
    const r = await page.request.post("http://localhost:8000/api/v1/auth/sign-in/", {
      headers: { "X-CSRFToken": token, "Content-Type": "application/json" },
      data: { email: OPS_EMAIL, password: "Rabbit123!" },
    });
    test.skip(r.status() !== 200, "运维台账号登录失败（dev 栈未起）");
  });

  test.afterEach(async () => {
    // 原实现裸调用弃引用（守卫空转）——Sprint-7 教训补口后与其他 spec 同口径
    expect(getErrs?.report() ?? [], "console/net errors").toEqual([]);
  });

  test("C.135 备份管理页：列表/立即备份/文案", async ({ page }) => {
    await page.goto(`${ADMIN}/ops/backups`);
    await expect(page.getByRole("heading", { name: "备份管理" })).toBeVisible();
    const table = page.locator('[data-sb-scope="backups-table"]');
    await expect(table).toBeVisible();
    await expect(page.locator('[data-sb-scope="backup-trigger"]')).toHaveText(/立即备份/);
    // 来源标注（C.135 表尾）：每日 03:07 文案在
    await expect(page.getByText("每日 03:07 全量（beat）")).toBeVisible();
  });

  test("C.134 限流监控页：旗标与配额快照", async ({ page }) => {
    await page.goto(`${ADMIN}/ops`);
    await expect(page.getByRole("heading", { name: "限流监控" })).toBeVisible();
    await expect(page.locator('[data-sb-scope="rl-flags"]')).toContainText(/正常|降级中/);
    await expect(page.locator('[data-sb-scope="rl-table"]')).toContainText("60/min");
  });

  test("C.136 发布门禁页：四门禁/checklist 8 项/裁决守卫", async ({ page }) => {
    // 被测行为本身：裁决守卫负向提交 → 400（门禁未全过/参数非法——旧行为曾 500，
    // 修复后契约 400；原空调守卫使其隐形，Sprint-7 补口后显式登记）
    getErrs?.allow({ method: "POST", url: "/release-gates/", status: HTTP.BAD_REQUEST });
    await page.goto(`${ADMIN}/ops/release`);
    await expect(page.getByRole("heading", { name: "发布门禁" })).toBeVisible();
    // 无既有发布尝试 → 创建卡（C.136 首行）
    const createBtn = page.locator('[data-sb-scope="release-create-btn"]');
    if (await createBtn.isVisible()) {
      // 水合竞态防护：等待真实 POST 响应（CLAUDE.md 行为断言三件套）
      const [resp] = await Promise.all([
        page.waitForResponse((r) => r.url().includes("/instances/release-gates/create/")),
        createBtn.click(),
      ]);
      expect(resp.status()).toBe(201);
      await expect(page.locator('[data-sb-scope="release-gates"]')).toBeVisible();
    }
    await expect(page.locator('[data-sb-scope="release-checklist"]')).toContainText("preflight");
    // 门禁未全绿 → 裁决放行必被 BLOCKED_BY_GATE 拦（C.136 裁级行）
    await page.locator('[data-sb-scope="release-verdict-actions"] button').first().click();
    await expect(page.locator('[data-sb-scope="release-msg"]')).toContainText(/不可裁决|门禁/);
  });
});
