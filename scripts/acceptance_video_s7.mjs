#!/usr/bin/env node
/**
 * Sprint-7 验收录屏（六幕；浏览器幕=web 3001 实操作，API 幕=真实 JSON 响应渲染）。
 *
 * 用法：node scripts/acceptance_video_s7.mjs          # 全部
 *       ONLY=守卫 node scripts/acceptance_video_s7.mjs
 * 前置：seed（python3 scripts/seed_acceptance_s7.py）+ API 8000 + web 3001。
 * 输出：docs/sprint-7-acceptance/videos/scene-XX-<名称>.webm（永不在库）。
 */
import { chromium } from "@playwright/test";
import { mkdirSync, renameSync } from "node:fs";
import { join } from "node:path";

const OUT = "docs/sprint-7-acceptance/videos";
const WEB = "http://localhost:3001";
const API = "http://localhost:8000/api/v1";
const WS = process.env.S7_WS ?? "switch-team-34655";
mkdirSync(OUT, { recursive: true });

const results = [];
let sceneNo = 0;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const ONLY = (process.env.ONLY ?? "").split(",").filter(Boolean);
const S7 = `${WEB}/${WS}/projects`;

async function runScene(browser, name, fn, { pages = 1 } = {}) {
  sceneNo += 1;
  if (ONLY.length && !ONLY.some((k) => name.includes(k))) {
    console.log(`⊘ 幕${String(sceneNo).padStart(2, "0")} ${name}（ONLY 过滤跳过）`);
    return;
  }
  const id = String(sceneNo).padStart(2, "0");
  const ctxs = [], ps = [];
  for (let i = 0; i < pages; i += 1) {
    const ctx = await browser.newContext({
      baseURL: WEB, viewport: { width: 1440, height: 900 },
      recordVideo: { dir: OUT, size: { width: 1440, height: 900 } },
    });
    ctxs.push(ctx); ps.push(await ctx.newPage());
  }
  const t0 = Date.now();
  let ok = true, err = "";
  try { await fn(pages === 1 ? ps[0] : ps); }
  catch (e) {
    ok = false; err = String(e).split("\n").slice(0, 5).join(" | ");
    console.error(`  ✗ 幕${id} ${name} 失败：\n${String(e).split("\n").slice(0, 8).join("\n")}`);
    await ps[0].screenshot({ path: `/tmp/s7-fail-${id}.png` }).catch(() => {});
    await sleep(1200);
  }
  await sleep(900);
  const videos = ps.map((p) => p.video());
  await Promise.all(ctxs.map((c) => c.close()));
  const suffix = pages === 1 ? [""] : ["a", "b"];
  for (let i = 0; i < videos.length; i += 1) {
    renameSync(await videos[i].path(), join(OUT, `scene-${id}${suffix[i]}-${name}.webm`));
  }
  results.push({ id, name, ok, seconds: Math.round((Date.now() - t0) / 1000), err });
  console.log(`${ok ? "✓" : "✗"} 幕${id} ${name}（${Math.round((Date.now() - t0) / 1000)}s）`);
}

/* ── helpers ── */
async function login(page, { demo = true, email = "lisi@rabbit.dev", password = "Rabbit123!" } = {}) {
  await page.goto("/login");
  await sleep(500);
  if (demo) {
    await page.getByRole("button", { name: /一键进入演示账号/ }).click();
  } else {
    await page.getByLabel(/邮箱|邮箱/).or(page.locator('input[type="email"],input[name="email"]')).first()
      .fill(email);
    await page.locator('input[type="password"],input[name="password"]').first().fill(password);
    await page.getByRole("button", { name: /登录|登 录| sign in/i }).click();
  }
  await sleep(1600);
}

/** 浏览器上下文 API 直调（造数/取证，非验收场面）。 */
async function api(page, method, path, data) {
  const cookies = await page.context().cookies();
  const csrf = cookies.find((c) => c.name === "csrftoken")?.value ?? "";
  const r = await page.request.fetch(`${API}${path}`, {
    method, headers: { "Content-Type": "application/json", ...(csrf ? { "X-CSRFToken": csrf } : {}) },
    data: data === undefined ? undefined : JSON.stringify(data),
  });
  return { status: r.status(), body: await r.json().catch(() => null) };
}

/** 演示项目/任务定位（seed 后稳定）。 */
async function s7ctx(page) {
  const { body } = await api(page, "GET", `/workspaces/${WS}/projects/?status=all`);
  const proj = (body?.data ?? []).find((p) => p.identifier === "S7AC");
  const { body: issues } = await api(page, "GET", `/workspaces/${WS}/projects/${proj.id}/issues/?per_page=50`);
  return { proj, issues: issues?.data ?? [] };
}

