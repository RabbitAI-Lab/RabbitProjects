/** Sprint-1 验收缺陷回归（5 条）——每条都是「接口测试测不到、只在真实浏览器里暴露」的闭环。
 *
 *  背景：sprint-1 收口后验收发现 5 个功能不可用。根因清一色是**前后端契约漂移**
 *  （字段名/响应结构/网关路由），接口用例当时一条都没覆盖到，所以全绿却全坏。
 *  本 spec 把每一条钉死在浏览器里，与 `tests/jmeter/sprint-1-flow.py` 的
 *  CMT-03~05 / ACT-01~04 / SUB-03~05 / LBL-02~03 / FILE-07~10 / BRD-05~07 互为表里。
 *
 *  | # | 缺陷 | 根因 | 对应接口断言 |
 *  |---|---|---|---|
 *  | 1 | 评论头像是「?」 | 前端读 `actor.name`，后端只给 `display_name` | CMT-03/04/05 |
 *  | 2 | 项目设置标签页列表空、新建无效 | `document.cookie` 嗅探 HttpOnly 的 sessionid 恒 false + Link 到未注册路由 | LBL-02/03 |
 *  | 3 | 子任务勾选无反应 | checkbox 无 onChange 且带 readOnly；PATCH 发的是只读字段 `state_group` | SUB-03/04/05 |
 *  | 4 | 上传附件点了没反应 | 网关没有 `/uploads/` 反代，PUT 静默失败 | FILE-07~10 |
 *  | 5 | 卡片拖不进「已取消」 | 看板拉 states 没带 `include_cancelled=1` → 该列 state id 为 null → 静默 return | BRD-05/06/07 |
 *
 *  运行：E2E_NO_SERVER=1 pnpm exec playwright test tests/e2e/regressions-sprint1.spec.ts --reporter=line
 *  前置：web dev server(:3001) + API(:8000) + PG + MinIO(:9000) 均已启动（上传用例依赖 MinIO）。
 */
import { test, expect, type Page } from "@playwright/test";
import { attachConsoleGuard } from "./no-console-errors";

/** identifier/团队名每次运行唯一，否则 409 连锁失败（沿用 interactions.spec.ts 约定） */
const rid = (n = 5) =>
  Array.from({ length: n }, () => String.fromCharCode(65 + Math.floor(Math.random() * 26))).join("");

async function loginDemo(page: Page) {
  // 规范 ③：显式清 cookies，禁止依赖 Worker 复用自动清理
  await page.context().clearCookies();
  await page.goto("/login");
  await page.getByRole("button", { name: /一键进入演示账号/ }).click();
  // 稳定替代 waitForURL（同 auth.spec.ts / coverage.spec.ts）：跳转是 react-router 的
  // pushState，没有 load 事件；waitForURL 默认等 "load"，在导航竞态下会报
  // ERR_ABORTED / frame detached 或空等到超时。
  await page.waitForFunction(() => /\/projects$/.test(location.pathname), null, { timeout: 15_000 });
}

async function createProject(page: Page): Promise<string> {
  const name = `Reg ${Date.now() % 100000}`;
  const btn = page.getByRole("button", { name: /创建项目/ }).first();
  await btn.waitFor({ state: "visible", timeout: 20_000 });
  await btn.click();
  await page.getByLabel("项目名称 *").fill(name);
  await page.getByLabel("项目标识符 *").fill(rid());
  await page.getByRole("button", { name: "创建项目", exact: true }).click();
  await page.waitForURL(/\/projects\/.+\/board/, { timeout: 15_000 });
  return name;
}

/** 用「+ 创建任务」弹窗建一条任务并等它出现在看板上 */
async function createTask(page: Page, title: string) {
  await page.getByRole("button", { name: /\+ 创建任务/ }).click();
  await page.getByPlaceholder("任务标题").fill(title);
  await page.locator('[data-testid="create-task-submit"]').click();
  await expect(page.getByText(title).first()).toBeVisible({ timeout: 10_000 });
}

/** 点看板卡片打开抽屉（写入 ?peekIssue=） */
async function openDrawer(page: Page, title: string) {
  await page.locator("article").filter({ hasText: title }).first().click();
  await expect(page.locator("aside")).toBeVisible({ timeout: 10_000 });
}

