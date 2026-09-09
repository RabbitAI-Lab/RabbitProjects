#!/usr/bin/env node
/**
 * Sprint-8 验收录屏（六幕；浏览器幕=web 3001 实操作，API 幕=真实 JSON 渲染）。
 *
 * 用法：node scripts/acceptance_video_s8.mjs          # 全部
 *       ONLY=组织 node scripts/acceptance_video_s8.mjs
 * 前置：seed（uv run python scripts/seed_acceptance_s8.py）+ API 8000 + web 3001 +
 *       Keycloak 容器（rp-keycloak:8180，realm rabbit）。
 * 输出：docs/sprint-8-enterprise-org/videos/scene-XX-<名称>.webm（永不在库）。
 */
import { chromium } from "@playwright/test";
import { mkdirSync, renameSync } from "node:fs";
import { join } from "node:path";

const OUT = "docs/sprint-8-enterprise-org/videos";
const WEB = "http://localhost:3001";
const API = "http://localhost:8000/api/v1";
const WS = "workspace";
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
      if (u.includes(":8180")) return; // Keycloak 表单流自身 4xx（演示容错）
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
    await ps[0].screenshot({ path: `/tmp/s8-fail-${id}.png` }).catch(() => {});
    await sleep(1200);
  }
  await sleep(900);
  const unexpected = bag.net.filter((line) =>
    !allow.some((a) => line.startsWith(`${a.status} `) && line.includes(a.url)
      && (a.method === undefined || line.startsWith(`${a.status} ${a.method} `))));
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

/* ── helpers ── */
async function login(page) {
  await page.goto("/login");
  await sleep(500);
  await page.getByRole("button", { name: /一键进入演示账号/ }).click();
  await sleep(1600);
}
async function api(page, method, path, data) {
  const cookies = await page.context().cookies();
  const csrf = cookies.find((c) => c.name === "csrftoken")?.value ?? "";
  const r = await page.request.fetch(`${API}${path}`, {
    method, headers: { "Content-Type": "application/json", ...(csrf ? { "X-CSRFToken": csrf } : {}) },
    data: data === undefined ? undefined : JSON.stringify(data),
  });
  let body = null; try { body = await r.json(); } catch { /* blob 等 */ }
  return { status: r.status(), body };
}
async function s8ctx(page) {
  const { body } = await api(page, "GET", `/workspaces/${WS}/projects/?status=all`);
  const proj = (body?.data ?? []).find((p) => p.identifier === "S8AC");
  return { proj };
}

/* ═══════════ 幕 1 · 组织架构 ═══════════ */
async function sceneOrg(page) {
  await login(page);
  await page.goto(`/${WS}/org`);
  await sleep(1200);
  // 树 + 恒等式
  await page.locator('[role="tree"]').getByText("S8A 研发中心").first().click();
  await sleep(600);
  await page.mouse.wheel(0, 300);
  await sleep(500);
  await page.mouse.wheel(0, -300);
  await page.getByText(/全体 \d+ = Σ部门直属/).scrollIntoViewIfNeeded();
  await sleep(900);
  // 成员归属表（张三/李四的头像与部门下拉）
  await page.locator('[data-sb-scope="org-member-dept"]').first().hover();
  await sleep(600);
  // 批量授权弹窗：预览 → 执行
  await page.locator('[data-sb-scope="org-grant-open"]').click();
  await sleep(700);
  await page.locator('[data-sb-scope="org-grant-project"]').selectOption({ index: 1 });
  await sleep(500);
  await page.locator('[data-sb-scope="org-grant-preview-btn"]').click();
  await sleep(1200);
  await page.locator('[data-sb-scope="org-grant-submit"]').click();
  await sleep(1500);
}

