/** Sprint-2 TASK-004（多层级子任务）UI parity + 行为三件套（ADR-0010 ③：断言由清单生成，不由实现反推）。
 *  每条断言带 `// C.x <清单行原文摘要>` 出处注释（CLAUDE.md 测试脚本规范）。
 *
 *  覆盖（owner = TASK-004 §3）：
 *    C.37 列表树形展示（折叠箭头/缩进/子任务列/⊕ 展开下拉/折叠记忆/键盘）
 *    C.38 行内快速加子任务（乐观插入/连续录入/Esc/深度上限预判）
 *    C.39 拖拽移动子树 + 确认弹层（双区判定/成环 Toast）
 *    C.40 全屏树抽屉（stats 含根口径/节点点击开 Drawer/截断黄条）
 *    C.41 详情抽屉子任务分区升级（◔ x/y/前 20 条/查看全部/多层放开）
 *    C.63 <768px「移动到…」弹窗（父任务搜索选择器 → 同一 PATCH 链路）
 *
 *  演示数据：树用 API 在 beforeEach 内构建（勿改 seed）；登录走 UI（用户入口铁律），
 *  每条 test 先 clearCookies 再登录 → 点侧栏「任务列表」导航到达。
 *  状态码/错误码一律 import no-console-errors 的 API_TRUTH（CODES/HTTP），禁止硬编码。
 */
import { test, expect, type Page, type Response } from "@playwright/test";
import { attachConsoleGuard, CODES, HTTP } from "./no-console-errors";

const WS = "workspace";
/** API 走 web 同源（Vite 代理到 8000），与浏览器 context 共享 cookie */
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
  await page.getByLabel("项目名称 *").fill(`T004 ${Date.now() % 100000}`);
  await page.getByLabel("项目标识符 *").fill(rid());
  await page.getByRole("button", { name: "创建项目", exact: true }).click();
  await page.waitForURL(/\/projects\/.+\/board/, { timeout: 15_000 });
  const m = page.url().match(/\/([^/]+)\/projects\/([^/]+)\//);
  return { slug: m?.[1] ?? WS, id: m?.[2] ?? "" };
}

/** 与浏览器 context 共享 cookie 的 API 调用（返回 {status, body}；信封不解包，断言直读） */
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

async function mkIssue(page: Page, pid: string, name: string, parent?: Node4): Promise<Node4> {
  const path = parent
    ? `/api/v1/workspaces/${WS}/projects/${pid}/issues/${parent.id}/sub-issues/`
    : `/api/v1/workspaces/${WS}/projects/${pid}/issues/`;
  const { status, body } = await apiCall(page, "POST", path, { name });
  expect(status, `创建任务 ${name}`).toBe(HTTP.CREATED);
  const d = body?.data ?? {};
  return { id: d.id, key: d.issue_key, name: d.name };
}

/** 把任务置为已完成（PATCH state_id → completed 组；写侧只认 state_id） */
async function completeIssue(page: Page, pid: string, node: Node4) {
  const st = await apiCall(page, "GET", `/api/v1/workspaces/${WS}/projects/${pid}/states/`);
  const done = (st.body?.data as Array<{ id: string; group: string }>).find((s) => s.group === "completed");
  const { status } = await apiCall(page, "PATCH", `/api/v1/workspaces/${WS}/projects/${pid}/issues/${node.id}/`, { state_id: done.id });
  expect(status, `完成任务 ${node.name}`).toBe(HTTP.OK);
}

/** 用户入口：登录态下点侧栏「任务列表」导航到达（禁止直链深跳） */
async function gotoList(page: Page) {
  await page.getByRole("navigation").getByRole("link", { name: "任务列表" }).click();
  await page.waitForURL(/\/issues/, { timeout: 10_000 });
  await expect(page.locator('tbody tr[data-sb-scope="tree-row"]').first()).toBeVisible({ timeout: 15_000 });
}

const rowOf = (page: Page, name: string) =>
  page.locator('tr[data-sb-scope="tree-row"]').filter({ hasText: name }).first();

/** 展开某行（点折叠箭头）。expectFetch=true（默认）断言懒加载请求发出（首次展开）；
 *  缓存内的再展开（折叠→展开）不发请求（§3.1「折叠：数据保留缓存」），传 false。 */
