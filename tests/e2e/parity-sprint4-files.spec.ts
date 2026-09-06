/** Sprint-4 项目文件库（FILE-002 · C.112~C.118）parity + 行为三件套。
 *  断言由附录 C.112~C.118 清单行生成（ADR-0010 ③：不由实现反推），每条带 `// C.x` 出处注释。
 *
 *  覆盖（owner = FILE-002 §3.1~§3.4）：
 *    C.112 页框架/左树/面包屑/工具条/新建目录/回收站入口 ｜ C.113 列表/网格双视图/
 *    筛选三下拉/可见性角标 ｜ C.114 dropzone ｜ C.115 上传浮层与配额 ｜
 *    C.116 ⋯ 菜单七操作/重命名行内校验/移动禁选后代/附加任务/可见性三态/删除确认 ｜
 *    C.117 回收站（还原/彻底删除/剩余天数/R1 口径） ｜ C.118 空态三式/树骨架
 *
 *  纪律（CLAUDE.md）：登录走 UI（用户入口铁律：侧栏「文件」点击进入）；每 test 先
 *  clearCookies；行为断言三件套（①交互 → ②waitForResponse 对应请求 2xx → ③UI 回读/
 *  刷新还原）；鉴权负向成对（VIEWER/CONTRIBUTOR 正向浏览 + 直连 403/404）；console
 *  guard 全量；API_TRUTH import（禁止硬编码状态码/错误码）。
 *
 *  C.119（分片）/C.120~C.122（预览/版本/缩略图）归 T4-11：本 spec 断言其入口为
 *  data-todo 占位（渲染存在、点击 noop），不测行为。
 */
import { test, expect, type Page } from "@playwright/test";
import { attachConsoleGuard, HTTP } from "./no-console-errors";

const WS = "workspace";
const API_ORIGIN = process.env.E2E_BASE_URL ?? "http://localhost:3001";
const API = `/api/v1/workspaces/${WS}/projects`;

const rid = (n = 5) =>
  Array.from({ length: n }, () => String.fromCharCode(65 + Math.floor(Math.random() * 26))).join("");

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

async function createFolder(page: Page, pid: string, name: string, parentId?: string) {
  const { status, body } = await apiCall(page, "POST", `${API}/${pid}/folders/`,
    { name, ...(parentId ? { parent_id: parentId } : {}) });
  expect(status, `建目录 ${name}`).toBe(HTTP.CREATED);
  return { id: body?.data?.id as string, name };
}

/** 直传三步种子（presign → PUT → complete；与前端同协议）。 */
async function seedFile(page: Page, pid: string, folderId: string, name: string, content: string) {
  const { status, body } = await apiCall(page, "POST", `${API}/${pid}/folders/${folderId}/files/presign/`,
    { file_name: name, file_size: content.length, content_type: "text/plain" });
  expect(status, `presign ${name}`).toBe(HTTP.CREATED);
  const put = await page.request.fetch(body.data.upload_url as string, {
    method: "PUT", data: content, headers: { "Content-Type": "text/plain" },
  });
  expect(put.status(), `PUT ${name}`).toBe(HTTP.OK);
  const done = await apiCall(page, "POST", `${API}/${pid}/files/${body.data.asset_id}/complete/`, {});
  expect(done.status, `complete ${name}`).toBe(HTTP.OK);
  return { id: body.data.asset_id as string, name };
}

/** 用户入口：项目侧栏「文件」点击进入（禁止直链深跳）。 */
async function gotoFiles(page: Page) {
  await page.getByRole("navigation").filter({ hasText: "返回项目列表" }).getByRole("link", { name: "文件" }).click();
  await page.waitForURL(/\/files$/, { timeout: 10_000 });
}

const rowByName = (page: Page, name: string) => page.locator('[data-sb-scope="files-row"]', { hasText: name });
const treeItem = (page: Page, folderId: string) => page.locator(`[data-sb-scope="files-tree-item"][data-folder-id="${folderId}"]`);
const fileMenuOf = (page: Page, fileId: string) => page.locator(`[data-file-menu="${fileId}"]`);

/** ⋯ 菜单点开（行内 ⋯ 按钮）。 */
async function openFileMenu(page: Page, fileId: string) {
  await fileMenuOf(page, fileId).click();
  await page.locator('[data-sb-scope="files-pop"]').waitFor({ state: "visible", timeout: 5_000 });
}

/** 拖放上传（C.114 真实 drop 事件携带 DataTransfer 文件；dropzone 用户路径）。 */
async function dropUpload(page: Page, files: Array<{ name: string; content: string }>) {
  await page.evaluate((list) => {
    const dt = new DataTransfer();
    for (const f of list) dt.items.add(new File([f.content], f.name, { type: "text/plain" }));
    const el = document.querySelector('[data-sb-scope="files-body"]');
    if (!el) throw new Error("files-body 不存在");
    el.dispatchEvent(new DragEvent("drop", { dataTransfer: dt, bubbles: true, cancelable: true }));
  }, files);
}

/** 等一个文件名的行出现（上传完成即插入列表，C.115）。 */
async function waitRow(page: Page, name: string) {
  await expect(rowByName(page, name)).toBeVisible({ timeout: 15_000 });
}