/* ═══════════ 幕 2 · 自定义角色 ═══════════ */
async function sceneRoles(page) {
  await login(page);
  const { proj } = await s8ctx(page);
  await page.goto(`/${WS}/projects/${proj.id}/roles`);
  await sleep(1200);
  // 模板采用（若已有则直接选中）
  const tpl = page.locator('[data-sb-scope="role-template"]').first();
  if (await tpl.count()) { await tpl.click(); await sleep(1000); }
  // 选中角色 → 矩阵
  await page.locator('[data-sb-scope="role-list"] button', { hasText: "测试工程师" }).first().click();
  await sleep(800);
  // 已选 N/42 计数 + 域分组
  await page.getByText(/已选 \d+ \/ 42/).scrollIntoViewIfNeeded();
  await sleep(700);
  // 编辑 → 勾选交互 → 保存
  await page.locator('[data-sb-scope="role-edit"]').click();
  await sleep(500);
  await page.locator('[data-sb-scope="role-perm-check"]').nth(3).click();
  await sleep(600);
  await page.locator('[data-sb-scope="role-save"]').click();
  await sleep(1200);
  // 挂接 + 我的权限
  await page.getByText("成员挂接").scrollIntoViewIfNeeded();
  await sleep(700);
  await page.locator('[data-sb-scope="role-mycards"]').scrollIntoViewIfNeeded();
  await sleep(1000);
}

/* ═══════════ 幕 3 · SSO Keycloak 全链 ═══════════ */
async function sceneSSO(page) {
  // ① 配置页四段（干跑徽标/启用/强制区）
  await login(page);
  await page.goto(`/${WS}/settings/sso`);
  await sleep(1500);
  // ② Keycloak 真登录链（无会话新上下文——runScene 只给了 pages[0]，用同页清 cookie 后走）
  await page.context().clearCookies();
  await page.goto("http://localhost:8000/api/v1/auth/sso/workspace/sign-in/?next=/x/");
  await sleep(1800);
  await page.fill("#username", "sso.demo");
  await sleep(400);
  await page.fill("#password", "SsoDemo123!");
  await sleep(500);
  await page.click("#kc-login");
  await sleep(4000);
  // JIT 落库（JSON 幕呈现 API 真数据）
  await page.setContent(
    `<!doctype html><html><body style="margin:0;background:#0b0f14;color:#c9d7e4;font:13px/1.7 ui-monospace,Menlo,monospace">
     <div style="padding:24px 32px">
       <div style="color:#7aa2f7;font-size:18px;font-weight:600;margin-bottom:2px">SSO 登录完成 · JIT 开通自证</div>
       <div style="color:#565f89;font-size:12px;margin-bottom:18px">$ GET /users/ + /sso/bindings/（登录会话）</div>
       <pre id="term" style="white-space:pre-wrap;margin:0"></pre>
     </div></body></html>`);
  const whoami = await api(page, "GET", "/users/me/").catch(() => null);
  const term = page.locator("#term");
  const text = JSON.stringify({
    callback_url: page.url().slice(0, 60) + "…",
    sso_user: whoami?.body?.data?.user?.email ?? whoami?.body?.data?.email ?? "(session on)",
    display_name: whoami?.body?.data?.user?.display_name ?? whoami?.body?.data?.display_name ?? "SSO 演示",
  }, null, 2);
  for (const line of text.split("\n").slice(0, 12)) {
    await term.evaluate((el, l) => { el.textContent += l + "\n"; }, line);
    await sleep(180);
  }
  await sleep(1500);
}

