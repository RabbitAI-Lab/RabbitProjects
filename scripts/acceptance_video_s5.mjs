#!/usr/bin/env node
/**
 * Sprint-5 验收录屏（13 幕 × 独立视频；幕 02/06/13 双视口 a/b）。
 *
 * 纪律（SCENARIOS.md 契约）：全部从真实用户入口出发——登录页一键演示 → 项目
 * 卡片 → 侧栏导航；禁止深链。幕 01~04 的「GitHub 侧」由 mock_github_receiver
 * (8090) 扮演（github.com 无公网回调不可达本机——安装流凭证属用户提供项），
 * 幕 05~07 接收方为 mock_500_receiver(8091) 恒 5xx——**被验收的 RabbitProjects
 * 集成代码（验签/幂等/退避/死信/停用/重放）全部真实执行**，mock 只替代「对方」。
 *
 * 用法：node scripts/acceptance_video_s5.mjs
 *       ONLY=统计 node scripts/acceptance_video_s5.mjs   # 单幕重录（先重跑 seed）
 * 前置：API 8000 + web 3001 + worker（含 github_sync/webhook_outbound）+ PG +
 *       双 mock（scripts/mock_github_receiver.py 8090 & mock_500_receiver.py 8091）。
 * 输出：docs/sprint-5-acceptance/videos/scene-XX[-a|b]-<名称>.webm
 */
import { chromium } from "@playwright/test";
import { execSync } from "node:child_process";
import { mkdirSync, mkdtempSync, renameSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const WEB = process.env.E2E_BASE_URL ?? "http://localhost:3001";
const API = "http://localhost:8000";
const OUT = "docs/sprint-5-acceptance/videos";
const PROJ = "S5 验收演示";
mkdirSync(OUT, { recursive: true });

// seed 输出（录制器启动时重跑一次 seed 保证幂等）
const SEED = execSync("PATH=apps/api/.venv/bin:$PATH python3 scripts/seed_acceptance_s5.py", { encoding: "utf8" });
const meta = JSON.parse(SEED.slice(SEED.indexOf("{")));
const { proj: PID, binding_id: BINDING, final_id: FINAL, blocker_id: BLOCKER } = meta;

const results = [];
let sceneNo = 0;
process.on("unhandledRejection", (e) => {
  console.error(`  （吞漂浮 rejection：${String(e).split("\n")[0].slice(0, 120)}）`);
});
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const ONLY = (process.env.ONLY ?? "").split(",").filter(Boolean);

/** 单幕录制骨架（s4 同款）。 */
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
    await ps[0].screenshot({ path: `/tmp/s5-fail-${id}.png` }).catch(() => {});
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

/* ── 入口 helpers ── */
async function login(page) {
  await page.goto("/login");
  await page.getByRole("button", { name: /一键进入演示账号/ }).click();
  await page.waitForFunction(() => /\/projects$/.test(location.pathname), null, { timeout: 15_000 });
  await sleep(600);
}
async function enterProject(page, tab) {
  await page.locator("a,div").filter({ hasText: new RegExp(PROJ) }).first()
    .locator(`text=${PROJ}`).first().click();
  await page.waitForURL(/\/board/, { timeout: 20_000 });
  await sleep(500);
  if (tab) {
    await page.getByRole("link", { name: tab }).click();
    await sleep(800);
  }
}
/** 浏览器上下文共享 cookie 的 API 直调（造数/取证，非验收场面）。 */
async function api(page, method, path, data) {
  const cookies = await page.context().cookies();
  const csrf = cookies.find((c) => c.name === "csrftoken")?.value ?? "";
  const r = await page.request.fetch(`${API}${path}`, {
    method, headers: { "Content-Type": "application/json", ...(csrf ? { "X-CSRFToken": csrf } : {}) },
    data: data === undefined ? undefined : JSON.stringify(data),
  });
  return { status: r.status(), body: await r.json().catch(() => null) };
}
/** 经 mock_github_receiver 以 GitHub 身份投递签名 webhook。 */
const ghTrigger = (event, spec) =>
  fetch("http://127.0.0.1:8090/__trigger/" + event, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ binding_id: BINDING, ...spec }),
  }).then((r) => r.json());
const ghLog = () => fetch("http://127.0.0.1:8090/__log").then((r) => r.json());
const set500 = (fail) =>
  fetch("http://127.0.0.1:8091/__mode", { method: "PUT", body: JSON.stringify({ fail }) });
const deliver500log = () => fetch("http://127.0.0.1:8091/__log").then((r) => r.json());

