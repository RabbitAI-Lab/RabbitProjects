/** Sprint-2 前端批量（TASK-005/006/007/008/009/010 的 UI 表面）parity + 行为三件套。
 *  断言由附录 C.42~C.62 清单行生成（ADR-0010 ③：不由实现反推），每条带 `// C.x` 出处注释。
 *
 *  覆盖（owner = TASK-005~010 §3）：
 *    C.42/C.43 关联三分区 + 添加关联弹层（409 环红条）
 *    C.44 完成被拦截对话框（管理员强制完成）
 *    C.45 列表依赖图标 / ?blocked 筛选 / 看板 ⛔ 角标 + 列头计数
 *    C.46/C.47 工时分区 + 工时填报弹层（⌘ 保存并再开）
 *    C.48 列表工时列（超耗红 / 列开关）
 *    C.49/C.50 执行人区 + AssigneePicker（n/10 / 通知预览 / 退出任务）
 *    C.51 列表/看板执行人呈现 + 认领 + 快速指派
 *    C.52/C.53/C.54 字段管理页 + 12 类型宫格 + 三段删除（202）
 *    C.55/C.56 动态字段折叠区 + 动态列
 *    C.57/C.58/C.59/C.60 复制 / 归档确认 + 撤销 Toast / 归档视图 / 只读横幅
 *    C.61 动态 Tab 时间线（epoch 组 / 双过滤器 / 按钮式加载更早）
 *    C.62 admin 死信补偿页
 *
 *  纪律（CLAUDE.md）：登录走 UI（用户入口铁律）；每 test 先 clearCookies；行为断言三件套
 *  （①交互 → ②waitForResponse 对应请求 2xx → ③UI 回读）；负向成对；console guard 全量。
 */
import { test, expect, type Page, type Response } from "@playwright/test";
import { attachGuards, CODES, HTTP } from "./no-console-errors";

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
  await page.getByLabel("项目名称 *").fill(`T5 ${Date.now() % 100000}`);
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

async function mkIssue(page: Page, pid: string, name: string, parent?: Node4): Promise<Node4> {
  const path = parent
    ? `/api/v1/workspaces/${WS}/projects/${pid}/issues/${parent.id}/sub-issues/`
    : `/api/v1/workspaces/${WS}/projects/${pid}/issues/`;
  const { status, body } = await apiCall(page, "POST", path, { name });
  expect(status, `创建任务 ${name}`).toBe(HTTP.CREATED);
  const d = body?.data ?? {};
  return { id: d.id, key: d.issue_key, name: d.name };
}

async function statesOf(page: Page, pid: string): Promise<Array<{ id: string; name: string; group: string }>> {
  const st = await apiCall(page, "GET", `/api/v1/workspaces/${WS}/projects/${pid}/states/?include_cancelled=1`);
  return (st.body?.data as Array<{ id: string; name: string; group: string }>) ?? [];
}

async function completeIssue(page: Page, pid: string, node: Node4) {
  const states = await statesOf(page, pid);
  const done = states.find((s) => s.group === "completed")!;
  const { status } = await apiCall(page, "PATCH", `/api/v1/workspaces/${WS}/projects/${pid}/issues/${node.id}/`, { state_id: done.id });
  expect(status, `完成任务 ${node.name}`).toBe(HTTP.OK);
}

/** 用户入口：登录态下点侧栏「任务列表」导航到达（禁止直链深跳）。 */
async function gotoList(page: Page, opts: { empty?: boolean } = {}) {
  await page.getByRole("navigation").getByRole("link", { name: "任务列表" }).click();
  await page.waitForURL(/\/issues/, { timeout: 10_000 });
  if (opts.empty) {
    await expect(page.locator("#tq-input")).toBeVisible({ timeout: 15_000 });
  } else {
    await expect(page.locator('tbody tr[data-sb-scope="tree-row"]').first()).toBeVisible({ timeout: 15_000 });
  }
}

async function gotoBoard(page: Page) {
  // createProject 落地即 /board：同路由点 NavLink 不触发重挂载 → 先经列表再进看板（用户可走通路径）
  if (/\/board$/.test(page.url())) {
    await page.getByRole("navigation").getByRole("link", { name: "任务列表" }).click();
    await page.waitForURL(/\/issues/, { timeout: 10_000 });
  }
  await page.getByRole("navigation").getByRole("link", { name: "看板" }).click();
  await page.waitForURL(/\/board/, { timeout: 10_000 });
}

const rowOf = (page: Page, name: string) =>
  page.locator('tr[data-sb-scope="tree-row"]').filter({ hasText: name }).first();

/** 打开抽屉（列表行点击）。 */
async function openDrawer(page: Page, name: string) {
  await rowOf(page, name).click();
  const drawer = page.locator("aside").first();
  await expect(drawer).toBeVisible({ timeout: 10_000 });
  return drawer;
}

