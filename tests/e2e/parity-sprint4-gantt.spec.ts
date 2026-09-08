/** Sprint-4 甘特视图（GANTT-001 / GANTT-002 · C.98~C.111）parity + 行为三件套。
 *  断言由附录 C.98~C.111 清单行生成（ADR-0010 ③：不由实现反推），每条带 `// C.x` 出处注释。
 *
 *  覆盖（owner = GANTT-001 §3.1~§3.6 / GANTT-002 §3.1~§3.4）：
 *    C.98 页框架与甘特控制条 ｜ C.99 左栏行树 ｜ C.100 表头/底纹/今日线 ｜
 *    C.101 任务条七态 ｜ C.102 连线四型+冲突红点 ｜ C.103 未排期折叠区 ｜
 *    C.104 空态/骨架/预取失败 ｜ C.105 键盘导航与 aria ｜ C.106 三手势拖拽（含
 *    VIEWER 只读）｜ C.107 冲突确认弹层 ｜ C.108 未排期入轨 ｜ C.109 延期概览 ｜
 *    C.110 PNG 导出 ｜ C.111 键盘改期
 *
 *  纪律（CLAUDE.md）：登录走 UI（用户入口铁律：侧栏「甘特」点击进入）；每 test 先
 *  clearCookies；行为断言三件套（①交互 → ②waitForResponse 对应请求 2xx → ③UI 回读/
 *  刷新还原）；鉴权负向成对（VIEWER 入口禁用 + 直连 403）；console guard 全量；
 *  API_TRUTH import（禁止硬编码状态码/错误码）。
 */
import { test, expect, type Page, type Response } from "@playwright/test";
import { execSync } from "node:child_process";
import { attachGuards, CODES, HTTP } from "./no-console-errors";

// 造数自动清理（afterAll 幂等；S4_E2E_NO_CLEANUP=1 可跳过以保留现场调试）
test.afterAll(() => {
  if (process.env.S4_E2E_NO_CLEANUP) return;
  try {
    execSync("uv run --project apps/api python tests/e2e/_cleanup_s4.py", { stdio: "pipe", timeout: 180_000 });
  } catch (e) {
    console.warn("[cleanup] S4* 残留清理失败（不阻断报告；gate 以零残留为准）", e);
  }
});


const WS = "workspace";
const API_ORIGIN = process.env.E2E_BASE_URL ?? "http://localhost:3001";

const rid = (n = 5) =>
  Array.from({ length: n }, () => String.fromCharCode(65 + Math.floor(Math.random() * 26))).join("");

/** 本地时区日期偏移（甘特 BR-05 默认 Asia/Shanghai = 本地 dev 时区）。 */
function dISO(offsetDays: number): string {
  const d = new Date(Date.now() + offsetDays * 86_400_000);
  return d.toLocaleDateString("sv-SE");
}

async function loginDemo(page: Page) {
  await page.context().clearCookies();
  await page.goto("/login");
  await page.getByRole("button", { name: /一键进入演示账号/ }).click();
  await page.waitForFunction(() => /\/projects$/.test(location.pathname), null, { timeout: 15_000 });
}

/** 与浏览器 context 共享 cookie 的 API 调用（信封不解包，断言直读）。 */
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

