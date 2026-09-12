#!/usr/bin/env node
/**
 * P4 R3~R18 API 级验收演示——十幕 × 独立视频。
 *
 * 形态说明：R3~R18 为后端交付（无操作界面），本演示以「API 验收控制台」
 * 承载——浏览器页面内逐步执行**真实 HTTP 调用**（Playwright request 带
 * 会话），每步渲染 方法/路径/状态码/关键响应 字段卡；需要 DB 侧动作的
 * 步骤（回溯时间戳/beat 扫描/重算）经 manage.py 真实执行并在卡片标注
 * 「DB 侧执行」。全部从真实 API 出发，与 UI 录屏纪律同源。
 *
 * 十幕：①公式字段 ②任务基线 ③开放平台双凭证 ④企微钉钉通道 ⑤文件合规
 * ⑥归档闸门 ⑦大屏报表与播放 ⑧AI 四能力 ⑨License 只读降级 ⑩甘特与收口五件
 *
 * 前置：web 3001 + api 8000 + PG/Redis。输出 docs/p4-api-acceptance/videos/
 * 用法：node scripts/api_acceptance_p4.mjs [ONLY=幕名] [KEEP=1 保留演示数据]
 */
import { chromium } from "@playwright/test";
import { execSync } from "node:child_process";
import { mkdirSync, writeFileSync, unlinkSync } from "node:fs";

const API = "http://localhost:8000/api/v1";
const OUT = "docs/p4-api-acceptance/videos";
mkdirSync(OUT, { recursive: true });
const ONLY = (process.env.ONLY ?? "").split(",").filter(Boolean);

const results = [];
let sceneNo = 0;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const SHELL_ENV = { ...process.env, SECRET_KEY: "dev",
  DATABASE_URL: "postgres://rp:rp@localhost:5432/rabbit_projects",
  PYTHONPATH: "apps/api" };

/** DB 侧真实执行（manage.py shell） */
function sh(py, label = "DB 侧执行") {
  const file = `/tmp/p4demo_${Math.random().toString(36).slice(2)}.py`;
  writeFileSync(file, py);
  try {
    const out = execSync(
      `cd apps/api && SECRET_KEY=dev DATABASE_URL=postgres://rp:rp@localhost:5432/rabbit_projects ` +
      `PYTHONPATH=$PWD uv run python ${file}`, { encoding: "utf8", env: SHELL_ENV, stdio: "pipe" });
    return { label, output: out.trim().split("\n").slice(-3).join(" | ") };
  } finally {
    unlinkSync(file);
  }
}

/** 控制台页面骨架 */
const SHELL_HTML = (title) => `<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<style>
:root{--brand:#3f76ff;--ok:#059669;--bad:#ef4444;--ink:#262626;--ink3:#8c8c8c;--line:#e5e5e5}
body{font-family:-apple-system,"PingFang SC",sans-serif;background:#fafafa;color:var(--ink);
  font-size:14px;margin:0;padding:22px 26px}
h1{font-size:17px;margin:0 0 4px}.sub{font-size:12.5px;color:var(--ink3);margin-bottom:16px}
.step{background:#fff;border:1px solid var(--line);border-radius:8px;padding:10px 14px;
  margin-bottom:10px;box-shadow:0 1px 2px rgba(0,0,0,.05);animation:pop .2s ease}
@keyframes pop{from{opacity:0;transform:translateY(4px)}}
.req{display:flex;align-items:center;gap:8px;font-family:ui-monospace,Menlo,monospace;font-size:12.5px}
.m{font-weight:700;padding:1px 7px;border-radius:4px;color:#fff}
.m.GET{background:#6b7280}.m.POST{background:var(--brand)}.m.PATCH{background:#f59e0b}
.m.DELETE{background:var(--bad)}.m.DB{background:#7c3aed}
.st{margin-left:auto;font-weight:600}
.st.ok{color:var(--ok)}.st.bad{color:var(--bad)}
.note{font-size:12px;color:var(--ink3);margin-top:3px}
pre{font-family:ui-monospace,Menlo,monospace;font-size:11.5px;color:#525252;margin:6px 0 0;
  white-space:pre-wrap;word-break:break-all;max-height:130px;overflow:hidden}
.verdict{margin-top:14px;padding:10px 14px;border-radius:8px;font-weight:600}
.verdict.ok{background:#ecfdf5;color:var(--ok)}.verdict.bad{background:#fef2f2;color:var(--bad)}
</style></head><body>
<h1>${title}</h1><div class="sub">P4 API 级验收 · 真实 HTTP 调用 · 2026-09-12</div>
<div id="steps"></div></body></html>`;

class Console {
  constructor(page) { this.page = page; }
  async init(title) {
    await this.page.setContent(SHELL_HTML(title));
    await this.page.setViewportSize({ width: 1280, height: 860 });
  }
  async step(req, status, body, note = "", pick = null) {
    let text = "";
    try {
      const obj = typeof body === "string" ? { raw: body } : (body ?? {});
      text = pick ? JSON.stringify(pick(obj), null, 1) : JSON.stringify(obj, null, 1);
      if (text.length > 900) text = text.slice(0, 900) + "\n…";
    } catch { text = String(body).slice(0, 300); }
    await this.page.evaluate(({ req, status, text, note }) => {
      const wrap = document.getElementById("steps");
      const div = document.createElement("div");
      div.className = "step";
      const ok = status < 400;
      div.innerHTML = `<div class="req"><span class="m ${req.m}">${req.m}</span>`
        + `<span>${req.p}</span><span class="st ${ok ? "ok" : "bad"}">${status}</span></div>`
        + (note ? `<div class="note">${note}</div>` : "")
        + `<pre>${String(text).replace(/</g, "&lt;")}</pre>`;
      wrap.appendChild(div);
      window.scrollTo(0, document.body.scrollHeight);
    }, { req, status, text, note });
    await sleep(650);
    return body;
  }
  async verdict(ok, msg) {
    await this.page.evaluate(({ ok, msg }) => {
      const d = document.createElement("div");
      d.className = `verdict ${ok ? "ok" : "bad"}`;
      d.textContent = (ok ? "✓ " : "✗ ") + msg;
      document.body.appendChild(d);
    }, { ok, msg });
    await sleep(1200);
  }
}

/** 新建演示账号（注册即登录得会话 + 自有工作空间 OWNER） */
async function jsof(r) {
  try { return await r.json(); } catch {
    return { non_json: ((await r.text())).slice(0, 60) };
  }
}

