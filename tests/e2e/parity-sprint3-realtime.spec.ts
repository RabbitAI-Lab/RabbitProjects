/** Sprint-3 Phase 3-C 前端（COLLAB-004 实时接入）——双 context 双账号集成 e2e。
 *
 *  覆盖（owner = COLLAB-004 §3.1~§3.3 / §4.4；附录 C.92~C.97）：
 *    C.93 presence 头像列（joined/left 维护 + 自己影子位 + hover 成员卡）
 *    C.94 连接指示三态 + 详情弹层（rooms/延迟/重连）
 *    C.95 降级横幅（连接失败累计 30s）+ 立即重连 + 恢复撤除（BR-10）
 *    C.96 实时看板同步：IT-01 双账号拖卡 <1s 对端可见（300ms 淡入 + 列计数）
 *          + 本地拖拽保护 + version 旧于本地忽略（BR-07）+ 自己操作不回显（BR-08）
 *          + batch_id 聚合 Toast（§2.3 注 2）
 *    C.97 铃铛 notification.created 徽标 +1 弹跳 / 评论流「N 条新回复 ↓」浮条 /
 *          动态流 activity.created 增量划入 / 任务详情 brief 轻闪角标
 *
 *  运行前置（整文件 test.skip 守卫，console 说明留主线）：
 *    ① live:3000 /health 可达（未起：`pnpm --filter live dev` 后台起）；
 *    ② api 8000 已配置 LIVE_JWT_PRIVATE_KEY 可换票（当前 dev 未配时，运行者用
 *       独立副栈：api:8001 + live:3000 + web:3002（API_PROXY_TARGET=8001），
 *       E2E_BASE_URL=http://localhost:3002）。
 *
 *  worker 腿说明（直灌链路 = scripts/realtime_smoke.py B 分支的既有先例）：本 spec
 *  断言的是**前端实时链路**（browser → live WSS → LiveEventBus → MobX → DOM），事件
 *  由 spec 直灌 Redis rp:events（与真实 Worker publish_event 同一通道/载荷契约，
 *  §4.2.2）；Worker→publish 腿为后端已交付覆盖面（345 pytest + 冒烟 A 分支）。
 *  UI 侧写操作全部走真实浏览器（PATCH 2xx 三件套）。
 *
 *  纪律：登录走 UI；每 test clearCookies；console guard 全量；API_TRUTH import；
 *  突变自检结论见仓库报告（去 version 过滤 → S3R-1 STALE 用例红；拖拽保护去除 →
 *  保护用例红；去 actor 过滤 → live 侧 BR-08 先行过滤，集成层同因掩盖、纵深防御留码内）。
 */
