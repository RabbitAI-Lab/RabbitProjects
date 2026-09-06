#!/usr/bin/env node
/**
 * Sprint-4 验收录屏（13 幕 × 独立视频；幕 10/11/13 为双视口成对视频 a/b）。
 *
 * 纪律：所有场景从真实用户入口出发——登录页 →（一键演示账号 / 李四表单登录 →
 * 顶栏切工作空间）→ 项目卡片 → 侧栏导航（甘特/文件）→ 操作；禁止直接 goto 深链。
 * 例外两处（均为契约明文）：幕 10 匿名收件人直达 space /s/{slug}（分享链接即被
 * 验收功能本身、收件人唯一入口）；幕 11 越权分支在登录后页内直连 download-url
 * 取证（后端才是安全边界）。每幕独立 browser context（recordVideo 1440×900）、
 * 关键交互 waitForResponse 校验 2xx（录到的必须是成功画面）、失败画面留 1.2s、
 * ONLY= 幕名过滤。幕 ① 的万级数据集由 seed --gantt10k 现场建、录完即清（不进
 * 常驻演示库）。
 *
 * 用法：node scripts/acceptance_video_s4.mjs
 *       ONLY=分享全链路 node scripts/acceptance_video_s4.mjs   # 单幕重录（会先重跑 seed）
 * 前置：API 8000（全量 env）+ web 3001 + live 3000 + worker（activity 单代）+
 *       MinIO 9000 + space 3003（pnpm dev:space）+ PG。
 * 输出：docs/sprint-4-acceptance/videos/scene-XX[-a|b]-<名称>.webm + README 索引
 */
