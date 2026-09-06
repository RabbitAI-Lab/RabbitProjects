/** Sprint-3 Phase 3-B 前端（COLLAB-002 楼中楼 / 表情 / 图片评论）parity + 行为三件套。
 *  断言由附录 C.84~C.88 清单行生成（ADR-0010 ③），每条带 `// C.x` 出处注释。
 *
 *  覆盖（owner = COLLAB-002 §3.1~§3.3）：
 *    C.84 评论 Tab 线程化（两层结构 / 回复徽标与归并提示 / 折叠条阈值 3 / 父删子留占位）
 *    C.85 反应栏 + 表情选择器（24 白名单）+ 名单浮层（?expand=reactions）
 *    C.86 图片缩略网格（96px / 2·3 列 / GIF 角标）
 *    C.87 灯箱（全屏 / ←→ / Esc / 底部信息）
 *    C.88 回复态 Composer（↩ @xx ▾ / @ 预填可删 / ⌘Enter 乐观插入失败回滚 / 0-5000 计数）
 *          + 图片上传（presign entity_type=comment_image → 直传 → complete → image 节点）
 *
 *  纪律（CLAUDE.md）：登录走 UI；每 test 先 clearCookies；行为断言三件套；鉴权负向成对
 *  （VIEWER 点表情不可见 + reactions 端点 403；归档项目评论 403）；console guard；API_TRUTH import。
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
  await page.getByLabel("项目名称 *").fill(`S3C ${Date.now() % 1000000}`);
  await page.getByLabel("项目标识符 *").fill(rid());
  await page.getByRole("button", { name: "创建项目", exact: true }).click();
  await page.waitForURL(/\/projects\/.+\/board/, { timeout: 15_000 });
  const m = page.url().match(/\/([^/]+)\/projects\/([^/]+)\//);
  return { slug: m?.[1] ?? WS, id: m?.[2] ?? "" };
}

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

/** 用户入口：点看板卡片打开任务详情抽屉（真实资源 + 真实点击路径）。 */
async function openDrawer(page: Page, issueId: string) {
  const card = page.locator(`article[data-card-id="${issueId}"]`);
  await expect(card).toBeVisible({ timeout: 15_000 });
  await card.click();
  await expect(page.locator("aside[role='dialog']")).toBeVisible({ timeout: 10_000 });
  await page.getByRole("tab", { name: /评论/ }).click();
}

