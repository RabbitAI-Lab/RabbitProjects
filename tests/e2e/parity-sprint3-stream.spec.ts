/** Sprint-3 Phase 3-C 前端（COLLAB-003 动态流页）parity + 行为三件套。
 *  断言由附录 C.89~C.92 清单行生成（ADR-0010 ③：不由实现反推），每条带 `// C.x` 出处注释。
 *
 *  覆盖（owner = COLLAB-003 §3.1~§3.3）：
 *    C.89 动态流视图条 + 侧栏入口 + 过滤条（URL 同源）
 *    C.90 三态行 / 日期分区 / 相对时间 / 软删 chip / 加载更早（到底提示）
 *    C.91 批量明细抽屉（头部/变更摘要/明细行/epoch 轻量拉取）
 *    C.92 60s/推送增量锚（stream_cursor 在响应中可见——水位断言走 API 层）
 *    E2E-05/IT-04 鉴权负向成对：移出成员 → 前端错误态 + 后端 API 404
 *
 *  纪律（CLAUDE.md）：登录走 UI（用户入口铁律——侧栏「动态」导航进入）；每 test 先
 *  clearCookies；行为断言三件套；console guard 全量；API_TRUTH import。
 *  前置：celery worker（activity 队列）在跑——动态行为异步落库，spec 内轮询收敛。
 */
import { test, expect, type Page, type Response } from "@playwright/test";
import { attachConsoleGuard, HTTP } from "./no-console-errors";

const WS = "workspace";
const API_ORIGIN = process.env.E2E_BASE_URL ?? "http://localhost:3001";

const rid = (n = 5) =>
  Array.from({ length: n }, () => String.fromCharCode(65 + Math.floor(Math.random() * 26))).join("");

async function loginDemo(page: Page) {
  await page.context().clearCookies();
  await page.goto("/login");
  await page.getByRole("button", { name: /一键进入演示账号/ }).click();
  await page.waitForFunction(() => /\/projects$/.test(location.pathname), null, { timeout: 15_000 });
}