async function expandRow(page: Page, name: string, expectFetch = true): Promise<Response | null> {
  let respP: Promise<Response> | null = null;
  if (expectFetch) {
    respP = page.waitForResponse(
      (r) => r.url().includes("parent_id=") && r.request().method() === "GET" && r.status() === HTTP.OK,
      { timeout: 10_000 },
    );
  }
  await rowOf(page, name).locator('[data-sb-scope="tree-toggle"]').click();
  const resp = respP ? await respP : null;
  await expect(rowOf(page, name)).toHaveAttribute("aria-expanded", "true");
  return resp;
}

test.describe("Sprint-2 TASK-004 多层级子任务（C.37~C.41 / C.63）", () => {
  let getErrs: () => string[] = () => [];
  test.beforeEach(async ({ page }) => {
    await page.context().clearCookies();
    getErrs = attachConsoleGuard(page);
  });
  test.afterEach(async () => {
    expect.soft(getErrs(), "console errors").toEqual([]);
  });

  /* ── C.37 任务列表·树形展示 ─────────────────────────────── */
  test("T004-1 C.37 树形行：折叠箭头（有子级才渲染）+ aria-level + 子任务进度列 + ?parent_id= 懒加载", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page);
    const root = await mkIssue(page, proj.id, "树根甲");
    const c1 = await mkIssue(page, proj.id, "甲一", root);
    await mkIssue(page, proj.id, "甲二", root);
    await mkIssue(page, proj.id, "甲一一", c1);
    await completeIssue(page, proj.id, c1);
    await gotoList(page);

    // C.37 树形行结构：树容器 role=tree；「子任务」独立列 88px
    await expect.soft(page.locator('table[role="tree"]')).toBeVisible();
    await expect.soft(page.locator("th").filter({ hasText: "子任务" })).toBeVisible();

    // C.37 子任务进度列：直接子级口径 x/y（甲一已完成、甲二未完成 → 1/2）+ aria-label 不依赖颜色
    const rootRow = rowOf(page, "树根甲");
    await expect.soft(rootRow.locator('[data-sb-scope="tree-sub-progress"]')).toHaveText("1/2");
    await expect.soft(rootRow.locator('[data-sb-scope="tree-sub-progress"]'))
      .toHaveAttribute("aria-label", "子任务 2 个，已完成 1 个");
    // 未展开：子行不可见；箭头 aria-expanded=false
    await expect(rowOf(page, "甲一")).toHaveCount(0);
    await expect.soft(rootRow).toHaveAttribute("aria-expanded", "false");
    await expect.soft(rootRow).toHaveAttribute("role", "treeitem");

    // C.37 折叠箭头：点击懒加载 ?parent_id=&order_by=sort_order&per_page=50（行为三件套②）
    const resp = await expandRow(page, "树根甲");
    expect(new URL(resp.url()).searchParams.get("parent_id")).toBe(root.id);
    expect(new URL(resp.url()).searchParams.get("order_by")).toBe("sort_order");
    // C.37 树形行结构：整行缩进（aria-level=2 子行 / =3 孙行）
    await expect.soft(rowOf(page, "甲一")).toBeVisible();
    await expect.soft(rowOf(page, "甲一")).toHaveAttribute("aria-level", "2");
    await expect.soft(rowOf(page, "甲一一")).toHaveCount(0);

    await expandRow(page, "甲一");
    await expect.soft(rowOf(page, "甲一一")).toHaveAttribute("aria-level", "3");
    // C.37 子任务进度列：无子级显示 —（甲二无子级 / 甲一一为叶子）
    await expect.soft(rowOf(page, "甲二").locator("td").last()).toHaveText("—");

    // C.37 折叠：再点箭头收起（数据保留缓存）
    await rowOf(page, "树根甲").locator('[data-sb-scope="tree-toggle"]').click();
    await expect(rowOf(page, "甲一")).toHaveCount(0);
  });

  /* ── C.37「⊕ 展开 ▾」下拉 + 折叠记忆 + 键盘导航 ─────────── */
  test("T004-2 C.37「⊕ 展开 ▾」三项下拉；折叠记忆刷新还原（localStorage）；←→↑↓ 键盘导航", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page);
    const root = await mkIssue(page, proj.id, "记忆根");
    const child = await mkIssue(page, proj.id, "记忆子", root);
    await mkIssue(page, proj.id, "记忆孙", child);
    await gotoList(page);

    // C.37「⊕ 展开 ▾」下拉三项
    await page.getByRole("button", { name: "展开控制" }).click();
    for (const item of ["全部展开（≤500 节点）", "收起到第 1 层", "收起到第 2 层"]) {
      await expect.soft(page.getByRole("menuitem").filter({ hasText: item }), `下拉项「${item}」`).toBeVisible();
    }
    // 「全部展开」逐层懒加载到第 3 层
    await page.getByRole("menuitem").filter({ hasText: "全部展开（≤500 节点）" }).click();
    await expect(rowOf(page, "记忆孙")).toBeVisible({ timeout: 15_000 });
    await expect.soft(rowOf(page, "记忆孙")).toHaveAttribute("aria-level", "3");
    // 「收起到第 1 层」：全收起
    await page.getByRole("button", { name: "展开控制" }).click();
    await page.getByRole("menuitem").filter({ hasText: "收起到第 1 层" }).click();
    await expect(rowOf(page, "记忆子")).toHaveCount(0);

    // 重新展开两级（数据保留缓存，不再发懒加载请求）→ 刷新还原（localStorage key issue-tree:collapsed:{projectId}）
    await expandRow(page, "记忆根", false);
    await expandRow(page, "记忆子", false);
    await expect(rowOf(page, "记忆孙")).toBeVisible();
    const ls = await page.evaluate(
      (pid: string) => localStorage.getItem(`issue-tree:collapsed:${pid}`),
      proj.id,
    );
    expect(ls, "折叠记忆落 localStorage").not.toBeNull();
    await page.reload();
    await expect(page.locator('tbody tr[data-sb-scope="tree-row"]').first()).toBeVisible({ timeout: 15_000 });
    await expect(rowOf(page, "记忆孙"), "刷新后展开状态还原").toBeVisible({ timeout: 15_000 });

    // C.37 键盘/无障碍：↑↓ 移动焦点、← 折叠、Enter 开详情
    const rootRow = rowOf(page, "记忆根");
    await rootRow.focus();
    await page.keyboard.press("ArrowDown");
    const focusedId = await page.evaluate(() => (document.activeElement as HTMLElement)?.dataset?.id ?? "");
    await expect.soft(focusedId, "↓ 下一行聚焦")
      .toBe((await rowOf(page, "记忆子").getAttribute("data-id")) ?? "");
    await rootRow.focus();
    await page.keyboard.press("ArrowLeft"); // ← 折叠
    await expect(rowOf(page, "记忆子")).toHaveCount(0);
    await rootRow.focus();
    await page.keyboard.press("ArrowRight"); // → 展开
    await expect(rowOf(page, "记忆子")).toBeVisible({ timeout: 10_000 });
    await rootRow.focus();
    await page.keyboard.press("Enter"); // Enter 打开详情
    await expect.soft(page.locator("aside").first()).toBeVisible({ timeout: 10_000 });
  });

  /* ── C.38 行内快速加子任务（乐观插入 / 连续录入 / Esc）──── */
  test("T004-3 C.38 行内快速加子任务：＋插入输入行 → 乐观临时行（…/opacity-60）→ POST 201 → 真实行 + 父徽标 +1；Esc 取消", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page);
    const root = await mkIssue(page, proj.id, "快加根");
    const c1 = await mkIssue(page, proj.id, "快加子", root);
    await completeIssue(page, proj.id, c1);
    await gotoList(page);

    const rootRow = rowOf(page, "快加根");
    await expect(rootRow.locator('[data-sb-scope="tree-sub-progress"]')).toHaveText("1/1");

    // C.38 插入输入行：目标行下方 ＋ 输入子任务标题后按回车… + Enter 保存 · Esc 取消
    await rootRow.hover();
    await rootRow.locator('[data-sb-scope="tree-quick-add"]').click();
    const input = page.locator('[data-sb-scope="tree-quick-input"]');
    await expect.soft(input).toBeVisible();
    await expect.soft(input).toHaveAttribute("placeholder", "输入子任务标题后按回车…");
    await expect.soft(page.getByText("Enter 保存 · Esc 取消")).toBeVisible();

    // 拖慢 POST 800ms：让乐观临时行（编号 …、opacity-60）可被确定性断言（突变自检锚点）
    await page.route("**/api/v1/workspaces/*/projects/*/issues/*/sub-issues/", async (route) => {
      await page.waitForTimeout(800);
      await route.continue();
    });
    const postP = page.waitForResponse(
      (r) => r.url().includes("/sub-issues/") && r.request().method() === "POST",
      { timeout: 15_000 },
    );
    await input.fill("快加新子");
    await input.press("Enter");
    // C.38 乐观插入：回车瞬间插入临时行（编号 …）
    await expect.soft(page.locator("tr", { hasText: "快加新子" }).filter({ hasText: "…" }).first(), "乐观临时行").toBeVisible({ timeout: 2_000 });
    const post = await postP;
    expect(post.status(), "POST sub-issues 2xx").toBe(HTTP.CREATED);
    const createdKey = ((await post.json()) as Record<string, any>).data.issue_key as string;
    // 成功后替换真实行（编号 = 项目前缀-序号，服务端下发）+ 父徽标 +1（1/1 → 1/2）
    await expect(rowOf(page, "快加新子")).toBeVisible({ timeout: 10_000 });
    await expect(rowOf(page, "快加新子")).toContainText(createdKey);
    await expect(page.locator("tr", { hasText: "快加新子" }).filter({ hasText: "…" })).toHaveCount(0);
    await expect(rootRow.locator('[data-sb-scope="tree-sub-progress"]'), "父徽标 +1").toHaveText("1/2");
    await expect.soft(page.getByText(`已创建子任务 ${createdKey}`)).toBeVisible();
    await page.unroute("**/api/v1/workspaces/*/projects/*/issues/*/sub-issues/");

    // C.38 提交语义：Esc 取消
    await rootRow.hover();
    await rootRow.locator('[data-sb-scope="tree-quick-add"]').click();
    await page.locator('[data-sb-scope="tree-quick-input"]').fill("不该出现的子任务");
    await page.keyboard.press("Escape");
    await expect(page.locator('[data-sb-scope="tree-quick-input"]')).toHaveCount(0);
    await expect(page.locator("tbody tr", { hasText: "不该出现的子任务" })).toHaveCount(0);
  });

  /* ── C.38/C.37 深度上限（E2E-04 负向）──────────────────── */
  test("T004-4 C.38 深度上限：第 5 层行不渲染「＋」；直连 API 409 RESOURCE_LIMIT_EXCEEDED", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page);
    const n1 = await mkIssue(page, proj.id, "深一层");
    const n2 = await mkIssue(page, proj.id, "深二层", n1);
    const n3 = await mkIssue(page, proj.id, "深三层", n2);
    const n4 = await mkIssue(page, proj.id, "深四层", n3);
    const n5 = await mkIssue(page, proj.id, "深五层", n4);
    await gotoList(page);

    // 全部展开到第 5 层
    await page.getByRole("button", { name: "展开控制" }).click();
    await page.getByRole("menuitem").filter({ hasText: "全部展开（≤500 节点）" }).click();
    const l5 = rowOf(page, "深五层");
    await expect(l5).toBeVisible({ timeout: 15_000 });
    await expect.soft(l5).toHaveAttribute("aria-level", "5");

    // C.38 深度上限：第 5 层行的悬浮「＋」不渲染（前端预判）；第 4 层仍渲染
    await l5.hover();
    await expect.soft(l5.locator('[data-sb-scope="tree-quick-add"]'), "第 5 层无「＋」").toHaveCount(0);
    const l4 = rowOf(page, "深四层");
    await l4.hover();
    await expect.soft(l4.locator('[data-sb-scope="tree-quick-add"]'), "第 4 层有「＋」").toHaveCount(1);

    // 直连 API 由后端 409 兜底（BR-02；错误码 import API_TRUTH）
    const { status, body } = await apiCall(page, "POST",
      `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${n5.id}/sub-issues/`, { name: "第六层" });
    expect(status).toBe(HTTP.CONFLICT);
    expect(body?.error?.code).toBe(CODES.limitExceeded);
    expect(body?.error?.details?.[0]?.code).toBe("DEPTH");
  });

  /* ── C.39 拖拽移动子树 + 确认弹层（E2E-02）──────────────── */
  test("T004-5 C.39 拖拽移动：中部 75% 成为子任务 → 确认弹层文案 → PATCH parent_id 200 → 树重排 + 计数联动", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page);
    const r1 = await mkIssue(page, proj.id, "拖根一");
    const c1a = await mkIssue(page, proj.id, "拖子一A", r1);
    await mkIssue(page, proj.id, "拖子一B", r1);
    const grand = await mkIssue(page, proj.id, "拖孙", c1a);
    const r2 = await mkIssue(page, proj.id, "拖根二");
    await gotoList(page);

    await expandRow(page, "拖根一");
    await expandRow(page, "拖子一A");
    await expect(rowOf(page, "拖孙")).toBeVisible();

    // C.39 双区判定：目标行中部（>25%）= 成为它的子任务
    await rowOf(page, "拖孙").locator('[data-sb-scope="tree-grip"]')
      .dragTo(rowOf(page, "拖根二"), { targetPosition: { x: 260, y: 14 } });

    // C.39 确认弹层：文案「将 “X”（含 N 个后代）移动到 “Y” 之下？」+ 移动/取消
    const confirmText = page.locator('[data-sb-scope="tree-confirm-move-text"]');
    await expect(confirmText).toBeVisible({ timeout: 10_000 });
    await expect(confirmText).toContainText('将 “拖孙”（含 0 个后代）移动到 “拖根二”');
    await expect(page.locator('[data-sb-scope="tree-confirm-move-go"]')).toBeVisible();

    // 行为三件套②：PATCH parent_id 发出且 2xx；请求体即目标父
    const patchP = page.waitForResponse(
      (r) => r.url().includes(`/issues/${grand.id}/`) && r.request().method() === "PATCH",
      { timeout: 15_000 },
    );
    await page.locator('[data-sb-scope="tree-confirm-move-go"]').click();
    const patch = await patchP;
    expect(patch.status()).toBe(HTTP.OK);
    expect(patch.request().postDataJSON()).toEqual({ parent_id: r2.id });

    // 行为三件套③：UI 回读——拖孙成为拖根二的子行（aria-level=2）、原父「拖子一A」计数清空、新父徽标 +1
    await expect(rowOf(page, "拖孙")).toHaveAttribute("aria-level", "2", { timeout: 10_000 });
    await expect.soft(rowOf(page, "拖根二").locator("td").last()).toHaveText("0/1");
    await expect.soft(rowOf(page, "拖子一A").locator("td").last()).toHaveText("—");
    await expect.soft(page.getByText("已移动到「拖根二」之下")).toBeVisible();
    // E2E-02：编号不变（sequence_id 不随移动重排）
    await expect(rowOf(page, "拖孙")).toContainText(grand.key);
  });

  /* ── C.63 移动端「移动到…」弹窗（<768px 键盘/触屏替代）──── */
  test("T004-6 C.63 <768px 行菜单「移动到…」：父任务搜索选择器 → 同一确认弹层 → PATCH parent_id 200", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page);
    const r1 = await mkIssue(page, proj.id, "移根一");
    const child = await mkIssue(page, proj.id, "移子", r1);
    const r2 = await mkIssue(page, proj.id, "移根二");
    // 触屏/窄屏断点后再进入列表（isMobile 由 matchMedia 驱动）
    await page.setViewportSize({ width: 375, height: 800 });
    await gotoList(page);

    await expandRow(page, "移根一");
    // C.63 触发条件：仅 <768px 行菜单「移动到…」（桌面把手不渲染）
    await expect.soft(rowOf(page, "移子").locator('[data-sb-scope="tree-grip"]'), "<768px 无拖拽把手").toHaveCount(0);
    await rowOf(page, "移子").locator('[data-sb-scope="tree-row-menu"]').click();
    await page.locator('[data-sb-scope="tree-row-move"]').click();

    // C.63 父任务搜索选择器：搜索候选（排除自身与后代）；选择后走与拖拽相同的 PATCH 链路
    const modal = page.locator('[data-sb-scope="tree-move-modal"]');
    await expect(modal).toBeVisible({ timeout: 10_000 });
    await expect.soft(modal.getByLabel("搜索父任务")).toBeVisible();
    await expect.soft(modal.getByText("选择新的父任务（留空 = 摘出为顶层）")).toBeVisible();
    await expect.soft(modal.getByText(/深度 ≤5 校验与环检测由服务端兜底/)).toBeVisible();
    await expect(modal.locator('[data-sb-scope="tree-move-candidate"]').filter({ hasText: "移根二" })).toBeVisible({ timeout: 10_000 });

    await modal.locator('[data-sb-scope="tree-move-candidate"]').filter({ hasText: "移根二" }).click();
    await expect(page.locator('[data-sb-scope="tree-confirm-move-text"]')).toContainText('将 “移子”（含 0 个后代）移动到 “移根二”', { timeout: 10_000 });
    const patchP = page.waitForResponse(
      (r) => r.url().includes(`/issues/${child.id}/`) && r.request().method() === "PATCH",
      { timeout: 15_000 },
    );
    await page.locator('[data-sb-scope="tree-confirm-move-go"]').click();
    const patch = await patchP;
    expect(patch.status()).toBe(HTTP.OK);
    expect(patch.request().postDataJSON()).toEqual({ parent_id: r2.id });
    await expect(rowOf(page, "移子")).toHaveAttribute("aria-level", "2", { timeout: 10_000 });
  });

  /* ── C.39 成环反馈（E2E-03 负向）───────────────────────── */
  test("T004-7 C.39 环移动：父任务拖到自己子级下 → 409 CYCLE → Toast 含环路径 + 环上节点红高亮 2s", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page);
    const root = await mkIssue(page, proj.id, "环根");
    const child = await mkIssue(page, proj.id, "环子", root);
    const grand = await mkIssue(page, proj.id, "环孙", child);
    await gotoList(page);

    await page.getByRole("button", { name: "展开控制" }).click();
    await page.getByRole("menuitem").filter({ hasText: "全部展开（≤500 节点）" }).click();
    await expect(rowOf(page, "环孙")).toBeVisible({ timeout: 15_000 });

    // 环根 → 环孙（自己的孙级）：拖拽路径不做后代排除（C.63 的移动到…弹窗才排除），由服务端 409 兜底
    await rowOf(page, "环根").locator('[data-sb-scope="tree-grip"]')
      .dragTo(rowOf(page, "环孙"), { targetPosition: { x: 260, y: 14 } });
    await expect(page.locator('[data-sb-scope="tree-confirm-move-text"]'))
      .toContainText('将 “环根”（含 2 个后代）移动到 “环孙”', { timeout: 10_000 });

    const patchP = page.waitForResponse(
      (r) => r.url().includes(`/issues/${root.id}/`) && r.request().method() === "PATCH",
      { timeout: 15_000 },
    );
    await page.locator('[data-sb-scope="tree-confirm-move-go"]').click();
    const patch = await patchP;
    expect(patch.status(), "环移动被 409 拒绝").toBe(HTTP.CONFLICT);

    // C.39 成环反馈：Toast 直出环路径（error.details[0].message 含「环路径：」）+ 树中环节点红高亮 2s
    const cycleToast = page.getByText(/环路径：/);
    await expect(cycleToast).toBeVisible({ timeout: 10_000 });
    await expect(cycleToast).toContainText("环根");
    await expect(cycleToast).toContainText("环孙");
    await expect.soft(page.locator("tr.cycle-flash").first(), "环上节点红高亮").toBeVisible();
    // E2E-03：无数据变更——环根仍是顶层
    await expect(rowOf(page, "环根")).toHaveAttribute("aria-level", "1");
    void child; void grand;
  });

  /* ── C.40 全屏树抽屉（E2E-01 stats 含根口径）────────────── */
  test("T004-8 C.40 全屏树：头部统计含根口径 · 节点点击开 Drawer · 截断黄条", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page);
    const root = await mkIssue(page, proj.id, "树全根");
    const back = await mkIssue(page, proj.id, "树先后端", root);
    const p1 = await mkIssue(page, proj.id, "树先分页", back);
    const p2 = await mkIssue(page, proj.id, "树先编码", p1);
    await mkIssue(page, proj.id, "树先基准", p2); // 第 5 层
    await mkIssue(page, proj.id, "树先前端", root);
    await completeIssue(page, proj.id, back);
    await gotoList(page);

    // 入口：详情子任务分区「查看全部 N 个 →」（C.40 入口；N = 直接子级 2）
    await rowOf(page, "树全根").click();
    const viewAll = page.locator('[data-sb-scope="drawer-sub-view-all"]');
    await expect(viewAll).toBeVisible({ timeout: 10_000 });
    await expect.soft(viewAll).toContainText("查看全部 2 个 →");
    await viewAll.click();

    const tree = page.locator('[data-sb-scope="tree-drawer"]');
    await expect(tree).toBeVisible({ timeout: 10_000 });
    // C.40 头部统计：stats 含根口径（total=6 含自身 · completed=1 · max_depth=4 相对层数）
    await expect.soft(tree.locator('[data-sb-scope="tree-stats"]'))
      .toHaveText("6 个任务（含自身）· 1 已完成 · 最深 4 层", { timeout: 10_000 });
    await expect.soft(tree.locator('[data-sb-scope="tree-node"]')).toHaveCount(6);

    // C.40 节点行：点击 → 打开该任务详情 Drawer；返回时树状态保留
    await tree.locator('[data-sb-scope="tree-node"]').filter({ hasText: "树先分页" }).click();
    // 全屏树自身也是 aside——用 aria-label 精确定位节点抽屉（任务详情 {key}）
    const nodeDrawer = page.locator(`aside[aria-label="任务详情 ${p1.key}"]`);
    await expect(nodeDrawer).toBeVisible({ timeout: 10_000 });
    await expect.soft(nodeDrawer).toContainText("树先分页");
    await nodeDrawer.getByRole("button", { name: "关闭" }).click();
    await expect(nodeDrawer).toHaveCount(0);
    await expect(tree, "关闭节点抽屉后树保留").toBeVisible();
    await tree.locator('[data-sb-scope="tree-close"]').click();
    await expect(tree).toHaveCount(0);
    // 关掉详情抽屉再重新进入（遮罩会拦截行点击）
    await page.locator("aside").first().getByRole("button", { name: "关闭" }).click();
    await expect(page.locator("aside")).toHaveCount(0);

    // C.40 截断黄条（meta.truncated=true）：拦截 subtree 响应注入截断态（500 节点不可实造，mock 服务端条件）
    await page.route("**/api/v1/workspaces/*/projects/*/issues/*/subtree/", async (route) => {
      await route.fulfill({
        status: HTTP.OK,
        contentType: "application/json",
        body: JSON.stringify({
          status: "success",
          data: {
            root: { id: root.id, parent_id: null, issue_key: root.key, sequence_id: 1, name: root.name, state_group: "started", assignee_ids: [], depth: 0, sub_issues_count: 2, completed_sub_issues_count: 0 },
            nodes: [
              { id: "n1", parent_id: root.id, issue_key: "RBT-9001", sequence_id: 9001, name: "截断节点一", state_group: "unstarted", assignee_ids: [], depth: 1 },
              { id: "n2", parent_id: "n1", issue_key: "RBT-9002", sequence_id: 9002, name: "截断节点二", state_group: "unstarted", assignee_ids: [], depth: 2 },
            ],
          },
          meta: { truncated: true, node_limit: 500 },
        }),
      });
    });
    await rowOf(page, "树全根").click();
    await page.locator('[data-sb-scope="drawer-sub-view-all"]').click();
    await expect(tree.locator('[data-sb-scope="tree-truncated"]')).toBeVisible({ timeout: 10_000 });
    await expect.soft(tree.locator('[data-sb-scope="tree-truncated"]'))
      .toContainText("结果超过 500 节点已截断 · 建议按状态 / 负责人筛选缩小范围");
    await page.unroute("**/api/v1/workspaces/*/projects/*/issues/*/subtree/");
  });

  /* ── C.41 详情抽屉子任务分区升级 ────────────────────────── */
  test("T004-9 C.41 子任务分区：◔ x/y +「＋」+「查看全部 N 个 →」+ 空态文案；子任务可再下挂（多层放开，MVP 一层提示条移除）", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page);
    const root = await mkIssue(page, proj.id, "分根");
    const child = await mkIssue(page, proj.id, "分子", root);
    await gotoList(page);

    // 打开二级任务（分子）抽屉：子任务分区对多层任务开放（原「MVP 阶段子任务仅支持一层」提示条移除）
    await expandRow(page, "分根");
    await rowOf(page, "分子").click();
    const drawer = page.locator("aside").first();
    await expect(drawer).toBeVisible({ timeout: 10_000 });
    await expect.soft(drawer.locator('[data-sb-scope="drawer-sub-limit"]'), "MVP 一层提示条已删除").toHaveCount(0);
    await expect.soft(drawer.getByPlaceholder("添加子任务，回车保存…"), "子任务行可继续下挂").toBeVisible();
    // C.41 添加行/空态：沿用 C.24 文案「暂无子任务，添加一个开始拆解」
    await expect.soft(drawer.locator('[data-sb-scope="drawer-sub-empty"]')).toHaveText("暂无子任务，添加一个开始拆解");
    // 无子级：不显示「查看全部」
    await expect.soft(drawer.locator('[data-sb-scope="drawer-sub-view-all"]')).toHaveCount(0);
    // 子任务下再挂一层（TASK-002 单层限制解除）
    await drawer.getByPlaceholder("添加子任务，回车保存…").fill("分子的子任务");
    const subPost = page.waitForResponse(
      (r) => r.url().includes(`/issues/${child.id}/sub-issues/`) && r.request().method() === "POST",
      { timeout: 15_000 },
    );
    await drawer.getByPlaceholder("添加子任务，回车保存…").press("Enter");
    expect((await subPost).status()).toBe(HTTP.CREATED);
    await expect(drawer.locator('[data-sb-scope="drawer-sub-row"]').filter({ hasText: "分子的子任务" })).toBeVisible({ timeout: 10_000 });
    await drawer.getByRole("button", { name: "关闭" }).click();

    // 打开父任务抽屉：分区头 ◔ x/y +「＋」+「查看全部 N 个 →」
    await rowOf(page, "分根").click();
    await expect(drawer).toBeVisible({ timeout: 10_000 });
    await expect.soft(drawer.locator('[data-sb-scope="drawer-sub-progress"]')).toHaveText("0/1");
    await expect.soft(drawer.locator('[data-sb-scope="drawer-sub-add"]')).toBeVisible();
    await expect.soft(drawer.locator('[data-sb-scope="drawer-sub-view-all"]')).toContainText("查看全部 1 个 →");
  });

  /* ── 级联删除（§2.4 / E2E-05）：DELETE 200 + deleted_count ─ */
  test("T004-10 级联删除：确认弹层明示后代数；DELETE 200 回传 deleted_count；整树从列表消失", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page);
    const root = await mkIssue(page, proj.id, "删根");
    const c1 = await mkIssue(page, proj.id, "删子一", root);
    await mkIssue(page, proj.id, "删孙", c1);
    await mkIssue(page, proj.id, "删子二", root);
    await gotoList(page);

    await rowOf(page, "删根").click();
    const drawer = page.locator("aside").first();
    await expect(drawer).toBeVisible({ timeout: 10_000 });
    await drawer.getByRole("button", { name: "更多操作" }).click();
    await drawer.getByRole("button", { name: "删除任务" }).click();

    // 确认弹层明示将删除的后代数量（subtree stats 同源：3 个后代；弹层挂在抽屉容器层，非 aside 内）
    await expect(page.getByText(/将同时删除 3 个子任务/)).toBeVisible({ timeout: 10_000 });

    // 行为三件套②：DELETE 200（不再是 204）+ data.deleted_count=4
    const delP = page.waitForResponse(
      (r) => r.url().includes(`/issues/${root.id}/`) && r.request().method() === "DELETE",
      { timeout: 15_000 },
    );
    await page.getByRole("button", { name: "删除", exact: true }).click();
    const del = await delP;
    expect(del.status(), "DELETE 200（级联回传受影响数）").toBe(HTTP.OK);
    expect((await del.json()).data.deleted_count).toBe(4);

    // E2E-05：成功后整树从列表消失
    await expect(page.locator("tbody tr").filter({ hasText: "删根" })).toHaveCount(0, { timeout: 15_000 });
    await expect(page.locator("tbody tr").filter({ hasText: "删子一" })).toHaveCount(0);
  });
});