import { test, expect, type Page, type Browser, type BrowserContext, type Response } from "@playwright/test";
import { execSync, spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
// 注：不使用 import.meta.url——Playwright 1.62 CJS transform 对 import.meta 的
// require 垫片有缺陷（实测 ReferenceError: require is not defined），以 cwd 推导。
import { attachConsoleGuard, HTTP } from "./no-console-errors";

const E2E_ORIGIN = process.env.E2E_BASE_URL ?? "http://localhost:3001";
const LIVE_HEALTH = process.env.E2E_LIVE_HEALTH ?? "http://localhost:3000/health";
const WS = "workspace";
/** 降级横幅用例允许操作 live 进程（仅当运行者声明自有：LIVE_OWNED_BY_SPEC=1）。 */
const LIVE_OWNED = process.env.LIVE_OWNED_BY_SPEC === "1";

/* ── live 可用性探测：不可达 → 整文件 skip（console 说明留主线）── */
let liveUp = false;
let liveSkipLogged = false;
function logLiveSkip(): void {
  if (liveSkipLogged) return;
  liveSkipLogged = true;
  // eslint-disable-next-line no-console
  console.log(
    "[S3R] live /health 不可达（" + LIVE_HEALTH + "）——parity-sprint3-realtime 整文件 test.skip。\n" +
    "     恢复方式：起 live（需 LIVE_JWT_PUBLIC_KEY/INTERNAL_KEY/REDIS_URL，见 scripts/gen_live_keys.sh\n" +
    "     与 scripts/realtime_smoke.py 头注），并确保 api 已配 LIVE_JWT_PRIVATE_KEY 可换票。\n" +
    "     其余 spec 主线不受影响（BR-10 降级：前端回落轮询、功能零损失）。",
  );
}
/** 探测放 beforeEach：test.skip(callback) 先于 beforeAll 钩子求值（实测），钩子内
 *  显式 test.skip 才能既探测又跳过。 */
test.beforeEach(async ({ request }) => {
  if (liveUp) return;
  try {
    const r = await request.get(LIVE_HEALTH, { timeout: 3_000 });
    liveUp = r.ok();
  } catch {
    liveUp = false;
  }
  if (!liveUp) {
    logLiveSkip();
    test.skip(true, "live 实时链路未就绪（COLLAB-004 e2e 前置）");
  }
});

/* ── Redis 直灌发布器（与 Worker publish_event 同通道 §4.2.2）── */
const here = process.cwd();
type IORedis = { publish(ch: string, msg: string): Promise<number>; disconnect(): void };
let publisherPromise: Promise<IORedis> | null = null;
function redis(): Promise<IORedis> {
  if (!publisherPromise) {
    const url = process.env.E2E_REDIS_URL ?? "redis://localhost:6379/0";
    // ioredis 只在 apps/live 的依赖树内——动态 import 其 CJS 入口（目录导入不被
    // ESM resolver 支持，直接指到 built/index.js）
    const modPath = path.resolve(here, "apps/live/node_modules/ioredis/built/index.js");
    const modUrl = "file://" + (modPath.startsWith("/") ? modPath : "/" + modPath);
    publisherPromise = import(modUrl)
      .then((mod) => {
        const Ctor = ((mod as { default?: unknown }).default ?? mod) as new (u: string) => IORedis;
        return new Ctor(url);
      })
      .catch((e) => {
        publisherPromise = null;
        throw e;
      });
  }
  return publisherPromise;
}
async function publishEvent(event: string, rooms: string[], payload: Record<string, unknown>): Promise<number> {
  const msg = JSON.stringify({ event, rooms, payload, occurred_at: new Date().toISOString() });
  return (await redis()).publish("rp:events", msg);
}
test.afterAll(async () => {
  if (publisherPromise) {
    try { (await publisherPromise).disconnect(); } catch { /* ignore */ }
    publisherPromise = null;
  }
});

const rid = (n = 5) =>
  Array.from({ length: n }, () => String.fromCharCode(65 + Math.floor(Math.random() * 26))).join("");

async function loginDemo(page: Page) {
  await page.context().clearCookies();
  await page.goto("/login");
  await page.getByRole("button", { name: /一键进入演示账号/ }).click();
  await page.waitForFunction(() => /\/projects$/.test(location.pathname), null, { timeout: 15_000 });
}

async function registerUser(ctx: BrowserContext, email: string, _name: string): Promise<Page> {
  const page = await ctx.newPage();
  await page.goto("/register");
  await page.getByLabel(/邮箱/).fill(email);
  await page.getByLabel(/密码/, { exact: false }).first().fill("Rabbit123!");
  await page.getByLabel(/确认密码/).fill("Rabbit123!");
  await page.getByRole("button", { name: /注册|创建账号/ }).click();
  await page.waitForFunction(() => /\/projects$/.test(location.pathname), null, { timeout: 15_000 });
  return page;
}

/** 与浏览器 context 共享 cookie 的 API 调用。 */
async function apiCall(page: Page, method: string, path: string, data?: unknown) {
  const cookies = await page.context().cookies();
  const csrf = cookies.find((c) => c.name === "csrftoken")?.value ?? "";
  const r = await page.request.fetch(`${E2E_ORIGIN}${path}`, {
    method,
    headers: { "Content-Type": "application/json", ...(csrf ? { "X-CSRFToken": csrf } : {}) },
    data: data === undefined ? undefined : JSON.stringify(data),
  });
  let body: Record<string, any> | null = null;
  try { body = (await r.json()) as Record<string, any>; } catch { /* 非 JSON */ }
  return { status: r.status(), body };
}

async function myUserId(page: Page): Promise<string> {
  const { status, body } = await apiCall(page, "GET", "/api/v1/users/me/");
  expect(status).toBe(HTTP.OK);
  return body?.data?.user?.id as string;
}

/** 捕获页面 /live WS 下行帧（payload 原文；BR-08 集成断言用）。 */
function captureLiveFrames(page: Page): string[] {
  const frames: string[] = [];
  page.on("websocket", (ws) => {
    if (!ws.url().includes("/live/connect")) return;
    ws.on("framereceived", (f) => frames.push(String(f.payload)));
  });
  return frames;
}

interface Dual {
  aPage: Page;
  bPage: Page;
  proj: { slug: string; id: string };
  aId: string;
  bId: string;
  bEmail: string;
  /** A 页 /live WS 下行帧（setupDual 即挂监听——含首连）。 */
  aFrames: string[];
  cleanup: () => Promise<void>;
}

/** 双账号装配：A=演示张三（建项目）→ 邀请 B（SMTP 降级 link）→ B 注册接受 → 入项目。 */
async function setupDual(browser: Browser): Promise<Dual> {
  const ctxA = await browser.newContext();
  const aPage = await ctxA.newPage();
  const aFrames = captureLiveFrames(aPage); // WS 帧监听须先于首连挂载
  await loginDemo(aPage);
  const btn = aPage.getByRole("button", { name: /创建项目/ }).first();
  await btn.waitFor({ state: "visible", timeout: 20_000 });
  await btn.click();
  await aPage.getByLabel("项目名称 *").fill(`S3R ${Date.now() % 1000000}`);
  await aPage.getByLabel("项目标识符 *").fill(rid());
  await aPage.getByRole("button", { name: "创建项目", exact: true }).click();
  await aPage.waitForURL(/\/projects\/.+\/board/, { timeout: 15_000 });
  const m = aPage.url().match(/\/([^/]+)\/projects\/([^/]+)\//);
  const proj = { slug: m?.[1] ?? WS, id: m?.[2] ?? "" };
  const aId = await myUserId(aPage);

  // B：注册 → 接受邀请（A 发；SMTP 降级回显 invite_links 于 meta）
  const bEmail = `s3r-b-${Date.now()}-${Math.floor(Math.random() * 1e4)}@rabbit.dev`;
  const invite = await apiCall(aPage, "POST", `/api/v1/workspaces/${WS}/invitations/`, { emails: [bEmail], role: 10 });
  expect(invite.status).toBe(HTTP.OK);
  const inviteLinks = ((invite.body?.meta?.invite_links ?? {}) as Record<string, string>);
  const link = (inviteLinks[bEmail] ?? "").match(/\/invite\/([A-Za-z0-9\-_.]+)/);
  expect(link, "SMTP 降级 invite_links（dev e2e 前置）").toBeTruthy();
  const ctxB = await browser.newContext();
  const bPage = await registerUser(ctxB, bEmail, "李四S3R");
  // 接受邀请：注册钩子对被邀请邮箱自动落成员（显式 accept 已被使用时 400 属预期），
  // 先显式试一次兜底再查成员表
  const tryAccept = async () => {
    const bcookies = await ctxB.cookies();
    const bcsrf = bcookies.find((c) => c.name === "csrftoken")?.value ?? "";
    await bPage.request.post(`${E2E_ORIGIN}/api/v1/invitations/${link![1]}/accept/`, {
      headers: { "Content-Type": "application/json", ...(bcsrf ? { "X-CSRFToken": bcsrf } : {}) }, data: {} });
  };
  await tryAccept();
  const bId = await myUserId(bPage);
  // A 把 B 加进项目（CONTRIBUTOR）
  let membersR = await apiCall(aPage, "GET", `/api/v1/workspaces/${WS}/members/?per_page=100`);
  let vm = ((membersR.body?.data ?? []) as Array<{ id: string; user: { email: string } }>).find((x) => x.user.email === bEmail);
  if (!vm) {
    await tryAccept();
    membersR = await apiCall(aPage, "GET", `/api/v1/workspaces/${WS}/members/?per_page=100`);
    vm = ((membersR.body?.data ?? []) as Array<{ id: string; user: { email: string } }>).find((x) => x.user.email === bEmail);
  }
  expect(vm, "B 应已成为工作空间成员（注册钩子或显式接受）").toBeTruthy();
  const add = await apiCall(aPage, "POST", `/api/v1/workspaces/${WS}/projects/${proj.id}/members/`, { member_ids: [vm!.user.id], role: 15 });
  expect([HTTP.OK, HTTP.CREATED], "项目成员添加（200/201）").toContain(add.status);
    expect(((add.body?.data ?? []) as Array<{ status?: string }>)[0]?.status ?? "added", "逐项应为 added").toBe("added");

  const cleanup = async () => { await ctxA.close(); await ctxB.close(); };
  return { aPage, bPage, proj, aId, bId, bEmail, aFrames, cleanup };
}

/** 双方进入同一看板并等实时已连接（绿点）。 */
async function bothOnBoard(d: Dual) {
  await d.aPage.waitForURL(/\/board/);
  await d.bPage.goto(`/${d.proj.slug}/projects`);
  const card = d.bPage.locator(`a[href*="/projects/${d.proj.id}"]`).first();
  await card.waitFor({ state: "visible", timeout: 15_000 });
  await card.click();
  await d.bPage.waitForURL(/\/board/, { timeout: 15_000 });
  for (const p of [d.aPage, d.bPage]) {
    await expect(p.locator('[data-sb-scope="conn-dot-btn"][aria-label="实时已连接"]'))
      .toBeVisible({ timeout: 20_000 });
  }
}

/** 目标列定位（state 分组：data-col=state group）。 */
async function mkIssueIn(page: Page, pid: string, name: string): Promise<{ id: string; key: string }> {
  const { status, body } = await apiCall(page, "POST", `/api/v1/workspaces/${WS}/projects/${pid}/issues/`, { name });
  expect(status).toBe(HTTP.CREATED);
  return { id: body?.data?.id, key: body?.data?.issue_key };
}

test.describe("Sprint-3 Phase 3-C 实时协作（COLLAB-004 · C.93~C.97）", () => {
  let getErrs: () => string[] = () => [];
  test.beforeEach(async ({ page }) => {
    await page.context().clearCookies();
    getErrs = attachConsoleGuard(page);
  });
  test.afterEach(async () => {
    expect.soft(getErrs(), "console errors").toEqual([]);
  });

  /* ═══════════ C.93 presence + C.94 连接指示 + C.96 IT-01 双端同步 <1s ═══════════ */

  test("S3R-1 C.93/C.94/C.96 IT-01 双账号看板：presence 2 人 / B 拖卡 A <1s 可见 / A 不回显 / 旧 version 忽略", async ({ browser }) => {
    test.setTimeout(150_000);
    const d = await setupDual(browser);
    const aErrs = attachConsoleGuard(d.aPage);
    const bErrs = attachConsoleGuard(d.bPage);
    try {
      await bothOnBoard(d);
      // C.93 presence：B 加入项目房间 → A 头像列 2 人（自己影子位 + B；aria-label）
      await expect(d.aPage.locator('[data-sb-scope="presence-cluster"][aria-label*="2 人"]'))
        .toBeVisible({ timeout: 15_000 });
      // C.94 连接详情弹层：状态/房间/延迟/重连 + [重连]
      await d.aPage.locator('[data-sb-scope="conn-dot-btn"]').click();
      const pop = d.aPage.locator('[data-sb-scope="conn-pop"]');
      await expect(pop).toBeVisible();
      await expect(pop).toContainText("已连接");
      await expect(pop).toContainText(`project:${d.proj.id}`);
      await d.aPage.locator("body").dispatchEvent("mousedown");
      // 数据：B 建一任务（unstarted 列）
      const issue = await mkIssueIn(d.bPage, d.proj.id, "S3R1 拖拽目标");
      await d.bPage.reload();
      await expect(d.bPage.locator(`article[data-card-id="${issue.id}"]`)).toBeVisible({ timeout: 20_000 });

      // C.96 IT-01：B 拖卡 待办 → 进行中（真实 PATCH；C.67 dragTo 同款）
      const patch = d.bPage.waitForResponse((r: Response) =>
        new RegExp(`/issues/${issue.id}/`).test(r.url()) && r.request().method() === "PATCH");
      const startedColA = d.aPage.locator('section[data-sb-scope="bcol"]').filter({ hasText: "进行中" }).first();
      const startedColB = d.bPage.locator('section[data-sb-scope="bcol"]').filter({ hasText: "进行中" }).first();
      await d.bPage.locator(`article[data-card-id="${issue.id}"]`).first()
        .dragTo(startedColB, { targetPosition: { x: 140, y: 200 } });
      const patchResp = await patch;
      expect(patchResp.status()).toBe(HTTP.OK);

      // 直灌 issue.state.changed（actor=B；真实 Worker 同通道/载荷 §4.2.2）→ A 端 <1s
      const t0 = Date.now();
      const n = await publishEvent("issue.state.changed", [`project:${d.proj.id}`, `issue:${issue.id}`], {
        issue_id: issue.id,
        actor_id: d.bId,
        version: new Date().toISOString(),
        brief: "state",
        from_group: "unstarted",
        to_group: "started",
      });
      expect(n).toBeGreaterThan(0);
      await expect(d.aPage.locator(`article[data-card-id="${issue.id}"]`)).toBeVisible({ timeout: 5_000 });
      // 卡片应出现在 A 的「进行中」列（300ms 淡入 + 列计数迁移）
      await expect(startedColA.locator(`article[data-card-id="${issue.id}"]`)).toBeVisible({ timeout: 5_000 });
      const elapsed = Date.now() - t0;
      // 排除轮询兜底（60s）干扰后的实时链路口径：秒级可见（IT-01）
      expect.soft(elapsed < 1_000, `publish→对端可见应 <1s（实际 ${elapsed}ms）`).toBe(true);

      // C.96 BR-08：自己的操作不回显——A 发起变更（actor=A）→ A 的 WS 不应收业务帧
      const before = d.aFrames.length;
      await apiCall(d.aPage, "PATCH", `/api/v1/workspaces/${WS}/projects/${d.proj.id}/issues/${issue.id}/`, { priority: "high" });
      await publishEvent("issue.updated", [`project:${d.proj.id}`], {
        issue_id: issue.id, actor_id: d.aId, version: new Date().toISOString(), brief: "priority",
      });
      await d.aPage.waitForTimeout(1_200);
      const bizFrames = d.aFrames.slice(before).filter((f) => /issue\.(updated|state\.changed)/.test(f));
      expect(bizFrames, "actor==me 的事件不得回显本人连接（BR-08，live 过滤）").toHaveLength(0);

      // C.96 BR-07：version 旧于本地（拖拽后 updated_at 已推进）→ 忽略——乱序回放既不
      // 迁移 DOM 也不触发收敛拉取（以 GET group_by 请求计数断言；突变：去 version
      // 过滤 → 计数 >0 → 本断言红——前端 fetch 收敛会掩盖事件载荷，须以请求数为准）
      let staleFetches = 0;
      d.aPage.on("request", (req) => {
        if (/\/issues\/\?.*group_by=/.test(req.url())) staleFetches += 1;
      });
      const beforeStale = staleFetches;
      const stale = await publishEvent("issue.state.changed", [`project:${d.proj.id}`], {
        issue_id: issue.id, actor_id: d.bId,
        version: "2000-01-01T00:00:00Z", // 远旧于本地
        brief: "state", from_group: "started", to_group: "cancelled",
      });
      expect(stale).toBeGreaterThan(0);
      await d.aPage.waitForTimeout(1_500);
      expect(staleFetches - beforeStale, "旧 version 事件不得触发收敛拉取（BR-07 乱序免疫）").toBe(0);
      await expect(d.aPage.locator('section[data-sb-scope="bcol"]').filter({ hasText: "已取消" })
        .locator(`article[data-card-id="${issue.id}"]`)).toHaveCount(0);

      expect.soft(aErrs(), "A console").toEqual([]);
      expect.soft(bErrs(), "B console").toEqual([]);
    } finally {
      await d.cleanup();
    }
  });

  /* ═══════════ C.97 铃铛 notification.created 徽标 +1 ═══════════ */

  test("S3R-2 C.97 A 指派 B → B 铃铛徽标秒级 +1（notification.created）", async ({ browser }) => {
    test.setTimeout(120_000);
    const d = await setupDual(browser);
    const bErrs = attachConsoleGuard(d.bPage);
    try {
      await bothOnBoard(d);
      const issue = await mkIssueIn(d.aPage, d.proj.id, "S3R2 指派目标");
      // 基线徽标（B）
      const bell = d.bPage.locator('[data-sb-scope="topbar-bell"]');
      const baseLabel = await bell.getAttribute("aria-label");
      const baseN = Number((baseLabel ?? "").match(/(\d+) 条未读/)?.[1] ?? 0);
      // A 真实指派（PUT assignees）+ 直灌 notification.created（user 房间）
      const put = await apiCall(d.aPage, "PUT", `/api/v1/workspaces/${WS}/projects/${d.proj.id}/issues/${issue.id}/assignees/`, { assignee_ids: [d.bId] });
      expect(put.status).toBe(HTTP.OK);
      await publishEvent("notification.created", [`user:${d.bId}`], {
        notification_id: `s3r-notif-${Date.now()}`, unread_delta: 1,
      });
      await expect(d.bPage.locator('[data-sb-scope="topbar-bell"]'))
        .toHaveAttribute("aria-label", new RegExp(`${baseN + 1} 条未读`), { timeout: 3_000 });
      expect.soft(bErrs(), "B console").toEqual([]);
    } finally {
      await d.cleanup();
    }
  });

  /* ═══════════ C.92/C.97 动态流 activity.created 增量划入 + C.97 评论流浮条 ═══════════ */

  test("S3R-3 C.92/C.97 B 在动态流页：A 产生动态/评论 → 新行划入 + 「N 条新回复 ↓」浮条", async ({ browser }) => {
    test.setTimeout(150_000);
    const d = await setupDual(browser);
    const bErrs = attachConsoleGuard(d.bPage);
    try {
      // B 停留动态流页（在顶可见 → ≤5 条划入路径）
      await d.bPage.goto(`/${d.proj.slug}/projects/${d.proj.id}/activity`);
      await expect(d.bPage.locator('[data-sb-scope="stream-row"], [data-sb-scope="stream-empty"]').first())
        .toBeVisible({ timeout: 15_000 });
      const issue = await mkIssueIn(d.aPage, d.proj.id, "S3R3 动态增量目标");
      // A 建任务（真实 POST）→ 直灌 activity.created（水位锚 payload）
      await publishEvent("activity.created", [`project:${d.proj.id}`], {
        actor_id: d.aId, issue_id: issue.id,
        stream_cursor: `${new Date().toISOString()}:00000000-0000-0000-0000-000000000000`,
        occurred_at: new Date().toISOString(),
      });
      // ≤5 条 + 在顶 → 顶部划入：新行出现（无需 B 交互）
      await expect(d.bPage.locator(`[data-sb-scope="stream-chip"][data-issue-key="${issue.key}"]`))
        .toBeVisible({ timeout: 4_000 });

      // 评论流：B 打开任务详情 → 评论 Tab；A 评论（真实 POST）→ 直灌 comment.created
      await d.bPage.goto(`/${d.proj.slug}/projects/${d.proj.id}/board?peekIssue=${issue.id}`);
      const drawer = d.bPage.locator('[role="dialog"][aria-label*="任务详情"]');
      await expect(drawer).toBeVisible({ timeout: 15_000 });
      await drawer.getByRole("tab", { name: /评论/ }).click();
      await expect(d.bPage.locator('[data-sb-scope="cmt-head"]')).toBeVisible({ timeout: 10_000 });
      const cmt = await apiCall(d.aPage, "POST", `/api/v1/workspaces/${WS}/projects/${d.proj.id}/issues/${issue.id}/comments/`, { comment_html: "S3R3 李四看这里（远端评论）" });
      expect(cmt.status).toBe(HTTP.CREATED);
      await publishEvent("comment.created", [`issue:${issue.id}`, `project:${d.proj.id}`], {
        actor_id: d.aId, comment_id: `s3r-cmt-${Date.now()}`, issue_id: issue.id,
      });
      // 底部「N 条新回复 ↓」浮条（不抢滚动位置）+ 点击滚到底部并清零
      // （N≥1：真实 Worker 链 + 直灌可能叠加，前端按 comment_id 去重后仍 ≥1）
      const floatBar = d.bPage.locator('[data-sb-scope="cmt-new-replies"]');
      await expect(floatBar).toBeVisible({ timeout: 4_000 });
      await expect(floatBar).toContainText(/\d+ 条新回复/);
      await floatBar.click();
      await expect(floatBar).toBeHidden({ timeout: 3_000 });
      expect.soft(bErrs(), "B console").toEqual([]);
    } finally {
      await d.cleanup();
    }
  });

  /* ═══════════ C.95 降级横幅（BR-10）+ 立即重连 + 恢复撤除 ═══════════ */

  test("S3R-4 C.95 断 live → 30s 降级横幅 → 重启 + 立即重连 → 横幅撤除、恢复已连接", async ({ browser, page }) => {
    test.setTimeout(180_000);
    // 仅当运行者自有 live 进程（LIVE_OWNED_BY_SPEC=1）才允许停/起；否则跳过留说明
    test.skip(!LIVE_OWNED,
      "live 由外部启动——不停用户服务；降级横幅由运行者以 LIVE_OWNED_BY_SPEC=1 复演（mock 变体见附录 C.95 备注）");
    getErrs = attachConsoleGuard(page);
    await loginDemo(page);
    const btn = page.getByRole("button", { name: /创建项目/ }).first();
    await btn.waitFor({ state: "visible", timeout: 20_000 });
    await btn.click();
    await page.getByLabel("项目名称 *").fill(`S3R4 ${Date.now() % 1000000}`);
    await page.getByLabel("项目标识符 *").fill(rid());
    await page.getByRole("button", { name: "创建项目", exact: true }).click();
    await page.waitForURL(/\/board/, { timeout: 15_000 });
    await expect(page.locator('[data-sb-scope="conn-dot-btn"][aria-label="实时已连接"]')).toBeVisible({ timeout: 20_000 });

    const liveEnvFile = process.env.E2E_LIVE_ENV_FILE ?? "/tmp/s3r/live.env";
    test.skip(!fs.existsSync(liveEnvFile),
      `live 启动 env 文件缺失（${liveEnvFile}）——降级横幅用例需运行者副栈 env（E2E_LIVE_ENV_FILE 可覆盖）`);
    // 停 live（自有进程）：按端口取 pid（tsx/node dist 均覆盖）
    const kill = (port: number) => {
      try {
        // 仅杀 LISTEN 进程：裸 lsof -ti tcp:PORT 会连带 vite 代理的 ESTABLISHED
        // 连接属主（实测把 3002 web 一并杀掉 → 浏览器 session closed）
        const pids = execSync(`lsof -ti tcp:${port} -sTCP:LISTEN || true`).toString().trim().split(/\s+/).filter(Boolean);
        for (const pid of pids) { try { process.kill(Number(pid), "SIGTERM"); } catch { /* 已退 */ } }
      } catch { /* lsof 不可用 */ }
    };
    const startLive = () => {
      const env: Record<string, string> = { ...process.env } as Record<string, string>;
      for (const line of fs.readFileSync(liveEnvFile, "utf8").split("\n")) {
        const m = line.match(/^([A-Z_]+)=(.*)$/);
        if (m) env[m[1]] = m[2].replace(/^"|"$/g, "");
      }
      const child = spawn("node", ["dist/index.js"], { cwd: path.resolve(here, "apps/live"), env, detached: true, stdio: "ignore" });
      child.unref();
    };

    kill(3000);
    // BR-10：连接失败累计 30s → 常驻黄条（backoff 1/2/4/8/16 → 30s 窗口跨过阈值）
    await expect(page.locator('[data-sb-scope="rt-degraded-banner"]')).toBeVisible({ timeout: 45_000 });
    await expect(page.locator('[data-sb-scope="rt-degraded-banner"]')).toContainText("实时同步暂停 · 已切换为定时刷新");
    await expect(page.locator('[data-sb-scope="rt-banner-reconnect"]')).toBeVisible();
    await expect(page.locator('[data-sb-scope="rt-banner-dismiss"]')).toBeVisible();

    // 重启 live → 恢复路径二选一：/health 30s 探测自动重连（BR-10）或手动 [立即重连]
    // （探测可能先到把横幅撤掉——按钮随即消失，点击路径仅在按钮仍在时走）
    startLive();
    const reBtn = page.locator('[data-sb-scope="rt-banner-reconnect"]');
    try {
      await reBtn.waitFor({ state: "visible", timeout: 4_000 });
      await reBtn.click();
    } catch {
      // 探测已先行恢复——由下方隐含断言收口
    }
    await expect(page.locator('[data-sb-scope="rt-degraded-banner"]')).toBeHidden({ timeout: 35_000 });
    await expect(page.locator('[data-sb-scope="conn-dot-btn"][aria-label="实时已连接"]')).toBeVisible({ timeout: 15_000 });
  });
});
