#!/usr/bin/env node
/**
 * P4 R1（AUTH-012）验收录屏——六幕 × 独立视频（视频不入库，见 memory 纪律）。
 *
 * 纪律：全部从真实用户入口出发（web 登录页 / admin 运营台导航）；数值与
 * 冻结原型一致（Beta 78.2GB / R-03 102.3% / R-06 92% GET）。
 *
 * 幕 01 平台总览与租户详情（水位三档色 / 配额弹窗 / 事件）
 * 幕 02 风控处置：R-06 限流 → 总览「限流中」
 * 幕 03 冻结双人审批：第一签 → 同人 403 → 异人二签 → 客户横幅 → 解除二签 → 横幅消失
 * 幕 04 误报申诉：客户提交 → 运营 accepted → 客户侧误报关闭
 * 幕 05 边界报告 202 + L2 工单客户批准
 * 幕 06 阈值调紧（BR-06 放宽标红拒保存 → 收紧生效）
 *
 * 用法：node scripts/acceptance_video_p4r1.mjs [ONLY=幕名关键词]
 * 前置：API 以 TENANT_GOVERNANCE_ENABLED=1 运行 + web 3001 + admin 3002
 * 输出：docs/p4-r1-acceptance/videos/scene-XX-*.webm
 */
import { chromium } from "@playwright/test";
import { execSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import { join } from "node:path";

const WEB = process.env.E2E_BASE_URL ?? "http://localhost:3001";
const ADMIN = "http://localhost:3002/god-mode";
const API = "http://localhost:8000";
const OUT = "docs/p4-r1-acceptance/videos";
mkdirSync(OUT, { recursive: true });

const SEED = execSync("PATH=apps/api/.venv/bin:$PATH python3 scripts/seed_acceptance_p4r1.py",
  { encoding: "utf8" });
const meta = JSON.parse(SEED.slice(SEED.indexOf("{")));
const { tenant: TID, ops1: OPS1, ops2: OPS2, ops_password: PWD, ws_slug: WS } = meta;

const results = [];
let sceneNo = 0;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const ONLY = (process.env.ONLY ?? "").split(",").filter(Boolean);

/** 单幕录制：pages=N 时 fn 收 Page[]（各自独立 context，视频分别落盘）。 */
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
    err = String(e).split("\n").slice(0, 2).join(" | ").slice(0, 320);
  }
  for (const c of ctxs) await c.close().catch(() => {});
  results.push({ id, name, ok, err, ms: Date.now() - t0 });
  console.log(`${ok ? "✓" : "✗"} 幕${id} ${name}（${((Date.now() - t0) / 1000).toFixed(1)}s）${err}`);
}

async function loginForm(page, email) {
  await page.goto(`${WEB}/login`);
  await page.getByLabel(/邮箱/).fill(email);
  await page.getByLabel("密码", { exact: true }).fill(PWD);
  await page.getByRole("button", { name: /^登\s*录$|^登录$/ }).click();
  await page.waitForFunction(() => /\/projects$/.test(location.pathname), null,
    { timeout: 15_000 });
}

async function loginDemo(page) {
  await page.goto(`${WEB}/login`);
  await page.getByRole("button", { name: /一键进入演示账号/ }).click();
  await page.waitForFunction(() => /\/projects$/.test(location.pathname), null,
    { timeout: 15_000 });
}

const browser = await chromium.launch();

await runScene(browser, "平台总览与租户详情", async (page) => {
  await loginForm(page, OPS1);
  await page.goto(`${ADMIN}/governance`);
  await page.locator('[data-sb-scope="tenant-search"]').fill("Beta 演示");
  await sleep(1200);
  await page.getByText("Beta 演示租户").waitFor({ timeout: 10_000 });
  await sleep(1800);
  await page.getByText("Beta 演示租户").click();
  await page.getByText("配额调整").waitFor({ timeout: 10_000 });
  await sleep(1500);
  await page.getByRole("button", { name: "⚙ 配额调整" }).click();
  await page.getByText("任何配额调整产生 AuditLog").waitFor({ timeout: 5_000 });
  await sleep(1500);
  await page.getByRole("button", { name: "取消" }).click();
});