async function createProject(page: Page): Promise<{ slug: string; id: string }> {
  const btn = page.getByRole("button", { name: /创建项目/ }).first();
  await btn.waitFor({ state: "visible", timeout: 20_000 });
  await btn.click();
  await page.getByLabel("项目名称 *").fill(`S3S ${Date.now() % 1000000}`);
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

async function mkIssue(page: Page, pid: string, name: string, extra: Record<string, unknown> = {}): Promise<{ id: string; key: string }> {
  const { status, body } = await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${pid}/issues/`, { name, ...extra });
  expect(status, `创建任务 ${name}`).toBe(HTTP.CREATED);
  return { id: body?.data?.id, key: body?.data?.issue_key };
}

/** 轮询动态流 API 直到出现匹配行（activity 异步落库——worker 收敛）。 */
async function waitStreamRow(page: Page, pid: string, match: (rows: any[]) => boolean, timeoutMs = 15_000): Promise<any[]> {
  const deadline = Date.now() + timeoutMs;
  let last: any[] = [];
  while (Date.now() < deadline) {
    const { status, body } = await apiCall(page, "GET", `/api/v1/workspaces/${WS}/projects/${pid}/activities/?per_page=50`);
    if (status === HTTP.OK) {
      last = body?.data ?? [];
      if (match(last)) return last;
    }
    await page.waitForTimeout(400);
  }
  return last;
}

/** 用户入口：项目内点侧栏「动态」到达流页（禁止直链深跳）。 */
async function gotoActivity(page: Page) {
  await page.getByRole("navigation").getByRole("link", { name: "动态" }).click();
  await page.waitForURL(/\/activity/, { timeout: 10_000 });
}

test.describe("Sprint-3 Phase 3-C 动态流页（COLLAB-003 · C.89~C.92）", () => {
  let getErrs: () => string[] = () => [];
  test.beforeEach(async ({ page }) => {
    await page.context().clearCookies();
    getErrs = attachConsoleGuard(page);
  });
  test.afterEach(async () => {
    expect.soft(getErrs(), "console errors").toEqual([]);
  });

  /* ═══════════ C.89/C.90 视图条 + 侧栏入口 + 三态行 + 分区 + 加载更早 ═══════════ */

  test("S3S-1 C.89/C.90 流页渲染：侧栏入口 / 视图条 / 过滤条 / 三态行 / 分区 / 相对时间 / 加载更早", async ({ page }) => {
    test.setTimeout(120_000);
    await loginDemo(page);
    const proj = await createProject(page);
    // 数据：31 批量占位（>30 组 → [加载更早] 按钮路径）→ 2 任务 + 1 评论（首页可见）
    for (let n = 0; n < 31; n++) await mkIssue(page, proj.id, `S3S1 批量占位 ${n}`);
    const i1 = await mkIssue(page, proj.id, "S3S1 登录改造");
    await mkIssue(page, proj.id, "S3S1 导出模板");
    const { status: cmtSt } = await apiCall(page, "POST",
      `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${i1.id}/comments/`,
      { comment_html: "S3S1 网关超时配置看下 upstream" });
    expect(cmtSt).toBe(HTTP.CREATED);
    await waitStreamRow(page, proj.id, (rows) =>
      rows.some((r) => r.kind === "comment" && (r.text ?? "").includes("网关超时")) && rows.length >= 30);

    // 用户入口：侧栏「动态」（pulse 图标 O5）
    await gotoActivity(page);
    await expect(page.locator('[data-sb-scope="stream-row"]').first()).toBeVisible({ timeout: 15_000 });
    // C.89 视图条：标题 + identifier + presence 消费位（O1）+ 连接指示
    await expect(page.locator('[data-sb-scope="activity-viewbar"]')).toContainText("动态");
    await expect(page.locator('[data-sb-scope="presence-cluster"]')).toBeVisible();
    await expect(page.locator('[data-sb-scope="conn-dot-btn"]')).toBeVisible();
    // C.89 过滤条：所有人 × 全部类型 + 60s hint
    await expect(page.locator('[data-sb-scope="stream-filter-actor"]')).toContainText("所有人");
    await expect(page.locator('[data-sb-scope="stream-filter-event"]')).toContainText("全部类型");
    await expect(page.locator('[data-sb-scope="stream-filters"]')).toContainText(/60s 自动刷新/);
    // C.90 语义列表 + 日期分区头（今天 sticky；role=heading）
    await expect(page.locator('[data-sb-scope="stream-list"]')).toHaveAttribute("aria-label", "项目动态流");
    await expect(page.locator('[data-sb-scope="stream-day"]').first()).toContainText("今天");
    // C.90 三态行：activity（含任务 chip）+ comment（💬 合流行）
    const activityRow = page.locator('[data-sb-scope="stream-row"][data-kind="activity"]').first();
    await expect(activityRow).toBeVisible();
    await expect(activityRow.locator('[data-sb-scope="stream-chip"]')).toBeVisible();
    const commentRow = page.locator('[data-sb-scope="stream-row"][data-kind="comment"]').first();
    await expect(commentRow).toContainText("评论了");
    await expect(commentRow).toContainText("网关超时");
    // C.90 相对时间 + hover 绝对时间（title=yyyy-MM-dd HH:mm）
    await expect(activityRow.locator("span[title]").first()).toHaveAttribute("title", /\d{4}-\d{2}-\d{2} \d{2}:\d{2}/);
    // C.90 [加载更早的动态]：>30 组 → 按钮式分页；点击 GET ?cursor= 追加（组感知游标）
    const moreBtn = page.locator('[data-sb-scope="stream-more"]');
    await expect(moreBtn).toBeVisible({ timeout: 10_000 });
    const cursorReq = page.waitForResponse((r: Response) =>
      /\/activities\/\?.*cursor=/.test(r.url()) && r.request().method() === "GET");
    await moreBtn.click();
    expect((await cursorReq).status()).toBe(HTTP.OK);
    // 追加后行数应增长（≥ 30 → 33+）
    const rowCount = await page.locator('[data-sb-scope="stream-row"]').count();
    expect(rowCount).toBeGreaterThan(30);
    // 到底提示（§3.3：历史深度有限——33 组两页内到底）
    for (let i = 0; i < 3; i++) {
      const btn = page.locator('[data-sb-scope="stream-more"]');
      if (!(await btn.isVisible())) break;
      await btn.click();
      await page.waitForTimeout(600);
    }
    await expect(page.locator('[data-sb-scope="stream-end"]')).toBeVisible({ timeout: 10_000 });
  });

  /* ═══════════ C.90 batch 行 + C.91 批量明细抽屉（行为级） ═══════════ */

  test("S3S-2 C.90/C.91 批量汇总行 + 明细抽屉（?epoch= 轻量拉取 → 跳任务详情）", async ({ page }) => {
    test.setTimeout(120_000);
    await loginDemo(page);
    const proj = await createProject(page);
    // 数据：3 任务 → PATCH bulk 状态（共享 epoch → BR-04 折叠 batch 行）
    const issues = [] as Array<{ id: string; key: string }>;
    for (let n = 0; n < 3; n++) issues.push(await mkIssue(page, proj.id, `S3S2 批量目标 ${n}`));
    const statesR = await apiCall(page, "GET", `/api/v1/workspaces/${WS}/projects/${proj.id}/states/?include_cancelled=1`);
    const states = (statesR.body?.data ?? []) as Array<{ id: string; name: string; group: string }>;
    const target = states.find((s) => s.group === "completed") ?? states[states.length - 1];
    // record_activity_batch 经 celery 异步落库（多 worker 竞争下重试至可见；≤6 次避限流）
    let sawBatch = false;
    for (let attempt = 0; attempt < 6 && !sawBatch; attempt++) {
      const bulk = await apiCall(page, "PATCH", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/bulk/`,
        { issue_ids: issues.map((i) => i.id), patch: { state_id: target.id } });
      expect(bulk.status).toBe(HTTP.OK);
      expect(bulk.body?.data?.updated).toBe(3);
      const rows = await waitStreamRow(page, proj.id, (rs) => rs.some((r) => r.kind === "batch" && r.batch_count === 3), 5_000);
      sawBatch = rows.some((r) => r.kind === "batch" && r.batch_count === 3);
    }
    expect(sawBatch, "bulk 同 epoch 跨任务应折叠 batch 行（BR-04）").toBe(true);

    await gotoActivity(page);
    // C.90 batch 行：bg-brand-50/40 + batch_count 加粗 + change_brief + ▸ 展开明细
    const batchRow = page.locator('[data-sb-scope="stream-row"][data-kind="batch"]').first();
    await expect(batchRow).toBeVisible({ timeout: 15_000 });
    await expect(batchRow.locator('[data-sb-scope="stream-batch-count"]')).toContainText("批量更新了 3 个任务");
    await expect(batchRow).toContainText(/状态/);
    // C.91 抽屉打开：GET ?epoch= 轻量拉取
    const epochReq = page.waitForResponse((r: Response) =>
      /\/activities\/\?.*epoch=/.test(r.url()) && r.request().method() === "GET");
    await batchRow.locator('[data-sb-scope="stream-batch-expand"]').click();
    expect((await epochReq).status()).toBe(HTTP.OK);
    // C.91 头部：操作者 + 汇总 + 绝对时间（epoch 副标）
    const drawer = page.locator('[data-sb-scope="batch-drawer"]');
    await expect(drawer).toBeVisible();
    await expect(drawer.locator('[data-sb-scope="batch-drawer-at"]')).toContainText(/epoch/);
    // C.91 变更摘要（全同直出）
    await expect(drawer.locator('[data-sb-scope="batch-drawer-brief"]')).toContainText("变更摘要：");
    // C.91 明细行：issue_key + 标题 + ↗ → 打开任务详情 Drawer（不离开流页）
    const detailRow = drawer.locator('[data-sb-scope="batch-drawer-row"]').first();
    await expect(detailRow).toContainText(issues[0]!.key.slice(0, 4));
    await detailRow.click();
    await expect(page.locator('[role="dialog"][aria-label*="任务详情"]')).toBeVisible({ timeout: 10_000 });
  });

  /* ═══════════ C.89 过滤条（组合 AND + URL 同源还原） ═══════════ */

  test("S3S-3 C.89 过滤条：event=comment 仅评论行 / actor 过滤 / URL 同源刷新还原", async ({ page }) => {
    test.setTimeout(120_000);
    await loginDemo(page);
    const proj = await createProject(page);
    const i1 = await mkIssue(page, proj.id, "S3S3 被评论任务");
    await mkIssue(page, proj.id, "S3S3 无评论任务");
    const { status: cmtSt } = await apiCall(page, "POST",
      `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${i1.id}/comments/`, { comment_html: "S3S3 只有这一条评论" });
    expect(cmtSt).toBe(HTTP.CREATED);
    await waitStreamRow(page, proj.id, (rows) => rows.some((r) => r.kind === "comment"));

    await gotoActivity(page);
    await expect(page.locator('[data-sb-scope="stream-row"]').first()).toBeVisible({ timeout: 15_000 });
    const before = await page.locator('[data-sb-scope="stream-row"]').count();
    expect(before).toBeGreaterThan(1);

    // 过滤 event=comment：GET ?event=comment（白名单语义组，§2.3）→ 仅评论行
    const evtReq = page.waitForResponse((r: Response) =>
      /\/activities\/\?.*event=comment/.test(r.url()) && r.request().method() === "GET");
    await page.locator('[data-sb-scope="stream-filter-event"]').click();
    await page.locator('[data-sb-scope="stream-filter-event-item"][data-value="comment"]').click();
    expect((await evtReq).status()).toBe(HTTP.OK);
    await expect(page.locator('[data-sb-scope="stream-row"][data-kind="activity"]')).toHaveCount(0);
    await expect(page.locator('[data-sb-scope="stream-row"][data-kind="comment"]')).toHaveCount(1);
    // URL 同源：?event=comment
    await page.waitForURL(/event=comment/);
    // 过滤条回显（按钮 brand 高亮 + 选中文案「评论」）
    await expect(page.locator('[data-sb-scope="stream-filter-event"]')).toContainText("评论");
    // URL 分享还原：刷新后过滤态保持（IT-06 / E2E-03 口径）
    await page.reload();
    await expect(page.locator('[data-sb-scope="stream-row"][data-kind="comment"]')).toHaveCount(1, { timeout: 15_000 });
    await expect(page.locator('[data-sb-scope="stream-filter-event"]')).toContainText("评论");

    // 组合 AND：actor=张三（评论作者本人）× event=comment → 恰 1 行（组合不放大不误杀）
    const membersR = await apiCall(page, "GET", `/api/v1/workspaces/${WS}/projects/${proj.id}/members/?per_page=100`);
    const me = ((membersR.body?.data ?? []) as Array<{ user: { display_name: string; id: string } }>)
      .find((m) => m.user.display_name === "张三");
    await page.locator('[data-sb-scope="stream-filter-actor"]').click();
    await page.locator(`[data-sb-scope="stream-filter-actor-item"][data-value="${me?.user.id}"]`).click();
    await page.waitForURL(/actor=/);
    await expect(page.locator('[data-sb-scope="stream-row"][data-kind="comment"]')).toHaveCount(1, { timeout: 10_000 });
    await expect(page.locator('[data-sb-scope="stream-filter-actor"]')).toContainText("张三");
    // 组合 AND 空态：切到 event=state（本项目无流转 Activity）→「该过滤条件下暂无动态」
    await page.locator('[data-sb-scope="stream-filter-event"]').click();
    await page.locator('[data-sb-scope="stream-filter-event-item"][data-value="state"]').click();
    await expect(page.locator('[data-sb-scope="stream-empty"]')).toContainText("该过滤条件下暂无动态", { timeout: 10_000 });
  });

  /* ═══════════ C.90 软删 chip（BR-06：动态保留 + 置灰不可点） ═══════════ */

  test("S3S-4 C.90 软删任务：动态保留在流中 + chip 置灰 ✕ 不可点（BR-06）", async ({ page }) => {
    test.setTimeout(120_000);
    await loginDemo(page);
    const proj = await createProject(page);
    const victim = await mkIssue(page, proj.id, "S3S4 将被删除的任务");
    await waitStreamRow(page, proj.id, (rows) => rows.some((r) => r.issue?.issue_key === victim.key));

    await gotoActivity(page);
    const chip = page.locator(`[data-sb-scope="stream-chip"][data-issue-key="${victim.key}"]`);
    await expect(chip).toBeVisible({ timeout: 15_000 });
    // 删除任务（软删，级联回传）→ 刷新后行保留 + is_deleted 投影（BR-06）
    const { status } = await apiCall(page, "DELETE", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${victim.id}/`);
    expect(status).toBe(HTTP.OK);
    await page.reload();
    const dead = page.locator(`[data-sb-scope="stream-chip-dead"][data-issue-key="${victim.key}"], [data-sb-scope="stream-chip-dead"]`).filter({ hasText: victim.key });
    await expect(dead.first()).toBeVisible({ timeout: 15_000 });
    await expect(dead.first()).toBeDisabled();
    await expect(dead.first()).toContainText("✕");
  });

  /* ═══════════ E2E-05/IT-04 鉴权负向成对：移出成员 → 前端错误态 + API 404 ═══════════ */

  test("S3S-5 E2E-05 移出成员后回访动态流：前端不可见 + 后端 API 404（BR-01 存在性隐藏）", async ({ page }) => {
    test.setTimeout(150_000);
    await loginDemo(page);
    const proj = await createProject(page);
    await mkIssue(page, proj.id, "S3S5 权限收缩前任务");

    // ② 受害者：全新账号，经 SMTP 降级 invite_links（meta）接受邀请 → 加入工作空间
    const victimEmail = `s3s-victim-${Date.now()}-${Math.floor(Math.random() * 1e4)}@rabbit.dev`;
    const invite = await apiCall(page, "POST", `/api/v1/workspaces/${WS}/invitations/`, { emails: [victimEmail], role: 10 });
    expect(invite.status).toBe(HTTP.OK);
    const inviteLinks = ((invite.body?.meta?.invite_links ?? {}) as Record<string, string>);
    const link = (inviteLinks[victimEmail] ?? "").match(/\/invite\/([A-Za-z0-9\-_.]+)/);
    expect(link, "SMTP 降级模式应回显 invite_links（dev e2e 前置）").toBeTruthy();

    const victim = await page.context().browser()!.newContext();
    const vpage = await victim.newPage();
    const vErrs = attachConsoleGuard(vpage);
    await victim.clearCookies();
    await vpage.goto("/register");
    await vpage.getByLabel(/邮箱/).fill(victimEmail);
    await vpage.getByLabel(/密码/, { exact: false }).first().fill("Rabbit123!");
    await vpage.getByLabel(/确认密码/).fill("Rabbit123!");
    await vpage.getByRole("button", { name: /注册|创建账号/ }).click();
    await vpage.waitForFunction(() => /\/projects$/.test(location.pathname), null, { timeout: 15_000 });
    // 接受邀请：注册钩子对「被邀请邮箱」自动落成员并翻转邀请（显式 accept 已被
    // 使用时 400 属预期）——先查成员表，缺则显式 accept 兜底
    const tryAccept = async () => {
      const vcookies = await victim.cookies();
      const vcsrf = vcookies.find((c) => c.name === "csrftoken")?.value ?? "";
      await vpage.request.post(`${API_ORIGIN}/api/v1/invitations/${link![1]}/accept/`, {
        headers: { "Content-Type": "application/json", ...(vcsrf ? { "X-CSRFToken": vcsrf } : {}) }, data: {} });
    };
    await tryAccept();
    // ③ 张三把受害者加进项目（PROJ-002 add，role=CONTRIBUTOR）
    let membersR = await apiCall(page, "GET", `/api/v1/workspaces/${WS}/members/?per_page=100`);
    let vm = ((membersR.body?.data ?? []) as Array<{ id: string; user: { email: string } }>).find((m) => m.user.email === victimEmail);
    if (!vm) { await tryAccept(); membersR = await apiCall(page, "GET", `/api/v1/workspaces/${WS}/members/?per_page=100`); vm = ((membersR.body?.data ?? []) as Array<{ id: string; user: { email: string } }>).find((m) => m.user.email === victimEmail); }
    expect(vm, "受害者应已成为工作空间成员（注册钩子或显式接受）").toBeTruthy();
    const add = await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${proj.id}/members/`, { member_ids: [vm!.user.id], role: 15 });
    expect([HTTP.OK, HTTP.CREATED], "项目成员添加（200/201）").toContain(add.status);
    expect(((add.body?.data ?? []) as Array<{ status?: string }>)[0]?.status ?? "added", "逐项应为 added").toBe("added");

    // 正向：受害者进入项目（真实成员；深链可用——登录态 + 资源真实）→ 侧栏动态
    await vpage.goto(`/${WS}/projects/${proj.id}/board`);
    await expect(vpage.getByRole("navigation").getByRole("link", { name: "动态" })).toBeVisible({ timeout: 15_000 });
    await vpage.getByRole("navigation").getByRole("link", { name: "动态" }).click();
    await vpage.waitForURL(/\/activity/, { timeout: 10_000 });
    await expect(vpage.locator('[data-sb-scope="stream-row"]').first()).toBeVisible({ timeout: 15_000 });

    // ④ 移出成员（张三）→ 受害者 API 404 + 前端错误态（不泄露内容）
    const pmR = await apiCall(page, "GET", `/api/v1/workspaces/${WS}/projects/${proj.id}/members/?per_page=100`);
    const pm = ((pmR.body?.data ?? []) as Array<{ id: string; user: { email: string } }>).find((m) => m.user.email === victimEmail);
    const rm = await apiCall(page, "DELETE", `/api/v1/workspaces/${WS}/projects/${proj.id}/members/${pm!.id}/`);
    expect([HTTP.OK, HTTP.NO_CONTENT], "移除项目成员").toContain(rm.status);
    // 后端 404（存在性隐藏，BR-01）——受害者自己的会话
    const vList = await vpage.request.get(`${API_ORIGIN}/api/v1/workspaces/${WS}/projects/${proj.id}/activities/`);
    expect(vList.status(), "移出后动态 API 应 404").toBe(HTTP.NOT_FOUND);
    // 前端：刷新后错误态（非白屏、不泄露行内容）
    await vpage.reload();
    await expect(vpage.locator('[data-sb-scope="stream-err"], [data-sb-scope="stream-empty"]').first()).toBeVisible({ timeout: 15_000 });
    const vbody = (await vpage.locator("body").innerText()).replace(/\s+/g, " ");
    expect(vbody.includes("S3S5 权限收缩前任务"), "不得泄露项目动态内容").toBe(false);
    expect.soft(vErrs(), "victim console errors").toEqual([]);
    await victim.close();
  });
});