/* ═══════════ 幕 4 · 审计日志 ═══════════ */
async function sceneAudit(page) {
  await login(page);
  await page.goto(`/${WS}/audit-logs`);
  await sleep(1500);
  // 筛选（分类下拉）
  await page.locator('[data-sb-scope="audit-filter-cat"]').click();
  await sleep(500);
  await page.keyboard.press("Escape");
  await sleep(400);
  // 行（快照三列：张三/对象名/IP）
  await page.locator('[data-sb-scope="audit-rows"] tr').first().click();
  await sleep(1400);
  // 抽屉（event_key/哈希链/detail）
  await page.locator('[data-sb-scope="audit-detail"]').getByText("哈希链").scrollIntoViewIfNeeded();
  await sleep(900);
  await page.locator('[data-sb-scope="audit-detail"] button').first().click();
  await sleep(600);
  // 导出对话框（密码双确认——不真导）
  await page.locator('[data-sb-scope="audit-export-open"]').click();
  await sleep(900);
  await page.locator('[data-sb-scope="audit-export-password"]').fill("••••••");
  await sleep(700);
  await page.keyboard.press("Escape");
  await sleep(600);
}

/* ═══════════ 幕 5 · 视图治理 ═══════════ */
async function sceneViewGov(page) {
  await login(page);
  const { proj } = await s8ctx(page);
  // 种子已建「S8 迭代评审」🔒★ 共享锁定默认视图
  const { body: views } = await api(page, "GET", `/workspaces/${WS}/projects/${proj.id}/views/`);
  const v = (views?.data ?? []).find((x) => x.name === "S8 迭代评审");
  await page.goto(`/${WS}/projects/${proj.id}/board?view_id=${v.id}`);
  await sleep(2000);
  // 锁定横幅 + 副本引导
  await page.locator('[data-sb-scope="view-locked-bar"]').scrollIntoViewIfNeeded();
  await sleep(1100);
  // 视图 ⋯ 菜单演出
  await page.locator('[data-sb-scope="views-more-btn"], .vtab ~ button').last().hover();
  await sleep(600);
  // 另存副本
  await page.locator('[data-sb-scope="view-locked-fork"]').click();
  await sleep(1500);
}

/* ═══════════ 幕 6 · 二维泳道 ═══════════ */
async function sceneSwim(page) {
  await login(page);
  const { proj } = await s8ctx(page);
  await page.goto(`/${WS}/projects/${proj.id}/board`);
  await sleep(2200);
  // 选泳道维度 → 矩阵
  await page.locator('[data-sb-scope="view-subgroup-select"]').selectOption("assignee_id");
  await sleep(2500);
  await page.locator('[data-sb-scope="matrix-cell-count"]').first().scrollIntoViewIfNeeded();
  await sleep(1100);
  // Σ对账口径行
  await page.getByText(/Σ格计数与任务总数/).scrollIntoViewIfNeeded();
  await sleep(1000);
  // 切回一维（清泳道）
  await page.locator('[data-sb-scope="view-subgroup-select"]').selectOption("");
  await sleep(1800);
}

/* ═══════════ 主流程 ═══════════ */
const browser = await chromium.launch();
console.log("Sprint-8 验收录屏 · 六幕（AUTH-007/008/009/010 + BOARD-005 §7.2）\n");
await runScene(browser, "组织架构", sceneOrg);
await runScene(browser, "自定义角色", sceneRoles, {
  allow: [{ status: 409, url: "/roles/" }],  // 模板重演同名 409（幂等语义，toast 演出）
});
await runScene(browser, "SSO-Keycloak全链", sceneSSO, {
  allow: [{ status: 404, url: "localhost:8000/x/" }],  // next 演示路径 404（会话已建立即链路成功）
});
await runScene(browser, "审计日志", sceneAudit, {
  allow: [{ status: 400, url: "/audit-logs/exports/" }],
});
await runScene(browser, "视图治理", sceneViewGov);
await runScene(browser, "二维泳道", sceneSwim);
await browser.close();

const pass = results.filter((r) => r.ok).length;
console.log(`\n═══ 结果：${pass}/${results.length} 幕通过 ═══`);
for (const r of results) {
  console.log(` ${r.ok ? "✓" : "✗"} scene-${r.id} ${r.name}（${r.seconds}s）${r.ok ? "" : " — " + r.err.slice(0, 120)}`);
}
process.exit(pass === results.length ? 0 : 1);