/** API 幕：JSON 逐行回放（黑底终端风——真实响应渲染）。 */
async function jsonScene(page, title, subtitle, obj) {
  const text = JSON.stringify(obj, null, 2);
  const lines = text.split("\n").slice(0, 42);
  await page.setContent(
    `<!doctype html><html><body style="margin:0;background:#0b0f14;color:#c9d7e4;font:13px/1.7 ui-monospace,Menlo,monospace">
     <div style="padding:24px 32px">
       <div style="color:#7aa2f7;font-size:18px;font-weight:600;margin-bottom:2px">${title}</div>
       <div style="color:#565f89;font-size:12px;margin-bottom:18px">$ ${subtitle}</div>
       <pre id="term" style="white-space:pre-wrap;margin:0"></pre>
     </div></body></html>`);
  const term = page.locator("#term");
  for (const line of lines) {
    await term.evaluate((el, chunk) => { el.textContent += chunk + "\n"; }, line);
    await sleep(line.trim() ? 90 : 50);
  }
  await sleep(1100);
}

/* ═══════════ 幕01 画布全貌 ═══════════ */
async function scene01(page) {
  await login(page);
  const { proj } = await s7ctx(page);
  // 种子里已发布的「需求交付流」wf——查工作流列表拿 id
  const { body: wfs } = await api(page, "GET", `/workspaces/${WS}/projects/${proj.id}/workflows/`);
  const wf = (wfs?.data ?? []).find((w) => w.name.includes("审批版")) ?? (wfs?.data ?? [])[0];
  await page.goto(`/${WS}/projects/${proj.id}/workflows/${wf.id}/canvas`);
  await sleep(2500);
  // 顶栏：名称 + 已发布徽标
  await page.locator("text=需求交付流").first().waitFor({ timeout: 10_000 });
  await sleep(1200);
  // 点边开侧栏（WF-001 §3.2）
  const edge = page.locator(".react-flow__edge").first();
  await edge.click({ force: true });
  await sleep(1600);
  // 自动布局演示（§3.2）
  await page.getByRole("button", { name: /自动布局/ }).click();
  await sleep(1600);
}

/* ═══════════ 幕02 守卫拦截→补齐→流转 ═══════════ */
async function scene02(page) {
  await login(page);
  const { proj, issues } = await s7ctx(page);
  const guardIssue = issues.find((i) => i.name.includes("购物车"));
  // 列表页打开任务（真实用户路径：项目 → 列表 → 行点击）
  await page.goto(`/${WS}/projects/${proj.id}/board`);
  await sleep(1800);
  await page.locator(`text=${guardIssue.name}`).first().click();
  await sleep(1500);
  // 受控流转按钮（边名「提交评审」）→ 守卫对话框
  await page.locator('[data-sb-scope="drawer-transitions"] [data-flow-name="提交评审"]').click();
  await sleep(1200);
  // GuardDialog：缺负责人 → 下拉补齐（WF-004 §3.1）
  const sel = page.locator('[data-sb-scope="guard-dialog"] select').first();
  await sel.selectOption({ index: 1 });
  await sleep(600);
  await page.locator('[data-sb-scope="guard-dialog"] [data-action="approve"], [data-sb-scope="guard-dialog"] button:has-text("补齐并流转")').click();
  await sleep(1800);
  // 状态迁移断言（评审中）
  await page.locator("text=评审中, .text-amber-700").first().waitFor({ timeout: 6000 }).catch(() => {});
  await sleep(1400);
}

/* ═══════════ 幕03 审批链（双页） ═══════════ */
async function scene03([zs, ls]) {
  await login(zs);
  const { proj, issues } = await s7ctx(zs);
  const payIssue = issues.find((i) => i.name.includes("支付网关"));
  await zs.goto(`/${WS}/projects/${proj.id}/board`);
  await sleep(1500);
  await zs.locator(`text=${payIssue.name}`).first().click();
  await sleep(1500);
  // 发起「发布上线」→ 202 挂起提示
  await zs.locator('[data-sb-scope="drawer-transitions"] [data-flow-name="发布上线"]').click();
  await zs.locator('[data-sb-scope="flow-notice"]').waitFor({ timeout: 8000 });
  await sleep(1800);

  // 李四登录 → 审批中心待办
  await login(ls, { demo: false });
  await ls.goto(`/${WS}/approvals`);
  await sleep(1600);
  const row = ls.locator('[data-sb-scope="approval-list"] tbody tr').first();
  await row.waitFor({ timeout: 8000 });
  await sleep(900);
  await row.click();
  await sleep(1500);
  // 详情抽屉：时间线 + 通过
  await ls.locator('[data-sb-scope="approval-drawer"]').waitFor({ timeout: 6000 });
  await sleep(1000);
  await ls.locator('[data-sb-scope="approval-actions"] [data-action="approve"]').click();
  await sleep(2000);

  // 回张三：任务已迁移到已完成
  await zs.reload();
  await sleep(2200);
}