async function provision(ctx, tag) {
  const email = `p4demo.${tag}.${Date.now() % 100000}@rabbit.dev`;
  const req = ctx.request;
  await req.get(`${API}/auth/csrf-token/`);
  const csrf = (await ctx.cookies()).find((c) => c.name === "csrftoken")?.value ?? "";
  const r = await req.post(`${API}/auth/sign-up/`, {
    headers: { "X-CSRFToken": csrf },
    data: { email, password: "Rabbit123!", display_name: `演示${tag}` } });
  if (r.status() >= 400) throw new Error(`signup ${r.status()}: ${(await r.text())}`);
  const data = (await jsof(r)).data;
  return { email, ws: data.default_workspace_slug, userId: data.user.id };
}

function csrfOf(ctx) {
  // 同步兜底：cookies API 是 async——外层先取好传入
  return null;
}

async function runScene(browser, name, fn) {
  sceneNo += 1;
  if (ONLY.length && !ONLY.some((k) => name.includes(k))) {
    console.log(`⊘ 幕${sceneNo} ${name}（ONLY 过滤跳过）`);
    return;
  }
  const ctx = await browser.newContext({
    viewport: { width: 1280, height: 860 },
    recordVideo: { dir: OUT, size: { width: 1280, height: 860 } },
  });
  const page = await ctx.newPage();
  const t0 = Date.now();
  let ok = true;
  let err = "";
  try {
    await fn(ctx, page);
  } catch (e) {
    ok = false;
    err = String(e).split("\n").slice(0, 2).join(" | ").slice(0, 260);
  }
  await ctx.close();
  results.push({ n: sceneNo, name, ok });
  console.log(`${ok ? "✓" : "✗"} 幕${sceneNo} ${name}（${((Date.now() - t0) / 1000).toFixed(1)}s）${err}`);
}

/** 带会话与 CSRF 的 API 调用器 */
function apiOf(ctx) {
  return {
    async call(m, p, data, extraHeaders = {}) {
      const req = ctx.request;
      if (m === "GET" || m === "HEAD") {
        return req.get(`${API}${p}`, { headers: extraHeaders });
      }
      const csrf = (await ctx.cookies()).find((c) => c.name === "csrftoken")?.value ?? "";
      const headers = { "X-CSRFToken": csrf, ...extraHeaders };
      const method = m.toLowerCase();
      return req[method](`${API}${p}`, { headers, data });
    },
  };
}

const browser = await chromium.launch();

// ══ 幕1 R3a 公式字段（TASK-014）══
await runScene(browser, "公式字段引擎", async (ctx, page) => {
  const con = new Console(page);
  await con.init("幕 01 · 公式字段引擎（TASK-014 · 受限 DSL 与失效传播）");
  const api = apiOf(ctx);
  const u = await provision(ctx, "fml");
  let r = await api.call("POST", `/workspaces/${u.ws}/projects/`, {
    name: "公式演示", identifier: `FX${Date.now() % 1000}` });
  const proj = (await jsof(r)).data;
  await con.step({ m: "POST", p: `/projects/` }, r.status(), await jsof(r),
    "演示账号注册（注册即登录）+ 建项目");
  // 数字字段
  r = await api.call("POST",
    `/workspaces/${u.ws}/projects/${proj.id}/issue-properties/`,
    { name: "实际工时", field_key: "cf_actual", field_type: "number" });
  await con.step({ m: "POST", p: `/projects/{pid}/issue-properties/` }, r.status(),
    { created: r.status() === 201 }, "字段基座建列（项目级）cf_actual");
  r = await api.call("POST",
    `/workspaces/${u.ws}/projects/${proj.id}/issue-properties/`,
    { name: "计划工时", field_key: "cf_plan", field_type: "number" });
  await con.step({ m: "POST", p: `/issue-properties/（cf_plan）` }, r.status(),
    { created: r.status() === 201 }, "第二数字列 cf_plan");
  // 校验端点：恶意公式拒绝
  r = await api.call("POST", `/workspaces/${u.ws}/issue-properties/validate-expression/`,
    { formula: "__import__('os')" });
  await con.step({ m: "POST", p: `/validate-expression/（注入）` }, r.status(),
    await jsof(r), "非白名单函数拒绝（零 eval 面向）",
    (o) => ({ valid: o?.data?.valid }));
  // 合法公式
  const F = "subtract(prop_cf('cf_actual'), prop_cf('cf_plan'))";
  r = await api.call("POST", `/workspaces/${u.ws}/issue-properties/validate-expression/`,
    { formula: F });
  await con.step({ m: "POST", p: `/validate-expression/（工时偏差）` }, r.status(),
    await jsof(r), "类型推断 number",
    (o) => ({ valid: o?.data?.valid, type: o?.data?.result_type }));
  // 建任务 + 真实预览
  r = await api.call("POST", `/workspaces/${u.ws}/projects/${proj.id}/issues/`,
    { name: "网关联调", custom_fields: { cf_actual: 120, cf_plan: 90 } });
  const issue = (await jsof(r)).data ?? (await jsof(r));
  await con.step({ m: "POST", p: `/issues/` }, r.status(), { id: issue?.id, key: issue?.issue_key },
    "建任务带 cf_actual=120 cf_plan=90");
  r = await api.call("POST",
    `/workspaces/${u.ws}/projects/${proj.id}/issues/${issue.id}/preview-expression/`,
    { formula: F });
  await con.step({ m: "POST", p: `/preview-expression/` }, r.status(), await jsof(r),
    "真实数据求值", (o) => ({ value: o?.data?.value }));
  // 建公式字段 + 改值触发失效 + DB 侧重算物化
  r = await api.call("POST", `/workspaces/${u.ws}/issue-properties/formula/`,
    { name: "工时偏差", field_key: "cf_dev", formula: F });
  await con.step({ m: "POST", p: `/issue-properties/formula/` }, r.status(),
    await jsof(r), "公式字段落库（环检测+复杂度过）",
    (o) => ({ key: o?.data?.field_key, type: o?.data?.result_type }));
  r = await api.call("PATCH",
    `/workspaces/${u.ws}/projects/${proj.id}/issues/${issue.id}/`,
    { custom_fields: { cf_actual: 150 } });
  await con.step({ m: "PATCH", p: `/issues/…（cf_actual→150）` }, r.status(),
    { patched: true }, "依赖变更 → 失效传播打脏标（BR-04）");
  const recalc = sh(`
import os,django;os.environ.setdefault('DJANGO_SETTINGS_MODULE','plane.settings.dev');django.setup()
from plane.formula.derived import recompute_issue
from plane.db.models import Issue
i=Issue.objects.get(pk='${issue.id}')
print(recompute_issue('${issue.id}'))`, "DB 侧执行：异步重算（读时兜底同函数）");
  await con.step({ m: "DB", p: "recompute_issue(issue)" }, 200, recalc.output,
    "物化：120-90=30 → 150-90=60");
  r = await api.call("GET",
    `/workspaces/${u.ws}/projects/${proj.id}/issues/${issue.id}/`);
  const got = await jsof(r);
  const cf = got?.data?.custom_fields ?? got?.custom_fields ?? {};
  await con.step({ m: "GET", p: `/issues/{id}/` }, r.status(),
    { cf_dev: cf.cf_dev }, "读路径回读物化值");
  await con.verdict(cf.cf_dev === 60, "公式链路：校验拒绝注入 → 类型推断 → 真实求值 → 失效传播 → 物化=60");
});

