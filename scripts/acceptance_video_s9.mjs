#!/usr/bin/env node
/**
 * Sprint-9 验收录屏（六幕：项目集/迭代燃尽/速率与累积流/健康度负载/Wiki/关键路径）。
 *
 * 用法：node scripts/acceptance_video_s9.mjs          # 全部
 *       ONLY=项目集 node scripts/acceptance_video_s9.mjs
 * 前置：seed（uv run --project apps/api python scripts/seed_acceptance_s9.py）+
 *       API 8000 + web 3001。输出 docs/sprint-9-enterprise-portfolio/videos/（永不在库）。
 */
import { chromium } from "@playwright/test";
import { mkdirSync, renameSync } from "node:fs";
import { join } from "node:path";

const OUT = "docs/sprint-9-enterprise-portfolio/videos";
const WEB = "http://localhost:3001";
mkdirSync(OUT, { recursive: true });

const results = [];
let sceneNo = 0;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const ONLY = (process.env.ONLY ?? "").split(",").filter(Boolean);

async function runScene(browser, name, fn, { pages = 1, allow = [] } = {}) {
  sceneNo += 1;
  if (ONLY.length && !ONLY.some((k) => name.includes(k))) {
    console.log(`⊘ 幕${String(sceneNo).padStart(2, "0")} ${name}（ONLY 过滤跳过）`);
    return;
  }
  const id = String(sceneNo).padStart(2, "0");
  const bag = { console: [], net: [] };
  const hook = (p) => {
    p.on("console", (m) => {
      if (m.type() !== "error") return;
      const t = String(m.text() ?? "").trim();
      if (/^Failed to load resource/i.test(t)) return;
      if (/\[vite\]|React DevTools|react-devtools/i.test(t)) return;
      const u = m.location?.()?.url ?? "";
      if (u.includes("/live/") || u.includes("realtime-token")) return;
      bag.console.push(t);
    });
    p.on("pageerror", (e) => bag.console.push(String(e).trim()));
    p.on("response", (r) => {
      const s = r.status();
      if (s < 400) return;
      const u = r.url();
      if (u.includes("realtime-token") || u.includes("/live/")) return;
      bag.net.push(`${s} ${r.request().method()} ${u}`);
    });
  };
  const ctxs = [], ps = [];
  for (let i = 0; i < pages; i += 1) {
    const ctx = await browser.newContext({
      baseURL: WEB, viewport: { width: 1440, height: 900 },
      recordVideo: { dir: OUT, size: { width: 1440, height: 900 } },
    });
    ctx.on("page", hook);
    ctxs.push(ctx); ps.push(await ctx.newPage());
  }
  const t0 = Date.now();
  let ok = true, err = "";
  try { await fn(pages === 1 ? ps[0] : ps); }
  catch (e) {
    ok = false; err = String(e).split("\n").slice(0, 5).join(" | ");
    console.error(`  ✗ 幕${id} ${name} 失败：\n${String(e).split("\n").slice(0, 8).join("\n")}`);
    await ps[0].screenshot({ path: `/tmp/s9-fail-${id}.png` }).catch(() => {});
    await sleep(1200);
  }
  await sleep(900);
  const unexpected = bag.net.filter((line) =>
    !allow.some((a) => line.startsWith(`${a.status} `) && line.includes(a.url)));
  if (ok && (unexpected.length || bag.console.length)) {
    ok = false;
    err = [`非预期网络失败: ${unexpected.join(" ; ") || "无"}`, `console 错误: ${bag.console.join(" ; ") || "无"}`].join(" | ");
    console.error(`  ✗ 幕${id} ${name} 守卫拦截：\n${err}`);
  }
  const videos = ps.map((p) => p.video());
  await Promise.all(ctxs.map((c) => c.close()));
  const suffix = pages === 1 ? [""] : ["a", "b"];
  for (let i = 0; i < videos.length; i += 1) {
    renameSync(await videos[i].path(), join(OUT, `scene-${id}${suffix[i]}-${name}.webm`));
  }
  results.push({ id, name, ok, seconds: Math.round((Date.now() - t0) / 1000), err });
  console.log(`${ok ? "✓" : "✗"} 幕${id} ${name}（${Math.round((Date.now() - t0) / 1000)}s）`);
}

async function login(page) {
  await page.goto("/login");
  await sleep(500);
  await page.getByRole("button", { name: /一键进入演示账号/ }).click();
  await sleep(1800);
}


/** projects 列表取数（偶发空 body——SyntaxError 时重试一次）。 */
async function fetchProj(page, ident) {
  for (let i = 0; i < 3; i += 1) {
    try {
      const r = await page.request.fetch(`${WEB}/api/v1/workspaces/workspace/projects/?status=all`);
      const b = await r.json();
      const proj = (b?.data ?? []).find((x) => x.identifier === ident);
      if (proj) return proj;
    } catch { /* 重试 */ }
    await sleep(600);
  }
  throw new Error(`project ${ident} not found`);
}