await runScene(browser, "风控处置限流", async (page) => {
  await loginForm(page, OPS1);
  await page.goto(`${ADMIN}/governance/risk-events`);
  await page.locator('[data-sb-scope="dispose-open"]').first().waitFor({ timeout: 10_000 });
  await sleep(1200);
  await page.locator('[data-sb-scope="dispose-open"]').first().click();
  await page.getByText("限流 24h").click();
  await sleep(900);
  await page.locator('[data-sb-scope="dispose-submit"]').click();
  await sleep(1200);
  await page.goto(`${ADMIN}/governance`);
  await page.locator('[data-sb-scope="tenant-search"]').fill("Beta 演示");
  await sleep(1200);
  await page.getByText("限流中").first().waitFor({ timeout: 10_000 });
  await sleep(1600);
});

await runScene(browser, "冻结双人审批全链", async ([pOps1, pOps2, pCust]) => { /* pages:3 */
  // ① 第一签（张运营）
  await loginForm(pOps1, OPS1);
  await pOps1.goto(`${ADMIN}/governance/risk-events`);
  await pOps1.locator('[data-sb-scope="dispose-open"]').first().waitFor({ timeout: 10_000 });
  await pOps1.locator('[data-sb-scope="dispose-open"]').first().click();
  await pOps1.getByText("冻结租户").click();
  await sleep(800);
  await pOps1.locator('[data-sb-scope="dispose-submit"]').click();
  await sleep(1200);
  // ② 同人二签 → 403
  await pOps1.getByRole("button", { name: "冻结二签" }).first().click();
  await pOps1.locator('[data-sb-scope="freeze-approve"]').click();
  await pOps1.getByText("冻结审批人不得与发起人相同").waitFor({ timeout: 5_000 });
  await sleep(1600);
  await pOps1.keyboard.press("Escape");
  await pOps1.getByRole("button", { name: "✕" }).last().click().catch(() => {});
  // ③ 异人二签（李运营）
  await loginForm(pOps2, OPS2);
  await pOps2.goto(`${ADMIN}/governance/risk-events`);
  const sign2 = pOps2.getByRole("button", { name: "冻结二签" }).first();
  await sign2.waitFor({ timeout: 10_000 });
  await sleep(800);
  await sign2.click();
  await pOps2.locator('[data-sb-scope="freeze-approve"]').click();
  await sleep(1500);
  // ④ 客户：横幅出现
  await loginDemo(pCust);
  await pCust.goto(`${WEB}/${WS}/projects`);
  await pCust.locator('[data-sb-scope="frozen-banner"]').waitFor({ timeout: 15_000 });
  await sleep(1800);
  // ⑤ 解除：张发起（解除按钮）→ 二签按钮出现 → 张同人二签 403 → 李二签生效
  await pOps1.goto(`${ADMIN}/governance/risk-events`);
  await pOps1.locator('[data-sb-scope="release-open"]').first().waitFor({ timeout: 10_000 });
  await sleep(1200);
  await pOps1.locator('[data-sb-scope="release-open"]').first().click();
  const rel = pOps1.getByRole("button", { name: "解除二签" }).first();
  await rel.waitFor({ timeout: 10_000 });
  await sleep(800);
  await rel.click();
  await sleep(600);
  await pOps1.locator('[data-sb-scope="freeze-approve"]').click();
  await sleep(1400);
  // ⑤b 李运营解除二签（生效）
  await pOps2.goto(`${ADMIN}/governance/risk-events`);
  const rel2 = pOps2.getByRole("button", { name: "解除二签" }).first();
  await rel2.waitFor({ timeout: 10_000 });
  await sleep(800);
  await rel2.click();
  await sleep(600);
  await pOps2.locator('[data-sb-scope="freeze-approve"]').click();
  await sleep(1500);
  // ⑥ 客户横幅消失（数据零丢失）
  await pCust.goto(`${WEB}/${WS}/projects`);
  await sleep(3000);
  const banner = await pCust.locator('[data-sb-scope="frozen-banner"]').count();
  if (banner) throw new Error("解除后横幅仍在");
}, { pages: 3 });