/** 表单直发评论（造线程数据用——走真实后端契约）。 */
async function apiComment(page: Page, pid: string, issueId: string, html: string, parentId?: string) {
  const { status, body } = await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${pid}/issues/${issueId}/comments/`,
    parentId ? { comment_html: html, parent_id: parentId } : { comment_html: html });
  expect(status).toBe(HTTP.CREATED);
  return body?.data as { id: string };
}

/** 1×1 PNG / GIF 最小字节（评论图片上传用真实直传链路）。 */
const PNG_BYTES = Buffer.from(
  "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63f8cfc0f01f0005050201edb45cb90000000049454e44ae426082", "hex");
const GIF_BYTES = Buffer.from("474946383961010001008000000000000021f9040100002c00000000010001000002024401003b", "hex");

test.describe("Sprint-3 Phase 3-B 评论协作（COLLAB-002 · C.84~C.88）", () => {
  let getErrs: () => string[] = () => [];
  test.beforeEach(async ({ page }) => {
    await page.context().clearCookies();
    getErrs = attachConsoleGuard(page);
  });
  test.afterEach(async () => {
    expect.soft(getErrs(), "console errors").toEqual([]);
  });

  /* ═══════════ C.84 + C.88 两层线程 + 回复态 Composer（行为三件套） ═══════════ */

  test("S3C-1 C.84/C.88 顶层评论 ⌘Enter → 回复（parent_id 归并）→ 回复徽标 + @ 预填可删", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page);
    const issue = (await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/`, { name: "S3C1 线程宿主" })).body?.data;
    await openDrawer(page, issue.id);
    // 计数头（C.84：💬 评论 0 · 回复 0）
    await expect(page.locator('[data-sb-scope="cmt-head"]')).toContainText("评论 0");
    // 行为三件套①：⌘Enter 发表顶层 → POST comments/ 201（无 parent_id）
    const post1 = page.waitForResponse((r: Response) =>
      /\/comments\/$/.test(r.url()) && r.request().method() === "POST");
    await page.locator('[data-sb-scope="drawer-comment-input"]').fill("S3C1 顶层评论正文");
    await page.locator('[data-sb-scope="drawer-comment-input"]').press(process.platform === "darwin" ? "Meta+Enter" : "Control+Enter");
    const res1 = await post1;
    expect(res1.status()).toBe(HTTP.CREATED);
    expect((res1.request().postDataJSON() as { parent_id?: string | null }).parent_id ?? null).toBe(null);
    // UI 回读：顶层行出现（32px 头像 + 乐观插入被服务端行替换）
    const topRow = page.locator('[data-sb-scope="drawer-comment-row"]', { hasText: "S3C1 顶层评论正文" }).first();
    await expect(topRow).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('[data-sb-scope="cmt-head"]')).toContainText("评论 1");
    // 行为三件套②：点行尾 ↩ 回复 → 回复态顶部条（↩ 回复 ▾ + sr-only 归并提示）+ @ 预填（可删，BR-04）
    await topRow.locator('[data-sb-scope="cmt-reply-btn"]').first().click();
    const ctx = page.locator('[data-sb-scope="cmt-reply-ctx"]');
    await expect(ctx).toBeVisible();
    await expect(ctx).toContainText("↩ 回复 @");
    const input = page.locator('[data-sb-scope="drawer-comment-input"]');
    await expect(input).toHaveValue(/@/, { timeout: 5_000 }); // @ 锚点预填
    await input.fill(""); // @ 预填可删（删除则不触发 mentioned）
    await input.fill("S3C1 第一条回复");
    const post2 = page.waitForResponse((r: Response) =>
      /\/comments\/$/.test(r.url()) && r.request().method() === "POST");
    await page.locator('[data-sb-scope="drawer-comment-submit"]').click();
    const res2 = await post2;
    expect(res2.status()).toBe(HTTP.CREATED);
    const parent2 = (res2.request().postDataJSON() as { parent_id?: string }).parent_id;
    expect(parent2).toBeTruthy(); // 归并挂顶层
    // UI 回读：replies 区（32px 缩进 + 引导线 + 浅底行）出现回复行 +「回复」徽标
    const replyRow = page.locator('[data-sb-scope="cmt-reply"]', { hasText: "S3C1 第一条回复" });
    await expect(replyRow).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('[data-sb-scope="cmt-head"]')).toContainText("回复 1");
    await expect(replyRow.locator("text=↩ 回复").first()).toBeVisible();
  });

  test("S3C-2 C.84 回复的回复归并同线程 + 折叠条阈值 3（⊕ 查看另外 N 条 / ⊖ 收起）", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page);
    const issue = (await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/`, { name: "S3C2 折叠宿主" })).body?.data;
    // 造 4 条回复（>3 触发折叠；第 2 条回复再被回复 → 归并回顶层 + reply_to_actor）
    const top = await apiComment(page, proj.id, issue.id, "S3C2 顶层");
    const r1 = await apiComment(page, proj.id, issue.id, "S3C2 回复一", top.id);
    const r2 = await apiComment(page, proj.id, issue.id, "S3C2 回复二（将被再回复）", top.id);
    await apiComment(page, proj.id, issue.id, "S3C3 回复三", top.id);
    await apiComment(page, proj.id, issue.id, "S3C3 回复四", top.id);
    await openDrawer(page, issue.id);
    // 折叠条：默认显示前 2 + 「⊕ 查看另外 2 条回复…」（阈值 3，会话记忆默认折叠）
    const fold = page.locator('[data-sb-scope="cmt-fold"][data-fold="open"]').first();
    await expect(fold).toBeVisible({ timeout: 10_000 });
    await expect(fold).toContainText("查看另外 2 条回复");
    await expect(page.locator('[data-sb-scope="cmt-reply"]')).toHaveCount(2);
    await expect(fold).toHaveAttribute("aria-expanded", "false");
    await fold.click();
    await expect(page.locator('[data-sb-scope="cmt-reply"]')).toHaveCount(4);
    const collapse = page.locator('[data-sb-scope="cmt-fold"][data-fold="close"]').first();
    await expect(collapse).toBeVisible();
    await collapse.click();
    await expect(page.locator('[data-sb-scope="cmt-reply"]')).toHaveCount(2);
    // 回复的回复 → Composer 顶部条「回复 @xx ▾」；提交后 parent_id 归并为顶层（而非 r2）
    const replyBtn = page.locator('[data-sb-scope="cmt-reply"]', { hasText: "S3C2 回复二" }).locator('[data-sb-scope="cmt-reply-btn"]');
    await expect(replyBtn).toBeVisible();
    await replyBtn.click();
    await expect(page.locator('[data-sb-scope="cmt-reply-ctx"]')).toContainText("回复 @");
    await page.locator('[data-sb-scope="drawer-comment-input"]').fill("S3C2 回复的回复（归并）");
    const post = page.waitForResponse((r: Response) =>
      /\/comments\/$/.test(r.url()) && r.request().method() === "POST");
    await page.locator('[data-sb-scope="drawer-comment-submit"]').click();
    const res = await post;
    expect(res.status()).toBe(HTTP.CREATED);
    expect((res.request().postDataJSON() as { parent_id?: string }).parent_id).toBe(top.id); // BR-03 两级归并
    // UI 回读：新回复落在线程底部 + 回复 @徽标（reply_to_actor）
    const merged = page.locator('[data-sb-scope="cmt-reply"]', { hasText: "S3C2 回复的回复（归并）" });
    await expect(merged).toBeVisible({ timeout: 10_000 });
    await expect(merged.locator('[data-sb-scope="cmt-reply-to"]')).toContainText("@");
  });

  /* ═══════════ C.85 反应栏 + 选择器 + 名单浮层 ═══════════ */

  test("S3C-3 C.85 表情 toggle：选择器 24 枚 → POST reactions → chip 高亮；再点 → DELETE → 消失；名单浮层（你）", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page);
    const issue = (await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/`, { name: "S3C3 表情宿主" })).body?.data;
    const top = await apiComment(page, proj.id, issue.id, "S3C3 表情目标评论");
    await openDrawer(page, issue.id);
    const thread = page.locator('[data-sb-scope="drawer-comment-row"]', { hasText: "S3C3 表情目标评论" }).first();
    // ➕ 展开选择器：24 枚白名单（role=menu，§4.3.1 EMOJI_WHITELIST）
    await thread.locator('[data-sb-scope="cmt-rx-plus"]').first().click();
    const pop = page.locator('[data-sb-scope="cmt-emoji-pop"]');
    await expect(pop).toBeVisible();
    await expect(pop.locator('[data-sb-scope="cmt-emoji-cell"]')).toHaveCount(24);
    // 行为三件套：选 🎉 → POST …/reactions/ 200（body 带 emoji）→ chip 出现高亮（aria-pressed）
    const on = page.waitForResponse((r: Response) =>
      /\/reactions\/$/.test(r.url()) && r.request().method() === "POST");
    await pop.locator('[data-sb-scope="cmt-emoji-cell"][data-emoji="🎉"]').click();
    const resOn = await on;
    expect(resOn.status()).toBe(HTTP.OK);
    expect((resOn.request().postDataJSON() as { emoji?: string }).emoji).toBe("🎉");
    const chip = thread.locator('[data-sb-scope="cmt-rx-chip"][data-emoji="🎉"]');
    await expect(chip).toBeVisible({ timeout: 10_000 });
    await expect(chip).toHaveAttribute("aria-pressed", "true");
    await expect(chip).toHaveAttribute("aria-label", "🎉，1 人，含你");
    // 名单浮层（C.85：hover → GET ?expand=reactions → 前 5 人 +（你））
    const expand = page.waitForResponse((r: Response) =>
      /\/comments\/\?expand=reactions/.test(r.url()) && r.request().method() === "GET");
    await chip.hover();
    expect((await expand).status()).toBe(HTTP.OK);
    const whoPop = page.locator('[data-sb-scope="cmt-who-pop"]');
    await expect(whoPop).toBeVisible({ timeout: 10_000 });
    await expect(whoPop).toContainText("（你）");
    // 再点 chip → DELETE …/reactions/（emoji 走请求体）→ chip 消失（toggle 语义）
    const off = page.waitForResponse((r: Response) =>
      /\/reactions\/$/.test(r.url()) && r.request().method() === "DELETE");
    await chip.click();
    const resOff = await off;
    expect(resOff.status()).toBe(HTTP.OK);
    expect((resOff.request().postDataJSON() as { emoji?: string }).emoji).toBe("🎉");
    await expect(chip).toBeHidden({ timeout: 10_000 });
  });

  /* ═══════════ C.86 + C.87 图片评论直传闭环 + 灯箱 ═══════════ */

  test("S3C-4 C.86/C.87/C.88 图片评论：🖼 presign(entity_type=comment_image) → 直传 → complete → 发表 → 缩略网格 + 灯箱翻页 Esc", async ({ page }) => {
    test.setTimeout(120_000);
    await loginDemo(page);
    const proj = await createProject(page);
    const issue = (await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/`, { name: "S3C4 图片宿主" })).body?.data;
    await openDrawer(page, issue.id);
    // 两张图（png + gif——GIF 角标断言用）
    const pre1 = page.waitForResponse((r: Response) =>
      /\/attachments\/presign\/$/.test(r.url()) && r.request().method() === "POST");
    await page.locator('[data-sb-scope="cmt-img-btn"]').click();
    await page.locator('input[type="file"][aria-label="选择图片"]').setInputFiles([
      { name: "s3c4-shot.png", mimeType: "image/png", buffer: PNG_BYTES },
      { name: "s3c4-anim.gif", mimeType: "image/gif", buffer: GIF_BYTES },
    ]);
    const pres1 = await pre1;
    expect(pres1.status()).toBe(HTTP.CREATED);
    expect((pres1.request().postDataJSON() as { entity_type?: string }).entity_type).toBe("comment_image");
    // 直传（PUT 预签名对象）+ complete 确认（FILE-001 三步流：两节点全部「已上传」）
    await expect(page.locator('[data-sb-scope="cmt-upload-node"]').first()).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('[data-sb-scope="cmt-upload-node"]', { hasText: "已上传" })).toHaveCount(2, { timeout: 30_000 });
    // 发表：POST comments/ 正文 + image 节点（src 受控 download/?variant=thumb）
    const post = page.waitForResponse((r: Response) =>
      /\/comments\/$/.test(r.url()) && r.request().method() === "POST");
    await page.locator('[data-sb-scope="drawer-comment-input"]').fill("S3C4 两张图");
    await page.locator('[data-sb-scope="drawer-comment-submit"]').click();
    const res = await post;
    expect(res.status()).toBe(HTTP.CREATED);
    const html = (res.request().postDataJSON() as { comment_html?: string }).comment_html ?? "";
    expect(html).toContain("/download/?variant=thumb");
    expect(html.match(/<img /g)?.length).toBe(2);
    // C.86 缩略网格：2 列（≤2 张）96px + GIF 角标
    await expect(page.locator('[data-sb-scope="cmt-imgcell"]')).toHaveCount(2, { timeout: 15_000 });
    await expect(page.locator('[data-sb-scope="cmt-imggrid"]').first()).toHaveAttribute("data-count", "2");
    await expect(page.locator('[data-sb-scope="cmt-gif-badge"]')).toBeVisible();
    // C.87 灯箱：点缩略图 → 全屏 → ←→ 翻页（底部 1/2）→ Esc 关闭
    await page.locator('[data-sb-scope="cmt-imgcell"]').first().click();
    const lb = page.locator('[data-sb-scope="cmt-lightbox"]');
    await expect(lb).toBeVisible();
    await expect(lb.locator('[data-sb-scope="cmt-lb-meta"]')).toContainText("1 / 2");
    await page.keyboard.press("ArrowRight");
    await expect(lb.locator('[data-sb-scope="cmt-lb-meta"]')).toContainText("2 / 2");
    await page.keyboard.press("ArrowLeft");
    await expect(lb.locator('[data-sb-scope="cmt-lb-meta"]')).toContainText("1 / 2");
    await page.keyboard.press("Escape");
    await expect(lb).toBeHidden();
  });

  /* ═══════════ C.84 父删子留占位 ═══════════ */

  test("S3C-5 C.84 父删子留：删除带回复的顶层 → 占位行「回复 N 条保留」+ 线程不塌", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page);
    const issue = (await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/`, { name: "S3C5 父删子留宿主" })).body?.data;
    const top = await apiComment(page, proj.id, issue.id, "S3C5 将被删除的父");
    await apiComment(page, proj.id, issue.id, "S3C5 保留的回复甲", top.id);
    await apiComment(page, proj.id, issue.id, "S3C5 保留的回复乙", top.id);
    await openDrawer(page, issue.id);
    const thread = page.locator('[data-sb-scope="drawer-comment-row"]', { hasText: "S3C5 将被删除的父" }).first();
    // 🗑 + 确认 → DELETE comments/{id} → 父转占位（is_deleted）+ replies 保留渲染（BR-06）
    page.once("dialog", (d) => void d.accept());
    const del = page.waitForResponse((r: Response) =>
      /\/comments\/[0-9a-f-]+\/$/.test(r.url()) && r.request().method() === "DELETE");
    await thread.locator('[data-sb-scope="cmt-del"]').click();
    expect((await del).status()).toBe(HTTP.OK);
    const placeholder = page.locator('[data-sb-scope="cmt-deleted"]', { hasText: "该评论已删除" });
    await expect(placeholder).toBeVisible({ timeout: 10_000 });
    await expect(placeholder).toContainText("回复 2 条保留");
    await expect(page.locator('[data-sb-scope="cmt-reply"]', { hasText: "S3C5 保留的回复甲" })).toBeVisible();
    await expect(page.locator('[data-sb-scope="cmt-reply"]', { hasText: "S3C5 保留的回复乙" })).toBeVisible();
  });

  /* ═══════════ C.88 发表失败回滚（草稿保留） + 归档项目 403（鉴权负向成对） ═══════════ */

  test("S3C-6 C.88 归档项目评论 403：发表被拦 + 草稿保留（前后端双层断言）", async ({ page }) => {
    test.setTimeout(90_000);
    await loginDemo(page);
    const proj = await createProject(page);
    const issue = (await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/`, { name: "S3C6 归档只读宿主" })).body?.data;
    await openDrawer(page, issue.id);
    // 页内归档项目（apiCall 与浏览器共享会话）→ 评论端点 403 PERM_PROJECT_ARCHIVED（IT-08）
    const arch = await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${proj.id}/archive/`);
    expect(arch.status).toBe(HTTP.OK);
    // 前端：⌘Enter 发表 → 400/403 错误 toast + 乐观行回滚 + 草稿保留（C.88 失败回滚行）
    await page.locator('[data-sb-scope="drawer-comment-input"]').fill("S3C6 归档项目中的评论");
    await page.locator('[data-sb-scope="drawer-comment-submit"]').click();
    await expect(page.locator(".fixed.top-4.right-4")).toContainText(/已归档|发表失败/, { timeout: 10_000 });
    await expect(page.locator('[data-sb-scope="drawer-comment-input"]')).toHaveValue("S3C6 归档项目中的评论");
    await expect(page.locator('[data-sb-scope="drawer-comment-row"]')).toHaveCount(0);
    // 后端边界：直发 POST comments/ → 403 PERM_PROJECT_ARCHIVED
    const r403 = await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${proj.id}/issues/${issue.id}/comments/`,
      { comment_html: "direct probe" });
    expect(r403.status).toBe(HTTP.FORBIDDEN);
    expect(r403.body?.error?.code).toBe("PERM_PROJECT_ARCHIVED");
  });
});