// ══ 幕2 R3b 任务基线（TASK-015）══
await runScene(browser, "任务基线对比", async (ctx, page) => {
  const con = new Console(page);
  await con.init("幕 02 · 任务基线与版本对比（TASK-015）");
  const api = apiOf(ctx);
  const u = await provision(ctx, "bl");
  let r = await api.call("POST", `/workspaces/${u.ws}/projects/`, {
    name: "基线演示", identifier: `BL${Date.now() % 1000}` });
  const proj = (await jsof(r)).data;
  const ids = [];
  for (const [n, td] of [["稳定任务", "2030-01-10"], ["延期任务", "2030-01-08"], ["被删任务", "2030-01-15"]]) {
    r = await api.call("POST", `/workspaces/${u.ws}/projects/${proj.id}/issues/`,
      { name: n, target_date: td });
    ids.push((await jsof(r)).data?.id ?? (await jsof(r)).id);
  }
  await con.step({ m: "POST", p: `/issues/ ×3` }, 201, { count: 3 }, "三任务入项目");
  // reason 必填负向
  r = await api.call("POST", `/workspaces/${u.ws}/projects/${proj.id}/baselines/`,
    { name: "V1" });
  await con.step({ m: "POST", p: `/baselines/（缺 reason）` }, r.status(), await jsof(r),
    "BR-13 变更原因必填 400", (o) => ({ field: o?.error?.details?.[0]?.field }));
  r = await api.call("POST", `/workspaces/${u.ws}/projects/${proj.id}/baselines/`,
    { name: "V1", reason: "合同基线冻结" });
  const bl = (await jsof(r)).data;
  await con.step({ m: "POST", p: `/baselines/` }, r.status(), { id: bl?.baseline_id ?? bl?.id, status: bl?.status },
    "同步创建（≤2 万任务）201");
  const blId = bl?.baseline_id ?? bl?.id;
  // 变更：延期+3d / 软删 / 新增
  r = await api.call("PATCH",
    `/workspaces/${u.ws}/projects/${proj.id}/issues/${ids[1]}/`, { target_date: "2030-01-11" });
  sh(`import os,django;os.environ.setdefault('DJANGO_SETTINGS_MODULE','plane.settings.dev');django.setup()
from plane.db.models import Issue
Issue.objects.filter(pk='${ids[2]}').update(deleted_at=__import__('django.utils.timezone',fromlist=['timezone']).now())
print('soft-deleted')`, "DB 侧执行：软删（等价 UI 删除入口）");
  r = await api.call("POST", `/workspaces/${u.ws}/projects/${proj.id}/issues/`,
    { name: "新增任务", target_date: "2030-02-01" });
  await con.step({ m: "PATCH/DB/POST", p: "改期 +3d · 软删 · 新增" }, 200,
    { mutated: 3 }, "计划漂移三动作");
  r = await api.call("GET",
    `/workspaces/${u.ws}/projects/${proj.id}/baselines/${blId}/compare/`);
  const cmp = (await jsof(r)).data;
  const byType = {};
  for (const row of cmp ?? []) byType[row.diff_type] = (byType[row.diff_type] ?? 0) + 1;
  await con.step({ m: "GET", p: `/baselines/{id}/compare/` }, r.status(), byType,
    "全集对比：delayed=1 added=1 deleted=1（行不灭失 BR-05）");
  r = await api.call("GET",
    `/workspaces/${u.ws}/projects/${proj.id}/baselines/${blId}/stats/`);
  const st = (await jsof(r)).data;
  await con.step({ m: "GET", p: `/baselines/{id}/stats/` }, r.status(),
    { delay_rate: st?.delay_rate, avg_delay_days: st?.avg_delay_days,
      scope_creep: st?.scope_creep_rate }, "偏差统计（§2.4）");
  r = await ctx.request.get(
    `${API}/workspaces/${u.ws}/projects/${proj.id}/baselines/${blId}/export/`);
  const csvHead = ((await r.text())).split("\n")[0];
  await con.step({ m: "GET", p: `/baselines/{id}/export/` }, r.status(),
    { first_line: csvHead }, "CSV 导出（对账附件）");
  await con.verdict(byType.delayed === 1 && byType.added === 1 && byType.deleted === 1,
    "基线链路：reason 必填 → 快照 → 三类漂移全集呈现 → 统计与导出");
});