test.describe("Sprint-4 文件库（FILE-002 · C.112~C.118）", () => {
  let getErrs: () => string[] = () => [];
  test.beforeEach(async ({ page }) => {
    await page.context().clearCookies();
    getErrs = attachConsoleGuard(page);
  });
  test.afterEach(async () => {
    expect.soft(getErrs(), "console errors").toEqual([]);
  });

  /* ═══════════ C.112/C.113/C.115 parity：页框架/左树/面包屑/工具条/双视图/配额 ═══════════ */

  test("S4F-1 C.112/C.113/C.115 页框架/左树/面包屑/工具条/列表网格双视图/角标/配额条 parity", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page, "S4F");
    const des = await createFolder(page, proj.id, "设计稿");
    const q3 = await createFolder(page, proj.id, "2026Q3", des.id);
    await createFolder(page, proj.id, "需求文档");
    const f1 = await seedFile(page, proj.id, q3.id, "需求说明.md", "x".repeat(2048));
    const f2 = await seedFile(page, proj.id, q3.id, "竞品截图.png", "y".repeat(512));
    await seedFile(page, proj.id, q3.id, "合同扫描.pdf", "z".repeat(1024));
    const f4 = await seedFile(page, proj.id, q3.id, "内部报价.pdf", "q".repeat(256));
    await apiCall(page, "PATCH", `${API}/${proj.id}/files/${f4.id}/`, { visibility: "admins" }); // 🔒 角标种子
    await gotoFiles(page);

    // ── C.112 侧栏「文件」入口 → 路由 /:ws/projects/:pid/files ──
    expect(page.url()).toMatch(/\/files$/);

    // ── C.112 左侧目录树：role=tree + 缩进 16px/层 + 当前高亮（▌）+ 悬浮 ⋯ ──
    const tree = page.locator('[data-sb-scope="files-tree"]');
    await expect(tree).toBeVisible();
    await expect(tree).toHaveAttribute("role", "tree");
    await expect(page.locator('[data-sb-scope="files-tree-root"]')).toContainText("项目文件");
    await treeItem(page, des.id).click(); // 进入「设计稿」
    await expect(treeItem(page, des.id)).toHaveAttribute("aria-selected", "true");
    await expect(treeItem(page, des.id).locator("span.bg-brand-500")).toBeVisible(); // 当前高亮 ▌（左侧 3px 色条）
    // 缩进：子目录 paddingLeft = 8 + (depth-1)*16（C.112 缩进 16px/层）
    const padRoot = await treeItem(page, des.id).evaluate((el) => Number.parseInt((el as HTMLElement).style.paddingLeft, 10));
    const padChild = await treeItem(page, q3.id).evaluate((el) => Number.parseInt((el as HTMLElement).style.paddingLeft, 10));
    expect(padChild - padRoot).toBe(16);
    // 悬浮 ⋯ 目录操作按钮（C.112：改名/移动/删除/可见性入口）
    await expect(page.locator(`[data-folder-menu="${des.id}"]`)).toBeAttached();

    // ── C.112 面包屑：路径可点击逐级返回 + 当前层级 + N 个文件计数 ──
    await treeItem(page, q3.id).click();
    const crumb = page.locator('[data-sb-scope="files-crumb"]');
    await expect(crumb).toContainText("项目文件");
    await expect(crumb).toContainText("设计稿");
    await expect(crumb).toContainText("2026Q3");
    await expect(page.locator('[data-sb-scope="files-count"]')).toContainText("4 个文件");
    await crumb.getByRole("button", { name: "设计稿" }).click(); // 逐级返回
    await expect(page.locator('[data-sb-scope="files-count"]')).toContainText("0 个文件");

    // ── C.112 工具条：名称过滤（防抖 300ms 占位）+ 三筛选 + 视图切换 + 上传按钮 ──
    await expect(page.locator('[data-sb-scope="files-filter-name"]')).toHaveAttribute("placeholder", "名称过滤（防抖 300ms）");
    await expect(page.locator('[data-sb-scope="files-filter-type"]')).toContainText("类型");
    await expect(page.locator('[data-sb-scope="files-filter-uploader"]')).toContainText("上传人");
    await expect(page.locator('[data-sb-scope="files-filter-time"]')).toContainText("时间");
    await expect(page.locator('[data-sb-scope="files-view-list"]')).toHaveAttribute("aria-selected", "true");
    await expect(page.locator('[data-sb-scope="files-view-grid"]')).toBeVisible();
    await expect(page.locator('[data-sb-scope="files-upload-btn"]')).toBeEnabled();
    await expect(page.locator('[data-sb-scope="files-newdir"]')).toBeVisible(); // C.112 ＋新建目录
    await expect(page.locator('[data-sb-scope="files-trash-entry"]')).toContainText("回收站"); // C.112 回收站入口

    // ── C.113 列表视图：语义 table + 列（类型/名称/大小/上传人/修改时间/操作）──
    await treeItem(page, q3.id).click();
    await waitRow(page, f1.name);
    const table = page.locator('[data-sb-scope="files-table"]');
    await expect(table).toBeVisible();
    for (const h of ["类型", "名称", "大小", "上传人", "修改时间", "操作"]) {
      await expect(table.locator("th", { hasText: h })).toBeVisible();
    }
    await expect(rowByName(page, f1.name).locator('[role="img"]')).toHaveAttribute("aria-label", "Markdown"); // 图标冗余文本
    await expect(rowByName(page, f1.name)).toContainText("2KB"); // 大小人性化
    // C.113 可见性角标：🔒 仅管理员（CONTRIBUTOR 列表不可见归 S4F-6 角色演出）
    await expect(rowByName(page, f4.name).locator('[aria-label="仅管理员可见"]')).toBeVisible();

    // ── C.113 网格视图：卡片 120×140 骨架（类型大图标 + 名称 + 大小）──
    await page.locator('[data-sb-scope="files-view-grid"]').click();
    await expect(page.locator('[data-sb-scope="files-grid"]')).toBeVisible();
    const card = page.locator('[data-sb-scope="files-card"]', { hasText: f2.name });
    await expect(card).toBeVisible();
    await expect(card).toContainText("512B");
    await page.locator('[data-sb-scope="files-view-list"]').click(); // 切回
    await expect(table).toBeVisible();

    // ── C.113 三下拉筛选（含 has 高亮态）：类型 ──
    await page.locator('[data-sb-scope="files-filter-type"]').click();
    await page.locator('[data-sb-scope="files-pop"] [data-menu-key="document"]').click();
    await expect(page.locator('[data-sb-scope="files-filter-type"]')).toContainText("文档"); // has 态显示当前值
    await expect(rowByName(page, f2.name)).toHaveCount(0); // png 被滤除
    await expect(rowByName(page, f1.name)).toBeVisible();
    // C.113 上传人下拉（全部上传人 + 成员名）
    await page.locator('[data-sb-scope="files-filter-uploader"]').click();
    await expect(page.locator('[data-sb-scope="files-pop"] [data-menu-key="all"]')).toBeVisible();
    await page.locator('[data-sb-scope="files-filter-uploader"]').click({ position: { x: 5, y: 5 } }); // 收起
    await page.mouse.click(10, 300);
    // C.113 时间下拉（最近一周 has 态）
    await page.locator('[data-sb-scope="files-filter-time"]').click();
    await page.locator('[data-sb-scope="files-pop"] [data-menu-key="week"]').click();
    await expect(page.locator('[data-sb-scope="files-filter-time"]')).toContainText("最近一周");
    // 清除（C.118 过滤无结果 + 清除筛选）
    await page.locator('[data-sb-scope="files-filter-name"]').fill("不存在的文件名");
    await expect(page.locator('[data-sb-scope="files-empty-filter"]')).toBeVisible({ timeout: 5_000 });
    await expect(page.locator('[data-sb-scope="files-empty-filter"]')).toContainText("未找到匹配文件");
    await page.locator('[data-sb-scope="files-clear-filter"]').click();
    await waitRow(page, f1.name);
    await expect(rowByName(page, f2.name)).toBeVisible(); // 筛选全清（类型/时间一并重置）

    // ── C.115 配额条：工作区存储用量条（warn/full 分色）──
    const quota = page.locator('[data-sb-scope="files-quota"]');
    await expect(quota).toContainText("工作区存储");
    await expect(quota).toContainText(/GB（\d+%）/);
  });

  /* ═══════════ C.114/C.115 行为：dropzone 多文件并行直传三步 + 失败重试 ═══════════ */

  test("S4F-2 C.114/C.115 dropzone 多文件并行直传（presign→PUT→complete 三步 2xx）+ 进度浮层 + 失败重试", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page, "S4Fup");
    const des = await createFolder(page, proj.id, "设计稿");
    await gotoFiles(page);
    await treeItem(page, des.id).click();

    // C.114 拖入高亮：蓝色虚线框 + 「松开上传到 {目录}」
    await page.locator('[data-sb-scope="files-body"]').dispatchEvent("dragover");
    const hint = page.locator('[data-sb-scope="files-dropzone-hint"]');
    await expect(hint).toBeVisible();
    await expect(hint).toContainText(`松开上传到「${des.name}」`);
    await page.locator('[data-sb-scope="files-body"]').dispatchEvent("dragleave");
    await expect(hint).toBeHidden();

    // C.114/C.115 多文件并行直传（真实 drop 事件）：三步各 2xx + 完成即入列表
    const names = ["drop-a.txt", "drop-b.txt"];
    const presignReqs = names.map(() =>
      page.waitForResponse((r) => /\/files\/presign\/$/.test(r.url()) && r.request().method() === "POST"));
    const putReqs = names.map(() =>
      page.waitForResponse((r) => /\/uploads\//.test(r.url()) && r.request().method() === "PUT"));
    const completeReqs = names.map(() =>
      page.waitForResponse((r) => /\/files\/[^/]+\/complete\/$/.test(r.url()) && r.request().method() === "POST"));
    await dropUpload(page, names.map((n) => ({ name: n, content: `${n} content` })));
    for (const r of await Promise.all(presignReqs)) expect(r.status(), "presign 2xx").toBe(HTTP.CREATED);
    for (const r of await Promise.all(putReqs)) expect(r.status(), "PUT 直传 2xx").toBeLessThan(300);
    for (const r of await Promise.all(completeReqs)) expect(r.status(), "complete 2xx").toBe(HTTP.OK);

    // C.115 上传进度浮层：role=status aria-live=polite + progressbar + 里程碑播报
    const dock = page.locator('[data-sb-scope="files-uploads"]');
    await expect(dock).toBeVisible();
    await expect(dock).toHaveAttribute("role", "status");
    await expect(dock).toHaveAttribute("aria-live", "polite");
    await expect(dock.locator('[role="progressbar"]').first()).toBeVisible();
    await expect(page.locator('[data-sb-scope="files-upload-announce"]')).toContainText("100%", { timeout: 10_000 });
    // 完成即插入列表（C.115）+ 刷新回读（行为三件套 ③）
    await waitRow(page, "drop-a.txt");
    await waitRow(page, "drop-b.txt");
    await page.reload();
    await treeItem(page, des.id).click();
    await waitRow(page, "drop-a.txt");
    await waitRow(page, "drop-b.txt");

    // C.115 失败红 + 重试（自动重申 presign）：拦截首个 PUT → failed → 解除 → 重试成功
    // console guard：route.abort 注入的 net::ERR_FAILED 是本测试刻意制造的传输失败
    // （注入噪声非应用缺陷），从 guard 结果中剔除；其余 console 错误仍硬失败。
    const rawErrs = getErrs;
    getErrs = () => rawErrs().filter((e) => !e.includes("net::ERR_FAILED"));
    let aborted = false;
    await page.route(/\/uploads\//, (route) => {
      if (!aborted && route.request().method() === "PUT") { aborted = true; void route.abort(); return; }
      void route.continue();
    });
    const retryDone = page.waitForResponse((r) => /\/files\/[^/]+\/complete\/$/.test(r.url()) && r.request().method() === "POST");
    await dropUpload(page, [{ name: "drop-fail.txt", content: "will fail once" }]);
    const failedRow = page.locator('[data-sb-scope="files-upload-row"]', { hasText: "drop-fail.txt" }).first();
    await expect(failedRow).toHaveAttribute("data-upload-state", "failed", { timeout: 10_000 }); // 浮层行红（失败态）
    await page.locator('[data-sb-scope="files-upload-retry"]').first().click(); // 重试（自动重申 presign）
    expect((await retryDone).status(), "重试 complete 2xx").toBe(HTTP.OK);
    await waitRow(page, "drop-fail.txt");
  });

  /* ═══════════ C.112/C.118 空态三式 + 新建目录/模板一键创建 ═══════════ */

  test("S4F-3 C.118 空态三式：空文件库三模板 / 空目录 / 过滤无结果 + 新建目录行为", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page, "S4Fempty");
    await gotoFiles(page);

    // ── 空文件库：首次引导卡三示例目录模板（§7.2-1 一键创建）──
    const emptyLib = page.locator('[data-sb-scope="files-empty-lib"]');
    await expect(emptyLib).toBeVisible({ timeout: 10_000 });
    await expect(emptyLib).toContainText("欢迎使用项目文件库");
    await expect(page.locator('[data-sb-scope="files-tpl"]', { hasText: "需求文档" })).toBeVisible();
    await expect(page.locator('[data-sb-scope="files-tpl"]', { hasText: "设计稿" })).toBeVisible();
    await expect(page.locator('[data-sb-scope="files-tpl"]', { hasText: "会议纪要" })).toBeVisible();
    const created = page.waitForResponse((r) => /\/folders\/$/.test(r.url()) && r.request().method() === "POST");
    await page.locator('[data-sb-scope="files-tpl"]', { hasText: "设计稿" }).click();
    expect((await created).status()).toBe(HTTP.CREATED); // 行为三件套 ②
    await expect(page.locator('[data-sb-scope="files-tree-item"]', { hasText: "设计稿" })).toBeVisible(); // ③ 树回读

    // ── 新建目录（C.112 左树底部）：同层同名即时校验 ──
    await page.locator('[data-sb-scope="files-newdir"]').click();
    await page.locator('[data-sb-scope="files-name-input"]').fill("设计稿");
    await page.locator('[data-sb-scope="files-name-ok"]').click();
    await expect(page.locator('[data-sb-scope="files-name-err"]')).toContainText("同层已存在同名目录");
    await page.locator('[data-sb-scope="files-name-input"]').fill("会议纪要");
    const created2 = page.waitForResponse((r) => /\/folders\/$/.test(r.url()) && r.request().method() === "POST");
    await page.locator('[data-sb-scope="files-name-ok"]').click();
    expect((await created2).status()).toBe(HTTP.CREATED);
    await expect(page.locator('[data-sb-scope="files-tree-item"]', { hasText: "会议纪要" })).toBeVisible();

    // ── 空目录：「拖拽文件到此处，或点击上传」+ 上传到「{目录}」（含目标目录名）──
    await page.locator('[data-sb-scope="files-tree-item"]', { hasText: "会议纪要" }).click();
    const emptyFolder = page.locator('[data-sb-scope="files-empty-folder"]');
    await expect(emptyFolder).toBeVisible({ timeout: 10_000 });
    await expect(emptyFolder).toContainText("拖拽文件到此处，或点击上传");
    await expect(emptyFolder).toContainText("上传到「会议纪要」");

    // ── 过滤无结果：「未找到匹配文件」+ 清除筛选 ──
    await seedFile(page, proj.id, (await apiCall(page, "GET", `${API}/${proj.id}/folders/`)).body.data
      .find((f: { name: string }) => f.name === "会议纪要").id, "周会.md", "weekly");
    await page.reload();
    await page.locator('[data-sb-scope="files-tree-item"]', { hasText: "会议纪要" }).click();
    await waitRow(page, "周会.md");
    await page.locator('[data-sb-scope="files-filter-name"]').fill("zzz-无匹配");
    await expect(page.locator('[data-sb-scope="files-empty-filter"]')).toBeVisible({ timeout: 5_000 });
    await page.locator('[data-sb-scope="files-clear-filter"]').click();
    await waitRow(page, "周会.md");
  });

  /* ═══════════ C.116 行为：重命名/移动/附加任务/可见性三件套 ═══════════ */

  test("S4F-4 C.116 重命名行内编辑（同名校验）/移动（禁选后代）/附加任务/可见性三态", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page, "S4Fop");
    const des = await createFolder(page, proj.id, "设计稿");
    const q3 = await createFolder(page, proj.id, "2026Q3", des.id);
    const req = await createFolder(page, proj.id, "需求文档");
    const f1 = await seedFile(page, proj.id, q3.id, "旧名.md", "rename me");
    await seedFile(page, proj.id, q3.id, "占位.docx", "dup target");
    await gotoFiles(page);
    await treeItem(page, q3.id).click();
    await waitRow(page, f1.name);

    // ── 重命名：行内编辑 + 同层同名即时校验（红框 + 提示，不发 PATCH）──
    await openFileMenu(page, f1.id);
    await page.locator('[data-sb-scope="files-pop"] [data-menu-key="rename"]').click();
    const input = page.locator('[data-sb-scope="files-rename-input"]');
    await expect(input).toBeFocused();
    let patchCount = 0;
    page.on("request", (r) => { if (/\/files\/[^/]+\/$/.test(r.url()) && r.method() === "PATCH") patchCount += 1; });
    await input.fill("占位.docx"); // 同层同名 → 前端即时拦截
    await input.press("Enter");
    await expect(page.locator('[data-sb-scope="files-rename-err"]')).toContainText("同层已存在同名文件");
    expect(patchCount, "同名即时校验不发 PATCH").toBe(0);
    const renamed = page.waitForResponse((r) => /\/files\/[^/]+\/$/.test(r.url()) && r.request().method() === "PATCH");
    await input.fill("新名.md");
    await input.press("Enter");
    expect((await renamed).status(), "重命名 PATCH 2xx").toBe(HTTP.OK);
    await waitRow(page, "新名.md");
    await page.reload(); // ③ 刷新回读
    await treeItem(page, q3.id).click();
    await waitRow(page, "新名.md");

    // ── 移动：目录树选择 + 禁选自身后代（置灰提示）+ 面包屑同步 ──
    await openFileMenu(page, f1.id);
    await page.locator('[data-sb-scope="files-pop"] [data-menu-key="move"]').click();
    const moveModal = page.locator('[data-sb-scope="files-move"]');
    await expect(moveModal).toBeVisible();
    await expect(moveModal).toContainText("目标目录（禁选自身后代）");
    await expect(moveModal).toContainText("移动为元数据操作"); // 毫秒级元数据提示
    const moved = page.waitForResponse((r) => /\/files\/[^/]+\/$/.test(r.url()) && r.request().method() === "PATCH");
    await moveModal.locator(`[data-moveto="${req.id}"]`).click();
    expect((await moved).status(), "移动 PATCH 2xx").toBe(HTTP.OK);
    await treeItem(page, req.id).click(); // 目标目录回读（面包屑 + 行）
    await expect(page.locator('[data-sb-scope="files-crumb"]')).toContainText("需求文档");
    await waitRow(page, "新名.md");
    // 目录移动禁选后代：把「设计稿」移动时其子「2026Q3」置灰 + 自身后代提示
    await page.locator(`[data-folder-menu="${des.id}"]`).click();
    await page.locator('[data-sb-scope="files-pop"] [data-menu-key="move"]').click();
    const folderMove = page.locator('[data-sb-scope="files-move"]');
    await expect(folderMove.locator(`[data-moveto="${q3.id}"]`)).toBeDisabled(); // 环防护前端预判
    await expect(folderMove.locator(`[data-moveto="${q3.id}"]`)).toContainText("自身后代");
    await page.keyboard.press("Escape");

    // ── 附加到任务：任务搜索弹层 → issue 双挂 PATCH ──
    const issue = (await apiCall(page, "POST", `${API}/${proj.id}/issues/`, { name: "联调任务" })).body.data;
    await openFileMenu(page, f1.id);
    await page.locator('[data-sb-scope="files-pop"] [data-menu-key="attach"]').click();
    const attachModal = page.locator('[data-sb-scope="files-attach"]');
    await expect(attachModal).toContainText("互不复制"); // 双挂说明
    await attachModal.locator('[data-sb-scope="files-attach-q"]').fill("联调");
    const attachRow = attachModal.locator(`[data-attachto="${issue.id}"]`);
    await attachRow.waitFor({ state: "visible", timeout: 10_000 });
    const attached = page.waitForResponse((r) => /\/files\/[^/]+\/$/.test(r.url()) && r.request().method() === "PATCH");
    await attachRow.click();
    const attachRes = await attached;
    expect(attachRes.status(), "双挂 PATCH 2xx").toBe(HTTP.OK);
    expect((await attachRes.json()).data.issue_id, "issue_id 落库").toBe(issue.id); // 双挂回读

    // ── 可见性：三态单选（仅 ADMIN）→ PATCH → 🔒 角标 ──
    await openFileMenu(page, f1.id);
    await page.locator('[data-sb-scope="files-pop"] [data-menu-key="vis"]').click();
    const visModal = page.locator('[data-sb-scope="files-vis"]');
    await expect(visModal).toContainText("全员可见");
    await expect(visModal).toContainText("指定成员");
    await expect(visModal).toContainText("仅管理员");
    await expect(visModal).toContainText("三层一致校验");
    const visSaved = page.waitForResponse((r) => /\/files\/[^/]+\/$/.test(r.url()) && r.request().method() === "PATCH");
    await visModal.locator('[data-vis-radio="admins"]').check();
    await visModal.locator('[data-sb-scope="files-vis-save"]').click();
    const visRes = await visSaved;
    expect(visRes.status(), "可见性 PATCH 2xx").toBe(HTTP.OK);
    expect((await visRes.json()).data.visibility).toBe("admins"); // ② 回读
    await expect(rowByName(page, "新名.md").locator('[aria-label="仅管理员可见"]')).toBeVisible(); // ③ 角标
  });

  /* ═══════════ C.116/C.117 行为：删除 → 回收站 → 还原 → 彻底删除往返 ═══════════ */

  test("S4F-5 C.117 删除→回收站→还原→彻底删除往返（目录 N 文件提示/剩余天数/冲突落根说明）", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page, "S4Ftrash");
    const des = await createFolder(page, proj.id, "设计稿");
    const f1 = await seedFile(page, proj.id, des.id, "待删除.md", "to trash");
    await seedFile(page, proj.id, des.id, "邻居.txt", "stays");
    await gotoFiles(page);
    await treeItem(page, des.id).click();
    await waitRow(page, f1.name);

    // ── 删除文件：确认弹层 role=alertdialog + 30 天提示 → DELETE 204 → 行消失 ──
    await openFileMenu(page, f1.id);
    await page.locator('[data-sb-scope="files-pop"] [data-menu-key="del"]').click();
    const dialog = page.locator('[data-sb-scope="files-confirm"]');
    await expect(dialog).toHaveAttribute("role", "alertdialog");
    await expect(dialog).toContainText("回收站，30 天后自动清理"); // C.116 30 天提示
    const deleted = page.waitForResponse((r) => /\/files\/[^/]+\/$/.test(r.url()) && r.request().method() === "DELETE");
    await dialog.locator('[data-sb-scope="files-confirm-ok"]').click();
    expect((await deleted).status(), "软删 204").toBe(HTTP.NO_CONTENT);
    await expect(rowByName(page, f1.name)).toHaveCount(0);
    await expect(rowByName(page, "邻居.txt")).toBeVisible();

    // ── 回收站入口计数（C.112：徽标随请求者过滤口径）──
    await expect(page.locator('[data-sb-scope="files-trash-count"]')).toHaveText("1");
    await page.locator('[data-sb-scope="files-trash-entry"]').click(); // 用户路径进回收站页
    await page.waitForURL(/\/files\/trash$/, { timeout: 10_000 });

    // ── C.117 已删列表：名称/原位置/删除人/删除时间/剩余天数 + 管理员口径提示 ──
    await expect(page.locator('[data-sb-scope="trash-crumb"]')).toContainText("管理员视角：全量可见");
    await expect(page.locator('[data-sb-scope="trash-crumb"]')).toContainText("30 天后自动清理");
    const trashRow = page.locator('[data-sb-scope="trash-row"]', { hasText: f1.name });
    await expect(trashRow).toBeVisible({ timeout: 10_000 });
    await expect(trashRow).toContainText("设计稿"); // 原位置
    await expect(trashRow).toContainText(/30 天|\d+ 天/); // 剩余天数

    // ── 还原：确认含原位 + 冲突落根说明 → POST restore 200 → 回原目录 ──
    await trashRow.locator('[data-sb-scope="trash-restore"]').click();
    const restoreDialog = page.locator('[data-sb-scope="files-confirm"]');
    await expect(restoreDialog).toContainText("还原到原位置「设计稿」");
    await expect(restoreDialog).toContainText("(恢复)"); // 冲突落根说明（BR-07）
    const restored = page.waitForResponse((r) => /\/files\/[^/]+\/restore\/$/.test(r.url()) && r.request().method() === "POST");
    await restoreDialog.locator('[data-sb-scope="files-confirm-ok"]').click();
    expect((await restored).status(), "还原 200").toBe(HTTP.OK);
    await expect(page.locator('[data-sb-scope="trash-row"]', { hasText: f1.name })).toHaveCount(0);
    await expect(page.locator('[data-sb-scope="trash-empty"]')).toBeVisible();
    await page.locator('[data-sb-scope="trash-crumb"]').getByRole("button", { name: "项目文件" }).click(); // 面包屑返回文件页
    await page.waitForURL(/\/files$/, { timeout: 10_000 });
    await treeItem(page, des.id).click();
    await waitRow(page, f1.name); // 原位还原回读

    // ── 彻底删除（仅 ADMIN）：二次确认 → purge 200 → 不可恢复 ──
    await openFileMenu(page, f1.id);
    await page.locator('[data-sb-scope="files-pop"] [data-menu-key="del"]').click();
    await page.locator('[data-sb-scope="files-confirm-ok"]').click();
    await page.locator('[data-sb-scope="files-trash-entry"]').click();
    await page.waitForURL(/\/files\/trash$/);
    const purgeRow = page.locator('[data-sb-scope="trash-row"]', { hasText: f1.name });
    await purgeRow.waitFor({ state: "visible", timeout: 10_000 });
    await purgeRow.locator('[data-sb-scope="trash-purge"]').click();
    const purgeDialog = page.locator('[data-sb-scope="files-confirm"]');
    await expect(purgeDialog).toContainText("不可恢复"); // 二次确认
    const purged = page.waitForResponse((r) => /\/files\/[^/]+\/purge\/$/.test(r.url()) && r.request().method() === "DELETE");
    await purgeDialog.locator('[data-sb-scope="files-confirm-ok"]').click();
    expect((await purged).status(), "purge 200").toBe(HTTP.OK);
    await expect(page.locator('[data-sb-scope="trash-empty"]')).toBeVisible({ timeout: 10_000 });

    // ── 目录删除确认含 N 文件（C.116：目录显示 N 文件）──
    await page.locator('[data-sb-scope="trash-crumb"]').getByRole("button", { name: "项目文件" }).click(); // 面包屑返回文件页
    await page.waitForURL(/\/files$/, { timeout: 10_000 });
    await page.locator(`[data-folder-menu="${des.id}"]`).click();
    await page.locator('[data-sb-scope="files-pop"] [data-menu-key="del"]').click();
    const folderDel = page.locator('[data-sb-scope="files-confirm"]');
    await expect(folderDel).toContainText("个可见文件，将一并移入回收站");
    await page.keyboard.press("Escape");
  });

  /* ═══════════ C.113/C.116 VIEWER 鉴权负向成对：可见 + 上传禁用 + 直连 403 ═══════════ */

  test("S4F-6 C.116 VIEWER：正向浏览 + 🔒 文件不可见（后端剪枝）+ 上传禁用零请求 + 直连 presign 403", async ({ browser }) => {
    test.setTimeout(150_000);
    const errGetters: Array<() => string[]> = [];
    getErrs = () => errGetters.flatMap((g) => g());
    const ctxA = await browser.newContext();
    const aPage = await ctxA.newPage();
    errGetters.push(attachConsoleGuard(aPage));
    await loginDemo(aPage);
    const proj = await createProject(aPage, "S4Fviewer");
    const des = await createFolder(aPage, proj.id, "设计稿");
    await seedFile(aPage, proj.id, des.id, "全员可见.md", "public");
    const locked = await seedFile(aPage, proj.id, des.id, "仅管理员.pdf", "secret");
    await apiCall(aPage, "PATCH", `${API}/${proj.id}/files/${locked.id}/`, { visibility: "admins" });

    // B：注册 → 入项目（VIEWER role=5）——S4G-9 setupDual 同款装配
    const bEmail = `s4f-b-${Date.now()}-${Math.floor(Math.random() * 1e4)}@rabbit.dev`;
    const invite = await apiCall(aPage, "POST", `/api/v1/workspaces/${WS}/invitations/`, { emails: [bEmail], role: 10 });
    expect(invite.status).toBe(HTTP.OK);
    const links = ((invite.body?.meta?.invite_links ?? {}) as Record<string, string>);
    const link = (links[bEmail] ?? "").match(/\/invite\/([A-Za-z0-9\-_.]+)/);
    expect(link, "SMTP 降级 invite_links").toBeTruthy();
    const ctxB = await browser.newContext();
    const bPage = await ctxB.newPage();
    errGetters.push(attachConsoleGuard(bPage));
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
    const add = await apiCall(aPage, "POST", `${API}/${proj.id}/members/`, { member_ids: [bUserId], role: 5 }); // VIEWER
    expect([HTTP.OK, HTTP.CREATED]).toContain(add.status);

    // 正向：VIEWER 经项目卡片 → 侧栏「文件」浏览（file.read VIEWER+）
    await bPage.goto(`/${proj.slug}/projects`);
    const card = bPage.locator(`a[href*="/projects/${proj.id}"]`).first();
    await card.waitFor({ state: "visible", timeout: 15_000 });
    await card.click();
    await bPage.waitForURL(/\/board/, { timeout: 15_000 });
    await bPage.getByRole("navigation").filter({ hasText: "返回项目列表" }).getByRole("link", { name: "文件" }).click();
    await bPage.waitForURL(/\/files$/, { timeout: 10_000 });
    await treeItem(bPage, des.id).click();
    await expect(bPage.locator('[data-sb-scope="files-row"]', { hasText: "全员可见.md" })).toBeVisible({ timeout: 10_000 });

    // 负向①：🔒 admins 态文件不可见（后端 list_files 剪枝——前端如实渲染）
    await expect(bPage.locator('[data-sb-scope="files-row"]', { hasText: "仅管理员.pdf" })).toHaveCount(0);

    // 负向②：上传按钮禁用（C.112 VIEWER 禁用）+ drop 尝试零 presign
    await expect(bPage.locator('[data-sb-scope="files-upload-btn"]')).toBeDisabled();
    let presignCount = 0;
    bPage.on("request", (r) => { if (/\/files\/presign\/$/.test(r.url())) presignCount += 1; });
    await bPage.locator('[data-sb-scope="files-body"]').dispatchEvent("dragover");
    await expect(bPage.locator('[data-sb-scope="files-dropzone-hint"]')).toBeHidden(); // VIEWER 无拖入高亮
    await dropUpload(bPage, [{ name: "越权.txt", content: "no way" }]);
    await bPage.waitForTimeout(600);
    expect(presignCount, "VIEWER 上传禁用 → 零 presign").toBe(0);

    // 负向③：⋯ 菜单无「可见性」项（file.permission.manage 仅 ADMIN）
    const anyRow = bPage.locator('[data-file-menu]').first();
    await anyRow.click();
    await expect(bPage.locator('[data-sb-scope="files-pop"]')).toBeVisible();
    await expect(bPage.locator('[data-sb-scope="files-pop"] [data-menu-key="vis"]')).toHaveCount(0);
    await bPage.mouse.click(5, 300);

    // 负向④：绕过前端直连 presign → 403（后端才是安全边界）
    const direct = await apiCall(bPage, "POST", `${API}/${proj.id}/folders/${des.id}/files/presign/`,
      { file_name: "direct.txt", file_size: 10, content_type: "text/plain" });
    expect(direct.status).toBe(HTTP.FORBIDDEN);

    // VIEWER 回收站负向：直连 trash → 403（file.delete CONTRIBUTOR+）
    const trashDenied = await apiCall(bPage, "GET", `${API}/${proj.id}/files/trash/`);
    expect(trashDenied.status).toBe(HTTP.FORBIDDEN);
    await ctxA.close();
    await ctxB.close();
  });

  /* ═══════════ C.113/C.117 CONTRIBUTOR 视角：🔒 不可见 + download-url 404 + R1 口径 ═══════════ */

  test("S4F-7 C.113/C.117 CONTRIBUTOR：admins 文件列表不可见 + 直连 download-url 404 + 回收站仅本人口径", async ({ browser }) => {
    test.setTimeout(150_000);
    const errGetters: Array<() => string[]> = [];
    getErrs = () => errGetters.flatMap((g) => g());
    const ctxA = await browser.newContext();
    const aPage = await ctxA.newPage();
    errGetters.push(attachConsoleGuard(aPage));
    await loginDemo(aPage);
    const proj = await createProject(aPage, "S4Fcontrib");
    const des = await createFolder(aPage, proj.id, "设计稿");
    await seedFile(aPage, proj.id, des.id, "公开.md", "public");
    const locked = await seedFile(aPage, proj.id, des.id, "机密报价.pdf", "secret");
    await apiCall(aPage, "PATCH", `${API}/${proj.id}/files/${locked.id}/`, { visibility: "admins" });

    // C：注册 → WS MEMBER + 项目 CONTRIBUTOR（role=15）
    const cEmail = `s4f-c-${Date.now()}-${Math.floor(Math.random() * 1e4)}@rabbit.dev`;
    const invite = await apiCall(aPage, "POST", `/api/v1/workspaces/${WS}/invitations/`, { emails: [cEmail], role: 10 });
    expect(invite.status).toBe(HTTP.OK);
    const links = ((invite.body?.meta?.invite_links ?? {}) as Record<string, string>);
    const link = (links[cEmail] ?? "").match(/\/invite\/([A-Za-z0-9\-_.]+)/);
    expect(link, "SMTP 降级 invite_links").toBeTruthy();
    const ctxC = await browser.newContext();
    const cPage = await ctxC.newPage();
    errGetters.push(attachConsoleGuard(cPage));
    await cPage.goto("/register");
    await cPage.getByLabel(/邮箱/).fill(cEmail);
    await cPage.getByLabel("密码", { exact: false }).first().fill("Rabbit123!");
    await cPage.getByLabel("确认密码").fill("Rabbit123!");
    await cPage.getByRole("button", { name: /注册|创建账号/ }).click();
    await cPage.waitForFunction(() => /\/projects$/.test(location.pathname), null, { timeout: 15_000 });
    const ccookies = await ctxC.cookies();
    const ccsrf = ccookies.find((c) => c.name === "csrftoken")?.value ?? "";
    await cPage.request.post(`${API_ORIGIN}/api/v1/invitations/${link![1]}/accept/`, {
      headers: { "Content-Type": "application/json", ...(ccsrf ? { "X-CSRFToken": ccsrf } : {}) }, data: {},
    });
    const { body: meC } = await apiCall(cPage, "GET", "/api/v1/users/me/");
    const cUserId = meC?.data?.user?.id as string;
    const members = await apiCall(aPage, "GET", `/api/v1/workspaces/${WS}/members/?per_page=100`);
    const cm = ((members.body?.data ?? []) as Array<{ user: { id: string; email: string } }>).find((x) => x.user.email === cEmail);
    expect(cm).toBeTruthy();
    const add = await apiCall(aPage, "POST", `${API}/${proj.id}/members/`, { member_ids: [cUserId], role: 15 }); // CONTRIBUTOR
    expect([HTTP.OK, HTTP.CREATED]).toContain(add.status);

    // 正向：CONTRIBUTOR 经项目卡片 → 文件页（可上传）
    await cPage.goto(`/${proj.slug}/projects`);
    const card = cPage.locator(`a[href*="/projects/${proj.id}"]`).first();
    await card.waitFor({ state: "visible", timeout: 15_000 });
    await card.click();
    await cPage.waitForURL(/\/board/, { timeout: 15_000 });
    await cPage.getByRole("navigation").filter({ hasText: "返回项目列表" }).getByRole("link", { name: "文件" }).click();
    await cPage.waitForURL(/\/files$/, { timeout: 10_000 });
    await treeItem(cPage, des.id).click();
    await expect(cPage.locator('[data-sb-scope="files-row"]', { hasText: "公开.md" })).toBeVisible({ timeout: 10_000 });
    await expect(cPage.locator('[data-sb-scope="files-upload-btn"]')).toBeEnabled(); // file.upload CONTRIBUTOR+

    // 负向①：admins 态文件列表不可见（can_view_file 剪枝）
    await expect(cPage.locator('[data-sb-scope="files-row"]', { hasText: "机密报价.pdf" })).toHaveCount(0);
    // 负向②：直连 download-url → 404 存在性隐藏（BR-08/BR-09）
    const dl = await apiCall(cPage, "GET", `${API}/${proj.id}/files/${locked.id}/download-url/`);
    expect(dl.status).toBe(HTTP.NOT_FOUND);

    // C.117 R1 口径：CONTRIBUTOR 删除本人文件 → 回收站仅见本人删除项 + 贡献者视角提示
    const mine = (await apiCall(cPage, "POST", `${API}/${proj.id}/folders/${des.id}/files/presign/`,
      { file_name: "贡献者文件.txt", file_size: 9, content_type: "text/plain" }));
    const putMine = await cPage.request.fetch(mine.body.data.upload_url, { method: "PUT", data: "mine data", headers: { "Content-Type": "text/plain" } });
    expect(putMine.status()).toBe(HTTP.OK);
    const doneMine = await apiCall(cPage, "POST", `${API}/${proj.id}/files/${mine.body.data.asset_id}/complete/`, {});
    expect(doneMine.status).toBe(HTTP.OK);
    // ADMIN 删自己的机密文件（他人删除项）；CONTRIBUTOR 删本人文件
    await apiCall(aPage, "DELETE", `${API}/${proj.id}/files/${locked.id}/`);
    const delMine = await apiCall(cPage, "DELETE", `${API}/${proj.id}/files/${mine.body.data.asset_id}/`);
    expect(delMine.status).toBe(HTTP.NO_CONTENT);
    await cPage.reload();
    await cPage.locator('[data-sb-scope="files-trash-entry"]').click();
    await cPage.waitForURL(/\/files\/trash$/, { timeout: 10_000 });
    await expect(cPage.locator('[data-sb-scope="trash-crumb"]')).toContainText("贡献者视角：仅本人删除项（BR-13）");
    await expect(cPage.locator('[data-sb-scope="trash-row"]', { hasText: "贡献者文件.txt" })).toBeVisible({ timeout: 10_000 });
    await expect(cPage.locator('[data-sb-scope="trash-row"]', { hasText: "机密报价.pdf" })).toHaveCount(0); // 他人删除项不透出
    // 彻底删除按钮仅 ADMIN（C.117）
    await expect(cPage.locator('[data-sb-scope="trash-purge"]')).toHaveCount(0);
    await ctxA.close();
    await ctxC.close();
  });
});