test.describe("Sprint-1 验收缺陷回归（评论头像 / 标签 / 子任务 / 附件 / 看板拖拽）", () => {
  let getErrs: () => string[] = () => [];
  test.beforeEach(async ({ page }) => {
    await page.context().clearCookies();
    getErrs = attachConsoleGuard(page);
  });
  test.afterEach(async () => {
    expect(getErrs(), "console errors").toEqual([]);
  });

  /* ── 缺陷 1：评论头像显示为「?」 ─────────────────────────────── */
  test("REG-1 评论头像与昵称渲染真人首字，不是兜底「?」", async ({ page }) => {
    test.setTimeout(60_000);
    await loginDemo(page);
    await createProject(page);
    await createTask(page, "头像回归任务");
    await openDrawer(page, "头像回归任务");

    // 切到评论 Tab 并发表一条（抽屉 comment 数据只在切 tab 时拉）
    await page.locator('[data-tab-key="comments"]').click();
    await page.locator('[data-sb-scope="drawer-comment-input"]').fill("<p>头像回归</p>");
    await page.locator('[data-sb-scope="drawer-comment-submit"]').click();

    const row = page.locator('[data-sb-scope="drawer-comment-row"]').first();
    await expect(row).toBeVisible({ timeout: 10_000 });
    // 昵称：演示账号是「张三」，绝不应该是兜底的「?」
    const nick = (await row.locator("b").first().innerText()).trim();
    expect(nick, "评论昵称").not.toBe("?");
    expect(nick.length, "评论昵称非空").toBeGreaterThan(0);
    // 头像：首字母取自 display_name；「?」说明前端又读错了字段名
    const initial = (await row.locator("span").first().innerText()).trim();
    expect(initial, "评论头像首字母").not.toBe("?");
    expect(initial, "头像首字母必须来自 display_name 首字").toBe(Array.from(nick)[0]);
  });

  /* ── 缺陷 2：项目设置「管理标签」→ 列表空 + 新建无效 ─────────── */
  test("REG-2 项目设置管理标签：弹窗打开、列表可回读、新建后立刻出现", async ({ page }) => {
    test.setTimeout(60_000);
    await loginDemo(page);
    await createProject(page);
    await page.goto(page.url().replace(/\/board$/, "/settings"));

    // 入口必须是弹窗（ADR-0011 #13）；曾 Link 到未注册的 settings/labels 路由 → 404
    await page.locator('[data-sb-scope="settings-open-labels"]').click();
    const modal = page.locator('[data-sb-scope="labels-admin-body"]');
    await expect(modal).toBeVisible({ timeout: 10_000 });

    // 列表区：初次为空态；关键是不能因为守卫误判而永远停留在空态
    await page.locator('[data-sb-scope="labels-admin-new"]').click();
    const labelName = `回归标签${Date.now() % 100000}`;
    await page.getByLabel("标签名称").fill(labelName);
    await page.getByRole("button", { name: "保存", exact: true }).click();

    // 缺陷表现：新建成功但列表不刷新（load() 被 HttpOnly cookie 嗅探短路）
    await expect(
      page.locator('[data-sb-scope="labels-admin-row"]').filter({ hasText: labelName }),
    ).toBeVisible({ timeout: 10_000 });

    // 二次校验：关掉再开，数据要能从服务端回读到（证明不是本地假象）
    await page.locator('[data-sb-scope="labels-admin-mask"]').click({ position: { x: 5, y: 5 } });
    await expect(modal).toHaveCount(0);
    await page.locator('[data-sb-scope="settings-open-labels"]').click();
    await expect(
      page.locator('[data-sb-scope="labels-admin-row"]').filter({ hasText: labelName }),
    ).toBeVisible({ timeout: 10_000 });
  });

  /* ── 缺陷 3：子任务勾选无反应 ───────────────────────────────── */
  test("REG-3 子任务勾选：checkbox 可点、进度更新、重开抽屉后仍已勾选", async ({ page }) => {
    test.setTimeout(60_000);
    await loginDemo(page);
    await createProject(page);
    await createTask(page, "子任务回归任务");
    await openDrawer(page, "子任务回归任务");

    await page.getByPlaceholder("添加子任务，回车保存…").fill("可被勾选的子任务");
    await page.keyboard.press("Enter");
    const subRow = page.locator('[data-sb-scope="drawer-sub-row"]').first();
    await expect(subRow).toBeVisible({ timeout: 10_000 });

    const cb = subRow.locator('input[type="checkbox"]');
    await expect(cb, "初始未勾选").not.toBeChecked();
    await cb.check();                       // 缺陷表现：点了完全没反应（无 onChange + readOnly）
    await expect(cb, "勾选后为 checked").toBeChecked({ timeout: 10_000 });
    await expect(page.locator('[data-sb-scope="drawer-sub-progress"]'), "进度 1/1")
      .toHaveText("1/1", { timeout: 10_000 });
    await expect(page.getByText("子任务已完成")).toBeVisible({ timeout: 10_000 });

    // 落库校验：关掉抽屉重开，仍应是勾选态。
    // 只修 onChange 而不修「PATCH 只读字段 state_group」的话，这里会退回未勾选。
    await page.getByRole("button", { name: "关闭" }).click();
    await expect(page.locator("aside")).toHaveCount(0);
    await openDrawer(page, "子任务回归任务");
    await expect(
      page.locator('[data-sb-scope="drawer-sub-row"] input[type="checkbox"]').first(),
    ).toBeChecked({ timeout: 10_000 });
    await expect(page.locator('[data-sb-scope="drawer-sub-progress"]')).toHaveText("1/1");
  });

  /* ── 缺陷 4：上传附件按钮点了没反应 ─────────────────────────── */
  test("REG-4 上传附件：选择文件后上传成功并出现在列表（含 download_url）", async ({ page }) => {
    test.setTimeout(60_000);
    await loginDemo(page);
    await createProject(page);
    await createTask(page, "附件回归任务");
    await openDrawer(page, "附件回归任务");
    await page.locator('[data-tab-key="attachments"]').click();
    await expect(page.locator('[data-sb-scope="drawer-attachments"]')).toBeVisible();

    // 隐藏 input 走 setInputFiles（等价于用户点「＋ 上传附件」后选文件）
    await page.locator('input[type="file"]').setInputFiles({
      name: "regression.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("sprint-1 附件回归\n"),
    });

    const row = page.locator('[data-sb-scope="drawer-attachments-row"]').first();
    await expect(row, "上传完成后列表出现文件行").toBeVisible({ timeout: 20_000 });
    await expect(row).toContainText("regression.txt");
    await expect(page.locator('[data-sb-scope="drawer-attachments-count"]')).toHaveText("附件 1");
    // 下载按钮的 href 必须来自服务端 download_url；曾因 reverse 缺 app: 命名空间而为空
    await expect(row.getByRole("button", { name: /下载/ })).toBeVisible();
    await expect(
      page.getByText("已上传 regression.txt"),
      "成功 toast（失败时应出现具体原因而不是静默）",
    ).toBeVisible({ timeout: 10_000 });
  });

  /* ── 缺陷 6：抽屉里改不了描述（只读 dangerouslySetInnerHTML） ── */
  test("REG-6 抽屉描述可编辑：输入 → 失焦落库 → 重开仍在", async ({ page }) => {
    test.setTimeout(60_000);
    await loginDemo(page);
    await createProject(page);
    await createTask(page, "描述回归任务");
    await openDrawer(page, "描述回归任务");

    const desc = page.locator('[data-sb-scope="drawer-desc-input"]');
    await expect(desc, "描述区是可编辑控件（不是只读渲染）").toHaveAttribute("contenteditable", "true");
    await desc.click();
    await page.keyboard.type("这是一段可编辑的描述");
    // 失焦落库：点标题区制造 blur
    await page.locator('[data-sb-scope="drawer-title-input"], aside h2').first().click();
    await expect(page.getByText("已保存")).toBeVisible({ timeout: 10_000 });

    // 落库校验：关掉抽屉重开，描述仍在
    await page.getByRole("button", { name: "关闭" }).click();
    await expect(page.locator("aside")).toHaveCount(0);
    await openDrawer(page, "描述回归任务");
    await expect(page.locator('[data-sb-scope="drawer-desc-input"]'))
      .toContainText("这是一段可编辑的描述", { timeout: 10_000 });
  });

  /* ── 缺陷 7：属性区七行只有「标签」可改，其余全是只读文本 ──── */
  test("REG-7 属性区行内编辑：优先级 / 负责人 / 开始 / 截止 选中即提交并落库", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    await createProject(page);
    await createTask(page, "属性回归任务");
    await openDrawer(page, "属性回归任务");

    // 优先级（C.23：行内编辑 ▾ 下拉，选中即提交）
    await page.getByRole("button", { name: "修改优先级" }).click();
    await expect(page.getByRole("menu").filter({ hasText: "紧急" })).toBeVisible();
    await page.getByRole("menuitem", { name: "紧急" }).click();
    await expect(page.getByText("已保存")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByRole("button", { name: "修改优先级" })).toContainText("紧急");

    // 负责人（后端只给 assignee_ids，前端要靠成员表解析姓名）
    await page.getByRole("button", { name: "修改负责人" }).click();
    await expect(page.getByRole("menu").filter({ hasText: "未分配" })).toBeVisible();
    await expect(page.getByRole("menuitem", { name: "张三" })).toBeVisible({ timeout: 10_000 });
    await page.getByRole("menuitem", { name: "张三" }).click();
    await expect(page.getByText("已保存")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByRole("button", { name: "修改负责人" })).toContainText("张三");

    // 开始 / 截止：真日期控件（此前是纯文本，连输入口都没有）
    await page.getByLabel("开始日期").fill("2026-09-01");
    await expect(page.getByLabel("开始日期")).toHaveValue("2026-09-01");
    await page.getByLabel("截止日期").fill("2026-09-30");
    await expect(page.getByLabel("截止日期")).toHaveValue("2026-09-30");
    // 乐观更新先把值回显出来，再等落库完成（「已保存」是 1.8s 的瞬时提示，
    // 不适合当作"上一步已完成"的信号——两次连续保存会互相误判）
    await expect(page.getByText("已保存")).toBeVisible({ timeout: 10_000 });

    // 落库校验：关掉抽屉重开，四项都还在
    await page.getByRole("button", { name: "关闭" }).click();
    await expect(page.locator("aside")).toHaveCount(0);
    await openDrawer(page, "属性回归任务");
    await expect(page.getByRole("button", { name: "修改优先级" })).toContainText("紧急", { timeout: 10_000 });
    await expect(page.getByRole("button", { name: "修改负责人" })).toContainText("张三");
    await expect(page.getByLabel("开始日期")).toHaveValue("2026-09-01");
    await expect(page.getByLabel("截止日期")).toHaveValue("2026-09-30");
  });

  /* ── 缺陷 5：卡片拖不进「已取消」 ───────────────────────────── */
  test("REG-5 看板：卡片可拖入「已取消」列并落库", async ({ page }) => {
    test.setTimeout(60_000);
    await loginDemo(page);
    await createProject(page);
    await createTask(page, "拖拽回归任务");

    const card = page.locator("article").filter({ hasText: "拖拽回归任务" }).first();
    const cancelledCol = page.locator('section[data-col="cancelled"]');
    await expect(cancelledCol, "看板渲染「已取消」列").toBeVisible();

    // HTML5 原生 DnD：Playwright 在 Chromium 下会派发真实 drag 事件
    await card.dragTo(cancelledCol);
    await expect(
      cancelledCol.locator("article").filter({ hasText: "拖拽回归任务" }),
      "卡片落入「已取消」列",
    ).toBeVisible({ timeout: 15_000 });
    // BOARD-002 §3.3 / E2E-3：拖入已取消要有可见反馈
    await expect(page.getByText(/已取消，可拖回/)).toBeVisible({ timeout: 10_000 });

    // 落库校验：刷新页面后仍在「已取消」列
    await page.reload();
    await expect(
      page.locator('section[data-col="cancelled"] article').filter({ hasText: "拖拽回归任务" }),
    ).toBeVisible({ timeout: 15_000 });
  });
});
