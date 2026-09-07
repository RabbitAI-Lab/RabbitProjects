#!/usr/bin/env node
/**
 * Sprint-6 验收录屏（六幕；终端幕=真实命令输出渲染，浏览器幕=admin 实操作）。
 *
 * 用法：node scripts/acceptance_video_s6.mjs        # 全部
 *       ONLY=发布门禁 node scripts/acceptance_video_s6.mjs
 * 前置：API 8000（全量 env）+ admin dev 3002 + rp-pg/rp-minio 容器。
 * 输出：docs/sprint-6-acceptance/videos/scene-XX-<名称>.webm（永不在库）。
 */
import { chromium } from "@playwright/test";
import { execSync, spawn } from "node:child_process";
import { mkdirSync, renameSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const OUT = "docs/sprint-6-acceptance/videos";
const ADMIN = "http://localhost:3002/god-mode";
mkdirSync(OUT, { recursive: true });

const results = [];
let sceneNo = 0;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const ONLY = (process.env.ONLY ?? "").split(",").filter(Boolean);

/** 真实执行命令并取完整输出（终端幕的数据源——绝不手写结果）。 */
function run(cmd, opts = {}) {
  try {
    return execSync(cmd, { encoding: "utf8", timeout: 600_000, ...opts });
  } catch (e) {
    return `${e.stdout ?? ""}\n[exit ${e.status}] ${e.stderr ?? e.message}`.trim();
  }
}

/** 终端幕：黑底等宽逐行回放真实输出（打字机节奏）。 */
async function termScene(page, title, subtitle, text, { maxLines = 40 } = {}) {
  const lines = text.split("\n").slice(-maxLines);
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
    await sleep(line.trim() ? 220 : 90);
  }
  await sleep(900);
}

async function runScene(browser, name, fn) {
  sceneNo += 1;
  if (ONLY.length && !ONLY.some((k) => name.includes(k))) {
    console.log(`⊘ 幕${String(sceneNo).padStart(2, "0")} ${name}（ONLY 跳过）`);
    return;
  }
  const id = String(sceneNo).padStart(2, "0");
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    recordVideo: { dir: OUT, size: { width: 1440, height: 900 } },
  });
  const page = await ctx.newPage();
  const t0 = Date.now();
  let ok = true, err = "";
  try { await fn(page); }
  catch (e) { ok = false; err = String(e).split("\n")[0].slice(0, 160); }
  const video = page.video();          // s5 同款：page.video() 句柄在 ctx.close 后 path() 可用
  await ctx.close();
  if (video) renameSync(await video.path(), join(OUT, `scene-${id}-${name}.webm`));
  results.push({ id, name, ok, err, sec: ((Date.now() - t0) / 1000).toFixed(0) });
  console.log(`${ok ? "✓" : "✗"} 幕${id} ${name} ${((Date.now() - t0) / 1000).toFixed(0)}s${err ? ` ← ${err}` : ""}`);
}

const browser = await chromium.launch();

// ── 幕 01：限流矩阵（8001 临时 RATE_LIMIT_ENABLED=1 栈，真实跑矩阵脚本）──
await runScene(browser, "限流矩阵", async (page) => {
  const killer = spawn("bash", ["-c",
    `cd apps/api && DATABASE_URL="postgresql://rp:rp@localhost:5432/rabbit_projects" SECRET_KEY=dev \
     REDIS_URL=redis://localhost:6379/0 CELERY_BROKER_URL=amqp://guest:guest@localhost:5672/ \
     RATE_LIMIT_ENABLED=1 nohup uv run python manage.py runserver 0.0.0.0:8001 --noreload > /tmp/rp-s6-8001.log 2>&1 & echo $!`]);
  const pid = parseInt((await new Promise((r) => killer.stdout.once("data", (d) => r(d.toString())))).trim(), 10);
  await sleep(4000);
  const out = run("uv run --project apps/api python tests/jmeter/sprint-6-ratelimit-matrix.py http://localhost:8001");
  try { process.kill(pid); } catch {}
  await termScene(page, "限流矩阵逐端点（L2 门控栈 :8001）",
    "python3 tests/jmeter/sprint-6-ratelimit-matrix.py http://localhost:8001", out);
  if (!out.includes("8 通过")) throw new Error("矩阵未全过");
});