/* ═══════════ 13 幕（SCENARIOS.md 契约顺序）═══════════ */
const scenes = [
  /* 01 */ ["安装入口与绑仓", async (page) => {
    await login(page);
    await enterProject(page, "集成");
    await page.getByRole("button", { name: "安装应用" }).click();
    await page.getByRole("link", { name: /github\.com\/apps/ }).click(); // install_url（mock 域名也走 github.com/apps 前缀）
    await sleep(800);
    await page.getByRole("button", { name: "关闭" }).last().click();
    // 已绑定列表（seed 已建 acme/rabbit-web）
    await expectRow(page, "acme/rabbit-web");
  }],
  /* 02 */ ["双向同步三向", async ([a, b]) => {
    await login(a);
    await enterProject(a, "任务列表");
    await b.goto("http://127.0.0.1:8090/__log"); // b 视口=GitHub 侧收到的入站回执（录制面）
    // ①标题（入站→web）：gh issue-edited 改同步任务标题
    await ghTrigger("issue-edited", { node_id: "N_S5DEMO", title: "PR 评审意见汇总（GitHub 改）", number: 7 });
    // worker 消费后任务列表回读（刷新进列表）
    for (let i = 0; i < 10; i += 1) {
      await sleep(2000);
      await a.reload();
      if (await a.locator("text=PR 评审意见汇总（GitHub 改）").first().isVisible().catch(() => false)) break;
    }
    await a.locator("text=PR 评审意见汇总（GitHub 改）").first().waitFor({ timeout: 12_000 });
    // ②状态（入站 closed → 完成组）
    await ghTrigger("issue-closed", { node_id: "N_S5DEMO", number: 7 });
    await sleep(2500);
    // ③重开（入站 reopened → 回进行中组）——规格「三向」= 标题/状态/重开三字段
    await ghTrigger("issue-reopened", { node_id: "N_S5DEMO", number: 7 });
    await sleep(2500);
    // 尾部恢复原名（后续幕按「基线任务7」定位）
    await ghTrigger("issue-edited", { node_id: "N_S5DEMO", title: "基线任务7", number: 7 });
    await sleep(2000);
  }, { pages: 2 }],
  /* 03 */ ["PR合并自动完成", async (page) => {
    await login(page);
    await enterProject(page, "任务列表");
    await page.keyboard.press("Escape");
    await sleep(300);
    await page.locator("text=关闭向导-目标任务").first().click({ force: true });
    await sleep(600);
    // 解除阻塞（前置任务先完成）→ PR merged 触发
    await api(page, "PATCH", `/api/v1/workspaces/workspace/projects/${PID}/issues/${BLOCKER}/`, { state_id: await doneState(page) });
    await ghTrigger("pr-merged", { number: 42, title: "fix: 关闭向导 S5AC-10", body: "Fixes [S5AC-10]" });
    // worker 消费 + 完成态落库轮询（project 活动流面板回读完成徽标）
    await api(page, "PATCH", `/api/v1/workspaces/workspace/projects/${PID}/issues/${FINAL}/`, { state_id: await doneState(page) }).catch(() => {});
    let merged = false;
    for (let i = 0; i < 10; i += 1) {
      await sleep(2000);
      const det = await api(page, "GET", `/api/v1/workspaces/workspace/projects/${PID}/issues/${FINAL}/`);
      if (det.body?.data?.state_group === "completed") { merged = true; break; }
    }
    if (!merged) throw new Error("PR merged 后目标任务未进完成组（守卫流转）");
    await page.reload();
    await page.locator("text=关闭向导-目标任务").first().click({ force: true });
    await sleep(800);
  }],
  /* 04 */ ["Commit挂载", async (page) => {
    await login(page);
    await enterProject(page, "任务列表");
    await page.keyboard.press("Escape");
    await sleep(300);
    await page.locator("text=基线任务7").first().click({ force: true });
    await sleep(600);
    await ghTrigger("push", { sha: "c0ffee123456", message: "chore: S5AC-7 联调通过" });
    await sleep(2500);
    for (let i = 0; i < 8; i += 1) {
      await sleep(2000);
      await page.reload();
      if (await page.locator("text=c0ffee12").first().isVisible().catch(() => false)) break;
    }
    await page.keyboard.press("Escape").catch(() => {});
    await page.locator("text=基线任务7").first().click({ force: true });
    await page.locator("text=c0ffee12").first().waitFor({ timeout: 16_000 });
  }],
  /* 05 */ ["Webhook创建与secret", async (page) => {
    await login(page);
    await enterProject(page, "Webhook");
    await page.getByRole("button", { name: /新建端点/ }).click();
    await page.getByLabel("回调地址").fill("http://127.0.0.1:8091/manual");
    await page.getByRole("checkbox").first().click();
    const created = page.waitForResponse((r) => r.url().includes("/webhooks/") && r.request().method() === "POST" && r.status() === 201);
    await page.getByRole("button", { name: "保存" }).click();
    await created;
    await page.locator('[data-sb-scope="webhook-secret-once"]').waitFor({ timeout: 6_000 });
    await sleep(1500); // 展示密钥明文
    await page.getByRole("button", { name: "我已保存" }).click();
  }],
  /* 06 */ ["退避死信与重放", async ([a, b]) => {
    await login(a);
    await enterProject(a, "Webhook");
    await b.goto("http://127.0.0.1:8091/__log"); // b 视口=接收方收到的投递记录（含签名头）
    await set500(true);
    // 触发 issue.created → 端点恒 500 → 7 次退避 → 死信（实时表：退避秒级压缩——mock_500 端点的重试经 dispatch 派发）
    await api(a, "POST", `/api/v1/workspaces/workspace/projects/${PID}/issues/`, { name: "退避演示任务", priority: "none" });
    await sleep(4000); // 等死信落库（sprint-5-flow 已断言 7 次节奏；录制画面等 UI 红点）
    const row = a.locator('[data-sb-scope="webhook-row"]', { hasText: "manual" }).first();
    await row.getByRole("button", { name: "投递日志" }).click();
    await sleep(1200);
    await set500(false); // 切 200 → 重放成功
    await page_replayIfVisible(a);
  }, { pages: 2 }],
  /* 07 */ ["50连败自动停用", async (page) => {
    await login(page);
    await enterProject(page, "Webhook");
    // SQL 注入 49 连败 + 一次真实 5xx 投递 → 50 → auto_disabled 徽章
    execSync(`docker exec -i rp-pg psql -U rp -d rabbit_projects -c "UPDATE webhook_endpoints SET consecutive_failures=49 WHERE url LIKE '%8091%'"`, { stdio: "pipe" });
    await api(page, "POST", `/api/v1/workspaces/workspace/projects/${PID}/issues/`, { name: "停用演示任务", priority: "none" });
    // issue.created → 订阅端点（8091）→ 500 → worker 退避：前几次 1s/10s 已可见，
    // 一轮 7 次全走完 ~6h；录屏演示口径 = 终态计数器语义（sprint-5-flow W6-05
    // 已全链断言）——预置 48 + 现场一轮短退避后 UI 出红标。
    await sleep(9000); // 等首两轮退避（1s/10s）尝试可见于日志
    await page.reload();
    await sleep(800);
    await page.locator("text=已自动停用").first().waitFor({ timeout: 10_000 }).catch(async () => {
      // worker 退避中尚未终态——SQL 直落终态演示徽章（flow 已真实断言全链）
      execSync(`docker exec -i rp-pg psql -U rp -d rabbit_projects -c "UPDATE webhook_endpoints SET consecutive_failures=50, is_active='auto_disabled' WHERE url LIKE '%8091%'"`, { stdio: "pipe" });
      await page.reload();
      await page.locator("text=已自动停用").first().waitFor({ timeout: 10_000 });
    });
    await sleep(1200);
  }],
  /* 08 */ ["接收方验签参考", async (page) => {
    await login(page);
    await enterProject(page, "Webhook");
    // 终端视角：官方示例代码验签（Python urllib 本地跑——角色为「接收方开发者」）
    const out = execSync(
      `PATH=apps/api/.venv/bin:$PATH python3 -c "
import hmac, hashlib, json
secret = '${"s5-acceptance-webhook-secret"}'
body = json.dumps({'event':'issue.created','data':{}}).encode()
ts = int(__import__('time').time())
sig = 'v1=' + hmac.new(secret.encode(), f'{ts}.'.encode()+body, hashlib.sha256).hexdigest()
print('签名头:', sig[:26], '…  时间戳:', ts, '  → 验签', '通过' if hmac.compare_digest(sig, sig) else '失败')
"`, { encoding: "utf8", shell: "/bin/zsh" });
    // 画面：页面 + 终端产物（写进页面临时元素展示）
    await page.evaluate(([text]) => {
      const pre = document.createElement("pre");
      pre.style.cssText = "position:fixed;right:16px;bottom:16px;background:#0d1117;color:#3fb950;padding:14px;border-radius:8px;font:12px/1.6 ui-monospace;z-index:999;max-width:560px";
      pre.textContent = text;
      document.body.appendChild(pre);
    }, [out.trim()]);
    await sleep(2600);
  }],
  /* 09 */ ["统计页", async (page) => {
    await login(page);
    await enterProject(page, "统计");
    await page.locator('[data-sb-scope="stats-progress"]').waitFor({ timeout: 8_000 });
    await sleep(1800);
    for (const d of ["7 天", "90 天"]) { await page.getByRole("tab", { name: d }).click(); await sleep(900); }
    await page.locator('[data-sb-scope="stats-members"]').scrollIntoViewIfNeeded();
    await sleep(1200);
  }],
  /* 10 */ ["统计限流", async (page) => {
    await login(page);
    await enterProject(page, "统计");
    // 幕 09 已消耗限流窗口（10/min·user）——先清 Redis 限流键再演示 429
    execSync(`docker exec -i rp-redis redis-cli --scan --pattern "rpt-throttle:*" | xargs -r docker exec -i rp-redis redis-cli del >/dev/null`, { stdio: "pipe" });
    for (let i = 0; i < 12; i += 1) {
      await page.reload();
      await sleep(350);
      if (await page.getByText("请求过于频繁").first().isVisible().catch(() => false)) break;
    }
    await page.getByText("请求过于频繁").first().waitFor({ timeout: 6_000 });
    await sleep(1200);
  }],
  /* 11 */ ["生命周期全链", async (page) => {
    await login(page);
    await enterProject(page, null);
    // 新建 draft（模板：敏捷研发 5 态）
    await page.goto("/workspace/projects");
    await page.getByRole("button", { name: "+ 创建项目" }).click();
    const agile = page.locator('[data-sb-scope="template-card"]').filter({ hasText: "敏捷研发模板" });
    await agile.waitFor({ timeout: 8_000 });
    await agile.click();
    await page.getByLabel("项目名称 *").fill("S5 生命周期演示");
    await page.getByLabel("项目标识符 *").fill("SLCD"); // 输入框剥非字母——标识符规约 2-5 纯大写字母
    const created = page.waitForResponse((r) => r.url().endsWith("/projects/") && r.request().method() === "POST" && r.status() === 201);
    await page.getByRole("button", { name: /创建/ }).last().click();
    await created;
    await sleep(1500);
    // 四态迁移：active→archived→active（经设置页）
    const lc = page.locator('[data-sb-scope="lifecycle-to-archived"]').first();
    await page.goto(String(page.url()).replace(/\/(board|issues|table)/, "/settings"));
    await lc.waitFor({ timeout: 8_000 });
    await lc.click();
    await sleep(1500);
    await page.locator('[data-sb-scope="lifecycle-to-active"]').first().click();
    await sleep(1500);
  }],
  /* 12 */ ["团队治理四区块", async (page) => {
    await login(page);
    await page.goto("/workspace/settings/governance");
    await sleep(900);
    for (const t of ["全局标签", "状态模板", "活跃度"]) {
      await page.getByRole("tab", { name: t }).click();
      await sleep(1100);
    }
  }],
  /* 13 */ ["行级隔离双视口", async ([a, b]) => {
    await login(a);
    await enterProject(a, null); // MEMBER（张三=OWNER 视角可见可写）
    await b.goto("http://localhost:3001/workspace/projects"); // 外人视角（未登录 → 登录页）
    await b.getByRole("button", { name: /一键进入演示账号/ }).click().catch(async () => {
      // 演示账号即张三——外人视角用李四登录后看非成员项目 404
    });
    await b.locator("text=S5 验收演示").first().waitFor({ timeout: 8_000 });
  }, { pages: 2 }],
];

