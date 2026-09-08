/** Sprint-3 Phase 3-B 前端（BOARD-004 批量操作）parity + 行为三件套。
 *  断言由附录 C.77~C.83 清单行生成（ADR-0010 ③：不由实现反推），每条带 `// C.x` 出处注释。
 *
 *  覆盖（owner = BOARD-004 §3.1~§3.4）：
 *    C.77 看板卡片多选态（ring + 左上角标 + ⌘/Shift 点选）
 *    C.78 列表/表格行多选态（行首复选框 + 列头全选 + bg 高亮）
 *    C.79 ⌘A 全选 + 截断黄条（>100「已选前 100 / 共 N」）+ Esc 清空
 *    C.80 批量工具条（状态/优先级即选即发 PATCH …/issues/bulk/；请求中锁定；Toast+清空+revalidate）
 *    C.81 指派/标签浮层（模式三选 + 应用数恒显 + 备注）
 *    C.82 失败定位弹层（role=alertdialog + 移除该项并重试）
 *    C.83 归档确认（含子树 N）+ 删除确认（preview 级联统计 + 数量输入激活 confirm_count）
 *
 *  纪律（CLAUDE.md）：登录走 UI（用户入口铁律）；每 test 先 clearCookies；行为断言三件套
 *  （①交互 → ②waitForResponse 对应请求断言载荷与状态 → ③UI 回读）；鉴权负向成对
 *  （VIEWER 前端工具条不可见 + 后端 bulk 写端点 403）；console guard 全量；API_TRUTH import。
 */
import { test, expect, type Page, type Response } from "@playwright/test";
import { attachGuards, HTTP } from "./no-console-errors";

const API_ORIGIN = process.env.E2E_BASE_URL ?? "http://localhost:3001";
/** 当前测试的工作空间 slug（每 test 独立注册用户 → 独立 bulk throttle 桶，BR-06 隔离）。 */
let WS = "";

const rid = (n = 5) =>
  Array.from({ length: n }, () => String.fromCharCode(65 + Math.floor(Math.random() * 26))).join("");

/** 注册全新用户（工作空间 Owner）——真实用户入口（注册 → 工作台 → 创建项目）。 */
async function loginFresh(page: Page) {
  await page.context().clearCookies();
  const email = `s3b-${Date.now() % 1000000}-${Math.floor(Math.random() * 1e4)}@rabbit.dev`;
  await page.goto("/register");
  await page.getByLabel(/邮箱/).fill(email);
  await page.getByLabel("密码", { exact: false }).first().fill("Rabbit123!");
  await page.getByLabel(/确认密码/).fill("Rabbit123!");
  await page.getByRole("button", { name: /注册|创建账号/ }).click();
  await page.waitForFunction(() => /\/projects$/.test(location.pathname), null, { timeout: 15_000 });
  WS = page.url().match(/\/([^/]+)\/projects$/)?.[1] ?? "";
  expect(WS).not.toBe("");
}

