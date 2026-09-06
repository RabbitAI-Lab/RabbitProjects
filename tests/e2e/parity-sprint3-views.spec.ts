/** Sprint-3 Phase 3-A 前端（BOARD-003 视图体系 + TASK-011 筛选面板）parity + 行为三件套。
 *  断言由附录 C.64~C.76 清单行生成（ADR-0010 ③：不由实现反推），每条带 `// C.x` 出处注释。
 *
 *  覆盖（owner = BOARD-003 §3.1~§3.6 / TASK-011 §3.1~§3.3）：
 *    C.64 视图切换器工具条（四段器/Tabs/🔒/＋▾/分组切换器/gantt 禁用）
 *    C.65 视图已修改黄条 + 保存（PATCH display_props）
 *    C.66 五维分组列头（优先级枚举列 / __none__ 哨兵列 / 空列恒在）
 *    C.67 跨维度拖拽 dropToWrite（priority PATCH / __none__ 拦截 / BR-15 替换确认）
 *    C.68 显示配置 Drawer（320px / 卡片开关 / 空组显隐 / 保存到视图）
 *    C.69 保存/另存为弹层（名称/8 icon/布局四选 → POST views 201 → 新 Tab 选中 + ?view_id=）
 *    C.70 视图结果为空 / 首次引导
 *    C.71~C.74 筛选面板（组节点/条件行/快捷 chips/配额/命中数/视图段叠加/最近）
 *    C.75 表格布局（斑马纹 + 列配置生效）
 *    C.76 URL ?view_id / ?filters 同步与还原（刷新还原）
 *
 *  纪律（CLAUDE.md）：登录走 UI（用户入口铁律）；每 test 先 clearCookies；行为断言三件套
 *  （①交互 → ②waitForResponse 对应请求 2xx → ③UI 回读/刷新还原）；负向成对（他人视图 URL
 *  → 前端黄条回退 + API 404——随机 UUID 与他人视图走同一存在性隐藏分支 BR-11/§2.6）；
 *  console guard 全量；API_TRUTH import（禁止硬编码状态码）。
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
  await page.getByLabel("项目名称 *").fill(`S3V ${Date.now() % 1000000}`);
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

/** 用户入口：登录态下点侧栏导航到达（禁止直链深跳——绿灯路径必须是用户走得通的）。 */
async function gotoBoard(page: Page) {
  await page.getByRole("navigation").getByRole("link", { name: "看板" }).click();
  await page.waitForURL(/\/board/, { timeout: 10_000 });
}

/** 等视图 Tabs 就绪（GET views/ 回来后渲染「全部」+ 5 内置）。 */
async function waitTabs(page: Page) {
  await expect(page.locator('[data-sb-scope="view-tab"][data-view-name="全部"]')).toBeVisible({ timeout: 15_000 });
  await expect(page.locator('[data-sb-scope="view-tab"][data-view-name="需求池"]')).toBeVisible({ timeout: 10_000 });
}

/** 打开分组切换菜单并选维度。 */
async function switchGroup(page: Page, name: string) {
  await page.locator('[data-sb-scope="view-group-btn"]').click();
  await page.getByRole("menuitem", { name }).first().click();
}