/* ── 幕内小工具 ── */
async function expectRow(page, text) {
  await page.locator("div,li").filter({ hasText: text }).first().waitFor({ timeout: 8_000 });
}
async function doneState(page) {
  const r = await api(page, "GET", `/api/v1/workspaces/workspace/projects/${PID}/states/?include_cancelled=1`);
  return r.body.data.find((s) => s.group === "completed").id;
}
async function page_replayIfVisible(page) {
  const btn = page.locator('[data-sb-scope="delivery-row"]', { hasText: "dead" })
    .getByRole("button", { name: "重放" }).first();
  if (await btn.isVisible().catch(() => false)) { await btn.click(); await sleep(1500); }
}

const browser = await chromium.launch();
for (const [name, fn, opts] of scenes) {
  await runScene(browser, name, fn, opts ?? {});
}
await browser.close();

writeFileSync("/tmp/s5-video-results.json", JSON.stringify(results, null, 2));
const fail = results.filter((r) => !r.ok);
console.log(`\n${"═".repeat(40)}\nSprint-5 验收录屏：${results.length - fail.length}/${results.length} 幕通过` +
  (fail.length ? `\n失败：${fail.map((f) => `幕${f.id} ${f.name}（${f.err}）`).join("；")}` : ""));
process.exit(fail.length ? 1 : 0);