// ── 幕 02：L1 边缘三区（nginx 严格变体容器实测）──
await runScene(browser, "L1边缘三区", async (page) => {
  // 落临时脚本执行（嵌套引号地狱规避——内容与 T6-02 实测同源）
  const { writeFileSync, rmSync } = await import("node:fs");
  const blast = `#!/usr/bin/env bash
set -u
docker network create rp-l1-v >/dev/null 2>&1
cat > /tmp/rp-l1-stub.py <<'PY'
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b'ok')
    def log_message(self, *a): pass
HTTPServer(('0.0.0.0', 9001), H).serve_forever()
PY
docker run -d --rm --name rp-l1-stub-v --network rp-l1-v \
  -v /tmp/rp-l1-stub.py:/srv/s.py:ro python:3.12-alpine python /srv/s.py >/dev/null
sleep 1
sed 's/rp-l1-stub:9001/rp-l1-stub-v:9001/g' /tmp/rp-l1test/stub.conf > /tmp/rp-l1test/stub-v.conf
docker run -d --rm --name rp-l1-ngx-v --network rp-l1-v -p 18091:80 \
  -v /tmp/rp-l1test/nginx-strict.conf:/etc/nginx/nginx.conf:ro \
  -v /tmp/rp-l1test/stub-v.conf:/etc/nginx/snippets/upstreams.conf:ro \
  -v /tmp/rp-l1test/common.conf:/etc/nginx/snippets/common.conf:ro nginx:1.27-alpine >/dev/null
sleep 1
codes=$(for i in $(seq 1 100); do
  code=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:18091/api/v1/workspaces/)
  echo "$code"
  if [ "$code" = "429" ] && [ ! -s /tmp/rp-l1-429.txt ]; then
    curl -s -i http://localhost:18091/api/v1/workspaces/ > /tmp/rp-l1-429.txt
  fi
done)
echo "100 连发：200=$(echo "$codes" | grep -c 200)  429=$(echo "$codes" | grep -c 429)"
echo "--- 网关 429 体与头（连发中抓取）---"
head -11 /tmp/rp-l1-429.txt
echo "--- health 豁免（BR-05）---"
h=$(for i in $(seq 1 20); do curl -s -o /dev/null -w "%{http_code}\n" http://localhost:18091/api/v1/health/; done | grep -c 200)
echo "health 200 计数：$h/20"
docker rm -f rp-l1-ngx-v rp-l1-stub-v >/dev/null 2>&1
docker network rm rp-l1-v >/dev/null 2>&1
exit 0`;
  writeFileSync("/tmp/rp-l1-blast.sh", blast);
  const out = run("bash /tmp/rp-l1-blast.sh 2>&1");
  rmSync("/tmp/rp-l1-blast.sh", { force: true });
  await termScene(page, "L1 边缘限流三区（nginx 实测）", "100 连发 /api/ → burst 60 + 429；health 豁免", out);
  if (!out.includes("429=") || !out.includes("RATE_LIMIT_EXCEEDED"))
    throw new Error(`L1 输出缺关键证据：${out.slice(0, 200).replace(/\n/g, " | ")}`);
});

// ── 幕 03：备份全链 + preflight ──
await runScene(browser, "备份全链", async (page) => {
  const backup = run("deploy/scripts/backup-now.sh 2>&1 | tail -3");
  const pre = run("deploy/scripts/preflight.sh 2>&1 | tail -9");
  const bucket = run(`docker exec rp-minio sh -c 'mc alias set local http://localhost:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null && mc ls --recursive local/rp-backups/ | tail -4'`);
  await termScene(page, "备份全链（三校验 + 配置快照随行）",
    "deploy/scripts/backup-now.sh && deploy/scripts/preflight.sh",
    `${backup}\n\n$ deploy/scripts/preflight.sh\n${pre}\n\n$ mc ls rp-backups/\n${bucket}`);
  if (!backup.includes("BACKUP_OK") || !pre.includes("ALL-OK")) throw new Error("备份链未全过");
});

// ── 幕 04：恢复演练（真实重跑，RTO + 冒烟）──
await runScene(browser, "恢复演练", async (page) => {
  const out = run("deploy/scripts/restore-drill.sh --mode local 2>&1 | tail -10");
  await termScene(page, "恢复演练（隔离库即弃，BR-09：RTO ≤30min）",
    "deploy/scripts/restore-drill.sh --mode local", out);
  if (!out.includes("SMOKE 18/18") || !out.includes("PASS")) throw new Error("演练未 PASS");
});

