/** Sprint-4 预览/版本/分享 + space 匿名页（FILE-003/FILE-004 · C.119~C.125）
 *  parity + 行为三件套。断言由附录 C.119~C.125 清单行生成（ADR-0010 ③），每条带 `// C.x` 出处注释。
 *
 *  覆盖（owner = FILE-003 §3.1~§3.5 / FILE-004 §3.1~§3.4）：
 *    C.120 预览抽屉五通道 + 排队/失败兜底 + 不可预览 + 文本超限 ｜ C.121 版本面板/
 *    对比双栏 diff/回滚三件套/上限提示 ｜ C.119 分片上传（并行/断点续传/暂停取消/MD5 片级核对）
 *    ｜ C.123 分享创建弹层 ｜ C.124 分享管理（延期/吊销二次确认/访问计数）｜
 *    C.122 缩略图落位（网格 + 列表悬浮 200ms）｜ C.125 space 匿名三态页。
 *
 *  纪律（CLAUDE.md）：用户入口铁律（登录 → 侧栏「文件」进入）；每 test 先 clearCookies；
 *  行为断言三件套（①交互 → ②waitForResponse 2xx → ③UI 回读/刷新还原）；console guard
 *  全量（route.abort 注入的网络错误按 S4F-2 先例过滤）；HTTP 常量 import no-console-errors。
 *
 *  space 域：apps/space dev server（3003，basename /spaces）由 CI/本机预启——
 *  spec 内 reachability 探测失败则 skip（说明：space 非常驻栈，起服脚本见
 *  package.json dev:space / 本文件头注释）。 */
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test, expect, type Page } from "@playwright/test";
import { execSync } from "node:child_process";
import { attachConsoleGuard, HTTP } from "./no-console-errors";

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
const SPACE_ORIGIN = process.env.E2E_SPACE_URL ?? "http://localhost:3003";
const SPACE_BASE = `${SPACE_ORIGIN}/spaces`; // react-router.config basename=/spaces（骨架既定）
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

async function createFolder(page: Page, pid: string, name: string) {
  const { status, body } = await apiCall(page, "POST", `${API}/${pid}/folders/`, { name });
  expect(status, `建目录 ${name}`).toBe(HTTP.CREATED);
  return { id: body?.data?.id as string, name };
}

/** 直传三步种子（presign → PUT → complete；binary 可传 Buffer；
 *  file_size 按 UTF-8 字节数申报——CJK 字符串 length ≠ 字节数，complete HEAD 会 400）。 */