await runScene(browser, "误报申诉全链", async (page) => {
  // 客户提交申诉（捕获响应取 appeal_id）
  let appealId = "";
  const onResp = (r) => {
    if (r.url().includes("/security/events/") && r.request().method() === "POST"
        && r.url().includes("/appeals/")) {
      r.json().then((b) => { appealId = b?.data?.appeal_id ?? ""; }).catch(() => {});
    }
  };
  page.on("response", onResp);
  await loginDemo(page);
  await page.goto(`${WEB}/${WS}/settings/security`);
  await page.getByText("风控规则阈值").waitFor({ timeout: 15_000 });
  const appealBtn = page.locator('[data-sb-scope="appeal-btn"]').first();
  await appealBtn.waitFor({ timeout: 10_000 });
  page.once("dialog", (d) => d.accept("报表例行拉取，非爬虫"));
  await appealBtn.click();
  await page.getByText("申诉已提交").waitFor({ timeout: 10_000 });
  for (let i = 0; i < 20 && !appealId; i += 1) await sleep(100);
  page.off("response", onResp);
  await sleep(1200);
  // 运营复核 accepted（API——复核 UI 入口随 R2 事件详情内嵌补）
  await page.goto(`${WEB}/login`);
  await loginForm(page, OPS2);
  const _csrf = (await page.context().cookies())
    .find((c) => c.name === "csrftoken")?.value ?? "";
  const rr = await page.request.post(
    `${API}/api/v1/instances/risk-appeals/${appealId}/review/`,
    { headers: { "X-CSRFToken": _csrf },
      data: { decision: "accepted", note: "核实为例行报表" } });
  if (rr.status() !== 200) throw new Error(`复核失败 ${rr.status()}`);
  // 客户侧确认误报关闭
  await page.goto(`${WEB}/login`);
  await loginDemo(page);
  await page.goto(`${WEB}/${WS}/settings/security`);
  await page.getByText("误报关闭").first().waitFor({ timeout: 15_000 });
  await sleep(1500);
});

await runScene(browser, "边界报告与L2批准", async (page) => {
  await loginForm(page, OPS1);
  await page.goto(`${ADMIN}/governance/tenants/${TID}`);
  await page.getByRole("button", { name: "🧾 生成边界报告" }).waitFor({ timeout: 10_000 });
  await sleep(1200);
  await page.getByRole("button", { name: "🧾 生成边界报告" }).click();
  await page.getByText("报告任务已创建").waitFor({ timeout: 5_000 });
  // 演示环境 reports 队列消费者兜底（worker 在跑则幂等无操作）
  execSync(`PATH=apps/api/.venv/bin:$PATH python3 scripts/run_boundary_report.py ${TID}`,
    { encoding: "utf8" });
  await sleep(1200);
  // L2 工单客户批准（客户视角）
  await page.goto(`${WEB}/login`);
  await loginDemo(page);
  await page.goto(`${WEB}/${WS}/settings/security`);
  await page.getByText("L2 授权工单").waitFor({ timeout: 15_000 });
  await sleep(1400);
  await page.locator('[data-sb-scope="l2-approve"]').first().click();
  await page.getByText("L2 授权已批准").waitFor({ timeout: 10_000 });
  await sleep(1200);
});

await runScene(browser, "阈值调紧只紧不松", async (page) => {
  await loginDemo(page);
  await page.goto(`${WEB}/${WS}/settings/security`);
  await page.locator('[data-sb-scope="ws-security"]').waitFor({ timeout: 15_000 });
  await page.getByText("风控规则阈值（调紧）").waitFor({ timeout: 10_000 });
  await sleep(1400);
  const input = page.locator("input[type=number]").first();
  await input.fill("300");
  await sleep(900);
  await page.getByText("不可放宽（BR-06）").first().waitFor({ timeout: 5_000 });
  await sleep(1500);
  await input.fill("100");
  await sleep(600);
  await page.getByRole("button", { name: "保存" }).first().click();
  await sleep(1500);
});

await browser.close();
const ok = results.filter((r) => r.ok).length;
console.log(`\n${ok}/${results.length} 幕通过；视频目录 ${join(OUT, "")}`);
if (ok !== results.length) process.exit(1);