async function createProject(page: Page): Promise<{ slug: string; id: string }> {
  const btn = page.getByRole("button", { name: /创建项目/ }).first();
  await btn.waitFor({ state: "visible", timeout: 20_000 });
  await btn.click();
  await page.getByLabel("项目名称 *").fill(`S3B ${Date.now() % 1000000}`);
  await page.getByLabel("项目标识符 *").fill(rid());
  await page.getByRole("button", { name: "创建项目", exact: true }).click();
  await page.waitForURL(/\/projects\/.+\/board/, { timeout: 15_000 });
  const m = page.url().match(/\/([^/]+)\/projects\/([^/]+)\//);
  return { slug: m?.[1] ?? WS, id: m?.[2] ?? "" };
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

interface Node4 { id: string; key: string; name: string }

async function mkIssue(page: Page, pid: string, name: string, extra: Record<string, unknown> = {}): Promise<Node4> {
  const { status, body } = await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${pid}/issues/`, { name, ...extra });
  expect(status, `创建任务 ${name}`).toBe(HTTP.CREATED);
  const d = body?.data ?? {};
  return { id: d.id, key: d.issue_key, name: d.name };
}

/** 用户入口：点侧栏导航到达任务列表（铁律：绿灯路径必须是用户走得通的）。 */
async function gotoList(page: Page) {
  await page.getByRole("navigation").getByRole("link", { name: "任务列表" }).click();
  await page.waitForURL(/\/issues/, { timeout: 10_000 });
}

/** 等工具条浮现并断言计数（C.80）。 */
async function expectBar(page: Page, n: number) {
  const bar = page.locator('[data-sb-scope="bulk-bar"]');
  await expect(bar).toBeVisible({ timeout: 5_000 });
  await expect(bar.locator('[data-sb-scope="bulk-count"]')).toContainText(`已选 ${n} 项`);
  await expect(bar).toHaveAttribute("role", "toolbar");
  await expect(bar).toHaveAttribute("aria-label", `批量操作，已选 ${n} 项`);
}

test.describe("Sprint-3 Phase 3-B 批量操作（BOARD-004 · C.77~C.83）", () => {
  let getErrs: ReturnType<typeof attachGuards> | undefined;
  test.beforeEach(async ({ page }) => {
    await page.context().clearCookies();
    getErrs = attachGuards(page);
  });
  test.afterEach(async () => {
    expect.soft(getErrs?.report() ?? [], "console/net errors").toEqual([]);
  });

  /* ═══════════ C.78 + C.80 列表多选 → 批量优先级（行为三件套） ═══════════ */

  test("S3B-1 C.78/C.80 列表复选框多选 → 优先级即选即发 PATCH bulk → Toast + 选中清空 + 行回读", async ({ page }) => {
    test.setTimeout(90_000);
    await loginFresh(page);
    const proj = await createProject(page);
    const a = await mkIssue(page, proj.id, "S3B1 任务甲", { priority: "low" });
    const b = await mkIssue(page, proj.id, "S3B1 任务乙", { priority: "low" });
    await gotoList(page);
    // C.78 行首复选框（hover 浮现；已选行 bg-brand-50）
    const cbA = page.locator(`tr[data-id="${a.id}"] [data-sb-scope="list-row-cb"]`);
    const cbB = page.locator(`tr[data-id="${b.id}"] [data-sb-scope="list-row-cb"]`);
    await expect(cbA).toBeVisible({ timeout: 15_000 });
    await cbA.click();
    await expect(page.locator(`tr[data-id="${a.id}"]`)).toHaveAttribute("aria-selected", "true");
    await cbB.click();
    await expectBar(page, 2);
    // 行为三件套：优先级 ▾ → 紧急 → PATCH …/issues/bulk/ 200（payload 断言）
    const patch = page.waitForResponse((r: Response) =>
      /\/issues\/bulk\/$/.test(r.url()) && r.request().method() === "PATCH");
    await page.locator('[data-sb-scope="bulk-pri-btn"]').click();
    await page.locator('[data-sb-scope="bulk-pri-item"][data-priority="urgent"]').click();
    const pres = await patch;
    expect(pres.status()).toBe(HTTP.OK);
    const body = pres.request().postDataJSON() as { issue_ids?: string[]; patch?: { priority?: string } };
    expect(body.issue_ids).toEqual(expect.arrayContaining([a.id, b.id]));
    expect(body.patch?.priority).toBe("urgent");
    // 成功 Toast「已更新 2 项」+ 选中清空（工具条滑出，BR-13 解除）
    await expect(page.locator(".fixed.top-4.right-4")).toContainText("已更新 2 项", { timeout: 10_000 });
    await expect(page.locator('[data-sb-scope="bulk-bar"]')).toBeHidden({ timeout: 10_000 });
    // UI 回读：重新拉取两任务优先级为 urgent（刷新后仍在）
    const ra = await apiCall(page, "GET", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${a.id}/`);
    expect(ra.body?.data?.priority).toBe("urgent");
    const rb = await apiCall(page, "GET", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${b.id}/`);
    expect(rb.body?.data?.priority).toBe("urgent");
  });

  /* ═══════════ C.77 看板卡片多选 + ⌘ 点选 ═══════════ */

  test("S3B-2 C.77 看板角标复选框 + ⌘ 点选切换 → 批量状态（PATCH bulk patch.state_id）", async ({ page }) => {
    test.setTimeout(90_000);
    await loginFresh(page);
    const proj = await createProject(page);
    const a = await mkIssue(page, proj.id, "S3B2 看板多选甲");
    await mkIssue(page, proj.id, "S3B2 看板多选乙");
    // 用户入口：默认即看板（createProject 落 /board）
    const cardA = page.locator(`article[data-card-id="${a.id}"]`);
    await expect(cardA).toBeVisible({ timeout: 15_000 });
    // C.77 左上角标复选框（hover/已选显示）→ ring 高亮 + aria-selected
    await cardA.hover();
    await cardA.locator('[data-sb-scope="board-card-cb"]').click();
    await expect(cardA).toHaveAttribute("aria-selected", "true");
    await expectBar(page, 1);
    // ⌘ 点选切换（不打开详情；再点取消）
    const cardB = page.locator('article[data-sb-scope="board-card"]').filter({ hasText: "S3B2 看板多选乙" }).first();
    await cardB.click({ modifiers: [process.platform === "darwin" ? "Meta" : "Control"] });
    await expectBar(page, 2);
    await cardB.click({ modifiers: [process.platform === "darwin" ? "Meta" : "Control"] });
    await expectBar(page, 1);
    // 行为三件套：状态 ▾ → 选项目状态 → PATCH bulk {patch:{state_id}}
    const states = await apiCall(page, "GET", `/api/v1/workspaces/${WS}/projects/${proj.id}/states/?include_cancelled=1`);
    const started = (states.body?.data as Array<{ id: string; name: string; group: string }>).find((s) => s.group === "started");
    expect(started).toBeTruthy();
    const patch = page.waitForResponse((r: Response) =>
      /\/issues\/bulk\/$/.test(r.url()) && r.request().method() === "PATCH");
    await page.locator('[data-sb-scope="bulk-state-btn"]').click();
    await page.locator(`[data-sb-scope="bulk-state-item"][data-state-id="${started!.id}"]`).click();
    const pres = await patch;
    expect(pres.status()).toBe(HTTP.OK);
    expect((pres.request().postDataJSON() as { patch?: { state_id?: string } }).patch?.state_id).toBe(started!.id);
    await expect(page.locator(".fixed.top-4.right-4")).toContainText("已更新 1 项", { timeout: 10_000 });
    // 回读：任务 state_group = started
    const ra = await apiCall(page, "GET", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${a.id}/`);
    expect(ra.body?.data?.state_group).toBe("started");
  });

  /* ═══════════ C.79 ⌘A 截断黄条 + Esc 清空（看板：列组内 100 上限外全量载入） ═══════════ */

  test("S3B-3 C.79 ⌘A 全选 101 张卡 → 截断黄条「已选前 100 / 共 101」→ Esc 清空", async ({ page }) => {
    test.setTimeout(180_000);
    await loginFresh(page);
    const proj = await createProject(page);
    // 造 101 张卡，状态轮转分散到各列（组内 group_per_page=100 上限——分散保证全部载入看板）
    const states = await apiCall(page, "GET", `/api/v1/workspaces/${WS}/projects/${proj.id}/states/?include_cancelled=1`);
    const stateIds = (states.body?.data as Array<{ id: string }>).map((s) => s.id);
    expect(stateIds.length).toBeGreaterThan(1);
    for (let i = 0; i < 101; i += 10) {
      await Promise.all(Array.from({ length: Math.min(10, 101 - i) }, (_, k) =>
        apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/`, {
          name: `S3B3 批量${i + k}`, state_id: stateIds[(i + k) % stateIds.length],
        })));
    }
    await page.reload();
    await expect(page.locator('article[data-sb-scope="board-card"]').first()).toBeVisible({ timeout: 20_000 });
    await page.waitForTimeout(1200); // 等全部列渲染（onCardsLoaded 上报 ⌘A 作用域）
    // ⌘A（C.79：刷新后焦点在 body——输入框聚焦时才不拦截）
    await page.keyboard.press(process.platform === "darwin" ? "Meta+a" : "Control+a");
    await expectBar(page, 100);
    // 截断黄条（BR-01：批量上限 100）
    const strip = page.locator('[data-sb-scope="bulk-trunc-strip"]');
    await expect(strip).toBeVisible();
    await expect(strip).toContainText("已选前 100 / 共 101");
    await strip.locator('[data-sb-scope="bulk-trunc-ok"]').click();
    await expect(strip).toBeHidden();
    // Esc 清空选中（C.79 Esc/✕ 行）
    await page.keyboard.press("Escape");
    await expect(page.locator('[data-sb-scope="bulk-bar"]')).toBeHidden({ timeout: 5_000 });
  });

  /* ═══════════ C.81 指派浮层：模式三选 + 应用数恒显 ═══════════ */

  test("S3B-4 C.81 批量指派浮层：替换/添加/移除三选 → PATCH bulk assignees add → Toast", async ({ page }) => {
    test.setTimeout(90_000);
    await loginFresh(page);
    const proj = await createProject(page);
    const a = await mkIssue(page, proj.id, "S3B4 指派目标");
    await gotoList(page);
    await page.locator(`tr[data-id="${a.id}"] [data-sb-scope="list-row-cb"]`).click();
    await expectBar(page, 1);
    await page.locator('[data-sb-scope="bulk-assign-btn"]').click();
    // C.81 浮层：标题应用数 + 模式三选（radiogroup，默认替换）+ 备注框 + 应用按钮恒显数量
    const pop = page.locator('[data-sb-scope="bulk-set-pop"]');
    await expect(pop).toBeVisible();
    await expect(pop).toContainText("批量指派 · 将应用到 1 个任务");
    await expect(pop.locator('[data-sb-scope="bulk-set-modes"] [role="radio"]')).toHaveCount(3);
    await expect(pop.locator('[data-sb-scope="bulk-set-mode"][data-mode="replace"]')).toHaveAttribute("aria-checked", "true");
    await expect(pop.locator('[data-sb-scope="bulk-set-apply"]')).toContainText("应用到 1 项");
    // 未选成员时应用 → toast 提示（不发包）
    await pop.locator('[data-sb-scope="bulk-set-apply"]').click();
    await expect(page.locator(".fixed.top-4.right-4")).toContainText("请先选择至少 1 个成员", { timeout: 5_000 });
    // 切「添加到」模式 + 勾第一个成员 + 备注 → PATCH assignees {mode:"add"}
    await pop.locator('[data-sb-scope="bulk-set-mode"][data-mode="add"]').click();
    await pop.locator('[data-sb-scope="bulk-set-row"] input[type="checkbox"]').first().check();
    const pickedId = await pop.locator('[data-sb-scope="bulk-set-row"] input[type="checkbox"]').first().getAttribute("data-v");
    await pop.locator('[data-sb-scope="bulk-set-note"]').fill("联调阶段统一对接");
    const patch = page.waitForResponse((r: Response) =>
      /\/issues\/bulk\/$/.test(r.url()) && r.request().method() === "PATCH");
    await pop.locator('[data-sb-scope="bulk-set-apply"]').click();
    const pres = await patch;
    expect(pres.status()).toBe(HTTP.OK);
    const body = pres.request().postDataJSON() as {
      issue_ids?: string[]; assignees?: { mode?: string; assignee_ids?: string[] }; comment?: string;
    };
    expect(body.issue_ids).toEqual([a.id]);
    expect(body.assignees?.mode).toBe("add");
    expect(body.assignees?.assignee_ids).toEqual([pickedId]);
    expect(body.comment).toBe("联调阶段统一对接");
    await expect(page.locator(".fixed.top-4.right-4")).toContainText("已更新 1 项", { timeout: 10_000 });
    // 回读：执行人含该成员
    const ra = await apiCall(page, "GET", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${a.id}/`);
    expect(ra.body?.data?.assignee_ids).toEqual(expect.arrayContaining([pickedId]));
  });

  /* ═══════════ C.82 失败定位：守卫拦截 → alertdialog → 移除该项并重试 ═══════════ */

  test("S3B-5 C.82 批量改状态被流转守卫拦截 → 400 details → 弹层定位 → 移除重试 200", async ({ page }) => {
    test.setTimeout(120_000);
    // 被测行为本身：守卫拦截 400（details 定位被阻塞项；BOARD-004 §C.82）
    getErrs?.allow({ method: "PATCH", url: "/issues/bulk/", status: HTTP.BAD_REQUEST });
    await loginFresh(page);
    const proj = await createProject(page);
    const blocker = await mkIssue(page, proj.id, "S3B5 前置任务"); // 不入批（入批会先完成、解阻塞）
    const normal = await mkIssue(page, proj.id, "S3B5 正常任务");
    const blocked = await mkIssue(page, proj.id, "S3B5 被阻塞任务");
    // TASK-005 关联：blocked is_blocked_by blocker（前置未完成 → 批量完成被拦）
    const rel = await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${blocked.id}/relations/`,
      { related_issue_id: blocker.id, relation_type: "is_blocked_by" });
    expect(rel.status).toBe(HTTP.CREATED);
    await gotoList(page);
    await page.locator(`tr[data-id="${normal.id}"] [data-sb-scope="list-row-cb"]`).click();
    await page.locator(`tr[data-id="${blocked.id}"] [data-sb-scope="list-row-cb"]`).click();
    await expectBar(page, 2);
    const states = await apiCall(page, "GET", `/api/v1/workspaces/${WS}/projects/${proj.id}/states/?include_cancelled=1`);
    const completed = (states.body?.data as Array<{ id: string; group: string }>).find((s) => s.group === "completed");
    // 第一发：PATCH 400（单事务全成败——blocked 项 BLOCKED_BY）
    const patch1 = page.waitForResponse((r: Response) =>
      /\/issues\/bulk\/$/.test(r.url()) && r.request().method() === "PATCH");
    await page.locator('[data-sb-scope="bulk-state-btn"]').click();
    await page.locator(`[data-sb-scope="bulk-state-item"][data-state-id="${completed!.id}"]`).click();
    const res1 = await patch1;
    expect(res1.status()).toBe(HTTP.BAD_REQUEST);
    const errBody = (await res1.json()) as { error?: { details?: Array<{ field: string; code: string; message: string }> } };
    const itemDetail = errBody.error?.details?.find((d) => d.field.startsWith("issue_ids["));
    expect(itemDetail?.code).toBe("BLOCKED_BY");
    expect(itemDetail?.message).toContain(blocked.key);
    // C.82 失败定位弹层（role=alertdialog）：标题 + 失败项（#2 = 选中列表 0 基索引 1）
    const dlg = page.locator('[data-sb-scope="bulk-fail-dialog"]');
    await expect(dlg).toBeVisible({ timeout: 5_000 });
    await expect(dlg).toHaveAttribute("role", "alertdialog");
    await expect(dlg).toContainText("2 项中 1 项未通过校验，本次未执行任何修改");
    await expect(dlg.locator('[data-sb-scope="bulk-fail-item"]')).toHaveCount(1);
    await expect(dlg.locator('[data-sb-scope="bulk-fail-item"]')).toContainText(blocked.key);
    // 首发整批回滚零残留（BE-3 口径）
    const rb = await apiCall(page, "GET", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${normal.id}/`);
    expect(rb.body?.data?.state_group).not.toBe("completed");
    // 「移除该项并重试（1 项）」→ 第二发 PATCH 200（显式剔除失败 id，BR-08）
    const patch2 = page.waitForResponse((r: Response) =>
      /\/issues\/bulk\/$/.test(r.url()) && r.request().method() === "PATCH");
    await dlg.locator('[data-sb-scope="bulk-fail-retry"]').click();
    const res2 = await patch2;
    expect(res2.status()).toBe(HTTP.OK);
    const body2 = res2.request().postDataJSON() as { issue_ids?: string[] };
    expect(body2.issue_ids).toEqual([normal.id]);
    expect(body2.issue_ids).not.toContain(blocked.id);
    await expect(page.locator(".fixed.top-4.right-4")).toContainText("已更新 1 项", { timeout: 10_000 });
    // 回读：正常项已完成、被阻塞项与前置保持原状
    const rAfter1 = await apiCall(page, "GET", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${normal.id}/`);
    expect(rAfter1.body?.data?.state_group).toBe("completed");
    const rAfter2 = await apiCall(page, "GET", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${blocked.id}/`);
    expect(rAfter2.body?.data?.state_group).not.toBe("completed");
  });

  /* ═══════════ C.83 归档确认 + 删除确认（preview + confirm_count） ═══════════ */

  test("S3B-6 C.83 批量归档：确认（含子树 N）→ POST bulk/archive → 列表退出", async ({ page }) => {
    test.setTimeout(90_000);
    await loginFresh(page);
    const proj = await createProject(page);
    const a = await mkIssue(page, proj.id, "S3B6 归档父");
    await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${a.id}/sub-issues/`, { name: "S3B6 归档子" });
    await gotoList(page);
    await page.locator(`tr[data-id="${a.id}"] [data-sb-scope="list-row-cb"]`).click();
    await expectBar(page, 1);
    // C.83 归档确认：可逆无需输入；含子树数（preview action=archive）
    const prev = page.waitForResponse((r: Response) =>
      /\/issues\/bulk\/preview\/$/.test(r.url()) && r.request().method() === "POST");
    await page.locator('[data-sb-scope="bulk-archive-btn"]').click();
    const pres = await prev;
    expect(pres.status()).toBe(HTTP.OK);
    expect((pres.request().postDataJSON() as { action?: string }).action).toBe("archive");
    const dlg = page.locator('[data-sb-scope="bulk-arch-dialog"]');
    await expect(dlg).toBeVisible({ timeout: 5_000 });
    await expect(dlg).toContainText("批量归档 1 个任务");
    await expect(dlg).toContainText("归档可逆");
    // POST …/issues/bulk/archive/ 200（archived_count = 选中+级联子树）
    const arch = page.waitForResponse((r: Response) =>
      /\/issues\/bulk\/archive\/$/.test(r.url()) && r.request().method() === "POST");
    await dlg.locator('[data-sb-scope="bulk-arch-ok"]').click();
    const ares = await arch;
    expect(ares.status()).toBe(HTTP.OK);
    const adata = ((await ares.json()) as { data?: { archived_count?: number; affected_total?: number } }).data;
    expect(adata?.archived_count).toBe(2); // 父 + 子（含子树级联，TASK-009 BR 口径）
    await expect(page.locator(".fixed.top-4.right-4")).toContainText("已归档", { timeout: 10_000 });
    // 回读：父任务 archived_at 置位（列表默认排除归档行——刷新后行消失）
    const ra = await apiCall(page, "GET", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${a.id}/`);
    expect(ra.body?.data?.archived_at).toBeTruthy();
  });

  test("S3B-7 C.83 批量删除：preview 级联统计 + 数量输入激活（BR-10）→ DELETE confirm_count → 行消失", async ({ page }) => {
    test.setTimeout(90_000);
    await loginFresh(page);
    const proj = await createProject(page);
    const a = await mkIssue(page, proj.id, "S3B7 删除甲");
    const b = await mkIssue(page, proj.id, "S3B7 删除乙");
    await gotoList(page);
    await page.locator(`tr[data-id="${a.id}"] [data-sb-scope="list-row-cb"]`).click();
    await page.locator(`tr[data-id="${b.id}"] [data-sb-scope="list-row-cb"]`).click();
    await expectBar(page, 2);
    // C.83 删除确认：POST preview action=delete（级联统计 aria-describedby）
    const prev = page.waitForResponse((r: Response) =>
      /\/issues\/bulk\/preview\/$/.test(r.url()) && r.request().method() === "POST");
    await page.locator('[data-sb-scope="bulk-delete-btn"]').click();
    const pres = await prev;
    expect(pres.status()).toBe(HTTP.OK);
    expect((pres.request().postDataJSON() as { action?: string }).action).toBe("delete");
    const dlg = page.locator('[data-sb-scope="bulk-del-dialog"]');
    await expect(dlg).toBeVisible({ timeout: 5_000 });
    await expect(dlg).toContainText("批量删除 2 个任务");
    await expect(dlg.locator('[data-sb-scope="bulk-del-stats"]')).toContainText("合计影响 2 个任务");
    // 数量输入错值不激活（BR-10 confirm_count）
    const go = dlg.locator('[data-sb-scope="bulk-del-ok"]');
    await expect(go).toBeDisabled();
    await dlg.locator('[data-sb-scope="bulk-del-count"]').fill("1");
    await expect(go).toBeDisabled();
    await dlg.locator('[data-sb-scope="bulk-del-count"]').fill("2");
    await expect(go).toBeEnabled();
    // DELETE …/issues/bulk/（confirm_count=2；Idempotency-Key 头由 api.ts 默认携带）
    const del = page.waitForResponse((r: Response) =>
      /\/issues\/bulk\/$/.test(r.url()) && r.request().method() === "DELETE");
    await go.click();
    const dres = await del;
    expect(dres.status()).toBe(HTTP.OK);
    const dbody = dres.request().postDataJSON() as { issue_ids?: string[]; confirm_count?: number };
    expect(dbody.confirm_count).toBe(2);
    expect(dbody.issue_ids).toEqual(expect.arrayContaining([a.id, b.id]));
    const ddata = ((await dres.json()) as { data?: { deleted?: number; affected_total?: number } }).data;
    expect(ddata?.deleted).toBe(2);
    await expect(page.locator(".fixed.top-4.right-4")).toContainText("已删除 2 项", { timeout: 10_000 });
    // 回读：软删（详情 404 存在性隐藏）
    const ra = await apiCall(page, "GET", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${a.id}/`);
    expect(ra.status).toBe(HTTP.NOT_FOUND);
  });

  /* ═══════════ C.78/C.80 鉴权负向成对：VIEWER 前端不可多选 + 后端 403 ═══════════ */

  test("S3B-8 C.78/C.80 鉴权负向成对：VIEWER 无工具条（复选框点选被拒）+ bulk 写端点 403", async ({ page, browser }) => {
    test.setTimeout(150_000);
    await loginFresh(page);
    const proj = await createProject(page);
    const a = await mkIssue(page, proj.id, "S3B8 VIEWER 只读目标");
    // ① 邀请新账号进工作空间（TEAM-002：注册即自动接受 pending 邀请——auth.signup 的
    //    accept_pending_invites 通道；无需再手动 POST accept）
    const vemail = `s3b-viewer-${Date.now() % 1000000}@rabbit.dev`;
    const inv = await apiCall(page, "POST", `/api/v1/workspaces/${WS}/invitations/`, { emails: [vemail], role: 10 });
    expect(inv.status).toBe(HTTP.OK);
    const ctx2 = await browser.newContext();
    const page2 = await ctx2.newPage();
    await page2.goto("/register");
    await page2.getByLabel(/邮箱/).fill(vemail);
    await page2.getByLabel("密码", { exact: false }).first().fill("Rabbit123!");
    await page2.getByLabel(/确认密码/).fill("Rabbit123!");
    await page2.getByRole("button", { name: /注册|创建账号/ }).click();
    await page2.waitForFunction(() => /\/projects$/.test(location.pathname), null, { timeout: 15_000 });
    // ② admin 把 viewer 加为项目 VIEWER（role=5；member_ids = 用户 id——
    //    add_members 按 WorkspaceMember.member_id（User FK）键检索）
    const mem = await apiCall(page, "GET", `/api/v1/workspaces/${WS}/members/?per_page=100`);
    const vm = (mem.body?.data as Array<{ id: string; user: { id: string; email: string } }>).find((m) => m.user.email === vemail);
    expect(vm).toBeTruthy();
    const add = await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${proj.id}/members/`, { member_ids: [vm!.user.id], role: 5 });
    expect(add.status).toBe(HTTP.OK); // success_response 200 + data[] 逐条分态（TEAM-002 范式）
    expect((add.body?.data as Array<{ status?: string }>)?.[0]?.status).toBe("added");
    // ③ viewer 用户路径进入项目（项目卡片入口）→ 列表 → 复选框点选被拒（无工具条）
    await page2.goto(`/${WS}/projects`);
    await page2.locator(`a[href*="${proj.id}"]`).first().waitFor({ state: "visible", timeout: 15_000 });
    await page2.locator(`a[href*="${proj.id}"]`).first().click();
    await page2.waitForURL(/\/board/, { timeout: 15_000 });
    await page2.getByRole("navigation").getByRole("link", { name: "任务列表" }).click();
    const row = page2.locator(`tr[data-id="${a.id}"]`);
    await expect(row).toBeVisible({ timeout: 15_000 });
    await row.locator('[data-sb-scope="list-row-cb"]').click();
    await expect(page2.locator(".fixed.top-4.right-4")).toContainText("当前角色无多选权限", { timeout: 5_000 });
    await expect(page2.locator('[data-sb-scope="bulk-bar"]')).toBeHidden();
    // ④ 后端边界：viewer 直发 bulk 写端点 → 403 PERM_ROLE_INSUFFICIENT（BE-14）
    const vcookies = await ctx2.cookies();
    const vcsrf2 = vcookies.find((c) => c.name === "csrftoken")?.value ?? "";
    const bulk403 = await page2.request.patch(`${API_ORIGIN}/api/v1/workspaces/${WS}/projects/${proj.id}/issues/bulk/`, {
      headers: { "Content-Type": "application/json", "X-CSRFToken": vcsrf2 },
      data: JSON.stringify({ issue_ids: [a.id], patch: { priority: "high" } }),
    });
    expect(bulk403.status()).toBe(HTTP.FORBIDDEN);
    expect(((await bulk403.json()) as { error?: { code?: string } }).error?.code).toBe("PERM_ROLE_INSUFFICIENT");
    await ctx2.close();
  });
});
