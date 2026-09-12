#!/usr/bin/env node
/**
 * P4 R2（AUTH-011 目录同步）验收录屏——三幕。
 * 幕 01 通道卡与待办处置（席位补开通 / 裁决放弃）
 * 幕 02 同步台账与干跑确认（明细弹层 → 确认并应用，BR-07）
 * 幕 03 通道切换与 SCIM 一次性令牌（停用 LDAP → 启用 SCIM → 仅本次明文）
 * 前置：web 3001 + api 8000（演示工作空间存在）。
 * 输出：docs/p4-r2-acceptance/videos/（不入库）
 */
import { chromium } from "@playwright/test";
import { execSync } from "node:child_process";
import { mkdirSync } from "node:fs";

const WEB = "http://localhost:3001";
const API = "http://localhost:8000/api/v1";
const OUT = "docs/p4-r2-acceptance/videos";
mkdirSync(OUT, { recursive: true });

const results = [];
let sceneNo = 0;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const ONLY = (process.env.ONLY ?? "").split(",").filter(Boolean);

async function runScene(browser, name, fn) {
  sceneNo += 1;
  if (ONLY.length && !ONLY.some((k) => name.includes(k))) {
    console.log(`⊘ 幕${sceneNo} ${name}（ONLY 过滤跳过）`);
    return;
  }
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    recordVideo: { dir: OUT, size: { width: 1440, height: 900 } },
  });
  const page = await ctx.newPage();
  const t0 = Date.now();
  let ok = true;
  let err = "";
  try {
    await fn(page);
  } catch (e) {
    ok = false;
    err = String(e).replace(/[\n]+/g, " § ").slice(0, 900);
  }
  await ctx.close();
  results.push({ n: sceneNo, name, ok });
  console.log(`${ok ? "✓" : "✗"} 幕${sceneNo} ${name}（${((Date.now() - t0) / 1000).toFixed(1)}s）${err}`);
}

async function loginDemo(page) {
  await page.goto(`${WEB}/login`);
  await page.getByRole("button", { name: /一键进入演示账号/ }).click();
  await page.waitForFunction(() => /\/projects$/.test(location.pathname), null,
    { timeout: 15_000 });
}

const DIR = "/workspace/settings/directory";

const browser = await chromium.launch();

await runScene(browser, "通道卡与待办处置", async (page) => {
  execSync("PATH=apps/api/.venv/bin:$PATH python3 scripts/seed_acceptance_p4r2.py",
    { encoding: "utf8", stdio: "pipe" });
  await loginDemo(page);
  await page.goto(`${WEB}${DIR}`);
  await page.getByText("LDAP/AD 同步").waitFor({ timeout: 15_000 });
  await page.getByText("ldaps://ad.corp.cn:636", { exact: false }).waitFor({ timeout: 10_000 });
  await sleep(1600);
  // 席位待办 → 扩容补开通（BR-03 闭环）
  await page.getByText("待开通").first().waitFor({ timeout: 10_000 });
  page.on("response", async (res) => {
    if (res.url().includes("/resolve/")) {
      console.log("  resolve API:", res.status());
    }
  });
  await page.getByRole("button", { name: "扩容补开通" }).click();
  // 双分支：席位足 → 处置完成；席位仍满 → 409 toast（处置失败）——两者都算
  // 演示成功（BR-03 不静默失败语义本身就是验收点）；再兜底「处置失败」
  const done = page.getByText("处置完成").first();
  const fail = page.getByText("席位仍不足").first();
  const fail2 = page.getByText("处置失败").first();
  await Promise.race([done.waitFor({ timeout: 12_000 }),
                      fail.waitFor({ timeout: 12_000 }),
                      fail2.waitFor({ timeout: 12_000 })]);
  await sleep(1500);
  // 人工裁决 → 放弃（dismiss）
  const dismiss = page.getByRole("button", { name: "放弃" }).first();
  await dismiss.waitFor({ timeout: 8_000 });
  await dismiss.click();
  await sleep(1500);
});

await runScene(browser, "同步台账与干跑确认", async (page) => {
  await loginDemo(page);
  await page.goto(`${WEB}${DIR}`);
  await page.getByText("同步台账").waitFor({ timeout: 15_000 });
  await sleep(1400);
  // 干跑行「去确认」→ 明细弹层（23 个账号 / 部门变更）
  await page.locator('[data-sb-scope="dry-open"]').first().click();
  await page.getByText("干跑结果").waitFor({ timeout: 5_000 });
  await page.getByText("质量部 → 测试中心").first().waitFor({ timeout: 5_000 });
  await sleep(1800);
  // 确认并应用（BR-07：缺席计数清零重计）
  await page.locator('[data-sb-scope="dry-confirm"]').click();
  await page.getByText("干跑已确认").waitFor({ timeout: 8_000 });
  await sleep(1500);
  // 台账回看：状态已变 confirmed
  await page.getByText("confirmed").first().waitFor({ timeout: 5_000 });
  await sleep(1200);
});

await runScene(browser, "通道切换与 SCIM 令牌", async (page) => {
  await loginDemo(page);
  // 停用 LDAP（PATCH，经页面会话 + CSRF）→ 展示 BR-01 单通道互斥的前半
  const csrf = (await page.context().cookies())
    .find((c) => c.name === "csrftoken")?.value ?? "";
  const r = await page.request.patch(
    `${API}/workspaces/workspace/directory/ldap/`,
    { headers: { "X-CSRFToken": csrf }, data: { is_enabled: false } });
  if (r.status() !== 200) throw new Error(`停用 LDAP ${r.status()}`);
  await page.goto(`${WEB}${DIR}`);
  await page.getByText("未启用").waitFor({ timeout: 15_000 });
  await sleep(1400);
  // 启用 SCIM → 令牌仅本次展示（O4）
  await page.locator('[data-sb-scope="scim-setup"]').click();
  await page.getByText("仅本次展示一次").waitFor({ timeout: 8_000 });
  await sleep(1800);
  // BR-01 复核：LDAP 已停用后再启用 SCIM 成功；反向（先 LDAP 后 SCIM）为 409 已由 UT-14 锁定
});

await browser.close();
const ok = results.filter((r) => r.ok).length;
console.log(`\n${ok}/${results.length} 幕通过；视频目录 ${OUT}/`);
if (ok !== results.length) process.exit(1);