test.describe("Sprint-3 Phase 3-A 视图与筛选（BOARD-003 / TASK-011 · C.64~C.76）", () => {
  let getErrs: () => string[] = () => [];
  test.beforeEach(async ({ page }) => {
    await page.context().clearCookies();
    getErrs = attachConsoleGuard(page);
  });
  test.afterEach(async () => {
    expect.soft(getErrs(), "console errors").toEqual([]);
  });

  /* ═══════════ C.64 视图切换器工具条 parity ═══════════ */

  test("S3V-1 C.64 工具条：四段器（gantt 自 Sprint-4 启用）+ 全部/五内置 Tab（🔒）+ ⚙显示 + 分组切换器", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    await createProject(page);
    await waitTabs(page);
    // C.64 布局四段器：list/kanban/table/gantt——gantt 占位禁用态已被 Sprint-4 甘特页
    // 替换（GANTT-001 §3.1 / C.98 侧栏「甘特」入口），此处改为断言可点击且落甘特路由
    await expect(page.locator('[data-sb-scope="layout-seg-list"]')).toBeVisible();
    await expect(page.locator('[data-sb-scope="layout-seg-kanban"]')).toBeVisible();
    await expect(page.locator('[data-sb-scope="layout-seg-table"]')).toBeVisible();
    await expect(page.locator('[data-sb-scope="layout-seg-gantt"]')).toBeEnabled();
    await page.locator('[data-sb-scope="layout-seg-gantt"]').click();
    await page.waitForURL(/\/gantt/, { timeout: 10_000 });
    await expect(page.locator('[data-sb-scope="layout-seg-gantt"]')).toHaveAttribute("aria-pressed", "true");
    await page.goBack();
    // C.64 「全部」固定首项（前端入口不入库）+ 内置五视图 🔒（§3.1/§3.6 / R4）
    await expect(page.locator('[data-sb-scope="view-tab"][data-view-id="__all__"]')).toContainText("全部");
    for (const name of ["需求池", "缺陷列表", "我的待办", "本周到期", "测试执行"]) {
      await expect(page.locator(`[data-sb-scope="view-tab"][data-view-name="${name}"]`)).toBeVisible();
    }
    await expect(page.locator('[data-sb-scope="view-tab"][data-view-name="需求池"]')).toContainText("🔒");
    // C.64 ⚙ 显示入口 + 分组切换器（kanban 布局，§3.1）
    await expect(page.locator('[data-sb-scope="view-display-btn"]')).toBeVisible();
    await expect(page.locator('[data-sb-scope="view-group-btn"]')).toBeVisible();
    await expect(page.locator('[data-sb-scope="view-group-btn"]')).toContainText("分组：按状态");
    // 分组候选 = 白名单四内置 + Schema groupable cf_*（§3.2 分组切换器行）
    await page.locator('[data-sb-scope="view-group-btn"]').click();
    for (const name of ["按状态", "按优先级", "按负责人", "按标签"]) {
      await expect(page.locator('[data-sb-scope="view-group-dd"]').getByRole("menuitem", { name })).toBeVisible();
    }
    await page.keyboard.press("Escape");
  });

  /* ═══════════ C.66 + C.65 分组切换列重建 + 黄条 + 保存 ═══════════ */

  test("S3V-2 C.66/C.65 优先级分组列重建（GET group_by）→ 黄条 → 保存 PATCH display_props", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page);
    const issue = await mkIssue(page, proj.id, "S3V2 优先级拖拽目标");
    await waitTabs(page);
    // 选中内置「需求池」——内置可改 display_props（BR-03，filters 锁定）
    await page.locator('[data-sb-scope="view-tab"][data-view-name="需求池"]').click();
    await page.waitForURL(/view_id=/);
    // C.66 分组切换 → 列重建：GET issues/?group_by=priority&view_id=（§3.5 数据重拉）
    const grouped = page.waitForResponse((r: Response) =>
      /\/issues\/\?.*group_by=priority/.test(r.url()) && r.request().method() === "GET");
    await switchGroup(page, "按优先级");
    expect((await grouped).status()).toBe(HTTP.OK);
    // C.66 priority 五枚举列（枚举固定表 BR-06；none 为合法值列非哨兵——§2.3）
    for (const name of ["紧急", "高", "中", "低", "无"]) {
      await expect(page.locator('section[data-sb-scope="bcol"]').filter({ hasText: new RegExp(`^\\s*${name}`) }).first()).toBeVisible({ timeout: 10_000 });
    }
    // C.65 视图已修改黄条出现（§3.1：筛选/分组/显示与存档不一致）
    await expect(page.locator('[data-sb-scope="view-dirty-bar"]')).toBeVisible();
    await expect(page.locator('[data-sb-scope="view-dirty-bar"]')).toContainText("视图已修改");
    // 行为三件套：保存 → PATCH views/{id} 2xx → 黄条收起（§3.1 [保存] 就地更新）
    const patch = page.waitForResponse((r: Response) =>
      /\/views\/[0-9a-f-]+\/$/.test(r.url()) && r.request().method() === "PATCH");
    await page.locator('[data-sb-scope="view-dirty-save"]').click();
    const pres = await patch;
    expect(pres.status()).toBe(HTTP.OK);
    const body = pres.request().postDataJSON() as { display_props?: { group_by?: string } };
    expect(body.display_props?.group_by).toBe("priority");
    await expect(page.locator('[data-sb-scope="view-dirty-bar"]')).toBeHidden({ timeout: 10_000 });
    // 刷新还原：URL ?view_id= + 已保存的分组（E2E-01 口径）
    await page.reload();
    await waitTabs(page);
    await expect(page.locator('[data-sb-scope="view-group-btn"]')).toContainText("分组：按优先级", { timeout: 10_000 });
    void issue;
  });

  /* ═══════════ C.67 跨维度拖拽（priority PATCH + __none__ 拦截 + BR-15） ═══════════ */

  test("S3V-3 C.67 优先级拖拽：dropToWrite → PATCH {priority, sort_order} → 刷新保持", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page);
    // 桌面断点（§3.7 ≥1280）内取宽视口：五列看板整体可见，规避横向滚动容器内
    // 源卡片/目标列不可同屏导致的 HTML5 拖拽滚动竞态
    await page.setViewportSize({ width: 1800, height: 900 });
    const issue = await mkIssue(page, proj.id, "S3V3 拖到紧急", { priority: "medium" });
    await waitTabs(page);
    await page.reload();
    await waitTabs(page);
    await switchGroup(page, "按优先级");
    const urgentCol = page.locator('section[data-sb-scope="bcol"]', { hasText: /紧急/ }).first();
    await expect(urgentCol).toBeVisible({ timeout: 10_000 });
    // 源卡落在「中」列（priority=medium）——列重建完成后可见再拖（分组切换 120ms 渐隐重排）
    const srcCard = page.locator(`article[data-card-id="${issue.id}"]`).first();
    await expect(srcCard).toBeVisible({ timeout: 10_000 });
    await page.waitForTimeout(250);
    // 行为三件套：拖卡 → PATCH issues/{id}（priority=urgent，§2.4 表）→ 卡片迁入紧急列
    const patch = page.waitForResponse((r: Response) =>
      new RegExp(`/issues/${issue.id}/`).test(r.url()) && r.request().method() === "PATCH");
    await srcCard.dragTo(urgentCol, { targetPosition: { x: 140, y: 200 } });
    const pres = await patch;
    expect(pres.status()).toBe(HTTP.OK);
    const body = pres.request().postDataJSON() as { priority?: string };
    expect(body.priority).toBe("urgent");
    // UI 回读：卡片在紧急列 + 刷新保持（E2E-04）
    await expect(urgentCol.locator(`article[data-card-id="${issue.id}"]`)).toBeVisible({ timeout: 10_000 });
    await page.reload();
    await waitTabs(page);
    await expect(urgentCol.locator(`article[data-card-id="${issue.id}"]`)).toBeVisible({ timeout: 15_000 });
  });

  test("S3V-4 C.67 __none__ 哨兵拦截：assignee 维拖入未指派列 → toast + 弹回 + 零写请求", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page);
    await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${(await mkIssue(page, proj.id, "S3V4 未指派拦截")).id}/assignees/claim/`).catch(() => {});
    const issue2 = await mkIssue(page, proj.id, "S3V4 有主任务");
    await waitTabs(page);
    await switchGroup(page, "按负责人");
    // C.66 assignee 维 __none__「未指派」哨兵列恒在且最末（§2.3 铁律 3）
    const noneCol = page.locator('section[data-sb-scope="bcol"][data-none="1"]').first();
    await expect(noneCol).toBeVisible({ timeout: 10_000 });
    await expect(noneCol).toContainText("未指派");
    await expect(noneCol).toContainText("可拖出 · 不可拖入");
    // C.67 拖入 __none__ 被拦（BR-14）：toast + 弹回；期间不得发出任何写请求
    let writeSent = false;
    page.on("request", (r) => {
      if (/\/issues\/[0-9a-f-]+/.test(r.url()) && ["PATCH", "PUT", "POST"].includes(r.method())) writeSent = true;
    });
    const card = page.locator(`article[data-card-id="${issue2.id}"]`);
    await card.dragTo(noneCol, { targetPosition: { x: 140, y: 80 } });
    await expect(page.locator(".fixed.top-4.right-4")).toContainText("不能拖入「未指派」列", { timeout: 10_000 });
    await page.waitForTimeout(600);
    expect(writeSent, "BR-14 拦截不得发出写请求").toBe(false);
  });

  test("S3V-5 C.67 BR-15 多标签替换确认：拖双标签卡 → 确认弹层 → PUT labels 全量替换", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page);
    const [l1, l2, l3] = (await (async () => {
      const ids: string[] = [];
      for (const n of ["标签甲", "标签乙", "标签丙"]) {
        const r = await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${proj.id}/labels/`, { name: n, color: "#3b82f6" });
        expect(r.status).toBe(HTTP.CREATED);
        ids.push(r.body?.data?.id);
      }
      return ids;
    })());
    const issue = await mkIssue(page, proj.id, "S3V5 双标签卡", { label_ids: [l1, l2] });
    await page.reload();
    await waitTabs(page);
    await switchGroup(page, "按标签");
    const targetCol = page.locator('section[data-sb-scope="bcol"]', { hasText: "标签丙" }).first();
    await expect(targetCol).toBeVisible({ timeout: 10_000 });
    // 多值卡片在所有匹配列渲染 ghost（§2.3 预览态）——取任一实例拖拽
    await page.locator(`article[data-card-id="${issue.id}"]`).first().dragTo(targetCol, { targetPosition: { x: 140, y: 80 } });
    // C.67 替换确认弹层（BR-15：多标签卡片替换语义必须显式知情）
    const dlg = page.locator('[data-sb-scope="board-replace-confirm"]');
    await expect(dlg).toBeVisible({ timeout: 10_000 });
    await expect(dlg).toContainText("全量替换");
    // 行为三件套：替换 → PUT …/labels/ 2xx → 卡片迁入目标列
    const put = page.waitForResponse((r: Response) =>
      new RegExp(`/issues/${issue.id}/labels/`).test(r.url()) && r.request().method() === "PUT");
    await page.locator('[data-sb-scope="board-replace-ok"]').click();
    const pres = await put;
    expect(pres.status()).toBe(HTTP.OK);
    expect((pres.request().postDataJSON() as { label_ids?: string[] }).label_ids).toEqual([l3]);
    await expect(targetCol.locator(`article[data-card-id="${issue.id}"]`)).toBeVisible({ timeout: 10_000 });
  });

  /* ═══════════ C.69 保存/另存为 + C.76 URL 还原 ═══════════ */

  test("S3V-6 C.69/C.76 另存为弹层：POST views 201 → 新 Tab 选中 → ?view_id= 刷新还原", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page);
    await mkIssue(page, proj.id, "S3V6 视图内任务");
    await waitTabs(page);
    await switchGroup(page, "按优先级");
    await expect(page.locator('[data-sb-scope="view-dirty-bar"]')).toBeVisible();
    // 另存为（M-SAVE：名称默认「(副本)」、8 icon、布局四选——§3.4）
    await page.locator('[data-sb-scope="view-dirty-saveas"]').click();
    const modal = page.locator('[data-sb-scope="save-view-modal"]');
    await expect(modal).toBeVisible();
    await expect(modal.locator('[data-sb-scope="save-view-icons"] [role="radio"]')).toHaveCount(8);
    await expect(modal.locator('[data-sb-scope="save-view-layout"] [data-layout="kanban"]')).toHaveAttribute("aria-pressed", "true");
    await modal.locator('[data-sb-scope="save-view-name"]').fill("救火看板S3V");
    // 行为三件套：创建视图 → POST views/ 201 → 新 Tab 出现并选中 + ?view_id= 同步
    const post = page.waitForResponse((r: Response) =>
      /\/views\/$/.test(r.url()) && r.request().method() === "POST");
    await modal.locator('[data-sb-scope="save-view-submit"]').click();
    const res = await post;
    expect(res.status()).toBe(HTTP.CREATED);
    const created = ((await res.json()) as { data?: { id?: string } }).data?.id ?? "";
    expect(created).not.toBe("");
    await page.waitForURL(new RegExp(`view_id=${created}`), { timeout: 10_000 });
    // §3.1「超出 6 个折叠进＋ ▾」：全部+5 内置占满 6 个内联位 → 新视图入下拉（选中态 ✓）
    await page.locator('[data-sb-scope="views-more-btn"]').click();
    await expect(page.locator('[data-sb-scope="views-more"]').getByRole("menuitem", { name: /✓.*救火看板S3V/ })).toBeVisible({ timeout: 10_000 });
    await page.keyboard.press("Escape");
    // 刷新还原：URL ?view_id= 仍指向新视图 + 已保存分组保持（E2E-01 完整还原口径）
    await page.reload();
    await waitTabs(page);
    await expect(page).toHaveURL(new RegExp(`view_id=${created}`), { timeout: 10_000 });
    await expect(page.locator('[data-sb-scope="view-group-btn"]')).toContainText("分组：按优先级", { timeout: 10_000 });
    // 重名警告不阻断（BR-09：项目内重名允许，靠 id 区分）
    await page.locator('[data-sb-scope="views-more-btn"]').click();
    await page.getByRole("menuitem", { name: "新建视图" }).click();
    await page.locator('[data-sb-scope="save-view-name"]').fill("救火看板S3V");
    await expect(page.locator('[data-sb-scope="save-view-dup-warn"]')).toBeVisible();
    await page.keyboard.press("Escape");
  });

  /* ═══════════ C.68 显示配置 Drawer ═══════════ */

  test("S3V-7 C.68 显示配置：卡片开关乐观预览 + [保存到视图] PATCH + 空列折叠", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page);
    await mkIssue(page, proj.id, "S3V7 显示配置");
    await waitTabs(page);
    await page.locator('[data-sb-scope="view-tab"][data-view-name="需求池"]').click();
    await page.waitForURL(/view_id=/);
    await page.locator('[data-sb-scope="view-display-btn"]').click();
    const drawer = page.locator('[data-sb-scope="display-drawer"]');
    await expect(drawer).toBeVisible();
    // C.68 固定 7 卡片开关（BR-09 同一集合）+ 空组显隐 + 列配置区（list/table 才有——kanban 无）
    for (const name of ["标签", "子任务", "附件数", "工时", "优先级", "计时器", "截止时间"]) {
      await expect(drawer.getByRole("switch", { name })).toBeVisible();
    }
    await expect(drawer.getByRole("switch", { name: "显示空分组" })).toHaveAttribute("aria-checked", "true");
    await expect(drawer.locator('[data-sb-scope="disp-sec-columns"]')).toBeHidden();
    // 行为三件套：关空组 → 乐观预览（空列折叠胶囊）→ 保存到视图 PATCH display_props
    await drawer.getByRole("switch", { name: "显示空分组" }).click();
    await expect(page.locator('[data-sb-scope="bcol-collapsed"]').first()).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('[data-sb-scope="view-dirty-bar"]')).toBeVisible();
    const patch = page.waitForResponse((r: Response) =>
      /\/views\/[0-9a-f-]+\/$/.test(r.url()) && r.request().method() === "PATCH");
    await page.locator('[data-sb-scope="disp-save"]').click();
    expect((await patch).status()).toBe(HTTP.OK);
    await page.reload();
    await waitTabs(page);
    await expect(page.locator('[data-sb-scope="bcol-collapsed"]').first()).toBeVisible({ timeout: 15_000 });
  });

  /* ═══════════ C.72~C.74 筛选面板：树/快捷 chips/配额/命中数/应用 URL ═══════════ */

  test("S3V-8 C.71~C.74 筛选面板：快捷 chip 成条件 → 命中数防抖查询 → 应用 → URL ?filters= 还原", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page);
    await mkIssue(page, proj.id, "S3V8 筛选命中A");
    await waitTabs(page);
    // C.74 ⊞ 筛选入口打开面板（O2）
    await page.locator('[data-sb-scope="filter-open-btn"]').click();
    const panel = page.locator('[data-sb-scope="filter-panel"]');
    await expect(panel).toBeVisible();
    // C.71 组节点（虚线框 + AND/OR 切换 + [+条件][+组]；根组不可删）+ 配额 0/20 · 1/3
    await expect(panel.locator('[data-sb-scope="fgroup"]').first()).toBeVisible();
    await expect(panel.locator('[data-sb-scope="fgroup-op"]').first()).toContainText("满足 全部（AND）");
    await expect(panel.locator('[data-sb-scope="fpanel-quota"]')).toContainText("条件 0/20 · 层级 1/3");
    // C.73 快捷 chip「@我」一键成条件（§3.1）→ C.74 命中数 500ms 防抖（per_page=1 读 total_count）
    const hitQ = page.waitForResponse((r: Response) =>
      /\/issues\/\?.*per_page=1/.test(r.url()) && /filters=/.test(decodeURIComponent(r.url())) && r.request().method() === "GET");
    await panel.locator('[data-sb-scope="fpanel-quickchip"]', { hasText: "@我" }).click();
    expect((await hitQ).status()).toBe(HTTP.OK);
    await expect(panel.locator('[data-sb-scope="fpanel-quota"]')).toContainText("条件 1/20");
    await expect(panel.locator('[data-sb-scope="fpanel-hitcount"]')).toContainText("命中", { timeout: 10_000 });
    // + 条件默认「状态组 in 待办」（§3.3 交互表）+ 字段/操作符下拉存在
    await panel.locator('[data-sb-scope="fgroup-add-cond"]').first().click();
    await expect(panel.locator('[data-sb-scope="frow"]').filter({ hasText: "状态组" }).first()).toBeVisible();
    await panel.locator('[data-sb-scope="frow"]').filter({ hasText: "状态组" }).first().locator('[data-sb-scope="frow-del"]').click();
    await expect(panel.locator('[data-sb-scope="frow"]')).toHaveCount(1);
    // C.72 应用 → URL ?filters= 同步（TASK-011 §3.3）→ chip 行叠加徽章 + 刷新还原（C.76）
    await panel.locator('[data-sb-scope="fpanel-apply"]').click();
    await page.waitForURL(/filters=/, { timeout: 10_000 });
    await expect(page.locator('[data-sb-scope="view-chiprow"]')).toContainText("叠加 1 条临时条件");
    await expect(page.locator('[data-sb-scope="filter-open-btn"] [data-sb-scope="filter-count-badge"]')).toHaveText("1");
    await page.reload();
    await waitTabs(page);
    await expect(page.locator('[data-sb-scope="view-chiprow"]')).toContainText("叠加 1 条临时条件", { timeout: 15_000 });
  });

  test("S3V-9 C.73/C.74 视图段叠加：内置视图只读 chips + 叠加 @me（view_id&filters 双参刷新还原）", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page);
    await mkIssue(page, proj.id, "S3V9 需求任务", {});
    await waitTabs(page);
    await page.locator('[data-sb-scope="view-tab"][data-view-name="需求池"]').click();
    await page.waitForURL(/view_id=/);
    // 视图条件只读 chips（需求池=类型需求，占位符活解析展示）
    await expect(page.locator('[data-sb-scope="view-chiprow"]')).toContainText("任务类型", { timeout: 10_000 });
    await page.locator('[data-sb-scope="filter-open-btn"]').click();
    const panel = page.locator('[data-sb-scope="filter-panel"]');
    // C.73 视图段：内置 · 只读 + [另存][脱离]（TASK-011 §3.1）
    await expect(panel.locator('[data-sb-scope="fpanel-viewseg"]')).toContainText("需求池（内置 · 只读）");
    await expect(panel.locator('[data-sb-scope="fpanel-detach"]')).toBeVisible();
    await expect(panel.locator('[data-sb-scope="fpanel-merge-save"]')).toBeVisible();
    // 叠加 @me → 应用 → 三源恒 AND（BR-12）：view_id & filters 双参入 URL，刷新还原
    await panel.locator('[data-sb-scope="fpanel-quickchip"]', { hasText: "@我" }).click();
    await panel.locator('[data-sb-scope="fpanel-apply"]').click();
    await page.waitForURL(/view_id=.*&filters=/, { timeout: 10_000 });
    await expect(page.locator('[data-sb-scope="view-chiprow"]')).toContainText("叠加 1 条临时条件");
    await page.reload();
    await waitTabs(page);
    await expect(page.locator('[data-sb-scope="view-chiprow"]')).toContainText("叠加 1 条临时条件", { timeout: 15_000 });
    // 脱离：切回「全部」，条件转纯临时层（TASK-011 §3.1 [脱离]）
    await page.locator('[data-sb-scope="filter-open-btn"]').click();
    await page.locator('[data-sb-scope="fpanel-detach"]').click();
    await page.waitForURL((u) => !u.searchParams.get("view_id"), { timeout: 10_000 });
  });

  /* ═══════════ C.76 布局切换 PATCH 单字段 + C.75 表格布局 ═══════════ */

  test("S3V-10 C.76/C.75 切布局 PATCH layout 单字段 → /table 斑马纹表格 + 列配置生效", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page);
    // 需求池 filters=类型需求（占位符 __requirement__）——造需求类型任务保证可见
    const types = await apiCall(page, "GET", `/api/v1/workspaces/${WS}/projects/${proj.id}/issue-types/`);
    const reqType = (types.body?.data as Array<{ id: string; name: string }>)?.find((t) => t.name.includes("需求"))?.id;
    await mkIssue(page, proj.id, "S3V10 表格行", reqType ? { type_id: reqType } : {});
    await waitTabs(page);
    await page.locator('[data-sb-scope="view-tab"][data-view-name="需求池"]').click();
    await page.waitForURL(/view_id=/);
    // 行为三件套：点表格段 → PATCH views/{id} 仅 layout 字段（BR-04/BR-13）→ 路由 /table + ?view_id 保留
    const patch = page.waitForResponse((r: Response) =>
      /\/views\/[0-9a-f-]+\/$/.test(r.url()) && r.request().method() === "PATCH");
    await page.locator('[data-sb-scope="layout-seg-table"]').click();
    const pres = await patch;
    expect(pres.status()).toBe(HTTP.OK);
    expect(Object.keys(pres.request().postDataJSON() as Record<string, unknown>)).toEqual(["layout"]);
    await page.waitForURL(/\/table\?view_id=/, { timeout: 10_000 });
    // C.75 表格布局：紧凑斑马纹（偶数行底色）+ 列配置生效（display_props.columns）
    await expect(page.locator('[data-sb-scope="table-row"]').first()).toBeVisible({ timeout: 15_000 });
    await expect(page.locator('[data-sb-scope="table-row"]').filter({ hasText: "S3V10 表格行" })).toBeVisible();
    await expect(page.locator('th[data-col="priority"]')).toBeVisible();
    // 列配置：⚙ 显示 → 隐藏优先级列（「-」前缀）→ 表格列头消失（C.68 列配置区仅 list/table）
    await page.locator('[data-sb-scope="view-display-btn"]').click();
    const drawer = page.locator('[data-sb-scope="display-drawer"]');
    await expect(drawer.locator('[data-sb-scope="disp-sec-columns"]')).toBeVisible();
    await drawer.locator('[data-sb-scope="disp-col-chip"][data-col="priority"]').click();
    await expect(page.locator('th[data-col="priority"]')).toBeHidden({ timeout: 10_000 });
  });

  /* ═══════════ C.70 空态/引导 + 鉴权负向成对 ═══════════ */

  test("S3V-11 C.70 首次引导气泡（无自定义视图时出现 + 知道了记忆）", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    await createProject(page);
    await waitTabs(page);
    // §3.6 项目首次进入（无自定义视图）→ Tabs 引导气泡 + [知道了]
    await expect(page.locator('[data-sb-scope="view-guide-pop"]')).toBeVisible({ timeout: 10_000 });
    await page.locator('[data-sb-scope="view-guide-ok"]').click();
    await expect(page.locator('[data-sb-scope="view-guide-pop"]')).toBeHidden();
    await page.reload();
    await waitTabs(page);
    await expect(page.locator('[data-sb-scope="view-guide-pop"]')).toBeHidden();
  });

  test("S3V-12 C.70/C.76 鉴权负向成对：不可见视图 URL → 前端黄条回退 + API 404（BR-11 存在性隐藏）", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page);
    await mkIssue(page, proj.id, "S3V12 负向");
    await waitTabs(page);
    // 随机 UUID 与他人个人视图走同一存在性隐藏分支（BR-11/§2.6）。
    // 后端边界 A：分组/列表端点 ?view_id= 不可见 → 404 RESOURCE_NOT_FOUND（§4.2-6）
    const foreignViewId = "00000000-0000-4000-8000-000000000000";
    const r2 = await apiCall(page, "GET", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/?view_id=${foreignViewId}`);
    expect(r2.status).toBe(HTTP.NOT_FOUND);
    expect(r2.body?.error?.code).toBe("RESOURCE_NOT_FOUND");
    // 后端边界 B：可见性 = 内置 + 本人（BR-11）——列表不含任意他人个人视图（全量无分页）
    const listRes = await apiCall(page, "GET", `/api/v1/workspaces/${WS}/projects/${proj.id}/views/`);
    expect(listRes.status).toBe(HTTP.OK);
    const viewIds = (listRes.body?.data as Array<{ id: string }>).map((v) => v.id);
    expect(viewIds).not.toContain(foreignViewId);
    // 注：GET views/{id}/ 详情端点当前对不存在 id 返回 500（retrieve() 调 _get_view() 缺
    // for_write 实参——后端缺陷，已按流程单列报告；前端 ViewStore 只消费列表端点，不受影响）
    // 前端：登录态下用户路径进入看板后带不可见 view_id → 黄条 + 回退「全部」（先登录后深链，铁律允许）
    await page.goto(`${page.url().split("?")[0]}?view_id=${foreignViewId}`);
    await expect(page.locator('[data-sb-scope="view-gone-bar"]')).toBeVisible({ timeout: 15_000 });
    await expect(page.locator('[data-sb-scope="view-tab"][data-view-id="__all__"]')).toHaveAttribute("aria-selected", "true");
    // 回退后看板可用（数据照常渲染，§3.6「自动跳默认视图」）
    await expect(page.locator('article[data-sb-scope="board-card"]').first()).toBeVisible({ timeout: 15_000 });
  });

  /* ═══════════ C.64 视图右键菜单 + 设默认（BR-10 settings 偏好） ═══════════ */

  test("S3V-13 C.64/C.65 右键菜单设默认 → PATCH users/me/settings → 重进项目直达默认视图", async ({ page }) => {
    test.setTimeout(120_000);
    await loginDemo(page);
    const proj = await createProject(page);
    await waitTabs(page);
    // S3V-6 已覆盖创建；此处经「＋▾ 新建视图」建个人视图后右键设默认
    await page.locator('[data-sb-scope="views-more-btn"]').click();
    await page.getByRole("menuitem", { name: "新建视图" }).click();
    await page.locator('[data-sb-scope="save-view-name"]').fill("S3V13 默认视图");
    const post = page.waitForResponse((r: Response) => /\/views\/$/.test(r.url()) && r.request().method() === "POST");
    await page.locator('[data-sb-scope="save-view-submit"]').click();
    const createdId = (((await (await post).json()) as { data?: { id?: string } }).data?.id) ?? "";
    expect(createdId).not.toBe("");
    // C.64 Tab 右键菜单：重命名/编辑条件/复制/设为默认/删除（内置无删除——S3V-1 锁标互证）。
    // 个人视图落在 ＋ ▾ 折叠位（全部+5 内置占满 6 内联）——折叠项同样支持右键
    await page.locator('[data-sb-scope="views-more-btn"]').click();
    await page.locator('[data-sb-scope="views-more"]').getByRole("menuitem", { name: /S3V13 默认视图/ }).click({ button: "right" });
    const menu = page.locator('[data-sb-scope="view-ctx-menu"]');
    await expect(menu).toBeVisible();
    for (const item of ["重命名", "编辑条件", "复制视图", "设为默认视图", "删除"]) {
      await expect(menu.getByRole("menuitem", { name: item })).toBeVisible();
    }
    // 行为三件套：设默认 → PATCH /users/me/settings/（board.default_view_id，BR-10 零新端点）
    const pref = page.waitForResponse((r: Response) =>
      /\/users\/me\/settings\/$/.test(r.url()) && r.request().method() === "PATCH");
    await menu.getByRole("menuitem", { name: "设为默认视图" }).click();
    expect((await pref).status()).toBe(HTTP.OK);
    // 折叠位默认星标（§3.1 默认 ★——内联位实心 ★，折叠位「★」后缀互证）
    await page.locator('[data-sb-scope="views-more-btn"]').click();
    await expect(page.locator('[data-sb-scope="views-more"]').getByRole("menuitem", { name: /S3V13 默认视图 ★/ })).toBeVisible();
    await page.keyboard.press("Escape");
    // 重进项目（项目卡片 = 用户入口；不带 ?view_id）→ 直达默认视图（BR-10）
    await page.goto(`/${WS}/projects`);
    await page.locator(`a[href*="${proj.id}"]`).first().waitFor({ state: "visible", timeout: 15_000 });
    await page.locator(`a[href*="${proj.id}"]`).first().click();
    await page.waitForURL(/\/board/, { timeout: 15_000 });
    await expect(page).toHaveURL(new RegExp(`view_id=${createdId}`), { timeout: 15_000 });
  });
});