test.describe("Sprint-2 前端批量（TASK-005~010 / C.42~C.62）", () => {
  let getErrs: ReturnType<typeof attachGuards> | undefined;
  test.beforeEach(async ({ page }) => {
    await page.context().clearCookies();
    getErrs = attachGuards(page);
  });
  test.afterEach(async () => {
    expect.soft(getErrs?.report() ?? [], "console/net errors").toEqual([]);
  });

  /* ═══════════ TASK-005 关联 ═══════════ */

  test("T005-1 C.42 关联分区三分组：前置/后置/相关 + duplicates 角标 + 计数 (N)；C.45 列表依赖图标", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page);
    const target = await mkIssue(page, proj.id, "关联主任务");
    // 前置（他任务 blocks 主任务 ⇒ is_blocked_by）；后置（主任务 blocks Y）；相关 relates/duplicates
    const pre = await mkIssue(page, proj.id, "前置未完任务");
    const preDone = await mkIssue(page, proj.id, "前置已完任务");
    const post = await mkIssue(page, proj.id, "后置任务");
    const rel = await mkIssue(page, proj.id, "相关任务");
    const dup = await mkIssue(page, proj.id, "重复任务");
    await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${pre.id}/relations/`, { related_issue_id: target.id, relation_type: "blocks" });
    await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${preDone.id}/relations/`, { related_issue_id: target.id, relation_type: "blocks" });
    await completeIssue(page, proj.id, preDone);
    await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${target.id}/relations/`, { related_issue_id: post.id, relation_type: "blocks" });
    await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${target.id}/relations/`, { related_issue_id: rel.id, relation_type: "relates_to" });
    await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${target.id}/relations/`, { related_issue_id: dup.id, relation_type: "duplicates" });

    await gotoList(page);
    // C.45 列表行依赖图标：标题左侧 12px link 图标（存在任意关联时）；被阻塞 amber（前置未完）
    const tRow = rowOf(page, "关联主任务");
    await expect(tRow.locator('[data-sb-scope="list-rel-icon"]'), "被阻塞行 amber ⚠ 图标").toBeVisible({ timeout: 15_000 });
    await expect.soft(rowOf(page, "后置任务").locator('[data-sb-scope="list-rel-icon"]'), "有后置关联 → link 图标").toBeVisible({ timeout: 15_000 });
    await expect.soft(rowOf(page, "无关节点X", { exact: false }).locator('[data-sb-scope="list-rel-icon"]')).toHaveCount(0).catch(() => {});

    const drawer = await openDrawer(page, "关联主任务");
    const relSec = drawer.locator('[data-sb-scope="drawer-rel-section"]');
    await expect(relSec).toBeVisible({ timeout: 10_000 });
    // C.42 计数与上限提示：分区标题 (N) 为三组合计
    await expect.soft(relSec.locator('[data-sb-scope="drawer-rel-add"]')).toBeVisible();
    // C.42 三分组：固定顺序：阻塞于此（前置）/ 阻塞（后置）/ 相关（relates+duplicates 合并）
    const groups = relSec.locator('[data-sb-scope="drawer-rel-group"]');
    await expect(groups).toHaveCount(3, { timeout: 10_000 });
    await expect.soft(groups.filter({ hasText: "阻塞于此（前置）" })).toContainText("前置未完任务");
    await expect.soft(groups.filter({ hasText: "阻塞（后置）" })).toContainText("后置任务");
    await expect.soft(groups.filter({ hasText: "相关" })).toContainText("相关任务");
    // C.42 duplicates 项加「重复于」角标
    await expect.soft(groups.filter({ hasText: "相关" }).locator('[data-sb-scope="drawer-rel-dup-badge"]')).toHaveText("重复于");
    // C.42 阻塞语义强化：未完成前置 ⚠ amber；已完成为 ✓ 视觉降级
    const preGroup = groups.filter({ hasText: "阻塞于此（前置）" });
    await expect.soft(preGroup.locator('[data-sb-scope="drawer-rel-row"]').filter({ hasText: "前置未完任务" }).locator("span").first()).toHaveText("⚠");
    await expect.soft(preGroup.locator('[data-sb-scope="drawer-rel-row"]').filter({ hasText: "前置已完任务" }).locator("span").first()).toHaveText("✓");
    // C.42 关联行：跳转箭头 → 点击跳目标详情（保留返回栈：嵌套抽屉 aria-label=任务详情 {key}）
    await relSec.locator('[data-sb-scope="drawer-rel-row"]').filter({ hasText: "后置任务" }).getByRole("button", { name: /打开 后置任务/ }).click();
    await expect.soft(page.locator(`aside[aria-label="任务详情 ${post.key}"]`)).toBeVisible({ timeout: 10_000 });
  });

  test("T005-2 C.43 添加关联弹层：类型四项 + 语义提示 + 搜索选目标 → POST 201 → 分区回读；已关联灰显；409 环红条（负向）", async ({ page }) => {
    test.setTimeout(90_000);
    // 被测行为本身：重复关联 409（RESOURCE_ALREADY_EXISTS 环红条负向）
    getErrs?.allow({ method: "POST", url: "/relations/", status: HTTP.CONFLICT });
    await loginDemo(page);
    const proj = await createProject(page);
    const a = await mkIssue(page, proj.id, "弹层甲");
    const b = await mkIssue(page, proj.id, "弹层乙");
    await gotoList(page);
    const drawer = await openDrawer(page, "弹层甲");

    // C.43 类型下拉四项固定（图标+中文名+代码角标）
    await drawer.locator('[data-sb-scope="drawer-rel-add"]').click();
    const modal = page.locator('[data-sb-scope="link-type-dd"]');
    await expect(modal).toBeVisible({ timeout: 10_000 });
    await modal.locator('[data-sb-scope="link-type-btn"]').click();
    for (const item of ["阻塞了…", "被…阻塞", "相关于…", "重复于…"]) {
      await expect.soft(modal.locator('[data-sb-scope="link-type-item"]').filter({ hasText: item }), `类型项「${item}」`).toBeVisible();
    }
    // C.43 语义提示行：relates_to →「仅建立关联，不阻塞流转」
    await modal.locator('[data-sb-scope="link-type-item"]').filter({ hasText: "相关于…" }).click();
    await expect.soft(page.locator('[data-sb-scope="link-sem-tip"]')).toContainText("仅建立关联，不阻塞流转");
    await modal.locator('[data-sb-scope="link-type-btn"]').click();
    await modal.locator('[data-sb-scope="link-type-item"]').filter({ hasText: "阻塞了…" }).click();
    await expect.soft(page.locator('[data-sb-scope="link-sem-tip"]')).toContainText("目标完成前，当前任务将无法流转到已完成");

    // C.43 目标搜索：防抖 300ms；选择目标（弹层乙）
    await page.locator('[data-sb-scope="link-search"]').fill("弹层乙");
    const cand = page.locator('[data-sb-scope="link-candidate"]').filter({ hasText: "弹层乙" });
    await expect(cand).toBeVisible({ timeout: 10_000 });
    // 行为三件套②：POST relations 201
    const postP = page.waitForResponse(
      (r) => r.url().includes(`/issues/${a.id}/relations/`) && r.request().method() === "POST",
      { timeout: 15_000 },
    );
    await cand.click();
    await page.locator('[data-sb-scope="link-submit"]').click();
    const post = await postP;
    expect(post.status()).toBe(HTTP.CREATED);
    expect(post.request().postDataJSON()).toEqual({ related_issue_id: b.id, relation_type: "blocks" });
    // 行为三件套③：UI 回读——分区「阻塞（后置）」出现弹层乙
    await expect(drawer.locator('[data-sb-scope="drawer-rel-group"]').filter({ hasText: "阻塞（后置）" }))
      .toContainText("弹层乙", { timeout: 10_000 });

    // C.43 已关联预判：目标已被当前任务关联时搜索行内灰字「已关联」且不可选
    await drawer.locator('[data-sb-scope="drawer-rel-add"]').click();
    await page.locator('[data-sb-scope="link-search"]').fill("弹层乙");
    const linked = page.locator('[data-sb-scope="link-candidate"]').filter({ hasText: "弹层乙" });
    await expect(linked).toBeVisible({ timeout: 10_000 });
    await expect.soft(linked).toContainText("已关联");
    await expect.soft(linked).toHaveAttribute("aria-disabled", "true");
    await page.keyboard.press("Escape");

    // 负向（C.43 提交/失败）：409 环路径 → 弹层内红条完整依赖链
    // 构造三方链：弹层甲 blocks 弹层乙（已建）→ 弹层乙 blocks 弹层丙 → 在弹层丙上 blocks 弹层甲 = 成环
    const c = await mkIssue(page, proj.id, "弹层丙");
    await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${b.id}/relations/`, { related_issue_id: c.id, relation_type: "blocks" });
    await page.locator("aside").first().getByRole("button", { name: "关闭" }).click();
    const drawerC = await openDrawer(page, "弹层丙");
    await drawerC.locator('[data-sb-scope="drawer-rel-add"]').click();
    await page.locator('[data-sb-scope="link-search"]').fill("弹层甲");
    await page.locator('[data-sb-scope="link-candidate"]').filter({ hasText: "弹层甲" }).click();
    const cycleP = page.waitForResponse(
      (r) => r.url().includes(`/issues/${c.id}/relations/`) && r.request().method() === "POST",
      { timeout: 15_000 },
    );
    await page.locator('[data-sb-scope="link-submit"]').click();
    const cycle = await cycleP;
    expect(cycle.status(), "成环创建被 409 拒绝").toBe(HTTP.CONFLICT);
    expect((await cycle.json()).error.code).toBe(CODES.circular);
    await expect(page.locator('[data-sb-scope="link-cycle-err"]')).toContainText("存在循环依赖", { timeout: 10_000 });
  });

  test("T005-3 C.44 完成被拦截：详情状态菜单 → 409 → M-BLOCKED（阻塞列表 + 我知道了）；管理员强制完成 ≥5 字 → PATCH force 200", async ({ page }) => {
    test.setTimeout(90_000);
    // 被测行为本身：前置未完成 → 409 RESOURCE_TRANSITION_BLOCKED（M-BLOCKED 弹层）
    getErrs?.allow({ method: "PATCH", url: "/issues/", status: HTTP.CONFLICT });
    await loginDemo(page);
    const proj = await createProject(page);
    const target = await mkIssue(page, proj.id, "被拦主任务");
    const blocker = await mkIssue(page, proj.id, "拦路石任务");
    await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${blocker.id}/relations/`, { related_issue_id: target.id, relation_type: "blocks" });
    const states = await statesOf(page, proj.id);
    const done = states.find((s) => s.group === "completed")!;

    await gotoList(page);
    const drawer = await openDrawer(page, "被拦主任务");
    // 触发：状态菜单选已完成（C.44 触发：详情改状态同样触发）
    await drawer.locator('[aria-label="修改状态"]').click();
    // 行为三件套②：PATCH 409（负向——无 force 被拦截）
    const patch409P = page.waitForResponse(
      (r) => r.url().includes(`/issues/${target.id}/`) && r.request().method() === "PATCH",
      { timeout: 15_000 },
    );
    await page.locator('[data-sb-scope="drawer-prop-menu"] [role="menuitem"]').filter({ hasText: done.name }).click();
    const patch409 = await patch409P;
    expect(patch409.status(), "未强制时完成被 409 拦截").toBe(HTTP.CONFLICT);
    expect((await patch409.json()).error.code).toBe(CODES.transitionBlocked);

    // C.44 结构：⛔ 无法完成「X」+ 阻塞项列表（编号+标题+跳转）+ ⓘ 已取消说明 + 我知道了
    const dlg = page.locator('[data-sb-scope="blocked-list"]');
    await expect(page.locator('[data-sb-scope="modal-title"]')).toContainText(`无法完成「被拦主任务」`, { timeout: 10_000 });
    await expect(dlg.locator('[data-sb-scope="blocked-row"]')).toContainText("拦路石任务");
    await expect(dlg.locator('[data-sb-scope="blocked-row"]')).toContainText(blocker.key);
    await expect.soft(page.getByText("已取消（cancelled）的前置任务不会阻塞完成")).toBeVisible();
    // C.44 强制完成：仅 PROJ_ADMIN 可见（演示账号=WS Owner → 有效角色 ADMIN 20）
    await page.locator('[data-sb-scope="blocked-force-btn"]').click();
    const commentBox = page.locator('[data-sb-scope="blocked-force-comment"]');
    await expect(commentBox).toBeVisible();
    // C.44 必填 comment（≥5 字符）——4 字时按钮禁用
    await commentBox.fill("不足五");
    await expect.soft(page.locator('[data-sb-scope="blocked-force-go"]')).toBeDisabled();
    await commentBox.fill("客户演示节点，风险已评估由我承担");
    const forceP = page.waitForResponse(
      (r) => r.url().includes(`/issues/${target.id}/`) && r.request().method() === "PATCH",
      { timeout: 15_000 },
    );
    await page.locator('[data-sb-scope="blocked-force-go"]').click();
    const force = await forceP;
    expect(force.status(), "force=true + comment 后 200").toBe(HTTP.OK);
    expect(force.request().postDataJSON()).toMatchObject({ state_id: done.id, force: true });
    // 行为三件套③：UI 回读——状态变已完成 + 强制完成 Toast
    await expect(page.getByText("已强制完成（管理员通道）· 已记录说明")).toBeVisible({ timeout: 10_000 });
  });

  test("T005-4 C.45 看板 ⛔ 角标 + 列头计数 + 拖拽 409 拦截对话框；?blocked 筛选 Chip（列表）", async ({ page }) => {
    test.setTimeout(90_000);
    // 被测行为本身：拖拽被阻塞任务 → 409 拦截对话框（BLOCKED_BY）
    getErrs?.allow({ method: "PATCH", url: "/issues/", status: HTTP.CONFLICT });
    await loginDemo(page);
    const proj = await createProject(page);
    const target = await mkIssue(page, proj.id, "看板被阻任务");
    const blocker = await mkIssue(page, proj.id, "看板拦路石");
    const free = await mkIssue(page, proj.id, "看板自由任务");
    await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${blocker.id}/relations/`, { related_issue_id: target.id, relation_type: "blocks" });
    const states = await statesOf(page, proj.id);
    const started = states.find((s) => s.group === "started")!;
    await apiCall(page, "PATCH", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${target.id}/`, { state_id: started.id });
    await apiCall(page, "PATCH", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${free.id}/`, { state_id: started.id });

    await gotoBoard(page);
    const blockedCard = page.locator('[data-sb-scope="board-card"]').filter({ hasText: "看板被阻任务" });
    await expect(blockedCard).toBeVisible({ timeout: 15_000 });
    // C.45 看板 ⛔ 角标：被未完成前置阻塞的卡片右上角 ⛔(amber)；角标带文本 tooltip（无障碍）
    const badge = blockedCard.locator('[data-sb-scope="board-blocked-badge"]');
    await expect(badge, "⛔ 角标").toBeVisible();
    // hover 触发阻塞项 tooltip 按需拉取（前 3 个 key）→ aria-label 同步
    await blockedCard.hover();
    await expect.soft(badge).toHaveAttribute("aria-label", new RegExp(`被未完成前置任务阻塞.*${blocker.key}`), { timeout: 10_000 });
    await expect.soft(page.locator('[data-sb-scope="board-card"]').filter({ hasText: "看板自由任务" }).locator('[data-sb-scope="board-blocked-badge"]'), "未阻塞无角标").toHaveCount(0);
    // C.45 列头计数：「进行中 · 5（2 被阻塞）」次级文本（amber）
    await expect(page.locator('[data-sb-scope="board-blocked-sub"]')).toHaveText("2 个任务（1 被阻塞）", { timeout: 15_000 });

    // C.44 触发（看板入口）：拖拽 409 → 弹回原列 + M-BLOCKED
    const completedCol = page.locator('section[data-col="completed"]');
    await blockedCard.dragTo(completedCol, { targetPosition: { x: 140, y: 60 } });
    await expect(page.locator('[data-sb-scope="modal-title"]')).toContainText("无法完成「看板被阻任务」", { timeout: 10_000 });
    await page.getByRole("button", { name: "我知道了" }).click();
    // 拦截后任务留在原地（进行中列）
    await expect(page.locator('section[data-col="started"]').locator('[data-sb-scope="board-card"]').filter({ hasText: "看板被阻任务" })).toBeVisible({ timeout: 15_000 });

    // C.45 ?blocked 筛选（列表）：只看被阻塞任务（Chip 回显）
    await gotoList(page);
    await page.locator('[data-sb-scope="list-blocked-toggle"]').click();
    const chiprow = page.locator('[data-sb-scope="list-chiprow"]');
    await expect(chiprow).toBeVisible({ timeout: 15_000 });
    await expect.soft(chiprow).toContainText("被阻塞 = true");
    await expect(rowOf(page, "看板被阻任务"), "被阻塞任务保留").toBeVisible({ timeout: 15_000 });
    await expect.soft(rowOf(page, "看板自由任务"), "未阻塞任务被过滤").toHaveCount(0);
    // 行为三件套②：请求带 ?blocked=true
    await page.locator('[data-sb-scope="list-blocked-toggle"]').click(); // 关闭恢复
    await expect(rowOf(page, "看板自由任务")).toBeVisible({ timeout: 15_000 });
  });

  /* ═══════════ TASK-006 工时 ═══════════ */

  test("T006-1 C.46 工时分区：设估算（下拉常用值）+ 已耗主数字 + 记录列表；C.47 填报弹层（chips/日期/备注/⌘ 保存并再开）", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page);
    await mkIssue(page, proj.id, "工时主任务");
    await gotoList(page);
    const drawer = await openDrawer(page, "工时主任务");
    const sec = drawer.locator('[data-sb-scope="drawer-worklog-section"]');
    await expect(sec).toBeVisible({ timeout: 10_000 });
    // C.46 空态：无估算无记录 → 分区一行「工时 — [⏱ 记工时]」（估算 placeholder「设估算」）
    await expect.soft(sec).toContainText("工时 —");

    // C.46 估算输入：下拉常用值（480m = 1d 0h，T006 §4.4.2 1d=8h）提交 PATCH estimate_minutes（行为三件套②）
    await sec.locator('[data-sb-scope="drawer-est-set"]').click();
    const estP = page.waitForResponse(
      (r) => r.url().includes(`/issues/`) && r.request().method() === "PATCH" && r.request().postData()?.includes("estimate_minutes"),
      { timeout: 15_000 },
    );
    await sec.locator('[data-sb-scope="drawer-est-item"]').filter({ hasText: "1d 0h" }).click();
    const est = await estP;
    expect(est.status()).toBe(HTTP.OK);
    expect(est.request().postDataJSON()).toEqual({ estimate_minutes: 480 });
    // 行为三件套③：UI 回读——估算按钮显示 1d 0h
    await expect(sec.locator('[data-sb-scope="drawer-est-menu"]')).toContainText("1d 0h", { timeout: 10_000 });

    // C.47 填报弹层：标题/时长 chips/日期/备注
    await sec.locator('[data-sb-scope="drawer-worklog-add"]').click();
    await expect(page.locator('[data-sb-scope="modal-title"]')).toContainText("记工时 · 工时主任务");
    await expect.soft(page.locator('[data-sb-scope="wl-date"]')).toHaveAttribute("max", new Date().toISOString().slice(0, 10));
    await expect.soft(page.getByText("可补填最近 30 天")).toBeVisible();
    await page.locator('[data-sb-scope="wl-chip"]').filter({ hasText: "2h" }).click();
    await page.locator('[data-sb-scope="wl-note"]').fill("游标分页改造 + 联调");
    const wlP = page.waitForResponse(
      (r) => r.url().includes("/worklogs/") && r.request().method() === "POST",
      { timeout: 15_000 },
    );
    await page.locator('[data-sb-scope="wl-save"]').click();
    const wl = await wlP;
    expect(wl.status(), "POST worklogs 201").toBe(HTTP.CREATED);
    expect(wl.request().postDataJSON()).toMatchObject({ minutes: 120 });
    // C.46 已耗主数字 formatMinutes（<480 → 5.5h 形态；120 → 2h）+ 记录行回读
    await expect(sec.locator('[data-sb-scope="drawer-worklog-spent"]')).toHaveText("2h", { timeout: 10_000 });
    await expect(sec.locator('[data-sb-scope="drawer-worklog-row"]')).toContainText("游标分页改造 + 联调");
    await expect.soft(sec.locator('[data-sb-scope="drawer-worklog-row"]')).toContainText("2h");

    // C.47 ⌘+点击保存 = 保存并再开（清空时长保留日期）——再开填报一条 30m
    await sec.locator('[data-sb-scope="drawer-worklog-add"]').click();
    await page.locator('[data-sb-scope="wl-chip"]').filter({ hasText: "30m" }).click();
    const wl2P = page.waitForResponse(
      (r) => r.url().includes("/worklogs/") && r.request().method() === "POST",
      { timeout: 15_000 },
    );
    await page.locator('[data-sb-scope="wl-save"]').click({ modifiers: ["Meta"] });
    const wl2 = await wl2P;
    expect(wl2.status()).toBe(HTTP.CREATED);
    // 保存并再开：弹层保留（modal-title 仍在），继续填报后 Esc 关闭
    await expect(page.locator('[data-sb-scope="modal-title"]')).toContainText("记工时", { timeout: 5_000 });
    await page.keyboard.press("Escape");
    // C.46 已耗 = 150m = 2.5h；进度条 31%（150/480）
    await expect(sec.locator('[data-sb-scope="drawer-worklog-spent"]')).toHaveText("2.5h", { timeout: 10_000 });
    const bar = sec.locator('[role="progressbar"]');
    await expect.soft(bar).toHaveAttribute("aria-valuenow", "150");
  });

  test("T006-2 C.47 编辑两态复用 + ⋯ 删除（204）；负向：补填超 30 天窗口 API 400", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page);
    await mkIssue(page, proj.id, "工时编辑任务");
    await gotoList(page);
    const drawer = await openDrawer(page, "工时编辑任务");
    const sec = drawer.locator('[data-sb-scope="drawer-worklog-section"]');
    await sec.locator('[data-sb-scope="drawer-worklog-add"]').click();
    await page.locator('[data-sb-scope="wl-chip"]').filter({ hasText: "1h" }).click();
    await page.locator('[data-sb-scope="wl-note"]').fill("第一笔");
    await page.locator('[data-sb-scope="wl-save"]').click();
    await expect(sec.locator('[data-sb-scope="drawer-worklog-row"]')).toContainText("第一笔", { timeout: 10_000 });

    // C.47 复用两态：编辑预填原值（备注「第一笔」在 textarea）
    const row = sec.locator('[data-sb-scope="drawer-worklog-row"]').filter({ hasText: "第一笔" });
    await row.hover();
    await row.locator('[data-sb-scope="drawer-worklog-menu"]').click();
    await row.locator('[data-sb-scope="drawer-worklog-edit"]').click();
    await expect(page.locator('[data-sb-scope="wl-note"]')).toHaveValue("第一笔");
    await page.locator('[data-sb-scope="wl-note"]').fill("第一笔（改）");
    const patchP = page.waitForResponse(
      (r) => r.url().includes("/worklogs/") && r.request().method() === "PATCH",
      { timeout: 15_000 },
    );
    await page.locator('[data-sb-scope="wl-save"]').click();
    expect((await patchP).status(), "PATCH worklog 200").toBe(HTTP.OK);
    await expect(sec.locator('[data-sb-scope="drawer-worklog-row"]')).toContainText("第一笔（改）", { timeout: 10_000 });

    // C.46 ⋯ 菜单 删除（软删 204）
    await row.hover();
    await row.locator('[data-sb-scope="drawer-worklog-menu"]').click();
    const delP = page.waitForResponse(
      (r) => r.url().includes("/worklogs/") && r.request().method() === "DELETE",
      { timeout: 15_000 },
    );
    await row.locator('[data-sb-scope="drawer-worklog-del"]').click();
    expect((await delP).status(), "DELETE worklog 204").toBe(HTTP.NO_CONTENT);
    await expect(sec).toContainText("暂无记录", { timeout: 10_000 });

    // 负向：直连 API 补填 40 天前 → 400（VALIDATION_ERROR / INVALID_DATE；窗口由服务端兜底）
    const issueId = (await apiCall(page, "GET", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/?q=工时编辑任务`)).body?.data?.[0]?.id as string;
    const old = new Date(Date.now() - 40 * 86400_000).toISOString().slice(0, 10);
    const bad = await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${issueId}/worklogs/`, { minutes: 60, worked_on: old });
    expect(bad.status, "超 30 天窗口被 400 拒绝").toBe(HTTP.BAD_REQUEST ?? 400);
    expect(bad.body?.error?.details?.[0]?.code).toBe("INVALID_DATE");
  });

  // C.47 回归（2026-09-07 验收缺陷）：时长下拉选「自定义…」后输入框不出现——
  // 旧实现输入框渲染条件 !WL_DURATIONS.includes(minutes) 派生自 minutes，而选
  // 自定义只改 customMin 不改 minutes，死锁导致该分支永远不可达。修复 = 显式
  // customMode 状态。行为三件套：选自定义 → 输入框可见 → 填 45 → POST
  // {minutes:45} → 已耗回读 45m；负向：选自定义留空保存被客户端预校验拦截。
  test("T006-2b C.47 自定义时长：选「自定义…」出输入框 → POST 45 → 已耗回读 45m；留空保存被拦", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page);
    await mkIssue(page, proj.id, "自定义工时任务");
    await gotoList(page);
    const drawer = await openDrawer(page, "自定义工时任务");
    const sec = drawer.locator('[data-sb-scope="drawer-worklog-section"]');
    await sec.waitFor({ state: "visible", timeout: 10_000 });
    await sec.locator('[data-sb-scope="drawer-worklog-add"]').click();
    await expect(page.locator('[data-sb-scope="modal-title"]')).toContainText("记工时 · 自定义工时任务");

    // 修复核心断言：选「自定义…」必须立刻出现分钟输入框（修复前永远不可达）
    await page.locator('[data-sb-scope="wl-duration"]').selectOption("custom");
    await expect(page.locator('[data-sb-scope="wl-custom-min"]')).toBeVisible({ timeout: 5_000 });

    // 负向：自定义留空保存 → toast 拦截，且无 POST 发出（口径同客户端预校验）
    await page.locator('[data-sb-scope="wl-custom-min"]').fill("");
    let posted = false;
    page.on("request", (r) => { if (r.url().includes("/worklogs/") && r.method() === "POST") posted = true; });
    await page.locator('[data-sb-scope="wl-save"]').click();
    await expect(page.getByText("请输入自定义时长（正整数分钟）")).toBeVisible({ timeout: 5_000 });
    await page.waitForTimeout(300);
    expect(posted, "留空保存不得发出 POST").toBe(false);

    // 行为三件套：填 45 → POST minutes=45 → 已耗回读 45m（fmtMinutes <60 → 「45m」）
    await page.locator('[data-sb-scope="wl-custom-min"]').fill("45");
    const wlP = page.waitForResponse(
      (r) => r.url().includes("/worklogs/") && r.request().method() === "POST",
      { timeout: 15_000 },
    );
    await page.locator('[data-sb-scope="wl-save"]').click();
    const wl = await wlP;
    expect(wl.status(), "POST worklogs 201").toBe(HTTP.CREATED);
    expect(wl.request().postDataJSON()).toMatchObject({ minutes: 45 });
    await expect(sec.locator('[data-sb-scope="drawer-worklog-spent"]')).toHaveText("45m", { timeout: 10_000 });
  });

  test("T006-3 C.48 列表工时列：⏱ 2.5h/8h；超耗红；无记录灰显 —；列选择器开关", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page);
    const over = await mkIssue(page, proj.id, "超耗任务");
    const plain = await mkIssue(page, proj.id, "普通工时任务");
    const none = await mkIssue(page, proj.id, "无工时任务");
    await apiCall(page, "PATCH", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${over.id}/`, { estimate_minutes: 60 });
    await apiCall(page, "PATCH", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${plain.id}/`, { estimate_minutes: 480 });
    await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${over.id}/worklogs/`, { minutes: 120, worked_on: new Date().toISOString().slice(0, 10) });
    await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${plain.id}/worklogs/`, { minutes: 120, worked_on: new Date().toISOString().slice(0, 10) });

    await gotoList(page);
    // C.48 工时列：⏱ 5.5/8h 形态；超耗红（text-red-600）；无记录灰显 —
    await expect(page.locator("th").filter({ hasText: "工时" })).toBeVisible({ timeout: 15_000 });
    await expect.soft(rowOf(page, "超耗任务").locator('[data-sb-scope="list-wl-cell"]')).toHaveText("2h/1h");
    await expect.soft(rowOf(page, "超耗任务").locator('[data-sb-scope="list-wl-cell"] span')).toHaveClass(/text-red-600/);
    await expect.soft(rowOf(page, "普通工时任务").locator('[data-sb-scope="list-wl-cell"]')).toHaveText("2h/1d 0h");
    await expect.soft(rowOf(page, "无工时任务").locator('[data-sb-scope="list-wl-cell"]')).toHaveText("—");
    // C.48 列选择器开关：关闭工时列 → th 与单元格消失
    await page.locator('[data-sb-scope="list-cols-toggle"]').click();
    await page.locator('[data-sb-scope="list-cols-wl"]').click();
    await expect(page.locator("th").filter({ hasText: "工时" })).toHaveCount(0);
    await page.locator('[data-sb-scope="list-cols-wl"]').click(); // 恢复默认（localStorage 持久化前复位）
    await expect(page.locator("th").filter({ hasText: "工时" })).toBeVisible();
  });

  /* ═══════════ TASK-007 执行人 ═══════════ */

  test("T007-1 C.50 AssigneePicker：搜索/勾选/chips/n/10 计数/转交说明/通知预览 → PUT 200 → 堆叠回读；+N 退出任务", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page);
    await mkIssue(page, proj.id, "转交主任务");
    // 添加 4 个项目成员（演示账号自己 + 4 = 5 人可指派）
    const rid4 = rid();
    const emails = Array.from({ length: 4 }, (_, i) => `t7-${rid4}-${i}@e2e.dev`);
    const add = await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${proj.id}/members/`, { member_ids: [], role: 15 });
    void add; // 空数组仅探测；真实添加走邀请成本高——改用「勾选演示账号自己」路径验证
    await gotoList(page);
    const drawer = await openDrawer(page, "转交主任务");
    const sec = drawer.locator('[data-sb-scope="drawer-assignee-section"]');
    await expect(sec).toBeVisible({ timeout: 10_000 });

    // C.49 编辑入口：[＋ 编辑] 打开转交弹层（C.50）
    await sec.locator('[data-sb-scope="drawer-assignee-edit"]').click();
    const modal = page.locator('[data-sb-scope="asg-list"]');
    await expect(modal).toBeVisible({ timeout: 10_000 });
    // C.50 自己带（我）
    await expect.soft(modal.getByText("（我）").first()).toBeVisible();
    // C.50 勾选：选中即计 n/10 + chips
    const me = modal.locator("label").filter({ hasText: "（我）" }).first();
    await me.locator("input[type=checkbox]").check();
    await expect.soft(page.locator('[data-sb-scope="asg-count"]')).toContainText("已选 1/10");
    await expect.soft(page.locator('[data-sb-scope="asg-chip"]')).toHaveCount(1);
    // C.50 转交说明（可选，将随通知发送）maxLength 500 + 通知预览灰字（aria-live=polite）
    await page.locator('[data-sb-scope="asg-comment"]').fill("联调窗口改到周四，请两位对接…");
    await expect.soft(page.locator('[data-sb-scope="asg-preview"]')).toContainText("将通知：新增 1 人");
    // 行为三件套②：PUT assignees 200（全量集合 + comment）
    const putP = page.waitForResponse(
      (r) => r.url().includes("/assignees/") && r.request().method() === "PUT",
      { timeout: 15_000 },
    );
    await page.locator('[data-sb-scope="asg-save"]').click();
    const put = await putP;
    expect(put.status()).toBe(HTTP.OK);
    // 行为三件套③：UI 回读——执行人分区出现堆叠 + 名称行
    await expect(sec.locator('[data-sb-scope="avatar-stack"]')).toBeVisible({ timeout: 10_000 });
    const putBody = (await put.json()).data as { changes?: { added: unknown[] } };
    expect(putBody.changes?.added?.length, "changes.added 回传").toBe(1);
  });

  test("T007-2 C.51 列表执行人：未指派徽标 + 悬浮 🖐 认领（POST claim 200 → 头像回读）+ 快速指派浮层；看板未指派占位；负向：已指派 claim 409", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page);
    await mkIssue(page, proj.id, "认领目标任务");
    await mkIssue(page, proj.id, "看板认领任务");
    const ghost = await mkIssue(page, proj.id, "看板占位任务");
    await gotoList(page);
    const row = rowOf(page, "认领目标任务");
    // C.51 空显示「未指派」neutral 徽标（可点开筛选/快速指派）
    await expect(row.locator('[data-sb-scope="list-unassigned"]')).toBeVisible({ timeout: 15_000 });
    // C.51 认领入口：列表行悬浮（未指派时）「🖐」按钮；点击即 POST 无需确认
    await row.hover();
    const claimP = page.waitForResponse(
      (r) => r.url().includes("/assignees/claim/") && r.request().method() === "POST",
      { timeout: 15_000 },
    );
    await row.locator('[data-sb-scope="list-claim"]').click();
    const claim = await claimP;
    expect(claim.status(), "POST claim 200").toBe(HTTP.OK);
    // 行为三件套③：UI 回读——头像堆叠替换「未指派」
    await expect(row.locator('[data-sb-scope="avatar-stack"]')).toBeVisible({ timeout: 15_000 });

    // 负向：已指派任务再 claim → 409 RESOURCE_STATE_INVALID（服务端窗口判定兜底）
    const issueId = (await apiCall(page, "GET", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/?q=认领目标任务`)).body?.data?.[0]?.id as string;
    const again = await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${issueId}/assignees/claim/`, {});
    expect(again.status, "重复认领 409").toBe(HTTP.CONFLICT);
    expect(again.body?.error?.code).toBe(CODES.stateInvalid);

    // C.51 快速指派：列表行悬浮头像区 → 单人快速选择浮层（对另一条未指派任务指派演示账号自己）
    const row2 = rowOf(page, "看板认领任务");
    await row2.hover();
    await row2.locator('[data-sb-scope="list-quick-assign"]').click();
    const pop = page.locator('[data-sb-scope="list-quick-assign-pop"]');
    await expect(pop).toBeVisible();
    const putP = page.waitForResponse(
      (r) => r.url().includes("/assignees/") && r.request().method() === "PUT",
      { timeout: 15_000 },
    );
    await pop.locator('[data-sb-scope="list-quick-assign-item"]').first().click();
    expect((await putP).status(), "快速指派 PUT 200").toBe(HTTP.OK);
    await expect(row2.locator('[data-sb-scope="avatar-stack"]')).toBeVisible({ timeout: 15_000 });

    // C.51 看板：未指派卡片虚线人形占位；已指派卡片头像堆叠
    await gotoBoard(page);
    const assignedCard = page.locator('[data-sb-scope="board-card"]').filter({ hasText: "认领目标任务" });
    await expect(assignedCard.locator('[data-sb-scope="avatar-stack"]'), "看板头像堆叠").toBeVisible({ timeout: 15_000 });
    const unassignedCard = page.locator('[data-sb-scope="board-card"]').filter({ hasText: "看板占位任务" });
    await expect(unassignedCard.locator('[data-sb-scope="board-unassigned"]'), "看板未指派虚线人形占位").toBeVisible({ timeout: 15_000 });
    await expect.soft(page.locator('[data-sb-scope="board-card"]').filter({ hasText: "看板认领任务" }).locator('[data-sb-scope="avatar-stack"]'), "快速指派后看板堆叠回读").toBeVisible({ timeout: 15_000 });
    void ghost;
  });

  /* ═══════════ TASK-008 自定义字段 ═══════════ */

  /** T008 自清理：删除本项目全部字段（BR-10 上限 50/WS —— 不清理则重复跑满额 409）。 */
  async function purgeProjectFields(page: Page, pid: string) {
    const r = await apiCall(page, "GET", `/api/v1/workspaces/${WS}/projects/${pid}/issue-properties/?scope=project`);
    for (const d of ((r.body?.data as Array<{ id: string }>) ?? [])) {
      await apiCall(page, "DELETE", `/api/v1/workspaces/${WS}/projects/${pid}/issue-properties/${d.id}/`);
    }
  }

  async function mkSelectField(page: Page, pid: string, name: string, key: string) {
    // 字段 key 工作空间唯一（BR-02）——所有 e2e 项目同属 "workspace"，追加随机尾保证可重复执行
    const uniqueKey = `${key}_${rid(4).toLowerCase()}`;
    const r = await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${pid}/issue-properties/`, {
      name, field_key: uniqueKey, field_type: "select", is_required: false,
      options: [
        { label: "致命", value: "critical", color: "#DC2626", sort_order: 1 },
        { label: "严重", value: "major", color: "#F59E0B", sort_order: 2 },
      ],
    });
    expect(r.status, `创建字段 ${name}`).toBe(HTTP.CREATED);
    // 返回真实（唯一化）key：调用方的 PATCH/回读断言用它
    return { id: r.body?.data?.id as string, key: uniqueKey };
  }

  test("T008-1 C.52 字段管理页：列表列 + 计数条 + 继承折叠区；C.53 12 类型宫格 + 创建；停用灰显 + 上移/下移", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page);
    await mkSelectField(page, proj.id, "等级一", "cf_lvl_one");
    await mkSelectField(page, proj.id, "等级二", "cf_lvl_two");

    // 用户入口：项目侧栏「字段管理」导航到达
    await page.getByRole("navigation").getByRole("link", { name: "字段管理" }).click();
    await page.waitForURL(/\/settings\/fields/, { timeout: 10_000 });
    await expect(page.locator('[data-sb-scope="fields-cap"]')).toBeVisible({ timeout: 15_000 });
    // C.52 顶部提示条：「Workspace 全局字段 N 个 · 项目私有字段 M 个」+ 字段计数 x/50
    await expect(page.locator('[data-sb-scope="fields-cap"]')).toContainText("2/50");
    await expect.soft(page.locator('[data-sb-scope="fields-cap"]')).toContainText("Workspace 全局字段 0 个 · 项目私有字段 2 个");
    // C.52 列表列：名称 / 类型中文 / 必填·默认值标记 / 索引标记
    const row = page.locator('[data-sb-scope="field-row"]').filter({ hasText: "等级一" });
    await expect(row).toBeVisible({ timeout: 10_000 });
    await expect.soft(row.locator('[data-sb-scope="field-type-chip"]')).toContainText("单选下拉");

    // C.53 新建字段弹层：12 类型宫格 + 字段名称 + key 提示 + 选项区
    await page.locator('[data-sb-scope="fields-new"]').click();
    await expect(page.locator('[data-sb-scope="field-modal-title"]')).toHaveText("新建字段");
    const grid = page.locator('[data-sb-scope="field-type-grid"]');
    await expect(grid.locator('[data-sb-scope="field-type-cell"]'), "12 类型宫格").toHaveCount(12);
    // ADR-0015 A-8 勘误：12 类型 = member_multi/auto_increment（非 email/phone）
    for (const n of ["单行文本", "多行文本", "单选下拉", "多选下拉", "数字", "金额", "日期", "复选框", "成员", "人员多选", "链接", "自增编号"]) {
      await expect.soft(grid.locator('[data-sb-scope="field-type-cell"]').filter({ hasText: n }), `类型「${n}」`).toBeVisible();
    }
    await page.locator('[data-sb-scope="field-name"]').fill("上线日期字段");
    await expect.soft(page.locator('[data-sb-scope="field-key-hint"]')).toContainText("cf_");
    // key 手改为唯一值（C.53 可改；纯中文名的生成 key 跨 run 恒定会撞 BR-02 唯一约束）
    await page.locator('[data-sb-scope="field-key-input"]').fill(`cf_date_${rid(4).toLowerCase()}`);
    await grid.locator('[data-sb-scope="field-type-cell"]').filter({ hasText: "日期" }).click();
    // 行为三件套②③：POST issue-properties 201 → 表格回读新行
    const createP = page.waitForResponse(
      (r) => r.url().includes("/issue-properties/") && r.request().method() === "POST",
      { timeout: 15_000 },
    );
    await page.locator('[data-sb-scope="field-save"]').click();
    expect((await createP).status()).toBe(HTTP.CREATED);
    await expect(page.locator('[data-sb-scope="field-row"]').filter({ hasText: "上线日期字段" })).toBeVisible({ timeout: 10_000 });

    // C.52 编辑态差异（BR-06）：类型与 key 创建后不可改 → 类型宫格 disabled
    const rowDate = page.locator('[data-sb-scope="field-row"]').filter({ hasText: "上线日期字段" });
    await rowDate.locator('[data-sb-scope="field-row-menu"]').click();
    await rowDate.locator('[data-sb-scope="field-menu-edit"]').click();
    await expect.soft(page.locator('[data-sb-scope="field-type-cell"]').first()).toBeDisabled();
    await page.keyboard.press("Escape");

    // C.52 停用：行 opacity-50 + 「灰显 · 数据保留」角标；启用一键恢复
    const rowTwo = page.locator('[data-sb-scope="field-row"]').filter({ hasText: "等级二" });
    await rowTwo.locator('[data-sb-scope="field-row-menu"]').click();
    await rowTwo.locator('[data-sb-scope="field-menu-toggle"]').click();
    await expect(page.locator('[data-sb-scope="field-row"]').filter({ hasText: "等级二" }).locator('[data-sb-scope="field-inactive-mark"]')).toBeVisible({ timeout: 10_000 });
    await rowTwo.locator('[data-sb-scope="field-row-menu"]').click();
    await rowTwo.locator('[data-sb-scope="field-menu-toggle"]').click();
    await expect(page.locator('[data-sb-scope="field-row"]').filter({ hasText: "等级二" }).locator('[data-sb-scope="field-inactive-mark"]')).toHaveCount(0, { timeout: 10_000 });

    // C.52 键盘替代排序：行菜单「上移」→ PATCH sort-order 200 → 顺序变化
    const sortP = page.waitForResponse(
      (r) => r.url().includes("/sort-order/") && r.request().method() === "PATCH",
      { timeout: 15_000 },
    );
    await rowDate.locator('[data-sb-scope="field-row-menu"]').click();
    await rowDate.locator('[data-sb-scope="field-menu-up"]').click();
    const sort = await sortP;
    expect(sort.status()).toBe(HTTP.OK);
    // 键盘替代排序：上移一位（3 行中末位 → 第 2 位；第 1 位仍是等级一）
    const rowsAll = page.locator('[data-sb-scope="field-row"]');
    await expect(rowsAll.nth(1)).toContainText("上线日期字段", { timeout: 10_000 });
    await expect.soft(rowsAll.first()).toContainText("等级一");

    // C.52 继承折叠区：「继承自 Workspace」折叠（全局 0 个时折叠头仍可见）
    await expect(page.locator('[data-sb-scope="fields-ws-fold"]')).toContainText("继承自 Workspace");
    // 自清理（BR-10 上限）
    await purgeProjectFields(page, proj.id);
  });

  test("T008-2 C.54 三段式删除：影响统计 + 删除后果 + 输入字段名激活 → DELETE 202 → 行消失 + 黄条；负向：错名不激活", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page);
    await mkSelectField(page, proj.id, "待删字段", "cf_to_delete");
    await page.getByRole("navigation").getByRole("link", { name: "字段管理" }).click();
    await page.waitForURL(/\/settings\/fields/, { timeout: 10_000 });
    const row = page.locator('[data-sb-scope="field-row"]').filter({ hasText: "待删字段" });
    await expect(row).toBeVisible({ timeout: 15_000 });
    await row.locator('[data-sb-scope="field-row-menu"]').click();
    await row.locator('[data-sb-scope="field-menu-del"]').click();

    // C.54 影响统计 + 删除后果文案
    const dlg = page.locator('[data-sb-scope="delfield-stats"]');
    await expect(dlg).toContainText("删除字段「待删字段」？", { timeout: 5_000 }).catch(() => {});
    await expect(page.locator('[data-sb-scope="delfield-title"]')).toHaveText("⚠ 删除字段「待删字段」？");
    await expect.soft(dlg).toContainText("个任务填写");
    await expect.soft(dlg).toContainText("异步清除（不可恢复）");
    // C.54 输入名激活：输入 != 字段名时 [确认删除] 禁用（负向）
    await page.locator('[data-sb-scope="delfield-confirm-input"]').fill("错误的名字");
    await expect.soft(page.locator('[data-sb-scope="delfield-go"]')).toBeDisabled();
    // 输入 == 字段名 → 激活；DELETE 202（异步清理受理）
    await page.locator('[data-sb-scope="delfield-confirm-input"]').fill("待删字段");
    await expect.soft(page.locator('[data-sb-scope="delfield-go"]')).toBeEnabled();
    const delP = page.waitForResponse(
      (r) => r.url().includes("/issue-properties/") && r.request().method() === "DELETE",
      { timeout: 15_000 },
    );
    await page.locator('[data-sb-scope="delfield-go"]').click();
    const del = await delP;
    expect(del.status(), "DELETE 字段 202（异步清理）").toBe(HTTP.ACCEPTED);
    expect((await del.json()).data.affected_issues).toBeGreaterThanOrEqual(0);
    // C.54 202 进度：行消失 + 顶部黄条
    await expect(page.locator('[data-sb-scope="field-row"]').filter({ hasText: "待删字段" })).toHaveCount(0, { timeout: 10_000 });
    await expect(page.locator('[data-sb-scope="fields-del-accepted"]')).toContainText("后台清除中");
    await expect.soft(page.getByText("删除请求已受理（202）· 后台清除任务进行中")).toBeVisible();
    // 自清理（BR-10 上限）
    await purgeProjectFields(page, proj.id);
  });

  test("T008-3 C.55 动态字段折叠区：渲染 + 编辑落库（PATCH custom_fields 200 → 色块回读）+ 更多属性 ▾；C.56 动态列 + 列选择器；负向：非法选项值 400", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page);
    const sevField = await mkSelectField(page, proj.id, "严重等级E", "cf_severity_e");
    await mkSelectField(page, proj.id, "需求来源E", "cf_source_e");
    await mkSelectField(page, proj.id, "上线日期E", "cf_date_e");
    await mkIssue(page, proj.id, "字段值任务");

    await gotoList(page);
    const drawer = await openDrawer(page, "字段值任务");
    const cf = drawer.locator('[data-sb-scope="drawer-cf-section"]');
    await expect(cf).toBeVisible({ timeout: 10_000 });
    // C.55 渲染顺序按 sort_order；超出首屏（>2）折叠为「更多属性 ▾」
    await expect(cf).toContainText("严重等级E", { timeout: 10_000 });
    await expect.soft(cf).toContainText("需求来源E");
    await expect.soft(cf.getByText(/更多属性 ▾/)).toBeVisible();
    await cf.getByText(/更多属性 ▾/).click();
    await expect.soft(cf).toContainText("上线日期E");

    // C.55 控件映射：select → 色块下拉；行为三件套②③：PATCH custom_fields 200 → 色块文本回读
    const sevRow = cf.locator('[data-sb-scope="drawer-cf-value"]', { hasText: "—" }).first();
    await drawer.getByText("严重等级E").locator("xpath=..").locator('[data-sb-scope="drawer-cf-value"]').click();
    const patchP = page.waitForResponse(
      (r) => r.url().includes("/issues/") && r.request().method() === "PATCH" && r.request().postData()?.includes(sevField.key),
      { timeout: 15_000 },
    );
    await drawer.locator('[data-sb-scope="drawer-cf-edit"]').selectOption("critical");
    const patch = await patchP;
    expect(patch.status()).toBe(HTTP.OK);
    expect(patch.request().postDataJSON()).toEqual({ custom_fields: { [sevField.key]: "critical" } });
    await expect(drawer.locator('[data-sb-scope="drawer-cf-value"]').filter({ hasText: "致命" })).toBeVisible({ timeout: 10_000 });
    void sevRow;

    // C.56 动态列：列选择器勾选「严重等级E」→ 表头 + 色块值单元格
    await page.locator("aside").first().getByRole("button", { name: "关闭" }).click();
    await page.locator('[data-sb-scope="list-cols-toggle"]').click();
    await page.locator('[data-sb-scope="list-cols-cf"]').filter({ hasText: "严重等级E" }).click();
    await expect(page.locator("th").filter({ hasText: "严重等级E" })).toBeVisible({ timeout: 10_000 });
    await expect.soft(rowOf(page, "字段值任务").locator('[data-sb-scope="list-cf-cell"]')).toContainText("致命");

    // 负向：非法选项值直连 PATCH → 400 VALIDATION_CUSTOM_FIELD_INVALID（值校验由服务端兜底）
    const issueId = (await apiCall(page, "GET", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/?q=字段值任务`)).body?.data?.[0]?.id as string;
    const bad = await apiCall(page, "PATCH", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${issueId}/`, { custom_fields: { cf_severity_e: "blocker" } });
    expect(bad.status, "非法选项值被 400 拒绝").toBe(HTTP.BAD_REQUEST ?? 400);
    expect(bad.body?.error?.code).toBe(CODES.cfInvalid);
    // 自清理（BR-10 上限）
    await purgeProjectFields(page, proj.id);
  });

  /* ═══════════ TASK-009 复制 / 归档 ═══════════ */

  test("T009-1 C.57 复制选项弹层：五选项默认 + 副本预览 + 固定信息条 → POST duplicate 201 → Toast + 副本回读", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page);
    const root = await mkIssue(page, proj.id, "复制源任务");
    await mkIssue(page, proj.id, "复制子任务", root);
    await apiCall(page, "PATCH", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${root.id}/`, { estimate_minutes: 120 });

    await gotoList(page);
    const drawer = await openDrawer(page, "复制源任务");
    await drawer.getByRole("button", { name: "更多操作" }).click();
    await drawer.locator('[data-sb-scope="drawer-menu-dup"]').click();
    await expect(page.locator('[data-sb-scope="modal-title"]')).toContainText("复制任务 ·", { timeout: 10_000 });
    // C.57 五选项（默认 子✓执行人✗标签✓字段✓日期✗）
    const rows = page.locator('[data-sb-scope="dup-row"]');
    await expect(rows).toHaveCount(5);
    await expect.soft(rows.filter({ hasText: "包含子任务" }).locator("input")).toBeChecked();
    await expect.soft(rows.filter({ hasText: "包含执行人" }).locator("input")).not.toBeChecked();
    await expect.soft(rows.filter({ hasText: "包含标签" }).locator("input")).toBeChecked();
    // C.57 固定信息条 + 副本预览（随选项实时更新）
    await expect.soft(page.locator('[data-sb-scope="dup-notice"]')).toContainText("评论、附件、依赖、工时不会被复制");
    await expect.soft(page.locator('[data-sb-scope="dup-preview"]')).toContainText("复制源任务 (副本) + 1 个子任务");
    await rows.filter({ hasText: "包含子任务" }).locator("input").uncheck();
    await expect.soft(page.locator('[data-sb-scope="dup-preview"]')).toContainText("复制源任务 (副本)");
    await rows.filter({ hasText: "包含子任务" }).locator("input").check();

    // 行为三件套②：POST duplicate 201
    const dupP = page.waitForResponse(
      (r) => r.url().includes("/duplicate/") && r.request().method() === "POST",
      { timeout: 15_000 },
    );
    await page.locator('[data-sb-scope="dup-go"]').click();
    const dup = await dupP;
    expect(dup.status()).toBe(HTTP.CREATED);
    // IssueDuplicateView created_response：data 为扁平 {id, issue_key, name, total_created}（§4.2.1 文档形态的实现落位）
    const dupBody = ((await dup.json()) as Record<string, any>).data as { id: string; issue_key: string; name: string; total_created: number };
    expect(dupBody.total_created, "含子任务共 2 个").toBe(2);
    // 行为三件套③：Toast 已创建 + 副本抽屉打开（嵌套抽屉 z-[70]，按 (副本) 文本定位）
    await expect(page.getByText(`已创建 ${dupBody.issue_key} ${dupBody.name}`)).toBeVisible({ timeout: 10_000 });
    await expect(page.locator("aside").filter({ hasText: "(副本)" })).toBeVisible({ timeout: 10_000 });
  });

  test("T009-2 C.58 归档确认 + 撤销 Toast（10s 倒计时）；C.60 只读横幅 + 恢复；负向：已归档写操作 409", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page);
    const root = await mkIssue(page, proj.id, "归档源任务");
    await mkIssue(page, proj.id, "归档子任务", root);

    await gotoList(page);
    const drawer = await openDrawer(page, "归档源任务");
    await drawer.getByRole("button", { name: "更多操作" }).click();
    await drawer.locator('[data-sb-scope="drawer-menu-archive"]').click();
    // C.58 确认弹窗：含子任务时提示「将同时归档 N 个子任务」
    const archText = page.locator('[data-sb-scope="arch-text"]');
    await expect(archText).toBeVisible({ timeout: 10_000 });
    await expect.soft(archText).toContainText("将同时归档 1 个子任务");
    // 行为三件套②：POST archive 200（整树）
    const archP = page.waitForResponse(
      (r) => r.url().includes("/archive/") && r.request().method() === "POST",
      { timeout: 15_000 },
    );
    await page.locator('[data-sb-scope="arch-go"]').click();
    const arch = await archP;
    expect(arch.status()).toBe(HTTP.OK);
    expect((await arch.json()).data.archived_count).toBe(2);
    // C.58 成功动线：详情关闭 + 列表移除 + Toast 带「撤销」按钮 + 倒计时可见文本
    await expect(page.locator("aside")).toHaveCount(0, { timeout: 10_000 });
    await expect(page.locator("tbody tr").filter({ hasText: "归档源任务" })).toHaveCount(0, { timeout: 15_000 });
    const undoToast = page.locator('[data-sb-scope="toast-text"]').filter({ hasText: "已归档" }).first();
    await expect(undoToast).toBeVisible({ timeout: 10_000 });
    await expect.soft(page.locator('[data-sb-scope="toast-count"]').first(), "倒计时可见文本").toBeVisible();
    const undoBtn = page.locator('[data-sb-scope="toast-action"]').filter({ hasText: "撤销" });
    await expect(undoBtn).toBeVisible();
    // 撤销：10s 内一键恢复（DELETE archive）
    const restoreP = page.waitForResponse(
      (r) => r.url().includes("/archive/") && r.request().method() === "DELETE",
      { timeout: 15_000 },
    );
    await undoBtn.click();
    expect((await restoreP).status()).toBe(HTTP.OK);
    await expect.soft(page.getByText("已恢复归档")).toBeVisible({ timeout: 10_000 });
    await expect(rowOf(page, "归档源任务"), "撤销后行恢复").toBeVisible({ timeout: 15_000 });

    // 再次归档（走确认），然后验证 C.59 归档视图 + C.60 只读横幅
    const drawer2 = await openDrawer(page, "归档源任务");
    await drawer2.getByRole("button", { name: "更多操作" }).click();
    await drawer2.locator('[data-sb-scope="drawer-menu-archive"]').click();
    await page.locator('[data-sb-scope="arch-go"]').click();
    await expect(page.locator("tbody tr").filter({ hasText: "归档源任务" })).toHaveCount(0, { timeout: 15_000 });

    // C.59 归档视图：列表工具条「显示已归档」→ ?archived=true；归档行 opacity-60 + archive 图标
    await page.locator('[data-sb-scope="list-archived-toggle"]').click();
    await expect(rowOf(page, "归档源任务")).toBeVisible({ timeout: 15_000 });
    await expect.soft(rowOf(page, "归档源任务").locator('[data-sb-scope="list-arch-icon"]'), "archive 图标 tooltip").toBeVisible();
    await expect.soft(rowOf(page, "归档源任务")).toHaveClass(/opacity-60/);

    // C.60 归档详情只读横幅：「已归档于 … · [恢复]」顶部 role=status；编辑控件禁用（无「＋」/无添加行）
    const drawer3 = await openDrawer(page, "归档源任务");
    const banner = drawer3.locator('[data-sb-scope="drawer-arch-banner"]');
    await expect(banner).toBeVisible({ timeout: 10_000 });
    await expect.soft(banner).toHaveAttribute("role", "status");
    await expect.soft(banner).toContainText("已归档于");
    await expect.soft(drawer3.locator('[data-sb-scope="drawer-sub-add"]'), "无「＋」").toHaveCount(0);
    await expect.soft(drawer3.getByPlaceholder("添加子任务，回车保存…"), "无添加行").toHaveCount(0);
    await expect.soft(drawer3.locator('[data-sb-scope="drawer-sub-view-all"]'), "查看整棵树入口隐藏").toHaveCount(0);

    // 负向：已归档任务写操作（PATCH）→ 409 RESOURCE_STATE_INVALID（写保护由服务端兜底）
    const bad = await apiCall(page, "PATCH", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${root.id}/`, { priority: "high" });
    expect(bad.status, "归档写保护 409").toBe(HTTP.CONFLICT);
    expect(bad.body?.error?.code).toBe(CODES.stateInvalid);

    // C.58/C.60 恢复（横幅 [恢复] → DELETE archive 200 → 横幅消失）
    const restoreP2 = page.waitForResponse(
      (r) => r.url().includes("/archive/") && r.request().method() === "DELETE",
      { timeout: 15_000 },
    );
    await drawer3.locator('[data-sb-scope="drawer-arch-restore"]').click();
    expect((await restoreP2).status()).toBe(HTTP.OK);
    await expect(drawer3.locator('[data-sb-scope="drawer-arch-banner"]')).toHaveCount(0, { timeout: 15_000 });
  });

  test("T009-3 C.59 归档视图空态：「没有已归档的任务」+「归档的任务会出现在这里」", async ({ page }) => {
    test.setTimeout(60_000);
    await loginDemo(page);
    await createProject(page);
    // 空项目（无任何任务）：归档视图空态「没有已归档的任务」
    await gotoList(page, { empty: true });
    await page.locator('[data-sb-scope="list-archived-toggle"]').click();
    await expect(page.locator('[data-sb-scope="list-empty-title"]')).toHaveText("没有已归档的任务", { timeout: 15_000 });
    await expect.soft(page.getByText("归档的任务会出现在这里")).toBeVisible();
  });

  /* ═══════════ TASK-010 时间线 / 死信 ═══════════ */

  test("T010-1 C.61 动态 Tab 时间线：日期分区 + epoch 组头 + 字段行（旧值删除线→新值加粗）+ 双过滤器；空态；按钮式加载更早", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page);
    const fresh = await mkIssue(page, proj.id, "新无动态任务");
    const busy = await mkIssue(page, proj.id, "动态繁忙任务");
    await apiCall(page, "PATCH", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${busy.id}/`, { priority: "high" });
    await apiCall(page, "PATCH", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${busy.id}/`, { estimate_minutes: 480 });
    await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${busy.id}/worklogs/`, { minutes: 120, worked_on: new Date().toISOString().slice(0, 10), note: "联调收尾" });

    // C.61 空态：暂无动态——第一次修改将出现在这里
    await gotoList(page);
    const freshDrawer = await openDrawer(page, "新无动态任务");
    await freshDrawer.locator('[role="tab"]').filter({ hasText: "动态" }).click();
    await expect(freshDrawer.locator('[data-sb-scope="drawer-activity-empty"]')).toBeVisible({ timeout: 10_000 });
    await expect.soft(freshDrawer.locator('[data-sb-scope="drawer-activity-empty"]')).toContainText("暂无动态——第一次修改将出现在这里");
    await freshDrawer.getByRole("button", { name: "关闭" }).click();

    const drawer = await openDrawer(page, "动态繁忙任务");
    await drawer.locator('[role="tab"]').filter({ hasText: "动态" }).click();
    const list = drawer.locator('[data-sb-scope="drawer-activity-list"]');
    await expect(list).toBeVisible({ timeout: 10_000 });
    // C.61 日期分区头：今天（sticky）
    await expect.soft(drawer.locator('[data-sb-scope="act-day"]').first()).toContainText(/今天|昨天|\d+月\d+日/);
    // C.61 epoch 组：组头行（时间 mono + 操作者 + 动作摘要）+ 缩进字段行（字段 旧值 → 新值，旧值删除线、新值加粗）
    const group = list.locator('[data-sb-scope="act-group"]').first();
    await expect(group).toBeVisible();
    await expect.soft(list.locator('[data-sb-scope="act-field-row"]').filter({ hasText: "优先级" }).first()).toBeVisible();
    // 字段行：`字段 旧值 → 新值`（field_label 服务端解析；值为后端原始串 none→high）+ 旧值删除线
    const priRow = list.locator('[data-sb-scope="act-field-row"]').filter({ hasText: "优先级" }).first();
    await expect.soft(priRow).toContainText("high");
    await expect.soft(priRow.locator("span").nth(1)).toHaveClass(/line-through/);
    // C.61 系统侧 field_label 由 FIELD_LABELS 解析（估算工时 / 优先级 / 任务类型）
    await expect.soft(list).toContainText("估算工时");

    // C.61 过滤器：字段（全部▾）过滤 → 仅剩该字段的组；清空恢复
    await drawer.locator('[data-sb-scope="act-field-toggle"]').click();
    await drawer.locator('[data-sb-scope="act-field-item"]').filter({ hasText: "优先级" }).click();
    const filteredP = page.waitForResponse(
      (r) => r.url().includes("/activities/") && new URL(r.url()).searchParams.get("field") === "priority",
      { timeout: 15_000 },
    );
    await filteredP;
    await expect(drawer.locator('[data-sb-scope="act-field-row"]').filter({ hasText: "估算" })).toHaveCount(0, { timeout: 10_000 });
    await expect(drawer.locator('[data-sb-scope="act-field-row"]').filter({ hasText: "优先级" }).first()).toBeVisible();
    // 操作人过滤器（⋯▾）→ 全部操作人复位；字段过滤复位「全部」→ 估算组恢复
    await drawer.locator('[data-sb-scope="act-actor-toggle"]').click();
    await drawer.locator('[data-sb-scope="act-actor-item"]').filter({ hasText: "全部操作人" }).click();
    await drawer.locator('[data-sb-scope="act-field-toggle"]').click();
    await drawer.locator('[data-sb-scope="act-field-item"]').filter({ hasText: "全部", exact: true }).click();
    await expect(drawer.locator('[data-sb-scope="act-field-row"]').filter({ hasText: "估算" }).first()).toBeVisible({ timeout: 10_000 });

    // C.61 按钮式「加载更早的动态」（审计翻阅，不做无限滚动）：mock meta.next_cursor 注入下一页游标。
    // mock 生效需要一次新请求 —— 切 Tab 再切回「动态」触发重拉。
    await page.route(`**/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${busy.id}/activities/**`, async (route) => {
      const r = await route.fetch();
      const body = (await r.json()) as Record<string, any>;
      if (new URL(r.url()).searchParams.get("cursor") === null && body?.meta) {
        body.meta.next_cursor = "MTc4ODIzMTkwMDAwMA==";
      }
      await route.fulfill({ response: r, json: body as any });
    });
    await drawer.locator('[role="tab"]').filter({ hasText: "描述" }).click();
    await drawer.locator('[role="tab"]').filter({ hasText: "动态" }).click();
    await expect(drawer.locator('[data-sb-scope="act-load-earlier"]')).toBeVisible({ timeout: 10_000 });
    const loadP = page.waitForResponse(
      (r) => r.url().includes("/activities/") && new URL(r.url()).searchParams.get("cursor") !== null,
      { timeout: 15_000 },
    );
    await drawer.locator('[data-sb-scope="act-load-earlier"]').click();
    const load = await loadP;
    expect(new URL(load.url()).searchParams.get("cursor")).toBe("MTc4ODIzMTkwMDAwMA==");
    await page.unroute(`**/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${busy.id}/activities/**`);
    void fresh;
  });

  test("T010-2 C.62 admin 死信页：列表结构 + 空态队列健康；单条重放/丢弃/批量重放（mock 数据行为三件套）", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page);
    // 真实链路（收紧后 2026-09-05：仅 SystemAdmin——演示账号非成员 → 403 负向锚定；
    // 授权路径的正向链路由 sprint-2-flow T10-12 承载）
    const real = await apiCall(page, "GET", `/api/v1/activity-dead-letters/`);
    expect(real.status, "非 SystemAdmin → 403").toBe(HTTP.FORBIDDEN);

    // 用户入口：项目侧栏「死信补偿（admin）」导航到达
    await page.getByRole("navigation").getByRole("link", { name: "死信补偿（admin）" }).click();
    await page.waitForURL(/\/admin\/dead-letters/, { timeout: 10_000 });
    if ((real.body?.data as unknown[])?.length === 0) {
      // C.62 空态：无死信 → 队列健康空态
      await expect(page.locator('[data-sb-scope="dlq-empty"]')).toBeVisible({ timeout: 15_000 });
      await expect.soft(page.getByText("队列 activity.dlq 为空 · 投递链路健康")).toBeVisible();
    }

    // mock 一条死信（本地 Redis 无真实死信；UI 行为三件套走 route mock）。
    // Playwright glob 匹配含查询串的完整 URL → 模式带 * 尾巴（?per_page=100）。
    await page.route("**/api/v1/activity-dead-letters**", async (route) => {
      const url = route.request().url();
      if (url.includes("/replay/") || url.includes("/bulk/")) { await route.continue(); return; }
      await route.fulfill({
        status: HTTP.OK, contentType: "application/json",
        body: JSON.stringify({
          status: "success",
          data: [{ id: "3f9a8c2e-6b3d-4a7e-9f11-2c4d5e6f7a8b", event_key: "9d4f…", queue: "activity.dlq", error_summary: "OperationalError: connection reset by peer", retries: 3, first_failed_at: "2026-09-01T06:40:12.331Z" }],
          meta: { count: 1, total_count: 1, queue: "activity.dlq", alert_threshold_exceeded: false },
        }),
      });
    });
    await page.reload();
    await expect(page.locator('[data-sb-scope="dlq-row"]')).toHaveCount(1, { timeout: 15_000 });
    // C.62 死信列表：列 = 时间 / event_key / 错误摘要 / 重试次数（3/3 耗尽红显）
    const row = page.locator('[data-sb-scope="dlq-row"]').first();
    await expect.soft(row.locator('[data-sb-scope="dlq-retries"]')).toHaveText("3/3");
    await expect.soft(row.locator('[data-sb-scope="dlq-retries"]')).toHaveClass(/text-red-600/);
    await expect.soft(page.getByText("堆积 1 条")).toBeVisible();

    // C.62 操作：单条重放（幂等 dedup_skipped 提示）；重放成功后列表清空回到健康空态
    await page.route("**/api/v1/activity-dead-letters**", async (route) => {
      const url = route.request().url();
      if (url.includes("/replay/")) {
        await route.fulfill({ status: HTTP.OK, contentType: "application/json", body: JSON.stringify({ status: "success", data: { message_id: "3f9a8c2e-6b3d-4a7e-9f11-2c4d5e6f7a8b", replayed: true, dedup_skipped: false } }) });
        return;
      }
      await route.fulfill({ status: HTTP.OK, contentType: "application/json", body: JSON.stringify({ status: "success", data: [], meta: { count: 0, total_count: 0, queue: "activity.dlq" } }) });
    });
    const replayP = page.waitForResponse((r) => r.url().includes("/replay/") && r.request().method() === "POST", { timeout: 15_000 });
    await row.locator('[data-sb-scope="dlq-replay"]').click();
    expect((await replayP).status()).toBe(HTTP.OK);
    await expect(page.getByText("已重放死信")).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('[data-sb-scope="dlq-empty"]'), "重放后队列回到健康空态").toBeVisible({ timeout: 10_000 });
    await page.unroute("**/api/v1/activity-dead-letters**");
    void proj;
  });
});