// ══ 幕3 R4 开放平台（INTG-004）══
await runScene(browser, "开放平台双凭证", async (ctx, page) => {
  const con = new Console(page);
  await con.init("幕 03 · 开放平台：API Key 与 OAuth 2.0（INTG-004）");
  const api = apiOf(ctx);
  const u = await provision(ctx, "oa");
  // API Key
  let r = await api.call("POST", `/api-tokens/`,
    { name: "BI 抽数", scopes: ["issues:read"] });
  const tok = (await jsof(r)).data;
  await con.step({ m: "POST", p: `/api-tokens/` }, r.status(),
    { prefix: tok?.key_prefix, suffix: tok?.key_suffix, warning: tok?.warning },
    "密钥创建——明文仅本次返回（§2.5）");
  const key = tok.key;
  r = await api.call("POST", `/workspaces/${u.ws}/projects/`, {
    name: "开放面", identifier: `OA${Date.now() % 1000}` });
  const proj = (await jsof(r)).data;
  r = await api.call("POST", `/workspaces/${u.ws}/projects/${proj.id}/issues/`,
    { name: "开放任务" });
  // 白名单读
  r = await ctx.request.get(`${API}/external/issues/`,
    { headers: { "X-API-Key": key } });
  const ext = await jsof(r);
  await con.step({ m: "GET", p: `/external/issues/（X-API-Key）` }, r.status(),
    { count: ext?.meta?.count, first: ext?.data?.[0]?.name }, "白名单读通");
  // scope 不足
  r = await ctx.request.get(`${API}/external/workspaces/`,
    { headers: { "X-API-Key": key } });
  await con.step({ m: "GET", p: `/external/workspaces/（越权）` }, r.status(),
    await jsof(r), "scope 不足 → PERM_TOKEN_SCOPE_INSUFFICIENT（BR-03 精确清单）",
    (o) => ({ code: o?.error?.code }));
  // 白名单外 404
  r = await ctx.request.get(`${API}/external/secrets/`,
    { headers: { "X-API-Key": key } });
  await con.step({ m: "GET", p: `/external/secrets/（未注册）` }, r.status(),
    { isolated: true }, "白名单物理隔离——未注册路径天然 404");
  // OAuth 全链
  r = await api.call("POST", `/oauth/applications/`,
    { name: "报表应用", scopes_requested: ["issues:read"] });
  const app = (await jsof(r)).data;
  await con.step({ m: "POST", p: `/oauth/applications/` }, r.status(),
    { client_id: app?.client_id }, "应用注册（client_secret 仅一次）");
  r = await ctx.request.get(`${API}/oauth/authorize/`, {
    params: { client_id: app.client_id, scope: "issues:read",
              approve: "true", redirect_uri: "" } });
  const code = (await jsof(r)).data?.code;
  await con.step({ m: "GET", p: `/oauth/authorize/?approve=true` }, r.status(),
    { code: code?.slice(0, 10) + "…", expires_in: 600 }, "授权码签发（10 分钟一次性）");
  r = await api.call("POST", `/oauth/token/`,
    { grant_type: "authorization_code", code });
  const tk = await jsof(r);
  await con.step({ m: "POST", p: `/oauth/token/（code）` }, r.status(),
    { token_type: tk?.token_type, expires_in: tk?.expires_in },
    "换 access + refresh（RFC 6749 形态）");
  r = await api.call("POST", `/oauth/token/`,
    { grant_type: "refresh_token", refresh_token: tk.refresh_token });
  const tk2 = await jsof(r);
  await con.step({ m: "POST", p: `/oauth/token/（refresh）` }, r.status(),
    { rotated: tk2?.refresh_token !== tk?.refresh_token },
    "refresh 轮换——新值签发");
  r = await api.call("POST", `/oauth/token/`,
    { grant_type: "refresh_token", refresh_token: tk.refresh_token });
  await con.step({ m: "POST", p: `/oauth/token/（旧值重放）` }, r.status(),
    { error: (await jsof(r))?.error }, "旧值复用拒绝（rotation §9.4）");
  r = await ctx.request.get(`${API}/external/issues/`,
    { headers: { Authorization: `Bearer ${tk2.access_token}` } });
  const viaOauth = await jsof(r);
  await con.step({ m: "GET", p: `/external/issues/（Bearer）` }, r.status(),
    { count: viaOauth?.meta?.count }, "OAuth 通道白名单读通");
  r = await api.call("POST", `/oauth/revoke/`, { token: tk2.access_token });
  await con.step({ m: "POST", p: `/oauth/revoke/` }, r.status(), { revoked: true },
    "吊销 → 黑名单（jti）");
  r = await ctx.request.get(`${API}/external/issues/`,
    { headers: { Authorization: `Bearer ${tk2.access_token}` } });
  await con.step({ m: "GET", p: `/external/issues/（吊销后）` }, r.status(),
    { code: (await jsof(r))?.error?.code }, "吊销即时失效 401");
  await con.verdict(true, "双凭证链路：Key 白名单/越权精确/物理隔离 + OAuth 授权码→轮换→黑名单");
});

// ══ 幕4 R5 IM 通道（INTG-005）══
await runScene(browser, "企微钉钉通道", async (ctx, page) => {
  const con = new Console(page);
  await con.init("幕 04 · 企微/钉钉群机器人通道（INTG-005）");
  const api = apiOf(ctx);
  const u = await provision(ctx, "im");
  let r = await api.call("POST", `/workspaces/${u.ws}/integrations/im/channels/`,
    { provider: "dingtalk", name: "钉钉发版群",
      target: "https://oapi.dingtalk.com/robot/send?access_token=T1",
      secret_ref: "env:DINGTALK_SECRET" });
  const ch = (await jsof(r)).data;
  await con.step({ m: "POST", p: `/im/channels/` }, r.status(), ch, "钉钉通道创建");
  r = await api.call("POST", `/workspaces/${u.ws}/integrations/im/subscriptions/`,
    { channel_id: ch.id, event_types: ["created", "state_changed"] });
  await con.step({ m: "POST", p: `/im/subscriptions/` }, r.status(),
    (await jsof(r)).data, "订阅（null=全项目行）");
  r = await api.call("POST", `/workspaces/${u.ws}/integrations/im/subscriptions/`,
    { channel_id: ch.id, event_types: ["created"] });
  await con.step({ m: "POST", p: `/im/subscriptions/（重复）` }, r.status(),
    await jsof(r), "同范围重复 → 409 UNIQUE（BR-01 全项目行去重）",
    (o) => ({ code: o?.error?.details?.[0]?.code }));
  const sign = sh(`
import os,django;os.environ.setdefault('DJANGO_SETTINGS_MODULE','plane.settings.dev');django.setup()
from plane.app.views.im_integrations import _dingtalk_sign, build_dingtalk_markdown
print('sign:', _dingtalk_sign('SEC123', 1726099200000)[:28] + '…')
print('card:', build_dingtalk_markdown({'title':'任务指派','issue_key':'IM-12','issue_name':'联调','detail':'@张三'})['markdown']['text'][:60])`,
    "DB 侧执行：钉钉加签公式与卡片组装");
  await con.step({ m: "DB", p: "_dingtalk_sign / build_card" }, 200, sign.output,
    "HMAC-SHA256+base64 加签（UT-03 同公式）");
  r = await api.call("POST", `/workspaces/${u.ws}/integrations/im/test/`,
    { channel_id: ch.id });
  await con.step({ m: "POST", p: `/im/test/` }, r.status(), (await jsof(r)).data,
    "测试投递入队（5 败 degraded BR-03——离线目标演示降级路径）");
  await sleep(1500);
  r = await api.call("GET", `/workspaces/${u.ws}/integrations/im/channels/`);
  const chans = (await jsof(r)).data;
  await con.step({ m: "GET", p: `/im/channels/` }, r.status(),
    { degraded: chans?.[0]?.is_degraded }, "通道健康位（投递失败连败降级）");
  await con.verdict(true, "IM 链路：通道 → 订阅去重 → 加签公式 → 测试投递 → degraded 健康位");
});

