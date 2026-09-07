#!/usr/bin/env node
/**
 * Sprint-5 第 14 幕·真 GitHub 联调实录（独立录制器，双视口 a/b）。
 *
 * 与 13 幕 mock 录制器的区别：GitHub 侧是 **github.com 真站**（App 4859835
 * `rabbit-projects` 安装于 RabbitAI-Lab 组织，installation 159744359），事件经
 * App 级 webhook → 公网穿透（passnat 域名）→ 本机 API；出站走 installation
 * token 直写真实仓库 rabbitai-lab/rabbittest（公开仓库，匿名可览——b 视口
 * 无需登录即为 GitHub 侧取证面）。本录制器 **不跑 seed、不依赖 8090/8091 mock**。
 *
 * 场面（SCENARIOS 契约之幕 14，收尾后补录）：
 *   a：登录 → S5 项目 → 集成页（真实绑定行）→ 任务列表 → GitHub 建任务/
 *       详情抽屉 GitHub 区块（真实 PR/Commit）/ 评论输入
 *   b：github.com/RabbitAI-Lab/RabbitTest —— issues（[S5AC-*] 前缀标题）、
 *       issue #2 双向评论、PR #4 已合并；a 视口评论后 reload 见同步落地。
 *
 * 用法：node scripts/acceptance_video_s14_real_github.mjs
 * 前置：API 8000（含 GITHUB_APP_* env）+ web 3001 + worker（新代码）+
 *       公网穿透指向 8000 + GitHub App webhook 已配置（详见 docs README 幕 14 节）。
 * 输出：docs/sprint-5-acceptance/videos/scene-14[a|b]-真GitHub联调.webm
 */
import { chromium } from "@playwright/test";
import { execSync } from "node:child_process";
import { mkdirSync, mkdtempSync, renameSync, appendFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const WEB = process.env.E2E_BASE_URL ?? "http://localhost:3001";
const OUT = "docs/sprint-5-acceptance/videos";
const PROJ = "S5 验收演示";
const GH = "https://github.com/RabbitAI-Lab/RabbitTest";
/** GH 侧动作助手（installation token 建 issue；脚本不入库——含本机私钥路径）。 */
const GH_HELPER = process.env.S14_GH_HELPER ?? "/tmp/s14_gh_issue.py";
mkdirSync(OUT, { recursive: true });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** 开任务抽屉（列表行周期刷新会抖动——force 点击 + 三次开兜）。 */
async function openDrawer(page, rowText) {
  // 先确定性关掉可能残留的抽屉（Escape 对失焦抽屉不生效——用 ✕ 按钮）
  await page.locator('button[aria-label="关闭"]').first().click().catch(() => {});
  await sleep(700);
  for (let i = 0; i < 3; i += 1) {
    await page.locator('[data-sb-scope="tree-row"]', { hasText: rowText })
      .first().click({ force: true, timeout: 6000 }).catch(() => {});
    await sleep(1100);
    if (await page.locator('[data-sb-scope="drawer-tab"]').first().isVisible().catch(() => false)) return;
    await page.locator('button[aria-label="关闭"]').first().click().catch(() => {});
    await sleep(700);
  }
  throw new Error(`抽屉未能打开：${rowText}`);
}

/** GitHub 页加载（重试 + 兜底 api.github.com JSON 视图，公开仓库匿名可读）。 */
async function ghGoto(page, url, jsonFallback) {
  for (let i = 0; i < 2; i += 1) {
    try {
      await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30_000 });
      await sleep(1800);
      return;
    } catch { /* 重试一次 */ }
  }
  if (jsonFallback) await page.goto(jsonFallback, { timeout: 30_000 });
}

process.on("unhandledRejection", (e) => {
  console.error(`  （吞漂浮 rejection：${String(e).split("\n")[0].slice(0, 120)}）`);
});