// ── 幕 05：admin 运维台实操作（浏览器）──
await runScene(browser, "运维台发布门禁", async (page) => {
  // 会话（ops-parity 系统管理员）
  const csrf = await (await page.request.get("http://localhost:8000/api/v1/auth/csrf-token/")).json();
  await page.request.post("http://localhost:8000/api/v1/auth/sign-in/", {
    headers: { "X-CSRFToken": csrf.data.csrf_token, "Content-Type": "application/json" },
    data: { email: "ops-parity@rabbit.dev", password: "Rabbit123!" },
  });
  // 限流监控页
  await page.goto(`${ADMIN}/ops`);
  await page.getByRole("heading", { name: "限流监控" }).waitFor({ timeout: 8000 });
  await sleep(2600);
  // 备份管理页（真实记录列表 + 文案）
  await page.goto(`${ADMIN}/ops/backups`);
  await page.locator('[data-sb-scope="backups-table"]').waitFor({ timeout: 8000 });
  await sleep(2800);
  // 发布门禁页（创建→签署→反签→裁决守卫→时间线）
  await page.goto(`${ADMIN}/ops/release`);
  await page.getByRole("heading", { name: "发布门禁" }).waitFor({ timeout: 8000 });
  await sleep(1500);
  const createBtn = page.locator('[data-sb-scope="release-create-btn"]');
  if (await createBtn.isVisible()) {
    await Promise.all([
      page.waitForResponse((r) => r.url().includes("/instances/release-gates/create/")),
      createBtn.click(),
    ]);
  }
  await page.locator('[data-sb-scope="release-checklist"]').waitFor({ timeout: 8000 });
  await sleep(1600);
  // 签署一项（真实 POST 200）
  const firstSign = page.locator('[data-sb-scope="release-cl-row"] button').first();
  await Promise.all([
    page.waitForResponse((r) => r.url().includes("/sign/") && r.request().method() === "POST"),
    firstSign.click(),
  ]);
  await sleep(1400);
  // 裁决放行 → BLOCKED_BY_GATE 红字（守卫演示）
  await page.locator('[data-sb-scope="release-verdict-actions"] button').first().click();
  await page.locator('[data-sb-scope="release-msg"]').waitFor({ timeout: 6000 });
  await sleep(2000);
  // 事件时间线展开
  await page.locator('[data-sb-scope="release-events"] summary').click();
  await sleep(2200);
});

// ── 幕 06：安全门禁 + 越权 64 格 ──
await runScene(browser, "安全门禁64格", async (page) => {
  // verdict：清洁报告 PASS + CRITICAL 拒（临时报告）
  const clean = `import json,sys,subprocess,tempfile,pathlib
d = pathlib.Path(tempfile.mkdtemp())
json.dump({"dependencies":[{"name":"django","vulns":[]}]}, open(d/"pip-audit.json","w"))
r = subprocess.run(["python3","ci/security_verdict.py"], capture_output=True, text=True, cwd="${process.cwd()}")
print(r.stdout.strip())`;
  const verdictOut = run(`python3 -c '${clean.replace(/'/g, "'\\\\''")}'`);
  const matrix = run("cd apps/api && DATABASE_URL=postgresql://rp:rp@localhost:5432/rabbit_projects SECRET_KEY=dev uv run --project . pytest tests/test_auth006.py -q -k matrix_64 2>&1 | tail -2");
  await termScene(page, "安全门禁四源判定 + 越权矩阵 64 格",
    "python3 ci/security_verdict.py  &&  pytest -k matrix_64",
    `$ security_verdict（清洁报告）\n${verdictOut}\n\n$ pytest IT-SEC 矩阵（四主体×四资源×四动作）\n${matrix}`);
  if (!verdictOut.includes("PASS") || !matrix.includes("64 passed")) throw new Error("安全幕证据不足");
});

await browser.close();
console.log("\n══ Sprint-6 验收录制 ══");
for (const r of results) console.log(`${r.ok ? "✓" : "✗"} scene-${r.id} ${r.name} ${r.sec}s${r.err ? " ← " + r.err : ""}`);
process.exit(results.every((r) => r.ok) ? 0 : 1);