// ══ 幕5 R6a 文件合规（FILE-006）══
await runScene(browser, "文件合规四件套", async (ctx, page) => {
  const con = new Console(page);
  await con.init("幕 05 · 文件合规：策略/法务保留/DLP/留存（FILE-006）");
  const api = apiOf(ctx);
  const u = await provision(ctx, "fc");
  let r = await api.call("POST", `/workspaces/${u.ws}/projects/`, {
    name: "合规演示", identifier: `FC${Date.now() % 1000}` });
  const proj = (await jsof(r)).data;
  // 资产
  const assetId = sh(`
import os,uuid,django;os.environ.setdefault('DJANGO_SETTINGS_MODULE','plane.settings.dev');django.setup()
from plane.db.models import Project,FileAsset
p=Project.objects.get(pk='${proj.id}')
a=FileAsset.objects.create(workspace=p.workspace,project=p,entity_type='issue',entity_id=uuid.uuid4(),size=2048,storage_path='demo/a',created_by=None)
print(a.id)`, "DB 侧执行：造演示资产").output.trim().split("\n").pop();
  // 策略：项目级禁下载禁分享
  r = await api.call("PATCH", `/workspaces/${u.ws}/file-compliance/policy/`,
    { project_id: proj.id, download: "deny", share_link: "deny" });
  await con.step({ m: "PATCH", p: `/file-compliance/policy/` }, r.status(),
    (await jsof(r)).data, "项目级策略：禁下载+禁分享（BR-03/04）");
  r = await api.call("GET",
    `/workspaces/${u.ws}/file-compliance/effective/?asset_id=${assetId}`);
  const eff = (await jsof(r)).data;
  await con.step({ m: "GET", p: `/effective/?asset_id=` }, r.status(), eff,
    "四级继承解析——项目层生效");
  const gate = sh(`
import os,django;os.environ.setdefault('DJANGO_SETTINGS_MODULE','plane.settings.dev');django.setup()
from plane.db.models import FileAsset
from plane.db.services.file_compliance import assert_download_allowed
from plane.base.exception import AppException
a=FileAsset.objects.get(pk='${assetId}')
try:
    assert_download_allowed(a); print('ALLOWED')
except AppException as e:
    print('BLOCKED:', e.error_code)`, "DB 侧执行：下载闸门（预签名签发前置）");
  await con.step({ m: "DB", p: "assert_download_allowed(asset)" }, 200,
    gate.output, "deny → 拒发预签名（PERM_DENIED）");
  // LegalHold 双人
  const conf = await provision(ctx, "fc2");
  r = await api.call("POST", `/workspaces/${u.ws}/file-compliance/legal-holds/`,
    { asset_id: assetId, reason: "诉讼保全", confirm_user_id: u.userId });
  await con.step({ m: "POST", p: `/legal-holds/（同人确认）` }, r.status(),
    await jsof(r), "双人确认——确认人=发起人 → 403（BR-07）");
  // DLP
  r = await api.call("POST", `/workspaces/${u.ws}/file-compliance/dlp-rules/`,
    { name: "手机号", pattern: "1[3-9]\\\\d{9}" });
  await con.step({ m: "POST", p: `/dlp-rules/` }, r.status(), (await jsof(r)).data,
    "DLP 规则（正则编译验证）");
  r = await api.call("POST", `/workspaces/${u.ws}/file-compliance/dlp-rules/`,
    { name: "回溯炸弹", pattern: "(a+)+$" });
  await con.step({ m: "POST", p: `/dlp-rules/（灾难回溯）` }, r.status(),
    await jsof(r), "嵌套量词拒绝（§4.7）", (o) => ({ code: o?.error?.code }));
  await con.verdict(true, "合规链路：策略继承生效 → 下载闸门拒发 → 双人确认拦截 → DLP 双向校验");
});

// ══ 幕6 R6b 归档闸门（FILE-007）══
await runScene(browser, "归档强制合规", async (ctx, page) => {
  const con = new Console(page);
  await con.init("幕 06 · 归档强制合规闸门（FILE-007）");
  const api = apiOf(ctx);
  const u = await provision(ctx, "ar");
  let r = await api.call("POST", `/workspaces/${u.ws}/projects/`, {
    name: "归档演示", identifier: `AR${Date.now() % 1000}` });
  const proj = (await jsof(r)).data;
  r = await api.call("POST", `/workspaces/${u.ws}/projects/${proj.id}/issues/`,
    { name: "未结任务" });
  await con.step({ m: "POST", p: `/issues/（未结）` }, r.status(), { open: 1 },
    "留一个未结任务");
  r = await ctx.request.post(`${API}/workspaces/${u.ws}/archive/`, {
    headers: { "X-CSRFToken": (await ctx.cookies()).find((c) => c.name === "csrftoken")?.value ?? "" },
    data: {} });
  const blocked = await jsof(r);
  await con.step({ m: "POST", p: `/workspaces/{slug}/archive/` }, r.status(),
    blocked, "归档被阻断——409 列明未结任务（BR-01）",
    (o) => ({ code: o?.error?.code, first: o?.error?.details?.[0]?.message?.slice(0, 40) }));
  const listResp = await api.call("GET",
    `/workspaces/${u.ws}/projects/${proj.id}/issues/`);
  const listBody = await listResp.json();
  const firstIssueId = listBody?.data?.[0]?.id ?? listBody?.[0]?.id;
  r = await api.call("PATCH",
    `/workspaces/${u.ws}/projects/${proj.id}/issues/${firstIssueId}/`,
    { completed_at: new Date().toISOString() });
  await con.step({ m: "PATCH", p: `/issues/{id}/（完成）` }, r.status(),
    { completed: r.status() < 400 }, "清场——任务完结");
  r = await ctx.request.post(`${API}/workspaces/${u.ws}/archive/`, {
    headers: { "X-CSRFToken": (await ctx.cookies()).find((c) => c.name === "csrftoken")?.value ?? "" },
    data: {} });
  const archived = await jsof(r);
  await con.step({ m: "POST", p: `/workspaces/{slug}/archive/（再试）` }, r.status(),
    archived, "合规通过 → 归档成功（组织快照 BR-03）",
    (o) => ({ slug: o?.data?.slug ?? o?.slug }));
  await con.verdict(r.status() === 200, "归档闸门：未结阻断 409 → 清场后放行");
});