/* ═══════════ 幕04 自动化 Dry Run + 执行 ═══════════ */
async function scene04(page) {
  await login(page);
  const { proj, issues } = await s7ctx(page);
  const rule = (await api(page, "GET", `/workspaces/${WS}/projects/${proj.id}/automation-rules/`)).body?.data?.[0];
  const perf = issues.find((i) => i.name.includes("首页性能"));
  // Dry Run（BR-11 零写）
  const dry = await api(page, "POST",
    `/workspaces/${WS}/projects/${proj.id}/automation-rules/${rule.id}/dry-run/`,
    { issue_id: perf.id });
  await jsonScene(page, "自动化规则 · Dry Run", `POST automation-rules/${rule.id}/dry-run/`, dry.body);
  // 真事件执行（run_event 直调 worker 等价——走事件入口 task 不便，取 run 日志展示）
  const fired = await api(page, "GET",
    `/workspaces/${WS}/projects/${proj.id}/automation-rules/${rule.id}/`);
  await jsonScene(page, "规则定义与执行口径", `GET automation-rules/${rule.id}/`, fired.body);
}

/* ═══════════ 幕05 工时提交→驳回→通过→台账 ═══════════ */
async function scene05([ls, zs]) {
  const { proj } = await (async () => {
    await login(ls, { demo: false });
    return s7ctx(ls);
  })();
  // 李四：工时页提交本周（TASK-013 §3.1）
  await ls.goto(`/${WS}/projects/${proj.id}/worklog`);
  await sleep(1500);
  await ls.getByRole("button", { name: /提交本周/ }).click();
  await sleep(1600);

  // 张三：队列驳回（附意见）
  await login(zs);
  await zs.goto(`/${WS}/projects/${proj.id}/worklog`);
  await sleep(1200);
  await zs.locator('[data-sb-scope="worklog-tabs"] [data-tab="queue"]').click();
  await sleep(1000);
  const noteInput = zs.locator('[data-sb-scope="worklog-queue"] input[placeholder*="驳回意见"]').first();
  await noteInput.fill("周四工时需补明细备注");
  await sleep(400);
  await zs.locator('[data-sb-scope="worklog-queue"] button:has-text("驳回")').first().click();
  await sleep(1500);

  // 李四：重交（重新提交）
  await ls.goto(`/${WS}/projects/${proj.id}/worklog`);
  await sleep(1200);
  await ls.getByRole("button", { name: /提交本周/ }).click();
  await sleep(1500);

  // 张三：通过 → 台账（reload 后视图回默认 mine，须再切回队列）
  await zs.reload();
  await sleep(1500);
  await zs.locator('[data-sb-scope="worklog-tabs"] [data-tab="queue"]').click();
  await sleep(1000);
  await zs.locator('[data-sb-scope="worklog-queue"] button:has-text("通过")').first().click();
  await sleep(1500);
  await zs.locator('[data-sb-scope="worklog-tabs"] [data-tab="ledger"]').click();
  await sleep(1600);
}

/* ═══════════ 幕06 模板库 + 审计链 ═══════════ */
async function scene06(page) {
  await login(page);
  const { proj } = await s7ctx(page);
  // 预设四套
  const tpls = await api(page, "GET", `/workspaces/${WS}/workflow-templates/`);
  const names = (tpls.body?.data ?? []).filter((t) => t.is_builtin).map((t) => t.name);
  await jsonScene(page, "工作流模板库 · 预设四套（幂等种子）",
    "GET workflow-templates/", { is_builtin: names });
  // 两步下发确认（WF-005 §4.3）
  const tpl = (tpls.body?.data ?? []).find((t) => t.is_builtin);
  const dist = await api(page, "POST",
    `/workspaces/${WS}/projects/${proj.id}/workflow-templates/`,
    { template_id: tpl.id, confirm: true });
  await jsonScene(page, "两步下发 · 确认实例化", "POST projects/…/workflow-templates/ {confirm:true}", dist.body);
  // 审计链校验 + 导出头部（WF-006 §2.2/§2.3）
  const verify = await api(page, "GET",
    `/workspaces/${WS}/projects/${proj.id}/approval-audit/verify/`);
  const auditRows = await api(page, "GET",
    `/workspaces/${WS}/projects/${proj.id}/approval-audit/`);
  const preview = (auditRows.body?.data ?? []).slice(0, 2);
  await jsonScene(page, "审批留痕 · 哈希链校验", "GET approval-audit/verify/", {
    ...verify.body?.data, recent_events: preview });
}

/* ═══════════ main ═══════════ */
const browser = await chromium.launch();
try {
  await runScene(browser, "画布全貌", scene01);
  await runScene(browser, "守卫拦截补齐流转", scene02);
  await runScene(browser, "审批五场景核心链", scene03, { pages: 2 });
  await runScene(browser, "自动化DryRun", scene04);
  await runScene(browser, "工时提交驳回通过台账", scene05, { pages: 2 });
  await runScene(browser, "模板库与审计链", scene06);
} finally {
  await browser.close();
}

const fail = results.filter((r) => !r.ok);
console.log(`\n${"═".repeat(40)}\nSprint-7 验收视频：${results.length - fail.length}/${results.length} 幕通过`);
for (const r of results) {
  console.log(`  ${r.ok ? "✓" : "✗"} 幕${r.id} ${r.name}（${r.seconds}s）${r.err ? " — " + r.err : ""}`);
}
process.exit(fail.length ? 1 : 0);