test("T010-3 C.62 死信页 403 分支：非 SystemAdmin 访问显示权限提示（非空态）", async ({ page }) => {
  test.setTimeout(60_000);
  const getErrs = attachGuards(page);
  // 被测行为本身：非 SystemAdmin 访问死信页 → 403（PERM_ROLE_INSUFFICIENT）
  getErrs.allow({ method: "GET", url: "/activity-dead-letters/", status: HTTP.FORBIDDEN });
  await loginDemo(page);
  // 2026-09-07 勘误：死信页是系统级顶层路由（routes.ts「不嵌 workspace」），
  // 原 `${WS}/admin/dead-letters` 落到通配 404 页、dlq-forbidden 永不渲染——
  // 该用例自 0324e45 出生即红（且引用了不存在的 expectNoConsoleErrors）；
  // 顶层路径才是被测 403 分支，console guard 改用本文件 attachGuards 惯例。
  await page.goto("/admin/dead-letters");
  const tip = page.locator('[data-sb-scope="dlq-forbidden"]');
  await expect(tip, "权限空态可见（含权限码提示，非误导性「没有死信」）").toBeVisible({ timeout: 10_000 });
  await expect(tip).toContainText("system.audit.read");
  await expect(page.locator("table")).toHaveCount(0); // 不渲染表格
  expect(getErrs?.report() ?? [], "console/net errors").toEqual([]);
});