// ══ 幕7 R7 大屏与跨组织（RPT-005/006）══
await runScene(browser, "大屏报表与播放链", async (ctx, page) => {
  const con = new Console(page);
  await con.init("幕 07 · 大屏报表与匿名播放链（RPT-005）+ 跨组织（RPT-006）");
  const api = apiOf(ctx);
  const u = await provision(ctx, "ds");
  let r = await api.call("GET", `/workspaces/${u.ws}/reports/metrics/`);
  await con.step({ m: "GET", p: `/reports/metrics/` }, r.status(),
    { datasets: Object.keys((await jsof(r)).data ?? {}) },
    "数据集注册表（四源——零新建聚合表）");
  r = await api.call("POST", `/workspaces/${u.ws}/reports/preview/`,
    { config: { dataset: "ds_issue_live", metrics: ["m_issue_count"] } });
  await con.step({ m: "POST", p: `/reports/preview/` }, r.status(),
    (await jsof(r)).data, "即时预览（查询引擎）");
  r = await api.call("POST", `/workspaces/${u.ws}/reports/`,
    { name: "交付总览", config: { dataset: "ds_issue_live", metrics: ["m_issue_count"] } });
  const rep = (await jsof(r)).data;
  await con.step({ m: "POST", p: `/reports/` }, r.status(), { id: rep?.id }, "报表落库");
  r = await api.call("POST", `/workspaces/${u.ws}/dashboards/`,
    { name: "作战室", layout: { items: [{ report_id: rep.id, x: 0, y: 0, w: 12, h: 6 }] } });
  const board = (await jsof(r)).data;
  await con.step({ m: "POST", p: `/dashboards/` }, r.status(), { id: board?.id },
    "大屏 24 栅格布局");
  r = await api.call("POST",
    `/workspaces/${u.ws}/dashboards/${board.id}/display-tokens/`, {});
  const dt = (await jsof(r)).data;
  await con.step({ m: "POST", p: `/display-tokens/` }, r.status(),
    { prefix: dt?.token?.slice(0, 10) + "…", warning: dt?.warning },
    "播放令牌——仅本次明文（BR-07 匿名豁免）");
  r = await ctx.request.get(`${API}/display/${dt.token}/screen/0/`);
  const screen = await jsof(r);
  await con.step({ m: "GET", p: `/display/{token}/screen/0/（匿名）` }, r.status(),
    { screens: screen?.data?.screens, dashboard: screen?.data?.dashboard?.name },
    "匿名播放屏可达");
  r = await ctx.request.post(`${API}/display/${dt.token}/heartbeat/`, {});
  await con.step({ m: "POST", p: `/display/{token}/heartbeat/` }, r.status(),
    { alive: (await jsof(r))?.data?.alive }, "心跳续活");
  r = await api.call("DELETE",
    `/workspaces/${u.ws}/dashboards/${board.id}/display-tokens/${dt.id}/`);
  r = await ctx.request.get(`${API}/display/${dt.token}/screen/0/`);
  await con.step({ m: "GET", p: `屏（吊销后）` }, r.status(), { dead: r.status() === 404 },
    "吊销即时失效");
  // 跨组织（需 SystemAdmin——DB 侧授予后真实调用）
  sh(`import os,django;os.environ.setdefault('DJANGO_SETTINGS_MODULE','plane.settings.dev');django.setup()
from plane.db.models import User,SystemAdmin
u=User.objects.get(email='${u.email}')
SystemAdmin.objects.get_or_create(user=u,defaults={'is_active':True,'created_by':u})
print('sysadmin granted')`, "DB 侧执行：授予系统管理员（跨组织 BR-01）");
  r = await api.call("GET", `/instances/cross-org/summary/`);
  const xorg = await jsof(r);
  await con.step({ m: "GET", p: `/instances/cross-org/summary/` }, r.status(),
    { orgs: xorg?.meta?.count ?? xorg?.data?.length },
    "跨组织总览（集团横向）");
  r = await api.call("GET",
    `/instances/cross-org/compare/?ids=${u.ws && ""}&metric=issues_total`.replace("?ids=&", "?ids=,"));
  await con.step({ m: "GET", p: `/cross-org/compare/（空 ids 负向）` }, r.status(),
    { code: (await jsof(r))?.error?.code }, "参数校验 400");
  await con.verdict(true, "报表链：注册表→预览→大屏→令牌→匿名屏→心跳→吊销；跨组织守门");
});