const t0 = Date.now();
let ok = true;
const ctxs = [], ps = [];
const browser = await chromium.launch();
try {
  for (let i = 0; i < 2; i += 1) {
    const ctx = await browser.newContext({
      baseURL: WEB, viewport: { width: 1440, height: 900 },
      recordVideo: { dir: OUT, size: { width: 1440, height: 900 } },
    });
    ctxs.push(ctx); ps.push(await ctx.newPage());
  }
  const [a, b] = ps;

  /* ── a 视口：登录 → 集成页（真实绑定）── */
  await a.goto("/login");
  await a.getByRole("button", { name: /一键进入演示账号/ }).click();
  await a.waitForFunction(() => /\/projects$/.test(location.pathname), null, { timeout: 15_000 });
  await sleep(600);
  await a.locator("a,div").filter({ hasText: new RegExp(PROJ) }).first()
    .locator(`text=${PROJ}`).first().click();
  await a.waitForURL(/\/board/, { timeout: 20_000 });
  await sleep(500);
  await a.getByRole("link", { name: "集成" }).click();
  await sleep(1200);
  await a.locator('[data-sb-scope="binding-row"]', { hasText: "rabbitai-lab/rabbittest" })
    .first().waitFor({ timeout: 10_000 });
  await sleep(1600); // 绑定行停留（真仓库全名的可视证据）

  /* ── b 视口：GitHub 公开仓库总览 ── */
  await ghGoto(b, `${GH}/pulls`, `${GH}/pulls`);
  await b.locator("text=#4").first().waitFor({ timeout: 20_000 }).catch(() => {});
  await sleep(1400);

  /* ── 实时入站：先就位任务列表，GitHub 建 issue（助手）→ 刷新见新任务 ── */
  await a.getByRole("link", { name: "任务列表" }).click();
  await sleep(1500);
  const inboundTitle = `真联调实录·入站建任务 ${new Date().toISOString().slice(11, 19)}`;
  const ghNo = execSync(
    `PATH=apps/api/.venv/bin:$PATH python3 ${GH_HELPER} "${inboundTitle}"`,
    { encoding: "utf8" }).trim();
  console.log(`  GH issue #${ghNo} 已建，等待任务落库…`);
  let seen = false;
  for (let i = 0; i < 15 && !seen; i += 1) {
    await sleep(2000);
    await a.reload();
    await sleep(1200);
    seen = await a.locator(`text=${inboundTitle}`).first().isVisible().catch(() => false);
  }
  if (!seen) throw new Error("入站任务未在 30s 内出现于任务列表");
  await sleep(1500);

  /* ── a 视口：S5AC-14 抽屉 GitHub 区块（真实 PR + Commit）── */
  await a.keyboard.press("Escape");
  await sleep(700);
  await openDrawer(a, "PR 合并与 Commit 挂载验证");
  await a.locator('[data-sb-scope="drawer-github-section"]').waitFor({ timeout: 8000 });
  await a.locator('[data-sb-scope="drawer-github-pr"]', { hasText: "#4" }).first()
    .waitFor({ timeout: 6000 });
  await a.locator('[data-sb-scope="drawer-github-commit"]').first().waitFor({ timeout: 6000 });
  await sleep(1800); // 区块停留（PR 链接 + commit sha 可视）

  /* ── a 视口：S5AC-13 评论输入（出站实弹）── */
  await openDrawer(a, "任务创建验证");
  await a.locator('[data-sb-scope="drawer-tab"][data-tab-key="comments"]').click();
  await sleep(900);
  const cmt = `实录出站评论——本条经 worker 推送 GitHub issue #2 ${new Date().toISOString().slice(11, 19)}`;
  await a.locator('[data-sb-scope="drawer-comment-input"]').fill(cmt);
  const respP = a.waitForResponse((r) => /\/comments\/$/.test(r.url()) && r.request().method() === "POST", { timeout: 15_000 });
  await a.keyboard.press("Meta+Enter");
  const resp = await respP;
  if (resp.status() >= 300) throw new Error(`评论 POST → ${resp.status()}`);
  console.log("  站内评论已提交，等待出站推送…");
  await sleep(6000); // worker 出站（installation token 写 GH）+ 抽屉停留

  /* ── b 视口：issue #2 双向评论取证（reload 见 a 视口评论落地）── */
  await ghGoto(b, `${GH}/issues/2`, "https://api.github.com/repos/rabbitai-lab/rabbittest/issues/2/comments");
  await b.locator("text=实录出站评论").first().waitFor({ timeout: 25_000 });
  await sleep(1600);
  await ghGoto(b, `${GH}/issues`, `${GH}/issues`);
  await b.locator("text=[S5AC-").first().waitFor({ timeout: 20_000 }).catch(() => {});
  await sleep(1800);
} catch (e) {
  ok = false;
  console.error(`✗ 幕14 失败：\n${String(e).split("\n").slice(0, 8).join("\n")}`);
  await ps[0].screenshot({ path: "/tmp/s14-fail-a.png" }).catch(() => {});
  await ps[1].screenshot({ path: "/tmp/s14-fail-b.png" }).catch(() => {});
  appendFileSync("/tmp/s14-fail.log", `${new Date().toISOString()} ${String(e)}\n`);
  await sleep(1200);
}
await sleep(900);
const videos = ps.map((p) => p.video());
await browser.close();
for (let i = 0; i < videos.length; i += 1) {
  renameSync(await videos[i].path(),
    join(OUT, `scene-14${i === 0 ? "a" : "b"}-真GitHub联调.webm`));
}
console.log(`${ok ? "✓" : "✗"} 幕14 真GitHub联调（${Math.round((Date.now() - t0) / 1000)}s）`);
process.exit(ok ? 0 : 1);