async function createProject(page: Page, prefix: string): Promise<{ slug: string; id: string }> {
  const btn = page.getByRole("button", { name: /创建项目/ }).first();
  await btn.waitFor({ state: "visible", timeout: 20_000 });
  await btn.click();
  await page.getByLabel("项目名称 *").fill(`${prefix} ${Date.now() % 1000000}`);
  await page.getByLabel("项目标识符 *").fill(rid());
  await page.getByRole("button", { name: "创建项目", exact: true }).click();
  await page.waitForURL(/\/projects\/.+\/board/, { timeout: 15_000 });
  const m = page.url().match(/\/([^/]+)\/projects\/([^/]+)\//);
  return { slug: m?.[1] ?? WS, id: m?.[2] ?? "" };
}

interface Seeded {
  slug: string; pid: string;
  parent: { id: string; key: string };
  child: { id: string; key: string };
  completed: { id: string; key: string };
  cancelled: { id: string; key: string };
  started: { id: string; key: string };
  overdue: { id: string; key: string };
  overdue2: { id: string; key: string };
  openStart: { id: string; key: string };
  openEnd: { id: string; key: string };
  blocker: { id: string; key: string };
  blocked: { id: string; key: string };
  relates: { id: string; key: string };
  dup: { id: string; key: string };
  conflictA: { id: string; key: string };
  conflictB: { id: string; key: string };
}

/** 甘特域全形状种子：七态条 + 四型连线（含冲突）+ 未排期 ×2 + 逾期 ×2（概览口径）。 */
async function seedGantt(page: Page, prefix: string): Promise<Seeded> {
  const proj = await createProject(page, prefix);
  const { body: meBody } = await apiCall(page, "GET", "/api/v1/users/me/");
  const meId = meBody?.data?.user?.id as string;
  const { body: stBody } = await apiCall(page, "GET", `/api/v1/workspaces/${WS}/projects/${proj.id}/states/?include_cancelled=1`);
  const states = (stBody?.data ?? []) as Array<{ id: string; group: string }>;
  const stOf = (g: string) => states.find((s) => s.group === g)?.id;
  const mk = async (name: string, extra: Record<string, unknown> = {}) => {
    const { status, body } = await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/`, { name, ...extra });
    expect(status, `创建 ${name}`).toBe(HTTP.CREATED);
    return { id: body?.data?.id as string, key: body?.data?.issue_key as string };
  };
  const rel = async (fromId: string, toId: string, relation_type: string) => {
    const { status } = await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${fromId}/relations/`, { related_issue_id: toId, relation_type });
    expect(status, `连线 ${relation_type}`).toBe(HTTP.CREATED);
  };

  // 树：父（聚合条——双 NULL）+ 子（完成划线）
  const parent = await mk("导出 PDF（里程碑）");
  const child = await mk("后端导出 API", { parent_id: parent.id, state_id: stOf("completed") });
  await apiCall(page, "PATCH", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${child.id}/`, { start_date: dISO(-5), target_date: dISO(-1) });
  const completed = child; // 同一条（完成态 + 划线样式）
  const cancelled = await mk("遗留方案", { state_id: stOf("cancelled"), target_date: dISO(2) });
  await apiCall(page, "PATCH", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${cancelled.id}/`, { start_date: dISO(-1) });
  const started = await mk("登录会话延长", { state_id: stOf("started"), target_date: dISO(6) });
  await apiCall(page, "PATCH", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${started.id}/`, { start_date: dISO(-2) });
  const overdue = await mk("修复 504 超时", { state_id: stOf("started"), target_date: dISO(-3), assignee_ids: [meId] });
  await apiCall(page, "PATCH", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${overdue.id}/`, { start_date: dISO(-8) });
  const overdue2 = await mk("环境变量文档", { target_date: dISO(-1), assignee_ids: [meId] }); // 开放端条逾期（start 空）——C.109 同源口径
  const openEnd = await mk("水印设计", { target_date: undefined, start_date: dISO(1) });
  await apiCall(page, "PATCH", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${openEnd.id}/`, { target_date: null, start_date: dISO(1) });
  const openStart = await mk("旧归档清理", { target_date: dISO(12) }); // start 空 → 左端开放
  // 连线四型：blocks（无冲突）/ blocks（冲突红点）/ relates_to / duplicates
  const blocker = await mk("连读 A", { target_date: dISO(9) });
  await apiCall(page, "PATCH", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${blocker.id}/`, { start_date: dISO(5) });
  const blocked = await mk("连读 B", { target_date: dISO(14) });
  await apiCall(page, "PATCH", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${blocked.id}/`, { start_date: dISO(11) });
  await rel(blocker.id, blocked.id, "blocks");
  const conflictA = await mk("冲读 A", { target_date: dISO(4) });
  await apiCall(page, "PATCH", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${conflictA.id}/`, { start_date: dISO(-2) });
  const conflictB = await mk("冲读 B", { target_date: dISO(9) });
  await apiCall(page, "PATCH", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${conflictB.id}/`, { start_date: dISO(-4) }); // B 起 -4d < A 终 4d → violation
  await rel(conflictA.id, conflictB.id, "blocks");
  const relates = await mk("相关读", { target_date: dISO(6) });
  await apiCall(page, "PATCH", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${relates.id}/`, { start_date: dISO(2) });
  await rel(started.id, relates.id, "relates_to");
  const dup = await mk("重复读", { target_date: dISO(8) });
  await apiCall(page, "PATCH", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${dup.id}/`, { start_date: dISO(3) });
  await rel(dup.id, overdue.id, "duplicates");
  // 未排期 ×2（双 NULL）
  await mk("图标规范整理");
  await mk("移动端适配清单");
  return { slug: proj.slug, pid: proj.id, parent, child, completed, cancelled, started, overdue, overdue2, openStart, openEnd, blocker, blocked, relates, dup, conflictA, conflictB };
}

/** 用户入口：侧栏「甘特」点击进入（禁止直链深跳）。 */
async function gotoGantt(page: Page) {
  await page.getByRole("navigation").filter({ hasText: "返回项目列表" }).getByRole("link", { name: "甘特" }).click();
  await page.waitForURL(/\/gantt/, { timeout: 10_000 });
}

/** 等甘特行渲染完成（首屏 rows 到位）。 */
async function waitRows(page: Page, minCount = 1) {
  await expect(page.locator('[data-sb-scope="gantt-body"]')).toBeVisible({ timeout: 15_000 });
  await expect(page.locator('[data-sb-scope="gantt-body"] [data-bar]').first()).toBeVisible({ timeout: 15_000 });
  await page.waitForFunction(
    (n) => document.querySelectorAll('[data-sb-scope="gantt-body"] [data-bar]').length >= n,
    minCount, { timeout: 15_000 },
  );
}

/** 甘特条像素左缘（style.left）。 */
async function barLeft(page: Page, issueId: string): Promise<number> {
  return page.locator(`[data-bar="${issueId}"]`).evaluate((el) => Number.parseFloat((el as HTMLElement).style.left));
}

/** 三手势拖拽（阈值 4px 激活——move 先小步跨阈值再到位）。 */
async function dragBarBy(page: Page, issueId: string, dxPx: number) {
  const box = await page.locator(`[data-bar="${issueId}"]`).boundingBox();
  expect(box, "拖拽目标条可见").toBeTruthy();
  const x0 = box!.x + Math.min(box!.width / 2, 30);
  const y = box!.y + box!.height / 2;
  await page.mouse.move(x0, y);
  await page.mouse.down();
  await page.mouse.move(x0 + 10, y, { steps: 2 }); // 过 4px 阈值 → 拖拽会话激活
  await page.mouse.move(x0 + dxPx, y, { steps: 6 });
}

test.describe("Sprint-4 甘特视图（GANTT-001/002 · C.98~C.111）", () => {
  let getErrs: ReturnType<typeof attachGuards> | undefined;
  test.beforeEach(async ({ page }) => {
    await page.context().clearCookies();
    getErrs = attachGuards(page);
    // BR-13 限流（overdue-summary）：本 spec 十余用例两分钟内连开甘特即超试错闸——
    // 被设计行为（限流不改数据语义，黄条降级）；其余端点断言不受影响
    getErrs.allow({ method: "GET", url: "/gantt/overdue-summary/", status: HTTP.TOO_MANY });
  });
  test.afterEach(async () => {
    expect.soft(getErrs?.report() ?? [], "console/net errors").toEqual([]);
  });

  /* ═══════════ C.98~C.103 / C.105 / C.109 parity 全表面 ═══════════ */

  test("S4G-1 C.98~C.103/C.105/C.109 页框架/行树/表头/条七态/连线四型/未排期/概览 parity", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const s = await seedGantt(page, "S4G");
    await gotoGantt(page);
    await waitRows(page, 10);

    // ── C.98 工具条分层：视图条 → 甘特控制条 → 延期概览条 → 图表区（O1 定稿）──
    await expect(page.locator('[data-sb-scope="viewswitch"]')).toBeVisible(); // 视图条（BOARD-003 框架）
    await expect(page.locator('[data-sb-scope="layout-seg-gantt"]')).toHaveAttribute("aria-pressed", "true"); // layout=gantt 高亮
    await expect(page.locator('[data-sb-scope="gantt-ctl"]')).toBeVisible(); // 甘特控制条
    // C.98 粒度三段器 日｜周｜月
    for (const g of ["day", "week", "month"]) {
      await expect(page.locator(`[data-sb-scope="gantt-gran-${g}"]`)).toBeVisible();
    }
    // C.98 今天导航 ←今天→ + ⤢ 缩放
    await expect(page.locator('[data-sb-scope="gantt-today"]')).toBeVisible();
    await expect(page.locator('[data-sb-scope="gantt-pan-left"]')).toBeVisible();
    await expect(page.locator('[data-sb-scope="gantt-pan-right"]')).toBeVisible();
    await expect(page.locator('[data-sb-scope="gantt-zoom"]')).toBeVisible();
    // C.98 快捷键提示行
    await expect(page.locator('[data-sb-scope="gantt-kbd-hint"]')).toContainText("1/2/3");
    // C.98 甘特 ⋯ 菜单：导出 PNG / 全屏 / 重置缩放（O2 勘误）
    await page.locator('[data-sb-scope="gantt-menu-btn"]').click();
    await expect(page.locator('[data-sb-scope="gantt-export"]')).toBeVisible();
    await expect(page.getByRole("menuitem", { name: "⛶ 全屏" })).toBeVisible();
    await expect(page.locator('[data-sb-scope="gantt-zoom-reset"]')).toBeVisible();
    await page.keyboard.press("Escape");
    await page.mouse.click(10, 300); // 收起菜单

    // ── C.99 左栏行树：任务 (N) + role=tree + 编号/标题/caret/缩进 ──
    await expect(page.locator('[data-sb-scope="gantt-left"]')).toBeVisible();
    await expect(page.getByText(new RegExp(`任务 \\(\\d+\\)`))).toBeVisible(); // 任务计数（GANTT-001 §3.1 ASCII）
    await expect(page.locator('[data-sb-scope="gantt-left"]')).toHaveAttribute("role", "tree");
    const grow = page.locator(`[data-grow="${s.child.id}"]`);
    await expect(grow).toBeVisible();
    await expect(grow).toContainText(s.child.key);
    await expect(grow).toContainText("后端导出 API");
    // C.99 行折叠：父行 caret ▾ → 点击收起子行（连线按 BR-07 收拢）
    const parentRow = page.locator(`[data-grow="${s.parent.id}"]`);
    await expect(parentRow.locator(".caret")).toContainText("▾");
    await expect(grow).toBeVisible();
    await parentRow.locator(".caret").click();
    await expect(grow).toBeHidden(); // 子行收起
    await expect(parentRow.locator(".caret")).toContainText("▸");
    await parentRow.locator(".caret").click(); // 展开（还原）
    await expect(grow).toBeVisible();

    // ── C.100 表头/底纹/今日线 ──
    await expect(page.locator('[data-sb-scope="gantt-body"]')).toHaveAttribute("role", "application");
    await expect(page.locator('[data-sb-scope="gantt-body"]')).toHaveAttribute("aria-roledescription", "甘特图");
    const todayLine = page.locator('[data-sb-scope="gantt-today-line"]');
    await expect(todayLine).toBeVisible();
    await expect(todayLine).toContainText("今天"); // 顶部角标
    await expect(page.locator(".rp-g-col.wknd").first()).toBeVisible(); // 周末底纹（6% 灰）
    // 列头日数字（day 粒度每列一天）
    await expect(page.locator(".rp-g-col").filter({ hasText: new RegExp(`^${String(new Date().getDate())}$`) }).first()).toBeVisible();

    // ── C.101 任务条样式矩阵 ──
    await expect(page.locator(`[data-bar="${s.child.id}"]`)).toHaveClass(/st-completed/); // 已完成（绿 15% + 划线）
    await expect(page.locator(`[data-bar="${s.child.id}"] .txt`)).toHaveCSS("text-decoration-line", "line-through");
    await expect(page.locator(`[data-bar="${s.cancelled.id}"]`)).toHaveClass(/st-cancelled/); // 取消（虚线）
    await expect(page.locator(`[data-bar="${s.cancelled.id}"]`)).toHaveCSS("border-top-style", "dashed");
    await expect(page.locator(`[data-bar="${s.started.id}"]`)).toHaveClass(/st-started/); // 进行中（状态色 + 50% 填充）
    await expect(page.locator(`[data-bar="${s.started.id}"] .fill`)).toHaveAttribute("style", /width:\s*50%/); // §1.2 started=50
    await expect(page.locator(`[data-bar="${s.overdue.id}"]`)).toHaveClass(/overdue/); // 逾期红框
    await expect(page.locator(`[data-bar="${s.overdue.id}"] .warn-ico`)).toBeVisible(); // ⚠ 颜色语义冗余
    await expect(page.locator(`[data-bar="${s.openStart.id}"]`)).toHaveClass(/open-start/); // 开放左端
    await expect(page.locator(`[data-bar="${s.openEnd.id}"]`)).toHaveClass(/open-end/); // 开放右端
    await expect(page.locator(`[data-bar="${s.started.id}"]`)).toHaveClass(/cross-today/); // 今日线跨越条左缘高亮
    // 聚合条（父双 NULL 子树有日期，BR-11）
    await expect(page.locator(`[data-bar="${s.parent.id}"]`)).toHaveClass(/agg/);
    // 条内文本：宽 ≥80px 显示编号+标题
    await expect(page.locator(`[data-bar="${s.started.id}"] .txt`)).toContainText(s.started.key);
    // C.101 条悬浮 tooltip：起止/进度/执行人（estimate 空不显示工时位）
    await page.locator(`[data-bar="${s.started.id}"]`).hover();
    await expect(page.locator('[data-sb-scope="gantt-bar-tip"]')).toContainText(/起止/);
    await expect(page.locator('[data-sb-scope="gantt-bar-tip"]')).toContainText("进度 50%");
    await expect(page.locator('[data-sb-scope="gantt-bar-tip"]')).not.toContainText("工时"); // estimate 空 → 不显示该位
    await page.mouse.move(5, 400);

    // ── C.102 连线四型 + 冲突红点 ──
    await expect(page.locator(".rp-glines path.rel-blocks").first()).toBeVisible(); // blocks 实线箭头
    await expect(page.locator(".rp-glines path.rel-relates_to").first()).toBeVisible(); // relates 虚线
    await expect(page.locator(".rp-glines path.rel-duplicates").first()).toBeVisible(); // duplicates 点划线
    await expect(page.locator("[data-cdot]").first()).toBeVisible(); // 排期冲突红点（冲读 B 起 < 冲读 A 终）

    // ── C.103 未排期折叠区 ──
    await expect(page.locator('[data-sb-scope="gantt-unsched-head"]')).toContainText("未排期 (2)");
    await page.locator('[data-sb-scope="gantt-unsched-head"]').click();
    await expect(page.locator('[data-sb-scope="gantt-unsched-list"] [data-unsched]').first()).toBeVisible();

    // ── C.105 行 aria-label 完整摘要（线性可听） ──
    const aria = await page.locator(`[data-bar="${s.child.id}"]`).getAttribute("aria-label");
    expect(aria).toMatch(new RegExp(`${s.child.key} .*，.*至.*，进度 100%，已完成`));

    // ── C.109 延期概览条：三数字 + 按人分布 + role=status + 明细跳转 ──
    const bar = page.locator('[data-sb-scope="gantt-overdue-bar"]');
    await expect(bar).toBeVisible();
    await expect(bar).toHaveAttribute("role", "status");
    await expect(page.locator('[data-sb-scope="gantt-overdue-count"]')).toHaveText("2"); // 逾期 2（含开放端条——同源口径）
    await expect(bar).toContainText("最长逾期 3 天");
    await expect(bar).toContainText(/演示(2)|张三(2)|\S+\(2\)/); // 按执行人分布（两人各 1 时为两条 (1)）
    await page.locator('[data-sb-scope="gantt-overdue-toggle"]').click();
    await expect(page.locator('[data-sb-scope="gantt-overdue-list"]')).toBeVisible();
    await expect(page.locator('[data-sb-scope="gantt-overdue-list"]')).toContainText("逾期 3 天"); // 明细按逾期天数降序
    // 20 截断提示仅 >20 出现（此处 2 条 → 无截断文案）
    await expect(page.locator('[data-sb-scope="gantt-overdue-list"]')).not.toContainText("完整清单见任务列表");
  });

  /* ═══════════ C.104 骨架 / 预取失败黄条 ═══════════ */

  test("S4G-2 C.104 首屏骨架（表头+条形骨架）与预取失败黄条+重试", async ({ page }) => {
    test.setTimeout(90_000);
    // 本 test 故意 route.abort 预取请求——浏览器网络层回声（net::ERR_FAILED）是
    // 被测降级路径的一部分，从 console guard 过滤（对齐 no-console-errors 白名单
    // 对 4xx 回声的处理先例）
    const rawErrs = getErrs;
    getErrs = Object.assign(() => rawErrs?.().filter((m) => !/net::ERR_FAILED|Failed to load resource/i.test(m)) ?? [], {
      report: () => rawErrs?.report().filter((m) => !/net::ERR_FAILED|Failed to load resource/i.test(m)) ?? [],
    });
    await loginDemo(page);
    const s = await seedGantt(page, "S4Gskel");
    // gantt/ 行取数延迟 1.2s → 骨架可见（占位宽度定长防 CLS）
    // 注：handler 内不用 page.waitForTimeout——导航期间 Page 句柄会失效
    await page.route(/\/gantt\/\?/, async (route) => {
      await new Promise((r) => setTimeout(r, 1_200));
      await route.continue();
    });
    await gotoGantt(page);
    await expect(page.locator('[data-sb-scope="gantt-skel"]').first()).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('[data-sb-scope="gantt-skel"]')).toHaveCount(12); // 12 行条形骨架（§3.5）
    await waitRows(page, 1);
    // 预取失败：拦断后续视窗请求 → End 键平移到远端 → 防抖预取失败 → 黄条
    const abort = async (route: import("@playwright/test").Route) => { await route.abort("failed"); };
    await page.route(/\/gantt\/\?/, abort);
    await page.locator('[data-sb-scope="gantt-body"]').focus();
    await page.keyboard.press("End");
    await expect(page.locator('[data-sb-scope="gantt-prefetch-failed"]')).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('[data-sb-scope="gantt-prefetch-failed"]')).toContainText("实时更新暂停"); // §2.4 文案
    // 重试：解除拦断 → 黄条消失（保持既有渲染不被清空）
    await page.unroute(/\/gantt\/\?/, abort);
    await page.locator('[data-sb-scope="gantt-prefetch-failed"] button').click();
    await expect(page.locator('[data-sb-scope="gantt-prefetch-failed"]')).toBeHidden({ timeout: 10_000 });
  });

  /* ═══════════ C.106 行为三件套：拖拽平移 → PATCH 2xx → UI 回读 ═══════════ */

  test("S4G-3 C.106 拖拽平移 +Δ3d：PATCH 200（仅 start/target 两字段）+ UI 回读 + 刷新保持", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page, "S4Gdrag");
    const { body } = await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/`, { name: "平移目标", target_date: dISO(9) });
    const it = { id: body?.data?.id as string, key: body?.data?.issue_key as string };
    await apiCall(page, "PATCH", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${it.id}/`, { start_date: dISO(4) });
    await gotoGantt(page);
    await waitRows(page, 1);
    const before = await barLeft(page, it.id);

    const patch = page.waitForResponse((r: Response) =>
      new RegExp(`/issues/${it.id}/`).test(r.url()) && r.request().method() === "PATCH");
    await dragBarBy(page, it.id, 3 * 36); // day 粒度 36px/天（原型 O3）→ Δ+3d
    // 拖起态徽标（aria-live 播报 Δ +3d）
    await expect(page.locator('[data-sb-scope="gantt-drag-badge"]')).toHaveText(/Δ \+3d/);
    await page.mouse.up();
    const resp = await patch;
    expect(resp.status()).toBe(HTTP.OK); // ② PATCH 2xx
    const payload = resp.request().postDataJSON() as Record<string, unknown>;
    expect(Object.keys(payload).sort()).toEqual(["start_date", "target_date"]); // C.106 仅写两字段（BR-01）
    expect(payload.start_date).toBe(dISO(7));
    expect(payload.target_date).toBe(dISO(12));
    // ③ UI 回读：条右移 3 天（108px）
    await expect.poll(() => barLeft(page, it.id)).toBe(before + 3 * 36);
    // 刷新保持（E2E-01：就位；刷新后仍在）
    await page.reload();
    await waitRows(page, 1);
    await expect.poll(() => barLeft(page, it.id)).toBe(before + 3 * 36);
  });

  /* ═══════════ C.106 调终点手势 + 钳制 ═══════════ */

  test("S4G-4 C.106 调终点右缘 +4d → PATCH target_date；钳制不发反转请求", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page, "S4Gresize");
    const { body } = await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/`, { name: "调终点目标", target_date: dISO(8) });
    const it = { id: body?.data?.id as string, key: body?.data?.issue_key as string };
    await apiCall(page, "PATCH", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${it.id}/`, { start_date: dISO(5) });
    await gotoGantt(page);
    await waitRows(page, 1);

    // 右缘 8px 热区（ew-resize + 4px 手柄）
    const rzSel = `[data-bar="${it.id}"] .rz.r`;
    await expect(page.locator(rzSel)).toBeAttached();
    const leftBefore = await barLeft(page, it.id);
    const widthBefore = await page.locator(`[data-bar="${it.id}"]`).evaluate((el) => Number.parseFloat((el as HTMLElement).style.width));
    const patch = page.waitForResponse((r: Response) =>
      new RegExp(`/issues/${it.id}/`).test(r.url()) && r.request().method() === "PATCH");
    const box = await page.locator(`[data-bar="${it.id}"]`).boundingBox();
    const y = box!.y + box!.height / 2;
    await page.mouse.move(box!.x + box!.width - 2, y);
    await page.mouse.down();
    await page.mouse.move(box!.x + box!.width + 10, y, { steps: 2 }); // 阈值激活
    await page.mouse.move(box!.x + box!.width + 4 * 36, y, { steps: 5 });
    await expect(page.locator('[data-sb-scope="gantt-drag-badge"]')).toHaveText(/工期 4d → 8d/); // C.106 徽标实时
    await page.mouse.up();
    const resp = await patch;
    expect(resp.status()).toBe(HTTP.OK);
    const payload = resp.request().postDataJSON() as Record<string, unknown>;
    expect(payload.target_date).toBe(dISO(12)); // 终点 +4d
    expect(payload.start_date).toBeUndefined(); // 调终点手势只写 target（差分提交）
    // UI 回读：左缘不动、宽度 +4 天（起止钳制语义的正面口径）
    await expect.poll(async () => page.locator(`[data-bar="${it.id}"]`).evaluate((el) => Number.parseFloat((el as HTMLElement).style.left))).toBe(leftBefore);
    await expect.poll(async () => page.locator(`[data-bar="${it.id}"]`).evaluate((el) => Number.parseFloat((el as HTMLElement).style.width))).toBe(widthBefore + 4 * 36);
  });

  /* ═══════════ C.98/E2E-02 粒度切换：锚点不变 + 不发请求 ═══════════ */

  test("S4G-5 C.98 粒度切换（1/2/3）：中心日期锚定不变、列头正确、纯前端重排零请求", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page, "S4Ggran");
    await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/`, { name: "粒度锚点", target_date: dISO(4) });
    await gotoGantt(page);
    await waitRows(page, 1);

    // 今日线居中比例（T 键落点）；粒度切换后该比例不变 = 锚点保持
    const ratio = () => page.evaluate(() => {
      const body = document.querySelector('[data-sb-scope="gantt-body"]') as HTMLElement;
      const line = document.querySelector('[data-sb-scope="gantt-today-line"]') as HTMLElement;
      return (line.getBoundingClientRect().left - body.getBoundingClientRect().left) / body.clientWidth;
    });
    await page.locator('[data-sb-scope="gantt-body"]').focus();
    await page.keyboard.press("t");
    const r0 = await ratio();
    expect(r0).toBeGreaterThan(0.3);
    expect(r0).toBeLessThan(0.7);

    let ganttGets = 0;
    page.on("request", (r) => { if (/\/gantt\/\?/.test(r.url())) ganttGets += 1; });
    // 取数静默判定：800ms 窗口内计数不再增长（默认视图重定向的二段取数也计入 base）
    for (let i = 0; i < 10; i += 1) {
      const seen = ganttGets;
      await page.waitForTimeout(800);
      if (ganttGets === seen) break;
    }
    const base = ganttGets;
    // 键盘 2 → 周粒度（§3.4：1/2/3 快捷键）
    await page.keyboard.press("2");
    await expect(page.locator('[data-sb-scope="gantt-gran-week"]')).toHaveAttribute("aria-selected", "true");
    await expect(page.locator(".rp-g-col").first()).toContainText(/–/); // 周列头：起–止区间
    const r1 = await ratio();
    expect(Math.abs(r1 - r0)).toBeLessThan(0.08); // 中心日期锚定不变（E2E-02）
    // 键盘 3 → 月粒度（列头 yyyy 年 m 月）
    await page.keyboard.press("3");
    await expect(page.locator(".rp-g-col").first()).toContainText(/年 \d+ 月/);
    await page.keyboard.press("1"); // 回日粒度
    await expect(page.locator('[data-sb-scope="gantt-gran-day"]')).toHaveAttribute("aria-selected", "true");
    await page.waitForTimeout(600); // 防抖窗口过后仍无新请求
    expect(ganttGets, "粒度切换三连不发 gantt/ 请求（数据不变纯前端重排）").toBe(base);
  });

  /* ═══════════ C.111 键盘改期：300ms 合并一次提交 ═══════════ */

  test("S4G-6 C.111 键盘改期 Shift+→×3 → 恰一次 PATCH + 播报 + 回读", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page, "S4Gkbd");
    const { body } = await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/`, { name: "键盘改期目标", target_date: dISO(7) });
    const it = { id: body?.data?.id as string, key: body?.data?.issue_key as string };
    await apiCall(page, "PATCH", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${it.id}/`, { start_date: dISO(3) });
    await gotoGantt(page);
    await waitRows(page, 1);
    const before = await barLeft(page, it.id);

    // 行选择（左栏点击）→ Shift+→ ×3（C.105 ↑↓ 行选择 + C.111 改期）
    await page.locator(`[data-grow="${it.id}"]`).click();
    await page.locator('[data-sb-scope="gantt-body"]').focus();
    const patches: Response[] = [];
    page.on("response", (r) => { if (new RegExp(`/issues/${it.id}/`).test(r.url()) && r.request().method() === "PATCH") patches.push(r); });
    await page.keyboard.press("Shift+ArrowRight");
    await page.keyboard.press("Shift+ArrowRight");
    await expect(page.getByRole("status").filter({ hasText: /向后移动/ }).first()).toBeVisible(); // aria-live 播报（C.111）
    await page.keyboard.press("Shift+ArrowRight");
    await page.waitForTimeout(900); // 300ms 合并窗口 + 网络往返
    expect(patches, "连续键击合并为一次 PATCH（UT-11）").toHaveLength(1);
    expect(patches[0]!.status()).toBe(HTTP.OK);
    expect((patches[0]!.request().postDataJSON() as Record<string, unknown>).start_date).toBe(dISO(6));
    await expect.poll(() => barLeft(page, it.id)).toBe(before + 3 * 36); // UI 回读 +3d
  });

  /* ═══════════ C.107 依赖冲突确认弹层 ═══════════ */

  test("S4G-7 C.107 拖被阻塞方早于前置：红点脉冲 + alertdialog「仍按此排期」保存/取消回位", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page, "S4Gconf");
    const mk = async (name: string, start: number, target: number) => {
      const { body: b } = await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/`, { name, target_date: dISO(target) });
      await apiCall(page, "PATCH", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${b?.data?.id}/`, { start_date: dISO(start) });
      return { id: b?.data?.id as string, key: b?.data?.issue_key as string };
    };
    const a = await mk("前置任务", 2, 6); // blocks 前置：终 6d
    const b = await mk("被阻塞任务", 8, 12); // 起 8d > 6d → 当前无冲突
    const { status } = await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${a.id}/relations/`, { related_issue_id: b.id, relation_type: "blocks" });
    expect(status).toBe(HTTP.CREATED);
    await gotoGantt(page);
    await waitRows(page, 2);
    // 连线层就绪（relations/bulk 返回）——冲突检测依赖 edges
    await expect(page.locator(".rp-glines path.rel-blocks").first()).toBeVisible({ timeout: 10_000 });
    const before = await barLeft(page, b.id);

    // 把 B 拖到早于前置完成日（-5d → 起 3d < 6d）→ 松手出确认弹层
    await dragBarBy(page, b.id, -5 * 36);
    await expect(page.locator('[data-sb-scope="gantt-drag-badge"]')).toContainText("⚠ 依赖冲突"); // 拖动中徽标追加冲突（C.106）
    const dlg = page.locator('[data-sb-scope="gantt-conflict-dialog"]');
    await page.mouse.up();
    await expect(dlg).toBeVisible();
    await expect(dlg).toHaveAttribute("role", "alertdialog"); // C.107 role
    await expect(dlg).toContainText(`新排期使该任务早于其前置 ${a.key} 的完成日`); // C.107 文案
    // 取消 → 无 PATCH + 弹回原位
    let patched = 0;
    page.on("request", (r) => { if (new RegExp(`/issues/${b.id}/`).test(r.url()) && r.method() === "PATCH") patched += 1; });
    await page.locator('[data-sb-scope="gantt-conflict-cancel"]').click();
    await expect(dlg).toBeHidden();
    await expect.poll(() => barLeft(page, b.id)).toBe(before); // 回位（§3.1 取消语义）
    // 再拖一次 → 仍按此排期 → PATCH 200 + 红点持续提示
    const patch = page.waitForResponse((r: Response) => new RegExp(`/issues/${b.id}/`).test(r.url()) && r.request().method() === "PATCH");
    await dragBarBy(page, b.id, -5 * 36);
    await page.mouse.up(); // 松手出弹层（dragBarBy 不含 up）
    await expect(dlg).toBeVisible();
    await page.locator('[data-sb-scope="gantt-conflict-ok"]').click();
    const resp = await patch;
    expect(resp.status()).toBe(HTTP.OK); // BR-06 提示非拦截：确认后放行
    expect(patched).toBe(1);
    await expect(page.locator("[data-cdot]").first()).toBeVisible(); // 连线红点持续提示（UT-08）
    await expect.poll(() => barLeft(page, b.id)).toBe(before - 5 * 36);
  });

  /* ═══════════ C.108 未排期拖入时间轴 ═══════════ */

  test("S4G-8 C.108 未排期拖入：3 天默认工期成条、计数减一、跟随徽标", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page, "S4Gunsched");
    const { body } = await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/`, { name: "待入轨任务" });
    const it = { id: body?.data?.id as string, key: body?.data?.issue_key as string };
    await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/`, { name: "留下未排期" });
    // 时间轴锚点条（dated）——空轴无行可渲染（未排期不入 rows，BR-03）
    await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/`, { name: "已排期锚点", target_date: dISO(5) });
    await gotoGantt(page);
    await waitRows(page, 1);
    await expect(page.locator('[data-sb-scope="gantt-unsched-head"]')).toContainText("未排期 (2)");
    await page.locator('[data-sb-scope="gantt-unsched-head"]').click();
    await expect(page.locator(`[data-unsched="${it.id}"]`)).toBeVisible();

    // 拖入时间轴（今日线右侧 +5 天落点）
    const bodyBox = await page.locator('[data-sb-scope="gantt-body"]').boundingBox();
    const todayBox = await page.locator('[data-sb-scope="gantt-today-line"]').boundingBox();
    const dropX = bodyBox!.x + (todayBox!.x - bodyBox!.x) + 5 * 36;
    const row = page.locator(`[data-unsched="${it.id}"]`);
    const rowBox = await row.boundingBox();
    await page.mouse.move(rowBox!.x + 40, rowBox!.y + rowBox!.height / 2);
    await page.mouse.down();
    await page.mouse.move(dropX + 60, rowBox!.y, { steps: 6 });
    // 跟随徽标：排期至 …（3 天）（原型 O6）
    await expect(page.locator('[data-sb-scope="gantt-drag-badge"]')).toHaveText(/排期至 .+（3 天）/);
    const patch = page.waitForResponse((r: Response) => new RegExp(`/issues/${it.id}/`).test(r.url()) && r.request().method() === "PATCH");
    await page.mouse.move(dropX, rowBox!.y, { steps: 2 });
    await page.mouse.up();
    const resp = await patch;
    expect(resp.status()).toBe(HTTP.OK);
    const payload = resp.request().postDataJSON() as Record<string, string>;
    expect(payload.target_date >= payload.start_date).toBe(true); // 3 天默认工期（BR-07）
    expect(new Date(payload.target_date).getTime() - new Date(payload.start_date).getTime()).toBe(2 * 86_400_000);
    // 计数减一 + 时间轴出现新条（E2E-04）
    await expect(page.locator('[data-sb-scope="gantt-unsched-head"]')).toContainText("未排期 (1)");
    await expect(page.locator(`[data-bar="${it.id}"]`)).toBeVisible();
  });

  /* ═══════════ C.106 VIEWER 只读：鉴权负向成对 ═══════════ */

  test("S4G-9 C.106 VIEWER 只读：甘特可见（正向）+ 拖拽禁用零 PATCH + 直连 403（负向）", async ({ browser }) => {
    test.setTimeout(120_000);
    // console guard 覆盖双页（聚合后 afterEach 统一断言）
    const errGetters: Array<() => string[]> = [];
    getErrs = Object.assign(() => errGetters.flatMap((g) => g()), {
      report: () => errGetters.flatMap((g) => g.report()),
      allow: () => {}, // 多页聚合无单一 allow 通道；需要时在页级 guard 上声明
    });
    const ctxA = await browser.newContext();
    const aPage = await ctxA.newPage();
    errGetters.push(attachGuards(aPage));
    await loginDemo(aPage);
    const proj = await createProject(aPage, "S4Gviewer");
    const { body } = await apiCall(aPage, "POST", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/`, { name: "只读目标", target_date: dISO(6) });
    const it = { id: body?.data?.id as string, key: body?.data?.issue_key as string };
    await apiCall(aPage, "PATCH", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${it.id}/`, { start_date: dISO(2) });

    // B：注册 → 入项目（VIEWER role=5）——S3R setupDual 同款装配
    const bEmail = `s4g-b-${Date.now()}-${Math.floor(Math.random() * 1e4)}@rabbit.dev`;
    const invite = await apiCall(aPage, "POST", `/api/v1/workspaces/${WS}/invitations/`, { emails: [bEmail], role: 10 });
    expect(invite.status).toBe(HTTP.OK);
    const links = ((invite.body?.meta?.invite_links ?? {}) as Record<string, string>);
    const link = (links[bEmail] ?? "").match(/\/invite\/([A-Za-z0-9\-_.]+)/);
    expect(link, "SMTP 降级 invite_links").toBeTruthy();
    const ctxB = await browser.newContext();
    const bPage = await ctxB.newPage();
    errGetters.push(attachGuards(bPage));
    await bPage.goto("/register");
    await bPage.getByLabel(/邮箱/).fill(bEmail);
    await bPage.getByLabel("密码", { exact: false }).first().fill("Rabbit123!");
    await bPage.getByLabel("确认密码").fill("Rabbit123!");
    await bPage.getByRole("button", { name: /注册|创建账号/ }).click();
    await bPage.waitForFunction(() => /\/projects$/.test(location.pathname), null, { timeout: 15_000 });
    const bcookies = await ctxB.cookies();
    const bcsrf = bcookies.find((c) => c.name === "csrftoken")?.value ?? "";
    await bPage.request.post(`${API_ORIGIN}/api/v1/invitations/${link![1]}/accept/`, {
      headers: { "Content-Type": "application/json", ...(bcsrf ? { "X-CSRFToken": bcsrf } : {}) }, data: {},
    });
    const { body: meB } = await apiCall(bPage, "GET", "/api/v1/users/me/");
    const bUserId = meB?.data?.user?.id as string;
    const members = await apiCall(aPage, "GET", `/api/v1/workspaces/${WS}/members/?per_page=100`);
    const vm = ((members.body?.data ?? []) as Array<{ user: { id: string; email: string } }>).find((x) => x.user.email === bEmail);
    expect(vm).toBeTruthy();
    const add = await apiCall(aPage, "POST", `/api/v1/workspaces/${WS}/projects/${proj.id}/members/`, { member_ids: [bUserId], role: 5 }); // VIEWER
    expect([HTTP.OK, HTTP.CREATED]).toContain(add.status);

    // 正向：VIEWER 经项目列表 → 甘特页可见（gantt.read VIEWER+，BR-12）
    await bPage.goto(`/${proj.slug}/projects`);
    const card = bPage.locator(`a[href*="/projects/${proj.id}"]`).first();
    await card.waitFor({ state: "visible", timeout: 15_000 });
    await card.click();
    await bPage.waitForURL(/\/board/, { timeout: 15_000 });
    await bPage.getByRole("navigation").filter({ hasText: "返回项目列表" }).getByRole("link", { name: "甘特" }).click();
    await bPage.waitForURL(/\/gantt/, { timeout: 10_000 });
    await expect(bPage.locator(`[data-bar="${it.id}"]`)).toBeVisible({ timeout: 15_000 });

    // 负向①：前端入口禁用——拖拽尝试零 PATCH + 只读提示（C.106 VIEWER 行）
    let patchCount = 0;
    bPage.on("request", (r) => { if (new RegExp(`/issues/${it.id}/`).test(r.url()) && r.method() === "PATCH") patchCount += 1; });
    await dragBarBy(bPage, it.id, 3 * 36);
    await bPage.mouse.up();
    await expect(bPage.getByText(/访客只读/).first()).toBeVisible({ timeout: 5_000 });
    await bPage.waitForTimeout(400);
    expect(patchCount, "VIEWER 拖拽禁用 → 不发 PATCH").toBe(0);

    // 负向②：绕过前端直连 PATCH → 403 PERM_ROLE_INSUFFICIENT（后端才是安全边界）
    const direct = await apiCall(bPage, "PATCH", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${it.id}/`, { target_date: dISO(9) });
    expect(direct.status).toBe(HTTP.FORBIDDEN);
    expect(direct.body?.error?.code).toBe(CODES.roleInsufficient);
    await ctxA.close();
    await ctxB.close();
  });

  /* ═══════════ C.110 PNG 导出 ═══════════ */

  test("S4G-10 C.110 导出 PNG：⋯ 菜单入口 + 自动下载 + 文件名规范 + ⌘E 同路", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page, "S4Gexport");
    await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/`, { name: "导出视口任务", target_date: dISO(5) });
    await gotoGantt(page);
    await waitRows(page, 1);
    await expect(page.getByText("正在渲染…")).toHaveCount(0);

    const dlPromise = page.waitForEvent("download", { timeout: 15_000 });
    await page.locator('[data-sb-scope="gantt-menu-btn"]').click();
    await page.locator('[data-sb-scope="gantt-export"]').click();
    await expect(page.getByText("正在渲染…")).toBeVisible({ timeout: 5_000 }); // C.110 导出中 Toast
    const dl = await dlPromise;
    expect(dl.suggestedFilename()).toMatch(/^.+-\d{8}-\d{4}\.png$/); // {项目}-{视图名}-{yyyyMMdd-HHmm}.png（§2.6）

    // ⌘E 同路（C.110 入口行）
    const dl2 = page.waitForEvent("download", { timeout: 15_000 });
    await page.locator('[data-sb-scope="gantt-body"]').focus();
    await page.keyboard.press("Meta+e");
    await dl2;
  });

  /* ═══════════ C.105/BR-14 实时：version 门 + 单实体增量补日期 ═══════════ */

  test("S4G-11 BR-14/COLLAB-004 甘特条实时刷新：新鲜事件移动、旧 version 忽略、自身不回显", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page, "S4Glive");
    const { body } = await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/`, { name: "实时目标", target_date: dISO(6) });
    const it = { id: body?.data?.id as string, key: body?.data?.issue_key as string };
    await apiCall(page, "PATCH", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${it.id}/`, { start_date: dISO(2) });
    await gotoGantt(page);
    await waitRows(page, 1);
    const barGeom = () => page.locator(`[data-bar="${it.id}"]`).evaluate((el) => ({
      left: Number.parseFloat((el as HTMLElement).style.left),
      width: Number.parseFloat((el as HTMLElement).style.width),
    }));
    const before = await barGeom();

    // 自身 API 改期（actor=me）：BR-08 不回显——条不动
    const selfPatch = await apiCall(page, "PATCH", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${it.id}/`, { target_date: dISO(16) });
    expect(selfPatch.status).toBe(HTTP.OK);
    expect(selfPatch.body?.data?.updated_at, "PATCH 响应含 updated_at（事件 version 基准）").toBeTruthy();
    await page.waitForTimeout(1_500);
    expect((await barGeom()).width).toBe(before.width);

    // 合成 issue.updated（新鲜 version + 他人 actor）→ 单实体增量拉取 → 终点右移 10 天
    // （起点不动 → 断言宽度增长 10*36px）
    const emit = (version: string) => page.evaluate(({ issueId, ver }) => {
      const bus = (window as unknown as { __rpGanttBus?: { emit: (e: string, env: unknown) => void } }).__rpGanttBus;
      if (!bus) throw new Error("__rpGanttBus 未挂载（dev-only 测试钩子）");
      bus.emit("issue.updated", {
        event: "issue.updated", seq: 9999, room: `project:${issueId}`,
        payload: { issue_id: issueId, actor_id: "00000000-0000-0000-0000-00000000beef", version: ver, brief: "dates" },
        occurred_at: new Date().toISOString(),
      });
    }, { issueId: it.id, ver: version });
    await emit(selfPatch.body?.data?.updated_at as string);
    await expect.poll(async () => (await barGeom()).width, { timeout: 8_000 }).toBe(before.width + 10 * 36);
    expect((await barGeom()).left).toBe(before.left); // 起点未动

    // 旧 version 事件（乱序迟到）→ version 门忽略（ADR-0021 数值化比较）——条不动
    await emit("2020-01-01T00:00:00Z");
    await page.waitForTimeout(1_200);
    expect((await barGeom()).width).toBe(before.width + 10 * 36);
  });
});