import { chromium, expect } from "@playwright/test";
import { execSync } from "node:child_process";
import { mkdirSync, mkdtempSync, renameSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const WEB = process.env.E2E_BASE_URL ?? "http://localhost:3001";
const SPACE = process.env.E2E_SPACE_URL ?? "http://localhost:3003";
const OUT = "docs/sprint-4-acceptance/videos";
const PROJ_NAME = "S4 验收演示";
const G10K_NAME = "万级甘特演示";
const LISI = { email: "lisi@rabbit.dev", password: "Rabbit123!" };
mkdirSync(OUT, { recursive: true });

const results = [];
let sceneNo = 0;
/** 幕 1 实测指标（README 万级数据集说明引用；仅幕 1 执行时填充）。 */
const G10K_METRICS = { ui: 0, rows: 0, rel: 0 };

// 场景失败收场时（context 关闭）漂浮的 waitForResponse 会 reject——吞掉防进程夭折；
// 幕成败由 runScene 的 try/catch 统一裁决
process.on("unhandledRejection", (e) => {
  console.error(`  （吞漂浮 rejection：${String(e).split("\n")[0].slice(0, 120)}）`);
});

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const dISO = (off) => new Date(Date.now() + off * 86_400_000).toLocaleDateString("sv-SE");
const ONLY = (process.env.ONLY ?? "").split(",").filter(Boolean);
const ok2xx = (r) => (r.status() >= 200 && r.status() < 300
  ? Promise.resolve(r)
  : Promise.reject(new Error(`${r.request().method()} ${r.url().slice(-70)} → ${r.status()}`)));

/** 单幕录制：pages=1 → scene-XX-名称.webm；pages=2 → scene-XXa/XXb（成对观看）。 */
async function runScene(browser, name, fn, { pages = 1 } = {}) {
  sceneNo += 1;
  if (ONLY.length && !ONLY.some((k) => name.includes(k))) {
    console.log(`⊘ 幕${String(sceneNo).padStart(2, "0")} ${name}（ONLY 过滤跳过）`);
    return;
  }
  const id = String(sceneNo).padStart(2, "0");
  const ctxs = [];
  const ps = [];
  for (let i = 0; i < pages; i += 1) {
    const ctx = await browser.newContext({
      baseURL: WEB,
      viewport: { width: 1440, height: 900 },
      recordVideo: { dir: OUT, size: { width: 1440, height: 900 } },
    });
    ctxs.push(ctx);
    ps.push(await ctx.newPage());
  }
  const t0 = Date.now();
  let ok = true;
  let err = "";
  try {
    await fn(pages === 1 ? ps[0] : ps);
  } catch (e) {
    ok = false;
    err = String(e).split("\n").slice(0, 6).join(" | ");
    console.error(`  ✗ 幕${id} ${name} 失败：\n${String(e).split("\n").slice(0, 10).join("\n")}`);
    await ps[0].screenshot({ path: `/tmp/s4-fail-${id}.png` }).catch(() => {});
    await sleep(1200); // 失败画面留 1.2s 便于验收方看到现场
  }
  await sleep(900); // 等视频尾部封帧
  const videos = ps.map((p) => p.video());
  await Promise.all(ctxs.map((c) => c.close()));
  const suffix = pages === 1 ? [""] : ["a", "b"];
  for (let i = 0; i < videos.length; i += 1) {
    renameSync(await videos[i].path(), join(OUT, `scene-${id}${suffix[i]}-${name}.webm`));
  }
  results.push({ id, name, ok, seconds: Math.round((Date.now() - t0) / 1000), err });
  console.log(`${ok ? "✓" : "✗"} 幕${id} ${name}（${Math.round((Date.now() - t0) / 1000)}s）`);
}

/* ── 真实入口 helpers（选择器与 tests/e2e/parity-sprint4-*.spec.ts 同源）── */

async function login(page) {
  await page.goto("/login");
  await page.getByRole("button", { name: /一键进入演示账号/ }).click();
  await page.waitForFunction(() => /\/projects$/.test(location.pathname), null, { timeout: 15_000 });
  await sleep(600);
}

/** 李四表单登录（真实入口）→ 顶栏切换器切到演示工作空间。 */
async function loginLisi(page) {
  await page.goto("/login");
  await page.locator("#email").fill(LISI.email);
  await page.locator("#pw").fill(LISI.password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.waitForFunction(() => /\/projects$/.test(location.pathname), null, { timeout: 15_000 });
  await sleep(600);
  await page.locator('[data-sb-scope="topbar-menu"]').first().click();
  await page.getByRole("option", { name: /张三 的工作空间/ }).click();
  await page.waitForFunction(() => /\/workspace\/projects$/.test(location.pathname), null, { timeout: 15_000 });
  await sleep(600);
}

async function enterProject(page, name = PROJ_NAME) {
  const card = page.locator("a,div").filter({ hasText: new RegExp(name) }).first();
  await card.locator(`text=${name}`).first().click();
  await page.waitForURL(/\/board/, { timeout: 20_000 });
  await sleep(900);
}

async function gotoGantt(page) {
  await page.getByRole("navigation").filter({ hasText: "返回项目列表" }).getByRole("link", { name: "甘特" }).click();
  await page.waitForURL(/\/gantt/, { timeout: 10_000 });
}

async function gotoFiles(page) {
  await page.getByRole("navigation").filter({ hasText: "返回项目列表" }).getByRole("link", { name: "文件" }).click();
  await page.waitForURL(/\/files$/, { timeout: 10_000 });
  await sleep(500);
}

async function waitRows(page, minCount = 1) {
  await page.locator('[data-sb-scope="gantt-body"]').waitFor({ timeout: 20_000 });
  await page.locator('[data-sb-scope="gantt-body"] [data-bar]').first().waitFor({ timeout: 20_000 });
  await page.waitForFunction(
    (n) => document.querySelectorAll('[data-sb-scope="gantt-body"] [data-bar]').length >= n,
    minCount, { timeout: 20_000 },
  );
}

const barLeft = (page, issueId) => page.locator(`[data-bar="${issueId}"]`)
  .evaluate((el) => Number.parseFloat((el).style.left));

/** 甘特行树虚拟滚动定位（行是 translateY 绝对定位——原生 scrollIntoView 无效，
 *  直接驱动滚动容器 scrollTop；左右两栏经 onScroll 同步）。 */
async function scrollGanttRow(page, issueId) {
  await page.evaluate((id) => {
    const row = document.querySelector(`[data-grow="${id}"]`);
    const body = document.querySelector('[data-sb-scope="gantt-body"]');
    if (!row || !body) return;
    const m = /translateY\(([\d.]+)px\)/.exec(row.style.transform ?? "");
    const t = m ? Number(m[1])
      : row.getBoundingClientRect().top - body.getBoundingClientRect().top + body.scrollTop;
    body.scrollTop = Math.max(0, t - 160);
  }, issueId);
  await sleep(450); // 虚拟行重建 + 双栏同步
}

/** 三手势拖拽（阈值 4px 激活——move 先小步跨阈值再到位；不含 mouse.up）。
 *  目标行先滚动入视口（演示位多在列表尾部，虚拟滚动 translateY 定位）。 */
async function dragBarBy(page, issueId, dxPx) {
  await scrollGanttRow(page, issueId);
  const bar = page.locator(`[data-bar="${issueId}"]`);
  const box = await bar.boundingBox();
  if (!box) throw new Error(`拖拽目标条 ${issueId} 不可见`);
  if (box.y < 60 || box.y > 880) throw new Error(`条 y=${Math.round(box.y)} 不在可拖视口内`);
  const x0 = box.x + Math.min(box.width / 2, 30);
  const y = box.y + box.height / 2;
  await page.mouse.move(x0, y);
  await page.mouse.down();
  await page.mouse.move(x0 + 10, y, { steps: 2 });
  await page.mouse.move(x0 + dxPx, y, { steps: 6 });
}

/** 甘特行树按标题找 issue id（左栏 [data-grow] 携带 id）。 */
async function rowIdByTitle(page, title) {
  const row = page.locator('[data-grow]').filter({ hasText: title }).first();
  await row.waitFor({ timeout: 15_000 });
  return row.getAttribute("data-grow");
}

/* ── 文件页 helpers ── */
const rowByName = (page, name) => page.locator('[data-sb-scope="files-row"]', { hasText: name });
const nameBtn = (page, name) => rowByName(page, name).locator('[data-sb-scope="files-name-preview"]');

async function treeClick(page, folderName) {
  await page.locator('[data-sb-scope="files-tree-item"]', { hasText: folderName }).first().click();
  await sleep(600);
}

async function openRowMenu(page, name) {
  await rowByName(page, name).locator('[data-file-menu]').click();
  await page.locator('[data-sb-scope="files-pop"]').waitFor({ state: "visible", timeout: 8_000 });
}

/** 页内 API 取证（与浏览器 context 共享 cookie；越权分支/会话快照用）。 */
async function apiFetch(page, method, path, data) {
  return page.evaluate(async ({ method, path: p, data: d }) => {
    const csrf = document.cookie.split("; ").find((c) => c.startsWith("csrftoken="))?.split("=")[1] ?? "";
    const r = await fetch(p, {
      method, credentials: "include",
      headers: { "Content-Type": "application/json", ...(csrf ? { "X-CSRFToken": csrf } : {}) },
      body: d === undefined ? undefined : JSON.stringify(d),
    });
    let body = null;
    try { body = await r.json(); } catch { /* 非 JSON */ }
    return { status: r.status, body };
  }, { method, path, data });
}

/* ═══════════════════════════ 幕定义 ═══════════════════════════ */

// 数据重置（幂等，走 API/SQL 不属于被验收场面；捕获 stdout 取仅预览分享 slug）
console.log("── seed：scripts/seed_acceptance_s4.py");
const seedOut = execSync("python3 scripts/seed_acceptance_s4.py",
  { encoding: "utf8", stdio: ["ignore", "pipe", "inherit"] });
const VIEW_SLUG = /仅预览链接 slug：([A-Za-z0-9_-]{22})/.exec(seedOut)?.[1] ?? "";
if (!VIEW_SLUG) throw new Error("seed 输出未含仅预览分享 slug（幕 10B 需要）");
console.log(`  仅预览分享 slug：${VIEW_SLUG}`);

const browser = await chromium.launch({ headless: true, slowMo: 130 });

/* 幕 1：万级滚动与粒度切换（GANTT-001 IT-01/IT-02 / 概览 §6-2）
 * 万级数据集由 seed --gantt10k 现场建（bench 同款 10,100 任务/5 年/1000 连线），
 * 录完即清——大负载不进常驻演示库。首屏 <1.5s 门禁为 bench 口径，本幕实测标 README。 */
await runScene(browser, "万级滚动与粒度切换", async (page) => {
  try {
    console.log("    [幕1] 建万级数据集（10,100 任务 / 5 年 / 1000 连线）…");
    execSync("python3 scripts/seed_acceptance_s4.py --gantt10k", { stdio: ["ignore", "pipe", "inherit"] });
    await login(page);
    await enterProject(page, G10K_NAME);

    // ── 首屏计时（rows + relations/bulk 合并——G1 门禁同口径；墙钟经
    //    request → response 事件对 + response.finished() 收体（responsefinished
    //    事件对 fetch 不触发——实测）。按 URL+method 关联——Playwright 每次
    //    事件返回新包装对象，Request 实例身份不可作键）──
    const ganttStats = [];
    const reqT0 = new Map();
    page.on("request", (r) => {
      if (/\/gantt\/(\?|relations\/bulk)/.test(r.url())) reqT0.set(`${r.method()} ${r.url()}`, Date.now());
    });
    page.on("response", (r) => {
      const key = `${r.request().method()} ${r.url()}`;
      const t0req = reqT0.get(key);
      if (t0req == null) return;
      reqT0.delete(key);
      void r.finished()
        .catch(() => {})
        .then(() => ganttStats.push({
          kind: r.url().includes("relations") ? "relations" : "rows",
          ms: Date.now() - t0req,
        }));
    });
    const t0 = Date.now();
    await gotoGantt(page);
    await waitRows(page, 10);
    const uiMs = Date.now() - t0;
    await sleep(1500); // 首屏第二段（视图重定向）与连线合并请求收尾
    const rowsMs = ganttStats.filter((s) => s.kind === "rows").reduce((a, b) => a + b.ms, 0);
    const relMs = ganttStats.filter((s) => s.kind === "relations").reduce((a, b) => a + b.ms, 0);
    G10K_METRICS.ui = uiMs;
    G10K_METRICS.rows = rowsMs;
    G10K_METRICS.rel = relMs;
    console.log(`    [幕1] 首屏实测：UI 到首条 ${uiMs}ms｜rows 合计 ${rowsMs}ms + relations 合计 ${relMs}ms = ${rowsMs + relMs}ms（G1 门禁口径 <1.5s，bench P95 99.5ms）`);

    // ── 虚拟滚动：DOM 行数有界（万级数据不进 DOM）──
    await sleep(600);
    const domRows = await page.locator('[data-grow]').count();
    if (domRows > 100) throw new Error(`虚拟滚动失效：DOM 行数 ${domRows} > 100`);
    console.log(`    [幕1] 虚拟滚动 DOM 行数 ${domRows}（万级数据有界渲染）`);
    await sleep(900);

    // ── 粒度切换 1/2/3（先于平移/滚轮——纵向行预取的迟到响应不污染零请求口径）：
    //    中心锚点不变 + 纯前端重排零请求（E2E-02）──
    const ratio = () => page.evaluate(() => {
      const body = document.querySelector('[data-sb-scope="gantt-body"]');
      const line = document.querySelector('[data-sb-scope="gantt-today-line"]');
      return (line.getBoundingClientRect().left - body.getBoundingClientRect().left) / body.clientWidth;
    });
    await page.locator('[data-sb-scope="gantt-today"]').click(); // ←今天→ 回中
    await sleep(700);
    let gets = 0;
    page.on("request", (r) => { if (/\/gantt\/\?/.test(r.url())) gets += 1; });
    for (let i = 0; i < 12; i += 1) { // 取数静默判定（重定向二段取数计入 base）
      const seen = gets;
      await sleep(800);
      if (gets === seen) break;
    }
    const base = gets;
    const r0 = await ratio();
    await page.locator('[data-sb-scope="gantt-body"]').focus();
    // 2 → 周（列头 起–止 区间）
    await page.keyboard.press("2");
    await expect(page.locator('[data-sb-scope="gantt-gran-week"]')).toHaveAttribute("aria-selected", "true", { timeout: 5_000 });
    await expect(page.locator(".rp-g-col").first()).toContainText(/–/, { timeout: 5_000 });
    const r1 = await ratio();
    if (Math.abs(r1 - r0) > 0.08) throw new Error(`周粒度锚点漂移 ${r0} → ${r1}`);
    await sleep(1100);
    // 3 → 月（列头 yyyy 年 m 月）
    await page.keyboard.press("3");
    await expect(page.locator(".rp-g-col").first()).toContainText(/年 \d+ 月/, { timeout: 5_000 });
    await sleep(1100);
    // 1 → 日（回段选器）
    await page.locator('[data-sb-scope="gantt-gran-day"]').click();
    await expect(page.locator('[data-sb-scope="gantt-gran-day"]')).toHaveAttribute("aria-selected", "true", { timeout: 5_000 });
    await sleep(900);
    if (gets !== base) throw new Error(`粒度切换三连多发 ${gets - base} 次 gantt/ 请求（应纯前端重排）`);
    console.log(`    [幕1] 粒度切换 日/周/月 三连：锚点保持（${r0.toFixed(3)}→${r1.toFixed(3)}）· 零新增取数请求`);

    // ── 平移预取：End 跳最右端 → 视窗取数（非全量）──
    const panStart = Date.now();
    const pan = page.waitForResponse((r) => /\/gantt\/\?/.test(r.url()) && r.request().method() === "GET", { timeout: 20_000 }).then(ok2xx);
    await page.keyboard.press("End");
    const panRes = await pan;
    console.log(`    [幕1] End 平移预取 ${Date.now() - panStart}ms 含断言开销（G2 门禁 <300ms）· 视窗裁剪非全量`);
    if (panRes.status() >= 300) throw new Error(`平移预取 ${panRes.status()}`);
    await waitRows(page, 1);
    await sleep(900);
    await page.keyboard.press("Home"); // 回左端
    await sleep(900);
    // 滚轮纵向滚动若干屏（流畅性演示）
    for (let i = 0; i < 3; i += 1) {
      await page.mouse.move(900, 600);
      await page.mouse.wheel(0, 900);
      await sleep(500);
    }
    const domRows2 = await page.locator('[data-grow]').count();
    if (domRows2 > 100) throw new Error(`纵向滚动后 DOM 行数 ${domRows2} > 100`);
    console.log(`    [幕1] 滚轮 ×3 屏后 DOM 行数 ${domRows2}（纵向同样有界）`);
    await sleep(1600); // 全景停留
  } finally {
    console.log("    [幕1] 清理万级数据集（录完即清，不进常驻演示库）…");
    execSync("python3 scripts/seed_acceptance_s4.py --gantt10k-clean", { stdio: ["ignore", "pipe", "inherit"] });
  }
});

/* 幕 2：拖拽改期持久化（GANTT-002 §7.2 / 概览 §6-2） */
await runScene(browser, "拖拽改期持久化", async (page) => {
  await login(page);
  await enterProject(page);
  await gotoGantt(page);
  await waitRows(page, 8);
  const id = await rowIdByTitle(page, "排期演示-平移目标");
  const before = await barLeft(page, id);

  const patch = page.waitForResponse((r) => new RegExp(`/issues/${id}/`).test(r.url()) && r.request().method() === "PATCH", { timeout: 15_000 });
  await dragBarBy(page, id, 3 * 36); // day 粒度 36px/天 → Δ+3d
  await expect(page.locator('[data-sb-scope="gantt-drag-badge"]')).toHaveText(/Δ \+3d/);
  await page.mouse.up();
  const resp = await patch;
  if (resp.status() !== 200) throw new Error(`PATCH ${resp.status()}`);
  const payload = resp.request().postDataJSON();
  const keys = Object.keys(payload).sort();
  if (keys.length !== 2 || keys[0] !== "start_date" || keys[1] !== "target_date") {
    throw new Error(`拖拽仅写 start/target（实际 ${keys.join(",")}）`);
  }
  if (payload.target_date < payload.start_date) throw new Error("违反 start≤target 约束");
  console.log(`    [幕2] PATCH 200 仅 {start_date, target_date}：${payload.start_date} → ${payload.target_date}`);
  await expect.poll(() => barLeft(page, id)).toBe(before + 3 * 36); // UI 回读 +3d
  await sleep(900);
  // 刷新保持（持久化）
  await page.reload();
  await waitRows(page, 8);
  await expect.poll(() => barLeft(page, id), { timeout: 15_000 }).toBe(before + 3 * 36);
  console.log("    [幕2] 刷新后条仍在 +3d 位（持久化 ✓）");
  await sleep(1600);
});

/* 幕 3：依赖连线与冲突确认（GANTT-002 §3.1 / TASK-005 / 概览 §6-2） */
await runScene(browser, "依赖连线与冲突确认", async (page) => {
  await login(page);
  await enterProject(page);
  await gotoGantt(page);
  await waitRows(page, 8);
  // 连线四型在场：blocks 实线 / relates 虚线 / duplicates 点划线 / 冲突红点
  await page.locator(".rp-glines path.rel-blocks").first().waitFor({ timeout: 15_000 });
  await page.locator(".rp-glines path.rel-relates_to").first().waitFor({ timeout: 10_000 });
  await page.locator(".rp-glines path.rel-duplicates").first().waitFor({ timeout: 10_000 });
  await page.locator("[data-cdot]").first().waitFor({ timeout: 10_000 });
  console.log("    [幕3] 连线四型在场（blocks/relates/duplicates/冲突红点）");
  await sleep(1200);

  const preKey = (await page.locator('[data-grow]').filter({ hasText: "排期演示-前置任务" }).first().innerText()).match(/S4AC-\d+/)?.[0] ?? "";
  const id = await rowIdByTitle(page, "排期演示-被阻塞任务");
  const before = await barLeft(page, id);

  // 拖到早于前置完成日 → 拖动徽标 ⚠ 依赖冲突 → 松手弹层
  let patched = 0;
  page.on("request", (r) => { if (new RegExp(`/issues/${id}/`).test(r.url()) && r.method() === "PATCH") patched += 1; });
  await dragBarBy(page, id, -5 * 36);
  await expect(page.locator('[data-sb-scope="gantt-drag-badge"]')).toContainText("⚠ 依赖冲突");
  await page.mouse.up();
  const dlg = page.locator('[data-sb-scope="gantt-conflict-dialog"]');
  await dlg.waitFor({ timeout: 8_000 });
  await expect(dlg).toHaveAttribute("role", "alertdialog");
  await expect(dlg).toContainText(`新排期使该任务早于其前置 ${preKey} 的完成日`);
  await sleep(1400); // 弹层文案可见
  // 取消 → 零写请求 + 弹回原位
  await page.locator('[data-sb-scope="gantt-conflict-cancel"]').click();
  await expect(dlg).toBeHidden();
  await expect.poll(() => barLeft(page, id)).toBe(before);
  if (patched) throw new Error(`取消路径不应发 PATCH（实际 ${patched}）`);
  console.log("    [幕3] 取消 → 零写请求 + 弹回原位");
  await sleep(900);
  // 再拖 → 「仍按此排期」→ PATCH 200 + 红点持续
  const patch = page.waitForResponse((r) => new RegExp(`/issues/${id}/`).test(r.url()) && r.request().method() === "PATCH", { timeout: 15_000 });
  await dragBarBy(page, id, -5 * 36);
  await page.mouse.up();
  await dlg.waitFor({ timeout: 8_000 });
  await sleep(700);
  await page.locator('[data-sb-scope="gantt-conflict-ok"]').click();
  const resp = await patch;
  if (resp.status() !== 200) throw new Error(`仍按此排期 PATCH ${resp.status()}`);
  await page.locator("[data-cdot]").first().waitFor({ timeout: 10_000 });
  await expect.poll(() => barLeft(page, id)).toBe(before - 5 * 36);
  console.log("    [幕3] 仍按此排期 → PATCH 200 + 红点持续提示（不自动顺延）");
  await sleep(1600);
});

/* 幕 4：延期概览（GANTT-002 §3.2 C.109 / 概览 §6-2） */
await runScene(browser, "延期概览", async (page) => {
  await login(page);
  await enterProject(page);
  const overdue = page.waitForResponse((r) => /\/gantt\/overdue-summary\//.test(r.url()) && r.request().method() === "GET", { timeout: 20_000 }).then(ok2xx);
  await gotoGantt(page);
  await overdue; // 概览三数字为服务端聚合（§2.3）
  await waitRows(page, 8);
  const bar = page.locator('[data-sb-scope="gantt-overdue-bar"]');
  await bar.waitFor({ timeout: 15_000 });
  await expect(bar).toHaveAttribute("role", "status");
  await expect(page.locator('[data-sb-scope="gantt-overdue-count"]')).toHaveText("5");
  await expect(bar).toContainText("最长逾期 5 天");
  for (const chip of ["张三(2)", "李四(2)", "王五(1)"]) {
    await expect(page.locator('[data-sb-scope="gantt-overdue-by"]')).toContainText(chip, { timeout: 8_000 });
  }
  console.log("    [幕4] 三数字：逾期 5 · 最长 5 天 · 按人 张三(2) 李四(2) 王五(1)");
  await sleep(1200);
  // 明细（按逾期天数降序）+ 行内跳转
  await page.locator('[data-sb-scope="gantt-overdue-toggle"]').click();
  const list = page.locator('[data-sb-scope="gantt-overdue-list"]');
  await list.waitFor({ timeout: 8_000 });
  await expect(list).toContainText("逾期 5 天"); // 首行 = 移动端适配回归（降序）
  await sleep(1000);
  await list.locator('[role="listitem"]').filter({ hasText: "移动端适配回归" }).locator("button", { hasText: "跳转" }).click();
  await page.locator('[data-grow].selected').filter({ hasText: "移动端适配回归" }).waitFor({ timeout: 10_000 });
  console.log("    [幕4] 明细跳转 → 左栏行选中定位 ✓");
  await sleep(1600);
});

/* 幕 5：未排期入轨与键盘改期（GANTT-002 C.108/C.111 / 概览 §6-6） */
await runScene(browser, "未排期入轨与键盘改期", async (page) => {
  await login(page);
  await enterProject(page);
  await gotoGantt(page);
  await waitRows(page, 8);

  // ① 未排期折叠区 → 拖入时间轴（3 天默认工期）
  await expect(page.locator('[data-sb-scope="gantt-unsched-head"]')).toContainText("未排期 (4)");
  await page.locator('[data-sb-scope="gantt-unsched-head"]').click();
  const itId = await page.locator('[data-unsched]').filter({ hasText: "待入轨任务" }).first().getAttribute("data-unsched");
  await page.locator(`[data-unsched="${itId}"]`).waitFor({ timeout: 8_000 });
  await sleep(600);
  const bodyBox = await page.locator('[data-sb-scope="gantt-body"]').boundingBox();
  const todayBox = await page.locator('[data-sb-scope="gantt-today-line"]').boundingBox();
  const dropX = bodyBox.x + (todayBox.x - bodyBox.x) + 5 * 36;
  const rowBox = await page.locator(`[data-unsched="${itId}"]`).boundingBox();
  await page.mouse.move(rowBox.x + 40, rowBox.y + rowBox.height / 2);
  await page.mouse.down();
  await page.mouse.move(dropX + 60, rowBox.y, { steps: 6 });
  await expect(page.locator('[data-sb-scope="gantt-drag-badge"]')).toHaveText(/排期至 .+（3 天）/);
  const patch = page.waitForResponse((r) => new RegExp(`/issues/${itId}/`).test(r.url()) && r.request().method() === "PATCH", { timeout: 15_000 });
  await page.mouse.move(dropX, rowBox.y, { steps: 2 });
  await page.mouse.up();
  const resp = await patch;
  if (resp.status() !== 200) throw new Error(`入轨 PATCH ${resp.status()}`);
  const pl = resp.request().postDataJSON();
  if (pl.target_date < pl.start_date) throw new Error("入轨默认工期违反约束");
  await expect(page.locator('[data-sb-scope="gantt-unsched-head"]')).toContainText("未排期 (3)");
  await page.locator(`[data-bar="${itId}"]`).waitFor({ timeout: 10_000 });
  console.log(`    [幕5] 入轨：${pl.start_date} → ${pl.target_date}（3 天默认工期），计数 4→3`);
  await sleep(1000);

  // ② 键盘改期：行选中 → Shift+→ ×3 → 300ms 合并为一次 PATCH + aria-live 播报
  const kbdId = await rowIdByTitle(page, "排期演示-键盘改期");
  await scrollGanttRow(page, kbdId);
  const before = await barLeft(page, kbdId);
  await page.locator(`[data-grow="${kbdId}"]`).click();
  await page.locator('[data-sb-scope="gantt-body"]').focus();
  const patches = [];
  page.on("response", (r) => { if (new RegExp(`/issues/${kbdId}/`).test(r.url()) && r.request().method() === "PATCH") patches.push(r); });
  await page.keyboard.press("Shift+ArrowRight");
  await page.keyboard.press("Shift+ArrowRight");
  await page.getByRole("status").filter({ hasText: /向后移动/ }).first().waitFor({ timeout: 8_000 });
  await page.keyboard.press("Shift+ArrowRight");
  await sleep(900); // 300ms 合并窗口 + 网络往返
  if (patches.length !== 1) throw new Error(`连续键击应合并一次 PATCH（实际 ${patches.length}）`);
  if (patches[0].status() !== 200) throw new Error(`键盘改期 PATCH ${patches[0].status()}`);
  const kp = patches[0].request().postDataJSON();
  if (kp.start_date !== dISO(6)) throw new Error(`键盘改期起点 ${kp.start_date} ≠ ${dISO(6)}`);
  await expect.poll(() => barLeft(page, kbdId)).toBe(before + 3 * 36);
  console.log(`    [幕5] 键盘改期：Shift+→×3 合并 1 次 PATCH（${kp.start_date}）+ 播报 ✓`);

  // ③ T 键回中今日线（§3.4 快捷键）
  const ratio = () => page.evaluate(() => {
    const body = document.querySelector('[data-sb-scope="gantt-body"]');
    const line = document.querySelector('[data-sb-scope="gantt-today-line"]');
    return (line.getBoundingClientRect().left - body.getBoundingClientRect().left) / body.clientWidth;
  });
  await page.keyboard.press("Home"); // 先离开
  await sleep(700);
  await page.keyboard.press("t");
  await sleep(700);
  const rt = await ratio();
  if (rt <= 0.3 || rt >= 0.7) throw new Error(`T 键未回中今日线（比例 ${rt.toFixed(3)}）`);
  console.log(`    [幕5] T 键回中今日线（视口比例 ${rt.toFixed(2)}）`);
  await sleep(1600);
});

/* 幕 6：PNG 导出（GANTT-002 §4.3.3 C.110 / 概览 §6-2） */
await runScene(browser, "PNG导出", async (page) => {
  await login(page);
  await enterProject(page);
  await gotoGantt(page);
  await waitRows(page, 8);
  await sleep(800);
  const dl1 = page.waitForEvent("download", { timeout: 20_000 });
  await page.locator('[data-sb-scope="gantt-menu-btn"]').click();
  await page.locator('[data-sb-scope="gantt-export"]').click();
  await expect(page.getByText("正在渲染…")).toBeVisible({ timeout: 5_000 }); // 导出中 Toast
  const dl = await dl1;
  const fname = dl.suggestedFilename();
  if (!/^.+-\d{8}-\d{4}\.png$/.test(fname)) throw new Error(`文件名规范不符：${fname}`);
  await dl.saveAs("/tmp/s4-gantt-export.png"); // 产物留存（水印三行人工核验）
  console.log(`    [幕6] ⋯ 菜单导出：${fname}（已存 /tmp/s4-gantt-export.png 供水印核验）`);
  await sleep(1000);
  // ⌘E 同路
  const dl2 = page.waitForEvent("download", { timeout: 20_000 });
  await page.locator('[data-sb-scope="gantt-body"]').focus();
  await page.keyboard.press("Meta+e");
  const d2 = await dl2;
  if (!/\.png$/.test(d2.suggestedFilename())) throw new Error("⌘E 导出失败");
  console.log(`    [幕6] ⌘E 同路导出：${d2.suggestedFilename()}`);
  await sleep(1500);
});

/* 幕 7：百兆分片续传（FILE-003 §7.2 C.119 / 概览 §6-3——真实 MinIO multipart）
 * 100MB → 8MB×13 片；悬停片 ≥3 确定断点 → 刷新中断 → 继续复用会话 → 已传片零重传。 */
await runScene(browser, "百兆分片续传", async (page) => {
  await login(page);
  await enterProject(page);
  await gotoFiles(page);
  await treeClick(page, "大文件");

  const BIG = 100 * 1024 * 1024;
  const tmpDir = mkdtempSync(join(tmpdir(), "s4-chunk-"));
  const bigPath = join(tmpDir, "整库备份-验收演示.zip");
  writeFileSync(bigPath, Buffer.alloc(BIG, 7));
  console.log(`    [幕7] 样本：${(BIG / 1024 / 1024).toFixed(0)}MB（13 片 × 8MB）已落盘`);

  // 悬停片 ≥3（断点确定化；在途 PUT 由刷新取消）
  let hold = true;
  const held = [];
  await page.route(/\/uploads\//, (route) => {
    const n = Number(new URL(route.request().url()).searchParams.get("partNumber") ?? 0);
    if (route.request().method() === "PUT" && n >= 3 && hold) { held.push(route); return; }
    void route.continue();
  });
  const patch1 = page.waitForResponse((r) => /\/chunks\/1\/$/.test(r.url()) && r.request().method() === "PATCH", { timeout: 90_000 });
  const patch2 = page.waitForResponse((r) => /\/chunks\/2\/$/.test(r.url()) && r.request().method() === "PATCH", { timeout: 90_000 });
  const init = page.waitForRequest((r) => /\/upload-sessions\/$/.test(r.url()) && r.method() === "POST", { timeout: 20_000 });
  await page.locator('[data-sb-scope="files-upload-input"]').setInputFiles(bigPath);
  await init; // >50MB 强制分片（BR-01）——走会话而非直传 presign
  const card = page.locator('[data-sb-scope="files-chunk-card"]').first();
  await card.waitFor({ timeout: 20_000 });
  await expect(card.locator('[role="progressbar"]')).toBeVisible();
  await expect(card).toContainText("并行 3", { timeout: 30_000 });
  console.log("    [幕7] 分片卡片：进度条 + 并行 3（片级 MD5 核对）");
  await patch1;
  await patch2;
  // 断点基线以服务端快照为准（响应头先行的落库竞态）
  const sid = await page.evaluate((fn) => {
    const k = Object.keys(localStorage).find((key) => key.startsWith("rp:chunk-session:") && key.includes(fn));
    return k ? (JSON.parse(localStorage.getItem(k) ?? "{}").sessionId ?? "") : "";
  }, "整库备份-验收演示.zip");
  if (!sid) throw new Error("localStorage 会话探测失败");
  let doneSnapshot = [];
  for (let i = 0; i < 20; i += 1) {
    const st = await apiFetch(page, "GET", `/api/v1/workspaces/workspace/projects/${page.url().match(/projects\/([^/]+)\//)?.[1]}/upload-sessions/${sid}/`);
    if (st.status === 200) {
      doneSnapshot = st.body?.data?.uploaded_chunks ?? [];
      if (doneSnapshot.includes(1) && doneSnapshot.includes(2)) break;
    }
    await sleep(500);
  }
  if (!(doneSnapshot.includes(1) && doneSnapshot.includes(2))) throw new Error(`断点基线未确定化：${doneSnapshot}`);
  console.log(`    [幕7] 断点基线（服务端 uploaded_chunks）：[${doneSnapshot.join(",")}]`);

  // ── 刷新中断（File 丢失；会话 + 片表留存）──
  await page.reload();
  hold = false;
  await Promise.allSettled(held.map((r) => r.abort()));
  await page.unroute(/\/uploads\//);
  await treeClick(page, "大文件");
  const banner = page.locator('[data-sb-scope="files-chunk-resume-banner"]');
  await banner.waitFor({ timeout: 15_000 });
  await expect(banner).toContainText("整库备份-验收演示.zip");
  await expect(banner).toContainText("断点续传"); // 卡片提示「已传片零重传」
  console.log("    [幕7] 刷新中断 → 检测到未完成的上传（继续/放弃）");

  // ── 继续：复用会话（零 init）+ 已传片零重传 ──
  let initCount = 0;
  page.on("request", (r) => { if (/\/upload-sessions\/$/.test(r.url()) && r.method() === "POST") initCount += 1; });
  const resumedPuts = [];
  await page.route(/\/uploads\//, (route) => {
    const n = Number(new URL(route.request().url()).searchParams.get("partNumber") ?? 0);
    if (route.request().method() === "PUT" && n > 0) resumedPuts.push(n);
    void route.continue();
  });
  const statusGet = page.waitForResponse((r) => new RegExp(`/upload-sessions/${sid}/$`).test(r.url()) && r.request().method() === "GET", { timeout: 20_000 }).then(ok2xx);
  await banner.locator('[data-sb-scope="files-chunk-resume-continue"]').click();
  await page.locator('[data-sb-scope="files-upload-input"]').setInputFiles(bigPath);
  await statusGet; // 断点片表 GET 200
  const complete = page.waitForResponse((r) => new RegExp(`/upload-sessions/${sid}/complete/$`).test(r.url()) && r.request().method() === "POST", { timeout: 240_000 }).then(ok2xx);
  await complete; // complete 201（ListParts 片级 ETag×MD5 全量核对）
  if (initCount !== 0) throw new Error(`续传不应重建会话（init ×${initCount}）`);
  for (const n of doneSnapshot) {
    if (resumedPuts.includes(n)) throw new Error(`片 ${n} 已传却重传（零重传失守）`);
  }
  if (resumedPuts.length >= 13) throw new Error(`续传片数 ${resumedPuts.length} 应 < 13`);
  await rowByName(page, "整库备份-验收演示.zip").waitFor({ timeout: 15_000 });
  console.log(`    [幕7] 续传：零 init · 重传片 [${resumedPuts.sort((a, b) => a - b).join(",")}]（基线片零重传）→ complete 201 → 入列`);
  await sleep(1600);
});

/* 幕 8：五通道预览（FILE-003 §2.3 C.120 / 概览 §6-3） */
await runScene(browser, "五通道预览", async (page) => {
  await login(page);
  await enterProject(page);
  await gotoFiles(page);
  await treeClick(page, "设计稿");
  await treeClick(page, "2026Q3");

  const openPreview = async (name) => {
    const p = page.waitForResponse((r) => /\/files\/[^/]+\/preview\/$/.test(r.url()) && r.request().method() === "GET", { timeout: 15_000 }).then(ok2xx);
    await nameBtn(page, name).click();
    await p;
    await page.locator('[data-sb-scope="preview-drawer"]').waitFor({ timeout: 10_000 });
    await sleep(700);
  };

  // ① 文本/Markdown（Monaco 只读正文）
  await openPreview("需求说明.md");
  await expect(page.locator('[data-sb-scope="preview-text-body"]')).toContainText("正文第一行", { timeout: 10_000 });
  await expect(page.locator('[data-sb-scope="preview-head-meta"]')).toContainText(/v1 · /);
  console.log("    [幕8] ① 文本通道：正文渲染 ✓");
  await page.keyboard.press("Escape");
  await sleep(400);

  // ② PDF（iframe 透传 302 换发——浏览器内建 pdfium 渲染）
  await openPreview("合同扫描.pdf");
  const frame = page.locator('[data-sb-scope="preview-pdf-frame"]');
  await frame.waitFor({ timeout: 10_000 });
  await expect(frame).toHaveAttribute("src", /\/files\/[^/]+\/(versions\/[^/]+\/content\/|derivatives\/preview\/)/);
  console.log("    [幕8] ② PDF 通道：iframe 透传 ✓");
  await page.keyboard.press("Escape");
  await sleep(400);

  // ③ 图片（缩略就绪 + 查看原图入口）
  await openPreview("演示截图.png");
  const img = page.locator('[data-sb-scope="preview-image-img"]');
  await img.waitFor({ timeout: 10_000 });
  await expect(img).toHaveAttribute("src", /\/derivatives\/thumbnail\//);
  await page.locator('[data-sb-scope="preview-original"]').waitFor({ timeout: 5_000 });
  console.log("    [幕8] ③ 图片通道：缩略 + 原图入口 ✓");
  await page.keyboard.press("Escape");
  await sleep(400);

  // ④ 视频（原生控件边下边播——webm 流式白名单）
  await openPreview("产品演示录屏.webm");
  const video = page.locator('[data-sb-scope="preview-video-el"]');
  await video.waitFor({ timeout: 10_000 });
  await video.evaluate((v) => v.play().catch(() => {})); // 实播 1.5s（画面可见运动）
  await sleep(1600);
  const paused = await video.evaluate((v) => v.paused);
  console.log(`    [幕8] ④ 视频通道：边下边播（playing=${!paused}）✓`);
  await page.keyboard.press("Escape");
  await sleep(400);

  // ⑤ Office 转码（本机无 soffice → 202 排队态如实展示——ADR-0022 D-1）
  await openPreview("转码排队.docx");
  const queued = page.locator('[data-sb-scope="preview-queued"]');
  await queued.waitFor({ timeout: 10_000 });
  await expect(queued).toContainText("正在转码预览… 预计约");
  await expect(queued).toContainText("LibreOffice");
  await page.locator('[data-sb-scope="preview-queued-dl"]').waitFor({ timeout: 5_000 }); // 先下载
  console.log("    [幕8] ⑤ Office 通道：202 排队态如实展示（LibreOffice 说明 + 先下载）");
  await page.keyboard.press("Escape");
  await sleep(1500);
});

/* 幕 9：多版本回滚（FILE-003 §3.4 C.121 / 概览 §6-3） */
await runScene(browser, "多版本回滚", async (page) => {
  await login(page);
  await enterProject(page);
  await gotoFiles(page);
  await treeClick(page, "版本演示");
  await nameBtn(page, "方案.md").click();
  const panel = page.locator('[data-sb-scope="preview-versions"]');
  await panel.waitFor({ timeout: 10_000 });
  await expect(panel).toContainText("版本（3）");
  await expect(panel).toContainText("上限 20 · 超出自动淘汰最旧非当前版本");
  await expect(page.locator('[data-sb-scope="preview-ver-row"][data-version="3"] [data-sb-scope="preview-ver-current"]')).toHaveText("● 当前");
  console.log("    [幕9] 版本面板：版本（3）/ v3 ●当前 / 上限 20 提示");
  await sleep(1000);

  // 回滚到 v1：确认「将创建新版本（内容同 v1）」→ POST 201 → v4 ●当前 + 正文还原
  await page.locator('[data-sb-scope="preview-ver-row"][data-version="1"] [data-sb-scope="preview-ver-rollback"]').click();
  const confirm = page.locator('[data-sb-scope="files-confirm"]');
  await confirm.waitFor({ timeout: 8_000 });
  await expect(confirm).toContainText("回滚到 v1？");
  await expect(confirm).toContainText("将创建新版本（内容同 v1）"); // 不删除任何版本（零拷贝）
  await sleep(900);
  const rollback = page.waitForResponse((r) => /\/rollback\/$/.test(r.url()) && r.request().method() === "POST", { timeout: 15_000 }).then(ok2xx);
  await confirm.locator('[data-sb-scope="files-confirm-ok"]').click();
  const rb = await rollback;
  const rbBody = await rb.json();
  if (rbBody?.data?.version_number !== 4) throw new Error(`回滚应产生 v4（实际 ${rbBody?.data?.version_number}）`);
  if (rbBody?.data?.source_version_number !== 1) throw new Error(`新版本应指向 v1（实际 ${rbBody?.data?.source_version_number}）`);
  await expect(panel).toContainText("版本（4）", { timeout: 10_000 });
  await expect(page.locator('[data-sb-scope="preview-ver-row"][data-version="4"] [data-sb-scope="preview-ver-current"]')).toBeVisible();
  await expect(page.locator('[data-sb-scope="preview-text-body"]')).toContainText("导航采用侧边栏方案", { timeout: 10_000 }); // 内容还原 = v1
  console.log("    [幕9] 回滚 v1 → v4 ●当前（指向 v1 零拷贝）+ 正文还原 ✓");

  // 刷新回读
  await page.keyboard.press("Escape");
  await page.reload();
  await treeClick(page, "版本演示");
  await nameBtn(page, "方案.md").click();
  await expect(page.locator('[data-sb-scope="preview-versions"]')).toContainText("版本（4）", { timeout: 10_000 });
  await expect(page.locator('[data-sb-scope="preview-head-meta"]')).toContainText("v4 ·");
  console.log("    [幕9] 刷新回读：v4 链保留 ✓");
  await sleep(1500);
});

/* 幕 10：分享全链路（FILE-004 C.123/C.125 / 概览 §6-3）——双视口成对
 * a 张三（web 3001）创建带密码 30 天下载分享；b 匿名收件人（space 3003，无登录）
 * 直达 /s/{slug}（分享链接即收件人唯一真实入口）：密码门 → 解锁 → 预览 → 下载；
 * 再开仅预览链接 → 无下载按钮。 */
await runScene(browser, "分享全链路", async ([a, b] = []) => {
  // —— a：创建分享（用户路径）——
  await login(a);
  await enterProject(a);
  await gotoFiles(a);
  await treeClick(a, "外发");
  await rowByName(a, "对外方案.md").waitFor({ timeout: 10_000 });
  await openRowMenu(a, "对外方案.md");
  await a.locator('[data-sb-scope="files-pop"] [data-menu-key="share"]').click();
  const dialog = a.locator('[data-sb-scope="share-create"]');
  await dialog.waitFor({ timeout: 8_000 });
  await expect(dialog.locator('[data-share-perm="download"]')).toBeChecked(); // 预览+下载 默认
  await expect(dialog.locator('[data-sb-scope="share-pwd-toggle"]')).toBeChecked(); // 密码默认开
  await dialog.locator('[data-sb-scope="share-pwd-input"]').fill("accept-2026");
  await expect(dialog.locator('[data-sb-scope="share-exp-btn"]')).toContainText("30 天"); // 默认 30 天
  await sleep(700);
  const create = a.waitForResponse((r) => /\/share-links\/$/.test(r.url()) && r.request().method() === "POST", { timeout: 15_000 }).then(ok2xx);
  await dialog.locator('[data-sb-scope="share-create-btn"]').click();
  const created = (await (await create).json()).data;
  if (!/^[A-Za-z0-9_-]{22}$/.test(created.slug)) throw new Error(`slug 不可枚举性破坏：${created.slug}`);
  await expect(a.locator('[data-sb-scope="share-link-input"]')).toHaveValue(/\/s\/[A-Za-z0-9_-]{22}$/);
  await expect(a.locator('[data-sb-scope="share-created-summary"]')).toContainText("预览 + 下载");
  await sleep(1000);
  await a.locator('[data-sb-scope="share-done"]').click();
  console.log(`    [幕10a] 创建分享：密码 + 30 天 + 预览+下载 → slug ${created.slug.slice(0, 8)}…（22 位）`);

  // —— b：匿名收件人（新 context 无 cookie）——
  await b.goto(`${SPACE}/spaces/s/${created.slug}`);
  const pwdCard = b.locator('[data-sb-scope="space-pwd"]');
  await pwdCard.waitFor({ timeout: 15_000 });
  await expect(pwdCard).toContainText("此文件已加密分享");
  await expect(pwdCard).not.toContainText("对外方案.md"); // 不泄露文件信息（BR-10）
  await sleep(800);
  await b.locator('[data-sb-scope="space-pwd-input"]').fill("wrong-password");
  await b.keyboard.press("Enter"); // 回车提交
  const errAlert = b.locator('[data-sb-scope="space-pwd-err"]');
  await errAlert.waitFor({ timeout: 10_000 });
  await expect(errAlert).toContainText(/密码错误.*剩余 \d+ 次尝试/);
  console.log("    [幕10b] 密码门：错密码 → 剩余次数提示（防爆破限流在途）");
  await sleep(900);
  await b.locator('[data-sb-scope="space-pwd-input"]').fill("accept-2026");
  const unlock = b.waitForResponse((r) => /\/public\/shares\/[^/]+\/unlock\//.test(r.url()) && r.request().method() === "POST", { timeout: 15_000 }).then(ok2xx);
  await b.locator('[data-sb-scope="space-pwd-go"]').click();
  await unlock;
  const fileCard = b.locator('[data-sb-scope="space-file"]');
  await fileCard.waitFor({ timeout: 15_000 });
  await expect(b.locator('[data-sb-scope="space-file-head"]')).toContainText("对外方案.md");
  await expect(b.locator('[data-sb-scope="space-file-head"]')).toContainText(/30 天后过期/);
  await expect(b.locator('[data-sb-scope="space-preview-text"]')).toContainText("分享链可达内容", { timeout: 15_000 });
  console.log("    [幕10b] 解锁 → 文件卡（30 天倒计时）+ 正文预览 ✓");
  await sleep(1000);
  const dlPromise = b.waitForEvent("download", { timeout: 20_000 });
  await b.locator('[data-sb-scope="space-download"]').click();
  const dl = await dlPromise;
  if (dl.suggestedFilename() !== "对外方案.md") throw new Error(`下载文件名 ${dl.suggestedFilename()}`);
  console.log("    [幕10b] 下载落盘：对外方案.md ✓");

  // —— b：仅预览链接（seed 预置：无密码永久 view）→ 无下载按钮 ——
  await b.goto(`${SPACE}/spaces/s/${VIEW_SLUG}`);
  await b.locator('[data-sb-scope="space-file"]').waitFor({ timeout: 15_000 });
  await expect(b.locator('[data-sb-scope="space-download"]')).toHaveCount(0);
  await expect(b.locator('[data-sb-scope="space-nodownload"]')).toContainText("当前链接仅限预览（无下载权限）");
  console.log("    [幕10b] 仅预览链接：无下载按钮 + 「仅限预览」提示 ✓");
  await sleep(1600);
}, { pages: 2 });

/* 幕 11：三态权限越权（FILE-002 C.113/C.116 / 概览 §6-4）——双视口成对
 * a 张三（ADMIN）UI 现场改「指定成员（仅张三）/仅管理员」；b 李四（CONTRIBUTOR）
 * 列表不可见 + 页内直连 download-url → 404 存在性隐藏。 */
await runScene(browser, "三态权限越权", async ([a, b] = []) => {
  // —— b 先入场：三文件全员态时全可见（改前对照）——
  await loginLisi(b);
  await enterProject(b);
  await gotoFiles(b);
  await treeClick(b, "权限样本");
  for (const n of ["全员可见.md", "指定成员-仅张三.md", "仅管理员-机密.md"]) {
    await rowByName(b, n).waitFor({ timeout: 10_000 });
  }
  console.log("    [幕11b] 改前对照：CONTRIBUTOR 三文件全可见（全员态）");
  await sleep(900);

  // —— a：UI 现场改三态（可见性弹层 = C.116 三态单选）——
  await login(a);
  await enterProject(a);
  await gotoFiles(a);
  await treeClick(a, "权限样本");
  await rowByName(a, "指定成员-仅张三.md").waitFor({ timeout: 10_000 });
  const membersId = await rowByName(a, "指定成员-仅张三.md").getAttribute("data-file-id");
  const adminsId = await rowByName(a, "仅管理员-机密.md").getAttribute("data-file-id");

  // ① 指定成员：勾选张三 → 保存
  await openRowMenu(a, "指定成员-仅张三.md");
  await a.locator('[data-sb-scope="files-pop"] [data-menu-key="vis"]').click();
  const vis1 = a.locator('[data-sb-scope="files-vis"]');
  await vis1.waitFor({ timeout: 8_000 });
  await expect(vis1).toContainText("三层一致校验"); // 三态三层说明
  await vis1.locator('[data-vis-radio="members"]').check();
  await vis1.locator('[data-sb-scope="files-vis-members"]').waitFor({ timeout: 5_000 });
  await vis1.locator("label").filter({ hasText: "张三" }).locator("input[data-vis-member]").check();
  const visSave1 = a.waitForResponse((r) => new RegExp(`/files/${membersId}/$`).test(r.url()) && r.request().method() === "PATCH", { timeout: 15_000 }).then(ok2xx);
  await vis1.locator('[data-sb-scope="files-vis-save"]').click();
  await visSave1;
  console.log("    [幕11a] 指定成员-仅张三.md → 👤 指定成员（勾张三）PATCH 2xx");

  // ② 仅管理员
  await openRowMenu(a, "仅管理员-机密.md");
  await a.locator('[data-sb-scope="files-pop"] [data-menu-key="vis"]').click();
  const vis2 = a.locator('[data-sb-scope="files-vis"]');
  await vis2.waitFor({ timeout: 8_000 });
  await vis2.locator('[data-vis-radio="admins"]').check();
  const visSave2 = a.waitForResponse((r) => new RegExp(`/files/${adminsId}/$`).test(r.url()) && r.request().method() === "PATCH", { timeout: 15_000 }).then(ok2xx);
  await vis2.locator('[data-sb-scope="files-vis-save"]').click();
  await visSave2;
  await expect(rowByName(a, "仅管理员-机密.md").locator('[aria-label="仅管理员可见"]')).toBeVisible({ timeout: 8_000 });
  console.log("    [幕11a] 仅管理员-机密.md → 🔒 仅管理员 PATCH 2xx + 角标");
  await sleep(1000);

  // —— b：刷新 → 后端剪枝（列表不可见）+ 直连 download-url 404 ——
  await b.reload();
  await treeClick(b, "权限样本");
  await rowByName(b, "全员可见.md").waitFor({ timeout: 10_000 });
  await expect(rowByName(b, "指定成员-仅张三.md")).toHaveCount(0);
  await expect(rowByName(b, "仅管理员-机密.md")).toHaveCount(0);
  console.log("    [幕11b] 改后：CONTRIBUTOR 列表仅剩全员文件（后端剪枝）");
  const projId = b.url().match(/projects\/([^/]+)\//)?.[1] ?? "";
  const d1 = await apiFetch(b, "GET", `/api/v1/workspaces/workspace/projects/${projId}/files/${membersId}/download-url/`);
  const d2 = await apiFetch(b, "GET", `/api/v1/workspaces/workspace/projects/${projId}/files/${adminsId}/download-url/`);
  if (d1.status !== 404 || d2.status !== 404) throw new Error(`直连 download-url 应 404（members=${d1.status} admins=${d2.status}）`);
  console.log("    [幕11b] 绕过前端直连 download-url：members 态 404 / admins 态 404（存在性隐藏）");
  await sleep(1600);
}, { pages: 2 });

/* 幕 12：回收站往返（FILE-002 C.117 / 概览 §6-3） */
await runScene(browser, "回收站往返", async (page) => {
  await login(page);
  await enterProject(page);
  await gotoFiles(page);
  await treeClick(page, "回收站样本");
  await rowByName(page, "待删除演示.md").waitFor({ timeout: 10_000 });

  // 删除 → 30 天提示 → DELETE 204 → 行消失 + 徽标计数
  await openRowMenu(page, "待删除演示.md");
  await page.locator('[data-sb-scope="files-pop"] [data-menu-key="del"]').click();
  const dialog = page.locator('[data-sb-scope="files-confirm"]');
  await dialog.waitFor({ timeout: 8_000 });
  await expect(dialog).toHaveAttribute("role", "alertdialog");
  await expect(dialog).toContainText("回收站，30 天后自动清理");
  await sleep(800);
  const del = page.waitForResponse((r) => /\/files\/[^/]+\/$/.test(r.url()) && r.request().method() === "DELETE", { timeout: 15_000 });
  await dialog.locator('[data-sb-scope="files-confirm-ok"]').click();
  const dres = await del;
  if (dres.status() !== 204) throw new Error(`软删 ${dres.status()}`);
  await expect(rowByName(page, "待删除演示.md")).toHaveCount(0);
  await expect(rowByName(page, "邻居.txt")).toBeVisible(); // 非级联邻居留存
  await expect(page.locator('[data-sb-scope="files-trash-count"]')).toHaveText("1");
  console.log("    [幕12] 删除 → 204 → 行消失（邻居留存）+ 回收站徽标 1");
  await sleep(900);

  // 回收站页 → 还原（确认含 (恢复) 冲突落根说明）→ POST 200 → 空站 + 原位回归
  await page.locator('[data-sb-scope="files-trash-entry"]').click();
  await page.waitForURL(/\/files\/trash$/, { timeout: 10_000 });
  await expect(page.locator('[data-sb-scope="trash-crumb"]')).toContainText("管理员视角：全量可见");
  const trow = page.locator('[data-sb-scope="trash-row"]', { hasText: "待删除演示.md" });
  await trow.waitFor({ timeout: 10_000 });
  await expect(trow).toContainText("回收站样本"); // 原位置
  await expect(trow).toContainText(/30 天|\d+ 天/); // 剩余天数
  await sleep(1000);
  await trow.locator('[data-sb-scope="trash-restore"]').click();
  const rdialog = page.locator('[data-sb-scope="files-confirm"]');
  await rdialog.waitFor({ timeout: 8_000 });
  await expect(rdialog).toContainText("还原到原位置「回收站样本」");
  await expect(rdialog).toContainText("(恢复)"); // 冲突落根说明（BR-07）
  await sleep(800);
  const restore = page.waitForResponse((r) => /\/files\/[^/]+\/restore\/$/.test(r.url()) && r.request().method() === "POST", { timeout: 15_000 }).then(ok2xx);
  await rdialog.locator('[data-sb-scope="files-confirm-ok"]').click();
  await restore;
  await expect(page.locator('[data-sb-scope="trash-row"]')).toHaveCount(0, { timeout: 10_000 });
  await page.locator('[data-sb-scope="trash-empty"]').waitFor({ timeout: 10_000 });
  console.log("    [幕12] 回收站 → 还原 200 → 站空");
  await page.locator('[data-sb-scope="trash-crumb"]').getByRole("button", { name: "项目文件" }).click();
  await page.waitForURL(/\/files$/, { timeout: 10_000 });
  await treeClick(page, "回收站样本");
  await rowByName(page, "待删除演示.md").waitFor({ timeout: 10_000 });
  console.log("    [幕12] 原位还原回读 ✓");
  await sleep(1600);
});

/* 幕 13：双端实时（GANTT-002 §4.4 改期广播 + FILE-003 §4.4 / COLLAB-004）——双视口成对
 * a 张三操作 / b 李四旁观：① 甘特拖改期 → b 端条 2 秒内同步；② 同名上传新版 →
 * b 版本面板无刷新自更新（file.version.created）。 */
await runScene(browser, "双端实时", async ([a, b] = []) => {
  // —— ① 甘特拖改期实时广播 ——
  await login(a);
  await enterProject(a);
  await gotoGantt(a);
  await waitRows(a, 8);
  await a.locator('[data-sb-scope="conn-dot-btn"][aria-label="实时已连接"]').waitFor({ timeout: 20_000 });
  await loginLisi(b);
  await enterProject(b);
  await gotoGantt(b);
  await waitRows(b, 8);
  await b.locator('[data-sb-scope="conn-dot-btn"][aria-label="实时已连接"]').waitFor({ timeout: 20_000 });
  await sleep(800);

  const id = await rowIdByTitle(a, "排期演示-平移目标");
  const bBefore = await barLeft(b, id);
  const patch = a.waitForResponse((r) => new RegExp(`/issues/${id}/`).test(r.url()) && r.request().method() === "PATCH", { timeout: 15_000 });
  await dragBarBy(a, id, 3 * 36);
  await a.mouse.up();
  const pres = await patch;
  if (pres.status() !== 200) throw new Error(`A 拖改期 PATCH ${pres.status()}`);
  const t0 = Date.now();
  await b.waitForFunction(
    (arg) => {
      const el = document.querySelector(`[data-bar="${arg.id}"]`);
      return el != null && Number.parseFloat(el.style.left) >= arg.left + 100; // +3d ≈ +108px
    },
    { id, left: bBefore },
    { timeout: 6_000, polling: 100 },
  );
  const elapsed = Date.now() - t0;
  console.log(`    [幕13①] A 拖改期 +3d → B 端条同步耗时 ${elapsed}ms（验收口径 <2s）`);
  if (elapsed >= 2_000) throw new Error(`B 端同步 ${elapsed}ms ≥ 2s`);
  await sleep(1400); // 远端条移动可见

  // —— ② 文件版本实时（file.version.created → 版本面板无刷新自更新）——
  await gotoFiles(b);
  await treeClick(b, "协作");
  const ticket = b.waitForRequest((r) => /\/realtime-token\/$/.test(r.url()) && r.method() === "POST", { timeout: 20_000 });
  await nameBtn(b, "联调笔记.md").click();
  await b.locator('[data-sb-scope="preview-drawer"]').waitFor({ timeout: 10_000 });
  const fileId = await b.locator('[data-sb-scope="files-row"]', { hasText: "联调笔记.md" }).first().getAttribute("data-file-id");
  const treq = await ticket;
  if (!((treq.postDataJSON?.() ?? JSON.parse(treq.postData() ?? "{}")).file_rooms ?? []).includes(fileId)) {
    throw new Error("票据 file_rooms 未携带资产（file:{asset_id} 第四类房间）");
  }
  await expect(b.locator('[data-sb-scope="preview-versions"]')).toHaveCount(0); // v1 单版本隐藏
  await expect(b.locator('[data-sb-scope="preview-text-body"]')).toContainText("第一版内容", { timeout: 15_000 });
  console.log("    [幕13②] B 打开预览（v1）· 票据 file_rooms 订阅 ✓");

  await gotoFiles(a);
  await treeClick(a, "协作");
  const upDone = a.waitForResponse((r) => /\/files\/[^/]+\/complete\/$/.test(r.url()) && r.request().method() === "POST", { timeout: 30_000 }).then(ok2xx);
  await a.locator('[data-sb-scope="files-upload-input"]').setInputFiles([{
    name: "联调笔记.md", mimeType: "text/markdown",
    buffer: Buffer.from("第二版内容（实时推送）", "utf8"),
  }]); // 同名上传 → 版本 +1
  await upDone;
  console.log("    [幕13②] A 同名上传 v2（complete 2xx）→ 等待 B 端 file.version.created …");
  await expect(b.locator('[data-sb-scope="preview-versions"]')).toContainText("版本（2）", { timeout: 20_000 });
  await expect(b.locator('[data-sb-scope="preview-text-body"]')).toContainText("第二版内容（实时推送）", { timeout: 15_000 });
  console.log("    [幕13②] B 版本面板无刷新自更新：版本（2）+ 正文切换 ✓");
  await sleep(1800);
}, { pages: 2 });

await browser.close();

/* ── 汇总 + README 索引 ──────────────────────────────────────── */
const pass = results.filter((r) => r.ok).length;
console.log("\n═══ Sprint-4 验收录屏完成 ═══");
console.log(`  ${pass}/${results.length} 幕成功；视频目录：${OUT}/`);
const COVERAGE = {
  万级滚动与粒度切换: "GANTT-001 IT-01/IT-02 / 概览 §6-2",
  拖拽改期持久化: "GANTT-002 §7.2 / 概览 §6-2",
  依赖连线与冲突确认: "GANTT-002 §3.1 / TASK-005 / 概览 §6-2",
  延期概览: "GANTT-002 §3.2（C.109）/ 概览 §6-2",
  未排期入轨与键盘改期: "GANTT-002 C.108/C.111 / 概览 §6-6",
  PNG导出: "GANTT-002 §4.3.3（C.110）/ 概览 §6-2",
  百兆分片续传: "FILE-003 §7.2（C.119）/ 概览 §6-3",
  五通道预览: "FILE-003 §2.3（C.120）/ 概览 §6-3",
  多版本回滚: "FILE-003 §3.4（C.121）/ 概览 §6-3",
  分享全链路: "FILE-004 §7.2（C.123/C.125）/ 概览 §6-3",
  三态权限越权: "FILE-002 §4（C.113/C.116）/ 概览 §6-4",
  回收站往返: "FILE-002 §3.4（C.117）/ 概览 §6-3",
  双端实时: "GANTT-002 §4.4 + FILE-003 §4.4 / COLLAB-004 / 概览 §6-2/§6-3",
};
const readme = [
  "# Sprint-4 验收录屏（13 幕，全部从用户实际入口出发）",
  "",
  "> 概览 §6 六条验收条款的幕映射：§6-2 甘特（幕 1~6）｜§6-3 文件（幕 7~10、12）｜§6-4 权限（幕 11）｜",
  "> §6-6 无障碍键盘（幕 5 含行选择/T/1/2/3/Shift 全套）｜§6-1（五规格文档结构）与 §6-5（孤儿清理/配额）",
  "> 为后台行为非用户路径，不设幕——分别由门禁（五规格评审 + sprint-4-flow 213 断言含 IT-09 三段）覆盖。",
  "",
  "| 幕 | 场景 | 验收条款 | 时长 | 结果 |",
  "| --- | --- | --- | --- | --- |",
  ...results.map((r) => `| ${r.id} | ${r.name}${["分享全链路", "三态权限越权", "双端实时"].includes(r.name) ? "（a/b 成对）" : ""} | ${COVERAGE[r.name] ?? "—"} | ${r.seconds}s | ${r.ok ? "✓" : "✗"} |`),
  "",
  "## 双视口成对视频（a/b 并排观看）",
  "",
  "- 幕 10：`10a` 张三视口（web 3001，创建分享弹层三件套）；`10b` 匿名收件人视口（space 3003——密码门/解锁/预览/下载/仅预览）。",
  "- 幕 11：`11a` 张三（ADMIN）UI 现场改三态；`11b` 李四（CONTRIBUTOR）改前对照 → 改后列表剪枝 + 直连 404。",
  "- 幕 13：`13a` 张三（拖改期 + 同名上传 v2）；`13b` 李四（甘特条 2s 内同步 + 版本面板无刷新自更新）。",
  "",
  "## 环境与复跑",
  "",
  "前置：API 8000（**全量 env 含 AWS_S3_***，CLAUDE.md 坑 #14）+ web 3001 + live 3000（`pnpm dev` 带起，坑 #17 密钥三件套）+ Celery worker（activity 队列单代，`celery inspect registered` 应含 `event_publisher`）+ MinIO 9000 + **space 3003**（`pnpm dev:space`；幕 10/13）+ PG（rp-pg）+ 演示账号 bootstrap（zhangsan@rabbit.dev 一键进入）。",
  "",
  "```bash",
  "python3 scripts/seed_acceptance_s4.py          # 数据准备（幂等；李四/王五账号自动创建，密码固定）",
  "node scripts/acceptance_video_s4.mjs           # 全量 13 幕（脚本会先自动重跑一次 seed）",
  "ONLY=分享全链路 node scripts/acceptance_video_s4.mjs   # 单幕重录（幕名子串匹配，逗号分隔多个）",
  "```",
  "",
  "产物：`videos/scene-XX[-a|b]-<名称>.webm`（1440×900，每幕独立 context；webm 不入 git）。",
  "",
  "### 万级数据集说明（幕 1）",
  "",
  "幕 1 的 10,100 任务 / 5 年跨度 / 1000 连线数据集由 `seed_acceptance_s4.py --gantt10k` 现场 SQL 构造（与 `tests/jmeter/sprint-4-bench-gantt.py` 同款——`generate_series` 直灌 + 确定性 UUID），**录完即清**（`--gantt10k-clean` 复查零残留），不进常驻演示库。首屏 <1.5s 为 bench G1 门禁口径（rows + relations/bulk 合并 P95，实测 P95 99.5ms）；本次录屏单次实测：**UI 到首条 ${G10K_METRICS.ui}ms，rows 合计 ${G10K_METRICS.rows}ms + relations 合计 ${G10K_METRICS.rel}ms = ${G10K_METRICS.rows + G10K_METRICS.rel}ms**（含 Playwright 事件开销的墙钟；门禁以 bench P95 为准，录屏演示流畅性）。",
  "",
  "### 已知事项",
  "",
  "1. **Office 预览排队态（幕 8 ⑤）**：本机无 `soffice`（LibreOffice），Office 转码按 ADR-0022 D-1 如实展示 202 排队态（「正在转码预览… 预计约 N 秒」+ LibreOffice 异步转 PDF 说明 + 先下载兜底）；compose 工具链分层已就位，装 soffice 后该通道转 ready 态。其余四通道（图片/PDF/文本/视频）为 ready 实操。",
  "2. **视频通道样本为 webm**（`产品演示录屏.webm`，取 sprint-3 验收录屏真实产物）：本机无 ffmpeg 无法生成 mp4 样本；webm 与 mp4 同属官方流式白名单（FILE-003 BR-12），通道行为一致。且视频扩展名仅分片会话白名单可达（直传 presign 白名单零回改——FILE-003 §1.4 #5 已知口径），故该样本经分片通道入库。",
  "3. **PNG 导出水印（幕 6）**：水印三行（项目名 / 时间戳 / 导出人）在导出渲染期间临时挂载、不驻留 UI——录屏画面不可见；导出产物已存 `/tmp/s4-gantt-export.png` 并经视觉核验（右下角三行：`S4 验收演示 / 2026-09-07 06:24 / 张三`，2× 分辨率 1800×1318）。",
  "4. 演示项目（S4 验收演示 / S4AC）录制后保留供回看；幕 1 万级数据集录完即清（见上）。",
  "",
  "## 与 sprint-3 验收目录的差异",
  "",
  "- 新增 `scripts/seed_acceptance_s4.py`（S4 数据准备：幂等 SQL 清场 + API 造数 + `--gantt10k` 万级数据集建/清）与 `scripts/acceptance_video_s4.mjs`（13 幕录制器）；沿用 s2/s3 的 seed + 录制器模式未改动它们。",
  "- 双视口成对视频从 s3 的 3 幕扩到 4 幕（新增匿名收件人视口——space 3003 域）；s3 的断线补偿幕（停 live 模拟）无对应 S4 条款，不在本批。",
  "- 视频 webm 同 s2/s3 不入 git（.gitignore 补 `docs/sprint-4-acceptance/videos/` 目录规则）；本 README 与 SCENARIOS.md 入库。",
  "",
  "观看建议：慢放 0.75× 可看清 Toast/徽标/连线细节；每幕开头的登录/导航即「用户实际入口」演示。",
  "",
  ...results.filter((r) => !r.ok).map((r) => `> ⚠ 幕${r.id}（${r.name}）录制中断于失败点，现场画面保留 1.2s${r.err ? `；首错：${r.err}` : ""}`),
].join("\n");
writeFileSync("docs/sprint-4-acceptance/README.md", readme + "\n");
console.log("  索引：docs/sprint-4-acceptance/README.md");
process.exit(pass === results.length ? 0 : 1);