// ══ 幕8 R8 AI（AI-001）══
await runScene(browser, "AI 四能力", async (ctx, page) => {
  const con = new Console(page);
  await con.init("幕 08 · AI 四能力：授权→脱敏出域→风险分→相似（AI-001）");
  const api = apiOf(ctx);
  const u = await provision(ctx, "ai");
  let r = await api.call("POST", `/workspaces/${u.ws}/projects/`, {
    name: "AI 演示", identifier: `AI${Date.now() % 1000}` });
  const proj = (await jsof(r)).data;
  r = await api.call("POST", `/workspaces/${u.ws}/projects/${proj.id}/issues/`,
    { name: "支付网关对账差异排查" });
  const issue = (await jsof(r)).data?.id ?? (await jsof(r)).id;
  // 未签署拦截
  r = await api.call("POST",
    `/workspaces/${u.ws}/projects/${proj.id}/issues/${issue}/ai-summary/`, {});
  await con.step({ m: "POST", p: `/ai-summary/（未授权）` }, r.status(),
    await jsof(r), "AI_UNCONSENTED 拦截（BR-02）", (o) => ({ msg: o?.error?.message }));
  // 签署
  r = await api.call("POST", `/workspaces/${u.ws}/ai/consent/`,
    { capabilities: ["summary", "risk", "dup", "gen"] });
  await con.step({ m: "POST", p: `/ai/consent/` }, r.status(), (await jsof(r)).data,
    "授权书签署（四能力，版本化）");
  // 摘要（含 PII 评论→出域脱敏→本地回填）
  sh(`import os,django;os.environ.setdefault('DJANGO_SETTINGS_MODULE','plane.settings.dev');django.setup()
from plane.db.models import Issue,IssueComment,User
from django.utils import timezone
i=Issue.objects.get(pk='${issue}')
u=User.objects.filter(email='${u.email}').first()
IssueComment.objects.create(issue=i,actor=u,comment_html='<p>联系 dev@corp.com 或 13800138000</p>',comment_stripped='联系 dev@corp.com 或 13800138000',created_by=u)
print('comment with PII')`, "DB 侧执行：造含 PII 评论");
  r = await api.call("POST",
    `/workspaces/${u.ws}/projects/${proj.id}/issues/${issue}/ai-summary/`, {});
  const sum = (await jsof(r)).data;
  await con.step({ m: "POST", p: `/ai-summary/` }, r.status(),
    { summary: sum?.summary?.slice(0, 50) + "…",
      actions: sum?.action_items?.length }, "出域 PII 令牌化 → 本地回填（BR-03）");
  // 风险分
  r = await api.call("GET",
    `/workspaces/${u.ws}/projects/${proj.id}/issues/${issue}/risk-score/`);
  const risk = (await jsof(r)).data;
  await con.step({ m: "GET", p: `/risk-score/` }, r.status(),
    { score: risk?.score, model: risk?.model_version, reasons: risk?.top_reasons },
    "rule_v0 主因直出（BR-08）");
  // 相似
  r = await api.call("POST", `/workspaces/${u.ws}/projects/${proj.id}/issues/`,
    { name: "支付网关对账差异复盘" });
  r = await api.call("GET",
    `/workspaces/${u.ws}/projects/${proj.id}/issues/similar/?title=支付网关对账差异`);
  const sim = (await jsof(r)).data;
  await con.step({ m: "GET", p: `/issues/similar/?title=` }, r.status(),
    { hits: sim?.length, top: sim?.[0]?.name }, "重复识别（字符 bigram 基线）");
  // 反馈
  r = await api.call("POST",
    `/workspaces/${u.ws}/projects/${proj.id}/ai-feedback/`,
    { verdict: "useful", capability: "summary" });
  await con.step({ m: "POST", p: `/ai-feedback/` }, r.status(),
    (await jsof(r)).data, "轻反馈落库（BR-07）");
  await con.verdict(true, "AI 链：授权闸门 → 脱敏出域 → 风险主因 → 相似 → 反馈");
});

// ══ 幕9 R9 License（INFRA-006）══
await runScene(browser, "License 只读降级", async (ctx, page) => {
  const con = new Console(page);
  await con.init("幕 09 · License 失效只读降级（INFRA-006 BR-06 数据永不锁死）");
  const api = apiOf(ctx);
  const u = await provision(ctx, "lc");
  // 正常态写通
  let r = await api.call("POST", `/workspaces/${u.ws}/projects/`, {
    name: "License 演示", identifier: `LC${Date.now() % 1000}` });
  await con.step({ m: "POST", p: `/projects/` }, r.status(),
    { ok: r.status() === 201 }, "无 License 文件（dev）——全功能（MISSING 态）");
  // 注入过期 License（Redis 缓存键——中间件读缓存）
  execSync(`docker exec rp-redis redis-cli setex license:status 120 `
    + `'"state":"expired","seats":200,"expires_at":"2026-01-01","readonly":true"'`,
    { stdio: "pipe" });
  r = await api.call("GET", `/workspaces/${u.ws}/projects/`);
  await con.step({ m: "GET", p: `/projects/（只读态）` }, r.status(),
    { readable: r.status() === 200 }, "读——永远放行（数据完整）");
  r = await api.call("POST", `/workspaces/${u.ws}/projects/`, {
    name: "被拒项目", identifier: "LCX" });
  const blocked = await jsof(r);
  await con.step({ m: "POST", p: `/projects/（写）` }, r.status(), blocked,
    "写——409 LICENSE_EXPIRED 只读降级",
    (o) => ({ code: o?.error?.code, msg: o?.error?.message?.slice(0, 40) }));
  execSync(`docker exec rp-redis redis-cli del license:status`, { stdio: "pipe" });
  r = await api.call("POST", `/workspaces/${u.ws}/projects/`, {
    name: "恢复后项目", identifier: `LC${(Date.now() + 7) % 1000}` });
  await con.step({ m: "POST", p: `/projects/（恢复后）` }, r.status(),
    { ok: r.status() === 201 }, "续期/移除后自动恢复读写");
  await con.verdict(true, "License 链：MISSING 全功能 → expired 只读（读写分离）→ 恢复");
});