async function seedFile(page: Page, pid: string, folderId: string, name: string, content: string | Buffer, mime: string) {
  const size = typeof content === "string" ? Buffer.byteLength(content, "utf8") : content.byteLength;
  const { status, body } = await apiCall(page, "POST", `${API}/${pid}/folders/${folderId}/files/presign/`,
    { file_name: name, file_size: size, content_type: mime });
  expect(status, `presign ${name}`).toBe(HTTP.CREATED);
  const put = await page.request.fetch(body.data.upload_url as string, {
    method: "PUT", data: content as string, headers: { "Content-Type": mime },
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
/** 行内文件名按钮（预览入口）——精确 data-sb-scope（getByRole 会同时命中 ⋯ 的 aria-label）。 */
const nameBtn = (page: Page, name: string) => rowByName(page, name).locator('[data-sb-scope="files-name-preview"]');
const treeItem = (page: Page, folderId: string) => page.locator(`[data-sb-scope="files-tree-item"][data-folder-id="${folderId}"]`);

async function openFileMenu(page: Page, fileId: string) {
  await page.locator(`[data-file-menu="${fileId}"]`).click();
  await page.locator('[data-sb-scope="files-pop"]').waitFor({ state: "visible", timeout: 5_000 });
}

/** 1×1 红 PNG（真实图片字节——worker PIL 缩略可成功；区别于伪字节的排队态种子）。 */
const REAL_PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
  "base64",
);

async function dropUpload(page: Page, files: Array<{ name: string; content: string; type: string }>) {
  await page.evaluate((list) => {
    const dt = new DataTransfer();
    for (const f of list) dt.items.add(new File([f.content], f.name, { type: f.type }));
    const el = document.querySelector('[data-sb-scope="files-body"]');
    if (!el) throw new Error("files-body 不存在");
    el.dispatchEvent(new DragEvent("drop", { dataTransfer: dt, bubbles: true, cancelable: true }));
  }, files);
}

test.describe("Sprint-4 预览/版本/分享 + space（FILE-003/004 · C.119~C.125）", () => {
  let getErrs: () => string[] = () => [];
  test.beforeEach(async ({ page }) => {
    await page.context().clearCookies();
    getErrs = attachConsoleGuard(page);
  });
  test.afterEach(async () => {
    expect.soft(getErrs(), "console errors").toEqual([]);
  });

  /* ═══════════ C.120 预览抽屉五通道 + 排队/超限/不可预览 + 框架 parity ═══════════ */

  test("S4P-1 C.120 预览抽屉五通道态：text/pdf/image(就绪+排队)/archive 元数据卡/超限引导 + 框架与 Esc", async ({ page }) => {
    test.setTimeout(150_000);
    await loginDemo(page);
    const proj = await createProject(page, "S4P");
    const des = await createFolder(page, proj.id, "设计稿");
    const fTxt = await seedFile(page, proj.id, des.id, "说明文档.md", "# 标题\n正文第一行\n- 列表项", "text/plain");
    await seedFile(page, proj.id, des.id, "合同扫描.pdf", "pdf-bytes-placeholder", "application/pdf");
    await seedFile(page, proj.id, des.id, "伪图片.png", "not-a-real-png", "image/png"); // 伪字节 → 缩略恒失败 → 202 排队
    await seedFile(page, proj.id, des.id, "转码中.docx", "fake-office", "application/msword" /* 短 mime：presign 序列化器 content_type ≤64 字符；office 判定走 .docx 扩展名 */);
    await seedFile(page, proj.id, des.id, "资产打包.zip", "zipbytes", "application/zip");
    await seedFile(page, proj.id, des.id, "大文本.md", "x".repeat(2 * 1024 * 1024 + 64), "text/plain"); // >2MB
    const realPng = await seedFile(page, proj.id, des.id, "真实截图.png", REAL_PNG, "image/png");
    // 等真实 PNG 缩略就绪（worker 派生；其余伪字节/office 恒排队是稳定态）
    for (let i = 0; i < 30; i++) {
      const r = await apiCall(page, "GET", `${API}/${proj.id}/files/${realPng.id}/preview/`);
      if (r.status === HTTP.OK && r.body?.data?.ready === true) break;
      await page.waitForTimeout(1_000);
    }
    await gotoFiles(page);
    await treeItem(page, des.id).click();

    // ── 文本通道：Monaco/只读正文渲染（C.120 文本/MD ≤2MB）──
    await nameBtn(page, fTxt.name).click();
    const drawer = page.locator('[data-sb-scope="preview-drawer"]');
    await expect(drawer).toBeVisible();
    await expect(drawer).toHaveAttribute("role", "dialog"); // C.120 role=dialog
    await expect(drawer).toHaveAttribute("aria-label", `文件预览 ${fTxt.name}`);
    await expect(page.locator('[data-sb-scope="preview-head-meta"]')).toContainText(/v1 · \d+B/); // 头部 vN·大小
    await expect(page.locator('[data-sb-scope="preview-text-body"]')).toContainText("正文第一行", { timeout: 10_000 }); // ③正文回读
    // 头部 [版本▾][下载] ✕（C.120）
    await expect(page.locator('[data-sb-scope="preview-vermenu"]')).toBeVisible();
    await expect(page.locator('[data-sb-scope="preview-dl"]')).toBeVisible();
    await expect(page.locator('[data-sb-scope="preview-close"]')).toBeVisible();
    await expect(page.locator('[data-sb-scope="preview-versions"]')).toHaveCount(0); // 单版本隐藏版本区（C.121/§3.4）
    await page.keyboard.press("Escape"); // Esc 关闭（§3.5）
    await expect(drawer).toHaveCount(0);

    // ── PDF 通道：iframe 透传 302 换发（pdf.js 无依赖 → iframe 承载，C.120）──
    await nameBtn(page, "合同扫描.pdf").click();
    const pdfFrame = page.locator('[data-sb-scope="preview-pdf-frame"]');
    await expect(pdfFrame).toBeVisible({ timeout: 10_000 });
    await expect(pdfFrame).toHaveAttribute("src", /\/files\/[^/]+\/(versions\/[^/]+\/content\/|derivatives\/preview\/)/);
    await page.keyboard.press("Escape");

    // ── 图片通道·就绪：缩略渲染 + 查看原图（C.120 image 缩略/原图）──
    await nameBtn(page, "真实截图.png").click();
    const img = page.locator('[data-sb-scope="preview-image-img"]');
    await expect(img).toBeVisible({ timeout: 10_000 });
    await expect(img).toHaveAttribute("src", /\/derivatives\/thumbnail\//); // 302 换发端点为 src
    await expect(page.locator('[data-sb-scope="preview-original"]')).toBeVisible(); // 原图入口
    await page.keyboard.press("Escape");

    // ── 排队态（202）：⏳ 预计时长 + 先下载 + LibreOffice 说明（C.120/§3.4）──
    for (const name of ["转码中.docx", "伪图片.png"]) {
      await nameBtn(page, name).click();
      const queued = page.locator('[data-sb-scope="preview-queued"]');
      await expect(queued).toBeVisible({ timeout: 10_000 });
      await expect(queued).toContainText("正在转码预览… 预计约");
      await expect(queued).toContainText("Office 文档经 LibreOffice 异步转 PDF（202 排队");
      await expect(page.locator('[data-sb-scope="preview-queued-dl"]')).toBeVisible(); // 先下载
      await page.keyboard.press("Escape");
    }

    // ── 不可预览类型：元数据卡 + 下载查看（C.120 archive/other 决策链）──
    await nameBtn(page, "资产打包.zip").click();
    const meta = page.locator('[data-sb-scope="preview-meta"]');
    await expect(meta).toBeVisible({ timeout: 10_000 });
    await expect(meta).toContainText("压缩包");
    await expect(meta).toContainText("不支持在线预览");
    await expect(page.locator('[data-sb-scope="preview-meta-dl"]')).toBeVisible();
    await page.keyboard.press("Escape");

    // ── 文本超限（>2MB）：「文件较大，请下载查看」（C.120/§3.4）──
    await nameBtn(page, "大文本.md").click();
    const tooLarge = page.locator('[data-sb-scope="preview-too-large"]');
    await expect(tooLarge).toBeVisible({ timeout: 10_000 });
    await expect(tooLarge).toContainText("文件较大，请下载查看");
    await page.keyboard.press("Escape");
    await expect(page.locator('[data-sb-scope="preview-drawer"]')).toHaveCount(0);
  });

  /* ═══════════ C.121 版本面板：列表/回滚三件套/对比双栏/同名并入/上限提示 ═══════════ */

  test("S4P-2 C.121 版本面板：列表 ●当前 + 对比双栏 diff aria + 回滚三件套 + 同名版本 +1 + 上限提示", async ({ page }) => {
    test.setTimeout(150_000);
    await loginDemo(page);
    const proj = await createProject(page, "S4Pver");
    const des = await createFolder(page, proj.id, "设计稿");
    const f = await seedFile(page, proj.id, des.id, "方案.md", "首页改版 Q3\n导航采用侧边栏方案\n视觉 token 延续 v2", "text/plain");
    await seedFile(page, proj.id, des.id, "方案.md", "首页改版 Q3\n导航改为顶部 Tab 方案\n新增深色模式适配\n埋点补齐曝光事件", "text/plain"); // 同名 → v2
    await gotoFiles(page);
    await treeItem(page, des.id).click();
    await expect(rowByName(page, "方案.md")).toHaveCount(1); // 同名不产生重复行（BR-07 并入）

    await nameBtn(page, "方案.md").click();
    const panel = page.locator('[data-sb-scope="preview-versions"]');
    await expect(panel).toBeVisible({ timeout: 10_000 });
    await expect(panel).toContainText("版本（2）");
    await expect(panel).toContainText("上限 20 · 超出自动淘汰最旧非当前版本"); // C.121 版本上限提示
    const v2Row = page.locator('[data-sb-scope="preview-ver-row"][data-version="2"]');
    const v1Row = page.locator('[data-sb-scope="preview-ver-row"][data-version="1"]');
    await expect(v2Row.locator('[data-sb-scope="preview-ver-current"]')).toHaveText("● 当前"); // 当前 ●
    await expect(v1Row.locator('[data-sb-scope="preview-ver-cmp"]')).toBeVisible(); // 文本类才有对比
    await expect(v1Row.locator('[data-sb-scope="preview-ver-rollback"]')).toBeVisible();
    await expect(v2Row.locator('[data-sb-scope="preview-ver-rollback"]')).toHaveCount(0); // 当前版本无回滚

    // ── [版本▾] 下拉链（C.121）──
    await page.locator('[data-sb-scope="preview-vermenu"]').click();
    await expect(page.locator('[data-sb-scope="files-pop"]')).toContainText("v2 · 当前");
    await expect(page.locator('[data-sb-scope="files-pop"]')).toContainText("v1");
    await page.mouse.click(800, 550); // 外击收起下拉（Esc 留给抽屉关闭语义）

    // ── 对比：双栏 diff 新增绿/删除红 + aria-label 差异计数（C.121/§3.5）──
    await v1Row.locator('[data-sb-scope="preview-ver-cmp"]').click();
    const diffCols = page.locator('[data-sb-scope="preview-diff-cols"]');
    await expect(diffCols).toBeVisible({ timeout: 15_000 });
    await expect(diffCols).toHaveAttribute("aria-label", "第 1 版与第 2 版差异：新增 3 行，删除 2 行");
    await expect(diffCols.locator(".del", { hasText: "导航采用侧边栏方案" })).toBeVisible(); // 删除红
    await expect(diffCols.locator(".ins", { hasText: "新增深色模式适配" })).toBeVisible(); // 新增绿
    await expect(page.locator('[data-sb-scope="preview-diff"]')).toContainText("v1 ↔ v2 · 新增 3 行，删除 2 行");
    await page.locator('[data-sb-scope="preview-diff-exit"]').click(); // 退出对比
    await expect(page.locator('[data-sb-scope="preview-versions"]')).toBeVisible();

    // ── 回滚三件套：确认文案「将创建新版本」→ POST 201 → 新版本指向旧对象（C.121/§7.2-2）──
    let rollbackPosts = 0;
    page.on("request", (r) => { if (/\/rollback\/$/.test(r.url()) && r.method() === "POST") rollbackPosts += 1; });
    await page.locator('[data-sb-scope="preview-ver-row"][data-version="1"] [data-sb-scope="preview-ver-rollback"]').click();
    const confirm = page.locator('[data-sb-scope="files-confirm"]');
    await expect(confirm).toContainText("回滚到 v1？");
    await expect(confirm).toContainText("将创建新版本（内容同 v1）"); // 不删除任何版本
    await confirm.getByRole("button", { name: "取消" }).click(); // 取消路径不发 POST（Esc 语义留给抽屉）
    expect(rollbackPosts, "取消确认不发 POST").toBe(0);
    await page.locator('[data-sb-scope="preview-ver-row"][data-version="1"] [data-sb-scope="preview-ver-rollback"]').click();
    const rollbackDone = page.waitForResponse((r) => /\/rollback\/$/.test(r.url()) && r.request().method() === "POST");
    await confirm.locator('[data-sb-scope="files-confirm-ok"]').click();
    const rollbackRes = await rollbackDone;
    expect(rollbackRes.status(), "回滚 POST 201").toBe(HTTP.CREATED); // ②行为
    const rollbackBody = (await rollbackRes.json()) as { data: { version_number: number; source_version_number: number } };
    expect(rollbackBody.data.version_number).toBe(3); // 新版本 v3
    expect(rollbackBody.data.source_version_number).toBe(1); // 指向 v1（零拷贝）
    await expect(page.locator('[data-sb-scope="preview-versions"]')).toContainText("版本（3）", { timeout: 10_000 }); // ③回读
    await expect(page.locator('[data-sb-scope="preview-ver-row"][data-version="3"] [data-sb-scope="preview-ver-current"]')).toBeVisible();
    await expect(page.locator('[data-sb-scope="preview-text-body"]')).toContainText("导航采用侧边栏方案", { timeout: 10_000 }); // 内容还原 = v1
    await page.keyboard.press("Escape");
    await page.reload(); // 刷新回读：链保留 + 镜像更新
    await treeItem(page, des.id).click();
    await nameBtn(page, "方案.md").click();
    await expect(page.locator('[data-sb-scope="preview-versions"]')).toContainText("版本（3）", { timeout: 10_000 });
    await expect(page.locator('[data-sb-scope="preview-head-meta"]')).toContainText("v3 ·");
  });

  /* ═══════════ C.119 分片上传：>50MB 强制分片 + 片失败重试 + 断点续传零重传 ═══════════ */

  test("S4P-3 C.119 分片上传：51MB 强制分片（并行 3/片级 MD5 核对）+ 注入片失败自动重传 + 刷新断点续传零重传", async ({ page }) => {
    test.setTimeout(300_000);
    // route.abort 注入的传输失败是本测试刻意制造（S4F-2 先例：guard 剔除 net::ERR_FAILED）
    const rawErrs = getErrs;
    getErrs = () => rawErrs().filter((e) => !e.includes("net::ERR_FAILED"));
    await loginDemo(page);
    const proj = await createProject(page, "S4Pchunk");
    const des = await createFolder(page, proj.id, "大文件");
    await gotoFiles(page);
    await treeItem(page, des.id).click();

    const BIG = 51 * 1024 * 1024; // >50MB → 强制分片（BR-01）
    // Playwright setInputFiles 禁传 >50MB buffer——落盘临时文件后传 path（文件名=续传匹配键）
    const tmpDir = mkdtempSync(join(tmpdir(), "s4p-chunk-"));
    const big1Path = join(tmpDir, "索引整库导出1.zip");
    const big2Path = join(tmpDir, "索引整库导出2.zip");
    writeFileSync(big1Path, Buffer.alloc(BIG, 7));
    writeFileSync(big2Path, Buffer.alloc(BIG, 9));

    // ── 第一段：注入片 1 失败一次（BR-03 自动重传）+ 悬停片 ≥2 直至卡片断言完成 ──
    //（本地 MinIO 7 片 <1s 完成——不悬停则卡片 UI 断言与完成竞态）
    const putPartNumbers: number[] = [];
    let failFirstPut = true;
    let holdRest = true;
    const heldRoutes: Array<{ continue: () => Promise<void>; abort: () => Promise<void> }> = [];
    await page.route(/\/uploads\//, (route) => {
      const req = route.request();
      const n = Number(new URL(req.url()).searchParams.get("partNumber") ?? 0);
      if (req.method() === "PUT" && n > 0) putPartNumbers.push(n);
      if (req.method() === "PUT" && n >= 2 && holdRest) { heldRoutes.push(route); return; }
      if (failFirstPut && req.method() === "PUT" && n === 1) { failFirstPut = false; void route.abort(); return; } // 只打片 1（拦截顺序不定，n>0 会误伤他片）
      void route.continue();
    });

    const initReq = page.waitForRequest((r) => /\/upload-sessions\/$/.test(r.url()) && r.method() === "POST");
    await page.locator('[data-sb-scope="files-upload-input"]').setInputFiles(big1Path);
    expect((await initReq).method()).toBe("POST"); // 走会话而非直传 presign（>50MB 分流）
    // C.119 卡片：进度 role=progressbar + 片统计（并行 3）+ 暂停/取消
    const card = page.locator('[data-sb-scope="files-chunk-card"]').first();
    await expect(card).toBeVisible({ timeout: 20_000 });
    await expect(card.locator('[role="progressbar"]')).toBeVisible();
    await expect(card).toContainText("并行 3", { timeout: 30_000 });
    await expect(page.locator('[data-sb-scope="files-chunk-pause-btn"]')).toBeVisible();
    // 释放悬停片 → 上传收尾（complete：ListParts 片级 ETag×MD5 全量核对）
    holdRest = false;
    await Promise.allSettled(heldRoutes.map((r) => r.continue()));
    const completeDone = page.waitForResponse((r) => /\/upload-sessions\/[^/]+\/complete\/$/.test(r.url()) && r.request().method() === "POST");
    expect((await completeDone).status(), "complete 201（片级 ETag×MD5 全量核对通过）").toBe(HTTP.CREATED);
    await expect(rowByName(page, "索引整库导出1.zip")).toBeVisible({ timeout: 15_000 }); // 完成即入列表
    // 失败片有自动重传（同片号 ≥2 次 PUT——BR-03 片级重试）
    const putCounts: Record<number, number> = {};
    putPartNumbers.forEach((n) => { putCounts[n] = (putCounts[n] ?? 0) + 1; });
    expect(putCounts[1] ?? 0, "注入失败的片 1 自动重传（≥2 次 PUT）").toBeGreaterThanOrEqual(2);

    // ── 第二段：断点续传——悬停片 ≥3 使断点确定化（片 1/2 登记）→ 刷新 → 继续（零重传）──
    await page.unroute(/\/uploads\//);
    let holdParts3Plus = true;
    const held2: Array<{ abort: () => Promise<void> }> = [];
    await page.route(/\/uploads\//, (route) => {
      const req = route.request();
      const n = Number(new URL(req.url()).searchParams.get("partNumber") ?? 0);
      if (req.method() === "PUT" && n >= 3 && holdParts3Plus) { held2.push(route); return; }
      void route.continue();
    });
    const patch1 = page.waitForResponse((r) => /\/chunks\/1\/$/.test(r.url()) && r.request().method() === "PATCH", { timeout: 90_000 });
    const patch2 = page.waitForResponse((r) => /\/chunks\/2\/$/.test(r.url()) && r.request().method() === "PATCH", { timeout: 90_000 });
    await page.locator('[data-sb-scope="files-upload-input"]').setInputFiles(big2Path);
    await patch1; await patch2;
    // 断点基线服务端确定化：轮询会话状态直至 uploaded_chunks ⊇ {1,2}（响应头先行的
    // 落库竞态以服务端读数为准——零重传断言以此快照为基线）
    const sessionId2 = await page.evaluate(() => {
      const k = Object.keys(localStorage).find((key) => key.startsWith("rp:chunk-session:") && key.includes("索引整库导出2.zip"));
      return k ? (JSON.parse(localStorage.getItem(k) ?? "{}").sessionId as string) : "";
    });
    expect(sessionId2, "localStorage 会话探测").toBeTruthy();
    let doneSnapshot: number[] = [];
    for (let i = 0; i < 20; i++) {
      const st = await apiCall(page, "GET", `${API}/${proj.id}/upload-sessions/${sessionId2}/`);
      expect(st.status).toBe(HTTP.OK);
      doneSnapshot = (st.body?.data?.uploaded_chunks ?? []) as number[];
      if (doneSnapshot.includes(1) && doneSnapshot.includes(2)) break;
      await page.waitForTimeout(500);
    }
    expect(doneSnapshot, "片 1/2 已登记（断点基线）").toEqual(expect.arrayContaining([1, 2]));
    // 悬停保持开启直接刷新——片 ≥3 的在途 PUT 被导航取消（无 post-snapshot 登记）
    await page.reload(); // 刷新（File 丢失 → 会话 + uploaded_chunks 留存）
    holdParts3Plus = false;
    await Promise.allSettled(held2.map((r) => r.abort())); // 清已死悬停句柄
    await page.unroute(/\/uploads\//);
    await treeItem(page, des.id).click();
    // C.119 探测行：「检测到未完成的上传 [继续] [放弃]」（localStorage session）
    const banner = page.locator('[data-sb-scope="files-chunk-resume-banner"]');
    await expect(banner).toBeVisible({ timeout: 10_000 });
    await expect(banner).toContainText("索引整库导出2.zip");
    // 继续（选择文件）→ 同名同大小 → 复用会话：无新 init POST，已传片零重传
    let initCount = 0;
    page.on("request", (r) => { if (/\/upload-sessions\/$/.test(r.url()) && r.method() === "POST") initCount += 1; });
    const resumedPuts: number[] = [];
    await page.route(/\/uploads\//, (route) => {
      const req = route.request();
      if (req.method() === "PUT") {
        const n = Number(new URL(req.url()).searchParams.get("partNumber") ?? 0);
        if (n > 0) resumedPuts.push(n);
      }
      void route.continue();
    });
    const statusReq = page.waitForResponse((r) => /\/upload-sessions\/[^/]+\/$/.test(r.url()) && r.request().method() === "GET");
    await banner.locator('[data-sb-scope="files-chunk-resume-continue"]').click();
    await page.locator('[data-sb-scope="files-upload-input"]').setInputFiles(big2Path);
    expect((await statusReq).status(), "断点片表 GET 200").toBe(HTTP.OK);
    const resumedComplete = page.waitForResponse((r) => /\/upload-sessions\/[^/]+\/complete\/$/.test(r.url()) && r.request().method() === "POST", { timeout: 240_000 });
    expect((await resumedComplete).status(), "续传 complete 201").toBe(HTTP.CREATED);
    expect(initCount, "续传不重建会话（零 init POST）").toBe(0);
    // 已传片零重传：断点基线（服务端 uploaded_chunks 快照）内的片号不再 PUT
    expect(resumedPuts.length, "续传片数 < 总片数").toBeLessThan(7);
    for (const n of doneSnapshot) {
      expect(resumedPuts, `片 ${n} 已登记 → 不重传（uploaded_chunks 跳过）`).not.toContain(n);
    }
    await expect(rowByName(page, "索引整库导出2.zip")).toBeVisible({ timeout: 15_000 });
  });

/* ═══════════ C.123/C.124 分享创建 + 管理（延期/吊销二次确认/访问计数） ═══════════ */

  test("S4P-4 C.123/C.124 分享创建弹层三件套 + 管理列表/延期 30 天/吊销二次确认（取消不发 DELETE）", async ({ page }) => {
    test.setTimeout(120_000);
    await loginDemo(page);
    const proj = await createProject(page, "S4Pshare");
    const des = await createFolder(page, proj.id, "合同");
    const f = await seedFile(page, proj.id, des.id, "客户方案.md", "share me", "text/plain");
    await gotoFiles(page);
    await treeItem(page, des.id).click();
    await expect(rowByName(page, f.name)).toBeVisible();

    // ── C.123 创建弹层 parity：权限单选/密码开关掩码/有效期下拉+自定义/警示行 ──
    await openFileMenu(page, f.id);
    await page.locator('[data-sb-scope="files-pop"] [data-menu-key="share"]').click();
    const dialog = page.locator('[data-sb-scope="share-create"]');
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText(`分享 · ${f.name}`);
    await expect(dialog.locator('[data-share-perm="view"]')).toBeVisible(); // 仅预览
    await expect(dialog.locator('[data-share-perm="download"]')).toBeChecked(); // 预览+下载 默认
    await expect(dialog.locator('[data-sb-scope="share-pwd-toggle"]')).toBeChecked(); // 密码默认开
    await expect(dialog.locator('[data-sb-scope="share-pwd-input"]')).toHaveAttribute("type", "password"); // 掩码
    await expect(dialog.locator('[data-sb-scope="share-pwd-eye"]')).toBeVisible(); // 👁 显示
    await expect(dialog.locator('[data-sb-scope="share-exp-btn"]')).toContainText("30 天"); // 默认 30 天
    await expect(dialog.locator('[data-sb-scope="share-warn"]')).toContainText("任何获得链接（与密码）的人都能访问该文件"); // 警示行

    // 有效期下拉：自定义 → 日期输入（O5）
    await dialog.locator('[data-sb-scope="share-exp-btn"]').click();
    await page.locator('[data-sb-scope="files-pop"] [data-menu-key="custom"]').click();
    await expect(dialog.locator('[data-sb-scope="share-exp-custom"]')).toBeVisible();
    await dialog.locator('[data-sb-scope="share-exp-btn"]').click();
    await page.locator('[data-sb-scope="files-pop"] [data-menu-key="d7"]').click(); // 7 天

    // 密码开关关闭再开（交互回读）
    await dialog.locator('[data-sb-scope="share-pwd-toggle"]').uncheck();
    await expect(dialog.locator('[data-sb-scope="share-pwd-input"]')).toHaveCount(0);
    await dialog.locator('[data-sb-scope="share-pwd-toggle"]').check();
    await dialog.locator('[data-sb-scope="share-pwd-input"]').fill("share-pw-123");

    // ── 创建：POST share-links 201 → 结果态链接 + 复制（C.123）──
    const createDone = page.waitForResponse((r) => /\/share-links\/$/.test(r.url()) && r.request().method() === "POST");
    await dialog.locator('[data-sb-scope="share-create-btn"]').click();
    const createRes = await createDone;
    expect(createRes.status(), "创建分享 201").toBe(HTTP.CREATED);
    const created = ((await createRes.json()) as { data: { slug: string; share_url: string } }).data;
    expect(created.slug).toMatch(/^[A-Za-z0-9_-]{22}$/); // 22 位不可枚举 slug
    const linkInput = page.locator('[data-sb-scope="share-link-input"]');
    await expect(linkInput).toHaveValue(/\/s\/[A-Za-z0-9_-]{22}$/); // 结果态链接
    await expect(page.locator('[data-sb-scope="share-created-summary"]')).toContainText("预览 + 下载");
    await expect(page.locator('[data-sb-scope="share-created-summary"]')).toContainText("已开启");
    await page.locator('[data-sb-scope="share-done"]').click();

    // ── C.124 管理弹层：列表/计数 + 延期 30 天 + 吊销二次确认 ──
    await openFileMenu(page, f.id);
    await page.locator('[data-sb-scope="files-pop"] [data-menu-key="sharemgr"]').click();
    const mgr = page.locator('[data-sb-scope="share-manage"]');
    await expect(mgr).toBeVisible();
    await expect(mgr.locator('[data-sb-scope="share-manage-title"]')).toContainText(`的分享（1）`);
    const shareRow = mgr.locator('[data-sb-scope="share-manage-row"]').first();
    await expect(shareRow).toContainText(`/s/${created.slug.slice(0, 10)}`); // 链接截断
    await expect(shareRow).toContainText("预览 + 下载");
    await expect(shareRow).toContainText(/天后/); // 有效期
    await expect(shareRow).toContainText("0 次"); // 访问计数

    // 延期 30 天：POST extend 200 → 行内有效期回读（7 → 37 天）
    const extendDone = page.waitForResponse((r) => /\/share-links\/[^/]+\/extend\/$/.test(r.url()) && r.request().method() === "POST");
    await shareRow.locator('[data-share-row-menu]').click();
    await page.locator('[data-sb-scope="files-pop"] [data-menu-key="extend"]').click();
    const extendRes = await extendDone;
    expect(extendRes.status(), "延期 200").toBe(HTTP.OK);
    expect(((await extendRes.json()) as { data: { expires_at: string } }).data.expires_at).toBeTruthy();
    await expect(shareRow.locator('[data-sb-scope="share-row-expiry"]')).toContainText(/3[0-9] 天后/); // 7+30=37 天后

    // 吊销：二次确认文案；取消不发 DELETE（mutation 防线），确认 → 204 → 行标已吊销
    let deleteCount = 0;
    page.on("request", (r) => { if (/\/share-links\/[^/]+\/$/.test(r.url()) && r.method() === "DELETE") deleteCount += 1; });
    await shareRow.locator('[data-share-row-menu]').click();
    await page.locator('[data-sb-scope="files-pop"] [data-menu-key="revoke"]').click();
    const revokeConfirm = page.locator('[data-sb-scope="files-confirm"]');
    await expect(revokeConfirm).toContainText("吊销分享？");
    await expect(revokeConfirm).toContainText("吊销后链接立即失效，访问者将看到「链接不存在或已失效」");
    await revokeConfirm.getByRole("button", { name: "取消" }).click();
    await page.waitForTimeout(400);
    expect(deleteCount, "取消二次确认不发 DELETE").toBe(0);
    await shareRow.locator('[data-share-row-menu]').click();
    await page.locator('[data-sb-scope="files-pop"] [data-menu-key="revoke"]').click();
    const revokeDone = page.waitForResponse((r) => /\/share-links\/[^/]+\/$/.test(r.url()) && r.request().method() === "DELETE");
    await page.locator('[data-sb-scope="files-confirm-ok"]').click();
    expect((await revokeDone).status(), "吊销 204").toBe(HTTP.NO_CONTENT);
    await expect(mgr.locator('[data-sb-scope="share-manage-row"]').first()).toContainText("已吊销", { timeout: 10_000 }); // ③回读
    await page.keyboard.press("Escape");
  });

  /* ═══════════ C.122 缩略图落位：网格缩略 + 列表悬浮 200ms 小卡 ═══════════ */

  test("S4P-5 C.122 缩略图落位：网格卡片缩略（derivatives/thumbnail 302）+ 列表悬浮 200ms 小卡", async ({ page }) => {
    test.setTimeout(120_000);
    await loginDemo(page);
    const proj = await createProject(page, "S4Pthumb");
    const des = await createFolder(page, proj.id, "设计稿");
    const png = await seedFile(page, proj.id, des.id, "封面图.png", REAL_PNG, "image/png");
    for (let i = 0; i < 30; i++) {
      const r = await apiCall(page, "GET", `${API}/${proj.id}/files/${png.id}/preview/`);
      if (r.status === HTTP.OK && r.body?.data?.ready === true) break;
      await page.waitForTimeout(1_000);
    }
    await gotoFiles(page);
    await treeItem(page, des.id).click();

    // 网格：卡片缩略替换类型图标（O4——src = derivatives/thumbnail 换发端点）
    await page.locator('[data-sb-scope="files-view-grid"]').click();
    const card = page.locator('[data-sb-scope="files-card"]', { hasText: png.name });
    await expect(card).toBeVisible();
    const thumb = card.locator('[data-sb-scope="files-card-thumb"]');
    await expect(thumb).toBeAttached();
    await expect(thumb).toHaveAttribute("src", /\/files\/[^/]+\/derivatives\/thumbnail\//);

    // 列表：行悬浮 200ms 出预览小卡（C.122/O4）
    await page.locator('[data-sb-scope="files-view-list"]').click();
    const row = rowByName(page, png.name);
    await expect(row).toBeVisible();
    await row.hover();
    const hoverCard = page.locator('[data-sb-scope="files-hover-card"]');
    await expect(hoverCard).toBeVisible({ timeout: 3_000 }); // 200ms 节流后出现
    await expect(hoverCard).toHaveAttribute("data-file-id", png.id);
    await page.mouse.move(10, 400); // 移开 → 小卡消失
    await expect(hoverCard).toHaveCount(0);
  });

  /* ═══════════ C.125 space 匿名三态页（密码门/文件页/失效页 + 锁定倒计时 + 移动优先） ═══════════ */

  test("S4P-6 C.125 space 匿名页三态：密码门（抖动+剩余次数）→ 文件页（倒计时/预览/下载 aria）+ 仅预览 + 锁定 + 失效统一", async ({ page }) => {
    test.setTimeout(180_000);
    // 前置：web 域造分享（密码 30 天 download / 无密码永久 view / 密码锁定专用 / 已吊销）
    await loginDemo(page);
    const proj = await createProject(page, "S4Pspace");
    const des = await createFolder(page, proj.id, "外发");
    const f = await seedFile(page, proj.id, des.id, "对外方案.md", "外部可见正文内容", "text/plain");
    const mk = async (payload: Record<string, unknown>) => {
      const r = await apiCall(page, "POST", `${API}/${proj.id}/files/${f.id}/share-links/`, payload);
      expect(r.status, "创建分享").toBe(HTTP.CREATED);
      return r.body?.data as { slug: string };
    };
    const withPwd = await mk({ permission: "download", password: "space-pw-1", expires_in_days: 30 });
    const viewOnly = await mk({ permission: "view" });
    const lockProbe = await mk({ permission: "view", password: "lock-pw-9" });
    const revoked = await mk({ permission: "download" });
    const del = await apiCall(page, "DELETE", `${API}/${proj.id}/share-links/${(await apiCall(page, "GET", `${API}/${proj.id}/files/${f.id}/share-links/`)).body.data.find((s: { slug: string }) => s.slug === revoked.slug).id}/`);
    expect(del.status).toBe(HTTP.NO_CONTENT);
    await page.context().clearCookies(); // 匿名视角

    // space dev server 探测：非常驻栈（未起则 skip——起服：pnpm dev:space / 见 spec 头注释）
    const spaceUp = await page.request.get(`${SPACE_ORIGIN}/spaces`).then((r) => r.ok()).catch(() => false);
    test.skip(!spaceUp, "space dev server(3003) 未运行——起服后复跑（pnpm --filter @rp/space dev）");

    // ── 失效页：吊销 → 统一「链接不存在或已失效」+ 三因文案（C.125/§2.4 同码同页）──
    await page.goto(`${SPACE_BASE}/s/${revoked.slug}`);
    const dead = page.locator('[data-sb-scope="space-dead"]');
    await expect(dead).toBeVisible({ timeout: 15_000 });
    await expect(dead).toContainText("链接不存在或已失效");
    await expect(dead).toContainText("链接可能已被吊销、过期或源文件已被删除");
    await expect(page.locator('[data-sb-scope="space-nav"]')).toContainText("RabbitProjects"); // 壳

    // ── 密码门：极简不泄露 + 自动 focus + 回车提交 + 错误 role=alert 剩余次数（C.125）──
    await page.goto(`${SPACE_BASE}/s/${withPwd.slug}`);
    const pwdCard = page.locator('[data-sb-scope="space-pwd"]');
    await expect(pwdCard).toBeVisible({ timeout: 15_000 });
    await expect(pwdCard).toContainText("此文件已加密分享");
    await expect(pwdCard).toContainText("由 RabbitProjects 提供安全分享");
    await expect(page.locator('[data-sb-scope="space-pwd-input"]')).toBeFocused(); // 自动 focus（§3.4）
    await expect(pwdCard).not.toContainText(f.name); // 不泄露文件信息（BR-10）
    await page.locator('[data-sb-scope="space-pwd-input"]').fill("wrong-password");
    await page.keyboard.press("Enter"); // 回车提交
    const errAlert = page.locator('[data-sb-scope="space-pwd-err"]');
    await expect(errAlert).toBeVisible({ timeout: 10_000 });
    await expect(errAlert).toHaveAttribute("role", "alert");
    await expect(errAlert).toContainText(/密码错误.*剩余 \d+ 次尝试/);
    // 正确密码 → 文件页
    await page.locator('[data-sb-scope="space-pwd-input"]').fill("space-pw-1");
    await page.locator('[data-sb-scope="space-pwd-go"]').click();
    const fileCard = page.locator('[data-sb-scope="space-file"]');
    await expect(fileCard).toBeVisible({ timeout: 15_000 });
    await expect(page.locator('[data-sb-scope="space-file-head"]')).toContainText(f.name); // 文件名
    await expect(page.locator('[data-sb-scope="space-file-head"]')).toContainText(/B · \d+ 天后过期/); // 大小 + 倒计时
    await expect(page.locator('[data-sb-scope="space-preview-text"]')).toContainText("外部可见正文内容", { timeout: 15_000 }); // 预览区复用通道
    await expect(page.locator('[data-sb-scope="space-hint"]')).toContainText("本链接由分享者创建，如需延期请联系分享者"); // 提示行
    // 下载按钮：aria-label 含文件名 → 点击走 content/?download=1 → 302 → attachment
    const dl = page.locator('[data-sb-scope="space-download"]');
    await expect(dl).toHaveAttribute("aria-label", `下载 ${f.name}`);
    const downloadPromise = page.waitForEvent("download", { timeout: 20_000 });
    await dl.click();
    expect((await downloadPromise).suggestedFilename()).toBe(f.name);

    // ── 仅预览：无密码直通 + 无下载按钮（BR-05）──
    await page.goto(`${SPACE_BASE}/s/${viewOnly.slug}`);
    await expect(page.locator('[data-sb-scope="space-file"]')).toBeVisible({ timeout: 15_000 });
    await expect(page.locator('[data-sb-scope="space-download"]')).toHaveCount(0);
    await expect(page.locator('[data-sb-scope="space-nodownload"]')).toContainText("当前链接仅限预览（无下载权限）");

    // ── 防爆破锁定：连错 5 次后第 6 次 → 锁定倒计时（BR-07/C.125）──
    await page.goto(`${SPACE_BASE}/s/${lockProbe.slug}`);
    await expect(page.locator('[data-sb-scope="space-pwd"]')).toBeVisible({ timeout: 15_000 });
    for (let i = 0; i < 5; i++) {
      await page.locator('[data-sb-scope="space-pwd-input"]').fill("bad-attempt");
      await page.locator('[data-sb-scope="space-pwd-go"]').click();
      await expect(page.locator('[data-sb-scope="space-pwd-err"]')).toBeVisible({ timeout: 10_000 });
    }
    await page.locator('[data-sb-scope="space-pwd-input"]').fill("bad-attempt");
    await page.locator('[data-sb-scope="space-pwd-go"]').click();
    const locked = page.locator('[data-sb-scope="space-locked"]');
    await expect(locked).toBeVisible({ timeout: 10_000 });
    await expect(locked).toContainText("尝试次数过多");
    await expect(locked).toContainText(/已锁定，请 \d{2}:\d{2} 后重试/); // 倒计时（10 分钟窗口）

    // ── 移动优先：375px 视口下文件卡自适应（§3.4）──
    await page.setViewportSize({ width: 375, height: 800 });
    await page.goto(`${SPACE_BASE}/s/${viewOnly.slug}`);
    await expect(page.locator('[data-sb-scope="space-file"]')).toBeVisible({ timeout: 15_000 });
    const cardBox = await page.locator('[data-sb-scope="space-file"]').boundingBox();
    expect(cardBox?.width ?? 0).toBeLessThanOrEqual(375); // max-w-full 自适应
  });

  /* ═══════════ WS 接线：file.version.created → 版本面板自动刷新（C.121/§4.4） ═══════════ */

  test("S4P-7 WS：file.version.created 事件刷新版本面板（双用户：A 上传同名新版 → B 面板无刷新自更新）", async ({ browser }) => {
    test.setTimeout(240_000);
    const errGetters: Array<() => string[]> = [];
    getErrs = () => errGetters.flatMap((g) => g());
    const ctxA = await browser.newContext();
    const aPage = await ctxA.newPage();
    errGetters.push(attachConsoleGuard(aPage));
    await loginDemo(aPage);
    const proj = await createProject(aPage, "S4Pws");
    const des = await createFolder(aPage, proj.id, "协作");
    const f = await seedFile(aPage, proj.id, des.id, "联调笔记.md", "第一版内容", "text/plain");

    // B：独立注册用户 + 项目 CONTRIBUTOR（live 按 actor 过滤本人连接——同用户双开收不到）
    const bEmail = `s4p-ws-${Date.now()}-${Math.floor(Math.random() * 1e4)}@rabbit.dev`;
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
    const bm = ((members.body?.data ?? []) as Array<{ user: { id: string; email: string } }>).find((x) => x.user.email === bEmail);
    expect(bm).toBeTruthy();
    const add = await apiCall(aPage, "POST", `${API}/${proj.id}/members/`, { member_ids: [bUserId], role: 15 }); // CONTRIBUTOR
    expect([HTTP.OK, HTTP.CREATED]).toContain(add.status);

    // B：用户入口进文件页 → 打开预览抽屉（?previewFile= → file 房间订阅）
    await bPage.goto(`/${proj.slug}/projects`);
    const card = bPage.locator(`a[href*="/projects/${proj.id}"]`).first();
    await card.waitFor({ state: "visible", timeout: 15_000 });
    await card.click();
    await bPage.waitForURL(/\/board/, { timeout: 15_000 });
    await bPage.getByRole("navigation").filter({ hasText: "返回项目列表" }).getByRole("link", { name: "文件" }).click();
    await bPage.waitForURL(/\/files$/, { timeout: 10_000 });
    await treeItem(bPage, des.id).click();
    await expect(rowByName(bPage, f.name)).toBeVisible({ timeout: 15_000 });
    // 换票带 file_rooms（file:{asset_id} 第四类房间）：抽屉打开 → setContext 重订
    const ticketReq = bPage.waitForRequest((r) => /\/realtime-token\/$/.test(r.url()) && r.method() === "POST"
      && JSON.parse(r.postData() ?? "{}").file_rooms?.includes?.(f.id));
    await nameBtn(bPage, f.name).click();
    await expect(bPage.locator('[data-sb-scope="preview-drawer"]')).toBeVisible();
    await expect(bPage.locator('[data-sb-scope="preview-versions"]')).toHaveCount(0); // v1 单版本 → 面板隐藏
    await expect(bPage.locator('[data-sb-scope="preview-text-body"]')).toContainText("第一版内容", { timeout: 15_000 });
    const ticketPost = (await ticketReq).postData();
    expect(JSON.parse(String(ticketPost ?? "{}")).file_rooms ?? []).toContain(f.id); // 票据 file_rooms 携带资产

    // A：API 上传同名新版本 → on_commit 投 file.version.created → live → B 面板刷新
    // （versions 重拉监听先于上传挂上——事件处理可能即刻发生）
    const versionsReq = bPage.waitForRequest((r) => /\/files\/[^/]+\/versions\/$/.test(r.url()) && r.method() === "GET", { timeout: 30_000 })
      .catch(() => null);
    await seedFile(aPage, proj.id, des.id, "联调笔记.md", "第二版内容（WS 推送）", "text/plain");
    await expect(bPage.locator('[data-sb-scope="preview-versions"]')).toContainText("版本（2）", { timeout: 30_000 });
    await expect(bPage.locator('[data-sb-scope="preview-text-body"]')).toContainText("第二版内容（WS 推送）", { timeout: 15_000 }); // 预览联动刷新
    expect(await versionsReq, "WS 事件触发版本列表重拉").not.toBeNull();
    await ctxA.close();
    await ctxB.close();
  });
});