/* ═══ 幕 1 · 项目集 ═══ */
async function scenePortfolio(page) {
  await login(page);
  await page.goto("/workspace/portfolios");
  await sleep(1400);
  await page.locator('[data-sb-scope="pf-tree"]').getByText("S9 电商平台 2.0").click();
  await sleep(1200);
  // 三卡 + 权重注脚滚动展示
  await page.locator('[data-sb-scope="pf-card-risks"]').scrollIntoViewIfNeeded();
  await sleep(800);
  await page.locator('[data-sb-scope="pf-projects"]').scrollIntoViewIfNeeded();
  await sleep(700);
  // 里程碑页
  await page.getByRole("link", { name: "里程碑" }).first().click();
  await sleep(1300);
  await page.getByText("全量联调").scrollIntoViewIfNeeded();
  await sleep(800);
  // 依赖图
  await page.getByRole("link", { name: "依赖图" }).first().click();
  await sleep(1500);
}

/* ═══ 幕 2 · 迭代与燃尽 ═══ */
async function sceneCycles(page) {
  await login(page);
  const proj = await fetchProj(page, "S9A1");
  await page.goto(`/workspace/projects/${proj.id}/cycles`);
  await sleep(1400);
  await page.locator('[data-sb-scope="burndown-card"]').scrollIntoViewIfNeeded();
  await sleep(1200);
  // 悬停燃尽数据点（tooltip）
  await page.locator('[data-sb-scope="burndown-card"] svg circle').nth(2).hover();
  await sleep(900);
  // 结束迭代弹窗（不提交）
  await page.locator('[data-sb-scope="cycle-row"]', { hasText: "Sprint 24" }).getByText("结束迭代").click();
  await sleep(1100);
  await page.keyboard.press("Escape");
  await sleep(500);
}

/* ═══ 幕 3 · 速率 + 累积流 ═══ */
async function sceneReports(page) {
  await login(page);
  const proj = await fetchProj(page, "S9A1");
  await page.goto(`/workspace/projects/${proj.id}/reports/velocity`);
  await sleep(1600);
  await page.locator('[data-sb-scope="velocity-conclusion"]').scrollIntoViewIfNeeded();
  await sleep(900);
  await page.goto(`/workspace/projects/${proj.id}/reports/cumulative-flow`);
  await sleep(1500);
}

/* ═══ 幕 4 · 健康度 + 负载 ═══ */
async function sceneHealth(page) {
  await login(page);
  const proj = await fetchProj(page, "S9A1");
  await page.goto(`/workspace/projects/${proj.id}/reports/health`);
  await sleep(1600);
  // 下钻抽屉（实时口径）
  await page.locator('[data-sb-scope="health-dim-overdue"] button').first().click();
  await sleep(1100);
  await page.keyboard.press("Escape");
  await page.goto(`/workspace/projects/${proj.id}/reports/workload`);
  await sleep(1400);
}

/* ═══ 幕 5 · Wiki ═══ */
async function sceneWiki(page) {
  await login(page);
  const proj = await fetchProj(page, "S9A1");
  await page.goto(`/workspace/projects/${proj.id}/wiki`);
  await sleep(1400);
  await page.locator('[data-sb-scope="wiki-tree"]').getByText("API 设计规范").click();
  await sleep(1300);
  await page.locator('[data-sb-scope="wiki-doc-body"]').scrollIntoViewIfNeeded();
  await sleep(700);
  // 版本历史
  await page.locator('[data-sb-scope="wiki-doc-foot"]').getByText(/历史 v/).click();
  await sleep(1200);
  await page.getByRole("button", { name: "对比当前" }).first().click();
  await sleep(1100);
  // 全局检索
  await page.goto("/workspace/wiki-search");
  await sleep(800);
  await page.locator('[data-sb-scope="wiki-search-bar"] input').fill("错误码");
  await page.locator('[data-sb-scope="wiki-search-bar"] button').click();
  await sleep(1300);
}

/* ═══ 幕 6 · 关键路径 ═══ */
async function sceneCriticalPath(page) {
  await login(page);
  const proj = await fetchProj(page, "S9G2");
  await page.goto(`/workspace/projects/${proj.id}/gantt`);
  await sleep(2200);
  await page.locator('[data-sb-scope="cp-toggle"]').check();
  await sleep(1400);
  await page.locator('[data-sb-scope="cp-analyze-link"]').click();
  await sleep(1500);
  await page.locator('[data-sb-scope="cpm-config"]').scrollIntoViewIfNeeded();
  await sleep(900);
}

const browser = await chromium.launch();
const scenes = [
  ["项目集", scenePortfolio, {}],
  ["迭代燃尽", sceneCycles, { allow: [{ status: 404, url: "/burndown/" }] }],
  ["速率与累积流", sceneReports, {}],
  ["健康度负载", sceneHealth, {}],
  ["Wiki", sceneWiki, {}],
  ["关键路径", sceneCriticalPath, { allow: [{ status: 429, url: "/gantt/critical-path/" }] }],
];
for (const [name, fn, opts] of scenes) await runScene(browser, name, fn, opts);
await browser.close();

const failed = results.filter((r) => !r.ok);
console.log(`\n═══ Sprint-9 验收视频：${results.length - failed.length}/${results.length} ═══`);
for (const r of results) console.log(`  ${r.ok ? "✓" : "✗"} 幕${r.id} ${r.name}（${r.seconds}s）${r.err ? " — " + r.err : ""}`);
process.exit(failed.length ? 1 : 0);