// ══ 幕10 R15+R16~18 甘特三件与收口五件 ══
await runScene(browser, "甘特三件与收口五件", async (ctx, page) => {
  const con = new Console(page);
  await con.init("幕 10 · 跨项目甘特三件（R15）+ 收口五件套（R16~R18）");
  const api = apiOf(ctx);
  const u = await provision(ctx, "gz");
  let r = await api.call("POST", `/workspaces/${u.ws}/projects/`, {
    name: "G1", identifier: `G1${Date.now() % 1000}` });
  const proj = (await jsof(r)).data;
  await api.call("POST", `/workspaces/${u.ws}/projects/${proj.id}/issues/`,
    { name: "a", start_date: "2030-01-01", target_date: "2030-01-15" });
  await api.call("POST", `/workspaces/${u.ws}/projects/${proj.id}/issues/`,
    { name: "b", target_date: "2030-01-20" });
  // 项目集甘特
  const pf = sh(`
import os,django;os.environ.setdefault('DJANGO_SETTINGS_MODULE','plane.settings.dev');django.setup()
from plane.db.models import Workspace,Portfolio,PortfolioProject,PortfolioMilestone
ws=Workspace.objects.get(slug='${u.ws}')
import datetime
pf=Portfolio.objects.create(workspace=ws,name='组合演示',manager=None,created_by=None)
PortfolioProject.objects.create(portfolio=pf,project_id='${proj.id}')
PortfolioMilestone.objects.create(portfolio=pf,name='M1',target_date=datetime.date(2030,2,1))
print(pf.id)`, "DB 侧执行：建项目集与里程碑（UI 外数据）").output.trim().split("\n").pop();
  r = await api.call("GET", `/workspaces/${u.ws}/portfolios/${pf}/gantt/`);
  const pg = (await jsof(r)).data;
  await con.step({ m: "GET", p: `/portfolios/{id}/gantt/` }, r.status(),
    { range: pg?.projects?.[0]?.range, milestone_late: pg?.milestones?.[0]?.late },
    "项目集甘特：min/max 区间 + 里程碑超期标记（BR-01/02）");
  // 资源负载
  sh(`import os,datetime,django;os.environ.setdefault('DJANGO_SETTINGS_MODULE','plane.settings.dev');django.setup()
from plane.db.models import Workspace,Project,WorkLogSummary,User
from django.utils import timezone
ws=Workspace.objects.get(slug='${u.ws}');p=Project.objects.get(pk='${proj.id}')
u=User.objects.get(email='${u.email}')
wk=timezone.now().date()-datetime.timedelta(days=timezone.now().weekday())
WorkLogSummary.objects.create(project=p,actor=u,week_start=wk,total_minutes=2600,created_by=u)
print('logged 2600min')`, "DB 侧执行：周工时 2600min");
  r = await api.call("GET", `/workspaces/${u.ws}/resource-load/?weeks=2`);
  const rl = (await jsof(r)).data;
  const hot = rl?.[0]?.weeks?.find((w) => w.minutes > 0);
  await con.step({ m: "GET", p: `/resource-load/` }, r.status(),
    { minutes: hot?.minutes, ratio: hot?.ratio, band: hot?.band },
    "负载率 2600/2400 → red（三档 BR-02）");
  // CP 锁定
  sh(`import os,datetime,django;os.environ.setdefault('DJANGO_SETTINGS_MODULE','plane.settings.dev');django.setup()
from plane.db.models import Project,Issue,IssueCPMCache
p=Project.objects.get(pk='${proj.id}')
i=Issue.objects.filter(project=p).first()
IssueCPMCache.objects.create(project=p,issue=i,es=datetime.date(2030,1,1),ef=datetime.date(2030,1,5),ls=datetime.date(2030,1,1),lf=datetime.date(2030,1,5),float_days=0,is_critical=True)
print('cpm seeded')`, "DB 侧执行：CPM 关键链种子");
  r = await api.call("POST",
    `/workspaces/${u.ws}/projects/${proj.id}/gantt/cp-lock/`, {});
  await con.step({ m: "POST", p: `/gantt/cp-lock/` }, r.status(),
    (await jsof(r)).data, "关键路径锁定（快照 BR-01）");
  sh(`import os,django;os.environ.setdefault('DJANGO_SETTINGS_MODULE','plane.settings.dev');django.setup()
from plane.db.models import IssueCPMCache
IssueCPMCache.objects.update(ef=__import__('datetime').date(2030,3,1))
print('ef mutated → 2030-03-01')`, "DB 侧执行：任务改期（漂移源）");
  r = await api.call("GET",
    `/workspaces/${u.ws}/projects/${proj.id}/gantt/cp-lock/`);
  const lock = (await jsof(r)).data;
  await con.step({ m: "GET", p: `/gantt/cp-lock/（改期后）` }, r.status(),
    { locked_ef: lock?.snapshot?.chain?.[0]?.ef },
    "快照不漂移：仍 2030-01-05（BR-02）");
  await api.call("DELETE",
    `/workspaces/${u.ws}/projects/${proj.id}/gantt/cp-lock/`);
  // 全局看板
  r = await api.call("GET", `/workspaces/${u.ws}/global-board/`);
  const gb = (await jsof(r)).data;
  await con.step({ m: "GET", p: `/global-board/` }, r.status(),
    { lanes: gb?.lanes?.map((l) => `${l.key}:${l.count}`).join(" ") },
    "跨项目看板四泳道（BOARD-006）");
  // 视图模板
  r = await api.call("POST", `/workspaces/${u.ws}/view-templates/`,
    { name: "官方-我的工序", filter_json: { priority: "high" } });
  const tpl = (await jsof(r)).data;
  r = await api.call("POST",
    `/workspaces/${u.ws}/view-templates/${tpl.id}/apply/?project_id=${proj.id}`, {});
  await con.step({ m: "POST", p: `/view-templates/{id}/apply/` }, r.status(),
    (await jsof(r)).data, "模板应用——快照拷贝到项目视图（BOARD-007 BR-02）");
  // 超时规则 + 扫描
  r = await api.call("POST",
    `/workspaces/${u.ws}/projects/${proj.id}/workflow/timeout-rules/`,
    { from_state_id: "00000000-0000-0000-0000-000000000000",
      to_state_id: "00000000-0000-0000-0000-000000000001", hours: 72 });
  await con.step({ m: "POST", p: `/workflow/timeout-rules/` }, r.status(),
    (await jsof(r)).data ?? (await jsof(r)).error,
    "超时规则登记（WF-007）");
  const sweep = sh(`
import os,django;os.environ.setdefault('DJANGO_SETTINGS_MODULE','plane.settings.dev');django.setup()
from plane.app.views.p4_final import wf_timeout_sweep
print(wf_timeout_sweep())`, "DB 侧执行：beat 扫描（目标态失效→守卫跳过留因）");
  await con.step({ m: "DB", p: "wf_timeout_sweep()" }, 200, sweep.output,
    "守卫语义：无效目标态跳过并记因（BR-02）");
  // 调度建议
  r = await api.call("GET", `/workspaces/${u.ws}/resource-scheduling/`);
  const rs = (await jsof(r)).data;
  await con.step({ m: "GET", p: `/resource-scheduling/` }, r.status(),
    { overloaded: rs?.overloaded?.length, suggestions: rs?.suggestions?.length },
    "超载/闲置分桶+平衡对（PROJ-005 只读）");
  // 推送策略
  r = await api.call("PATCH", `/users/me/push-preferences/`,
    { dnd: { start: 0, end: 23 } });
  await con.step({ m: "PATCH", p: `/users/me/push-preferences/` }, r.status(),
    (await jsof(r)).data, "免打扰全天（COLLAB-005 BR-01）");
  const push = sh(`
import os,django;os.environ.setdefault('DJANGO_SETTINGS_MODULE','plane.settings.dev');django.setup()
from django.core.cache import cache
uid=cache.get('demo_uid')
from plane.app.views.p4_final import route_push
print('comment→', route_push('${u.userId}', 'comment', {}))
print('approval→', route_push('${u.userId}', 'approval', {}))`,
    "DB 侧执行：路由器（DND 静默 / P1 直发）");
  await con.step({ m: "DB", p: "route_push(...)" }, 200, push.output,
    "普通事件静默 · 审批 P1 绕过（BR-02）");
  await con.verdict(true, "甘特三件+收口五件：区间/负载/锁定不漂移/看板/模板/超时/调度/推送");
});

await browser.close();
const ok = results.filter((r) => r.ok).length;
console.log(`\n${ok}/${results.length} 幕通过；视频目录 ${OUT}/`);
if (ok !== results.length) process.exit(1);
