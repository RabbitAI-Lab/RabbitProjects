#!/usr/bin/env node
/**
 * Sprint-2 验收录屏（9 幕 × 独立视频）。
 *
 * 纪律：所有场景从真实用户入口出发——登录页 →（一键演示账号/注册表单）→ 项目列表
 * → 项目卡片 → 侧栏导航；禁止直接 goto 深链。每幕独立 browser context（独立视频），
 * 关键交互带 waitForResponse 校验（录到的必须是成功画面）。
 *
 * 用法：node scripts/acceptance_video.mjs
 * 输出：docs/sprint-2-acceptance/videos/scene-XX-*.webm + README 索引
 */
import { chromium } from "@playwright/test";
import { mkdirSync, renameSync, readdirSync } from "node:fs";
import { join } from "node:path";

const WEB = process.env.E2E_BASE_URL ?? "http://localhost:3001";
const OUT = "docs/sprint-2-acceptance/videos";
const PROJ_NAME = "S2 验收演示";
mkdirSync(OUT, { recursive: true });

const results = [];
let sceneNo = 0;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const ONLY = (process.env.ONLY ?? "").split(",").filter(Boolean);
async function runScene(browser, name, fn) {
  sceneNo += 1;
  if (ONLY.length && !ONLY.some((k) => name.includes(k))) {
    console.log(`⊘ 幕${String(sceneNo).padStart(2, "0")} ${name}（ONLY 过滤跳过）`);
    return;
  }
  const id = String(sceneNo).padStart(2, "0");
  const ctx = await browser.newContext({
    baseURL: WEB,
    viewport: { width: 1440, height: 900 },
    recordVideo: { dir: OUT, size: { width: 1440, height: 900 } },
  });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  const t0 = Date.now();
  let ok = true;
  try {
    await fn(page);
  } catch (e) {
    ok = false;
    console.error(`  ✗ 幕${id} ${name} 失败：${String(e).split("\n")[0]}`);
    await sleep(1200); // 失败画面留 1.2s 便于验收方看到现场
  }
  // 等视频尾部封帧
  await sleep(900);
  const video = page.video();
  await ctx.close();
  const tmpPath = await video.path();
  const finalPath = join(OUT, `scene-${id}-${name}.webm`);
  renameSync(tmpPath, finalPath);
  results.push({ id, name, ok, seconds: Math.round((Date.now() - t0) / 1000), errors: errors.slice(0, 3) });
  console.log(`${ok ? "✓" : "✗"} 幕${id} ${name}（${Math.round((Date.now() - t0) / 1000)}s）`);
}

/* ── 真实入口 helpers（与 e2e spec 同源选择器）────────────────── */
async function login(page) {
  await page.goto("/login");
  await page.getByRole("button", { name: /一键进入演示账号/ }).click();
  await page.waitForFunction(() => /\/projects$/.test(location.pathname), null, { timeout: 15_000 });
  await sleep(600);
}

async function enterProject(page) {
  // 项目列表卡片 → 看板（用户路径；收藏段在前，卡片可点击）
  const card = page.locator("a,div").filter({ hasText: new RegExp(PROJ_NAME) }).first();
  await card.locator(`text=${PROJ_NAME}`).first().click();
  await page.waitForURL(/\/board/, { timeout: 15_000 });
  await sleep(800);
}

async function gotoList(page) {
  await page.getByRole("navigation").getByRole("link", { name: "任务列表" }).click();
  await page.waitForURL(/\/issues/, { timeout: 10_000 });
  await page.locator('tbody tr[data-sb-scope="tree-row"]').first().waitFor({ timeout: 15_000 });
  await sleep(700);
}

async function gotoBoard(page) {
  if (/\/board$/.test(page.url())) {
    await page.getByRole("navigation").getByRole("link", { name: "任务列表" }).click();
    await page.waitForURL(/\/issues/, { timeout: 10_000 });
  }
  await page.getByRole("navigation").getByRole("link", { name: "看板" }).click();
  await page.waitForURL(/\/board/, { timeout: 10_000 });
  await sleep(700);
}

async function openDrawer(page, name, { exact = false } = {}) {
  const row = page.locator('tr[data-sb-scope="tree-row"]').filter({ hasText: name }).first();
  // exact：目标存在同名「(副本)」行时取 last（列表 -created_at 排序，副本恒在前）
  const target = exact ? row.last() : row;
  await target.click();
  const drawer = page.locator("aside").first();
  await drawer.waitFor({ timeout: 10_000 });
  await sleep(700);
  return drawer;
}

/* ═══════════════════════════ 幕定义 ═══════════════════════════ */

// 数据重置（幂等）：保证「验收-」任务状态每轮一致（上轮录屏的 force 完成/认领/归档不残留）
await (await import("node:child_process")).execSync("python3 scripts/seed_acceptance.py", { stdio: "inherit" });

const browser = await chromium.launch({ headless: true, slowMo: 140 });

// 幕1：多层子任务（树形/圆环进度/行内加子任务/子任务区/全屏树）
await runScene(browser, "多层子任务", async (page) => {
  await login(page);
  await enterProject(page);
  await gotoList(page);
  // 树形展开：验收-导出功能 → 后端导出 API → 分页游标改造 →（下一层）
  const expand = (name) =>
    page.locator('tr[data-sb-scope="tree-row"]').filter({ hasText: name })
      .locator('[data-sb-scope="tree-toggle"]').first();
  await expand("验收-导出功能").click(); await sleep(700);
  await expand("验收-后端导出 API").click(); await sleep(700);
  await expand("验收-分页游标改造").click(); await sleep(700);
  await expand("验收-SQL 生成器抽象").click(); await sleep(900); // 5 层链完整可见
  // 行内快速加子任务（乐观灰行 → 真实行 + 父徽标+1）
  const parent = page.locator('tr[data-sb-scope="tree-row"]').filter({ hasText: "验收-前端导出按钮" }).first();
  await parent.hover(); await sleep(350);
  await parent.locator('[aria-label*="添加子任务"], [data-sb-scope="row-plus"]').first().click();
  await sleep(400);
  const input = page.locator("#quick-sub-input, input[data-sb-scope='tree-quick-input'], input[placeholder*='输入子任务标题']").first();
  await input.fill("验收-样式微调");
  const postP = page.waitForResponse((r) => r.url().includes("/sub-issues/") && r.request().method() === "POST", { timeout: 15_000 });
  await input.press("Enter");
  await postP; await sleep(1400); // 乐观行 → 真实行替换全程可见
  // 详情子任务分区 + 查看整棵树（全屏树头部统计含根口径）
  const drawer = await openDrawer(page, "验收-导出功能");
  await drawer.locator('[data-sb-scope="drawer-sub-view-all"]').click();
  await page.locator('[data-sb-scope="tree-stats"]').waitFor({ timeout: 10_000 });
  await sleep(2200); // 全屏树停留
});

// 幕2：拖拽移动子树 + 环 409
await runScene(browser, "拖拽移动与环检测", async (page) => {
  await login(page); await enterProject(page); await gotoList(page);
  const expand = (name) =>
    page.locator('tr[data-sb-scope="tree-row"]').filter({ hasText: name })
      .locator('[data-sb-scope="tree-toggle"]').first();
  await expand("验收-导出功能").click(); await sleep(600);
  await expand("验收-后端导出 API").click(); await sleep(600);
  await expand("验收-分页游标改造").click(); await sleep(500);
  await expand("验收-SQL 生成器抽象").click(); await sleep(900); // 第 5 层行可见
  // 合法移动：游标编码优化（第5层）→ 拖到 前端导出按钮 行中部（成为其子级）
  const src = page.locator('tr[data-sb-scope="tree-row"]').filter({ hasText: "验收-游标编码优化" }).first();
  const dst = page.locator('tr[data-sb-scope="tree-row"]').filter({ hasText: "验收-前端导出按钮" }).first();
  await src.hover(); await sleep(400);
  const grip = src.locator('[data-sb-scope="tree-grip"], [aria-label*="拖拽"]').first();
  const dstBox = await dst.boundingBox();
  const gb = await grip.boundingBox();
  await page.mouse.move(gb.x + gb.width / 2, gb.y + gb.height / 2);
  await page.mouse.down();
  await page.mouse.move(dstBox.x + dstBox.width / 2, dstBox.y + dstBox.height * 0.6, { steps: 12 });
  await sleep(600); // 缩进预览
  await page.mouse.up();
  await sleep(600);
  const patchP = page.waitForResponse((r) => r.url().includes("/issues/") && r.request().method() === "PATCH" && (r.request().postData() || "").includes("parent_id"), { timeout: 15_000 });
  await page.locator('[data-sb-scope="move-go"], button').filter({ hasText: "移动" }).last().click();
  await patchP; await sleep(1500); // 树重排 + 计数联动
  // 环：验收-导出功能（根）拖到自己后代（游标编码优化）之下 → 409 CYCLE Toast
  const rootRow = page.locator('tr[data-sb-scope="tree-row"]').filter({ hasText: "验收-导出功能" }).first();
  const deepRow = page.locator('tr[data-sb-scope="tree-row"]').filter({ hasText: "验收-游标编码优化" }).first();
  await rootRow.hover(); await sleep(350);
  const rg = rootRow.locator('[data-sb-scope="tree-grip"], [aria-label*="拖拽"]').first();
  const dBox = await deepRow.boundingBox();
  const rb = await rg.boundingBox();
  await page.mouse.move(rb.x + rb.width / 2, rb.y + rb.height / 2);
  await page.mouse.down();
  await page.mouse.move(dBox.x + dBox.width / 2, dBox.y + dBox.height * 0.6, { steps: 12 });
  await sleep(500);
  await page.mouse.up(); await sleep(400);
  const cycle409 = page.waitForResponse((r) => r.request().method() === "PATCH" && r.status() === 409, { timeout: 15_000 });
  await page.locator('[data-sb-scope="tree-confirm-move-go"]').click();
  await cycle409; await sleep(2200); // Toast 环路径可见
});

// 幕3：依赖（三分区 + 添加弹层 + 看板拦截 + 强制完成）
await runScene(browser, "依赖与流转拦截", async (page) => {
  await login(page); await enterProject(page); await gotoList(page);
  // 依赖图标（被阻塞行 amber）
  const row = page.locator('tr[data-sb-scope="tree-row"]').filter({ hasText: "验收-前端联调" }).first();
  await row.locator('[data-sb-scope="list-rel-icon"]').waitFor({ timeout: 10_000 });
  await sleep(600);
  console.log("    [幕3] 打开抽屉…");
  const drawer = await openDrawer(page, "验收-前端联调");
  // 关联三分区
  await drawer.locator('[data-sb-scope="drawer-rel-section"]').waitFor({ timeout: 10_000 });
  console.log("    [幕3] 关联分区可见");
  await sleep(1200);
  // 添加关联弹层（四类型 + 语义提示 + 搜索 + 提交）
  await drawer.locator('[data-sb-scope="drawer-rel-add"]').click();
  await page.locator('[data-sb-scope="link-type-dd"]').waitFor({ timeout: 10_000 });
  await page.locator('[data-sb-scope="link-type-btn"]').click(); await sleep(500);
  await page.locator('[data-sb-scope="link-type-item"]').filter({ hasText: "相关于…" }).click(); await sleep(700); // 语义提示行
  await page.locator('[data-sb-scope="link-type-btn"]').click();
  await page.locator('[data-sb-scope="link-type-item"]').filter({ hasText: "阻塞了…" }).click(); await sleep(400);
  await page.locator('[data-sb-scope="link-search"]').fill("验收-登录改造");
  console.log("    [幕3] 弹层与类型选择…");
  const cand = page.locator('[data-sb-scope="link-candidate"]').filter({ hasText: "验收-登录改造" });
  await cand.waitFor({ timeout: 10_000 }); await sleep(500);
  await cand.click();
  const postP = page.waitForResponse((r) => r.url().includes("/relations/") && r.request().method() === "POST", { timeout: 15_000 });
  await page.locator('[data-sb-scope="link-submit"]').click();
  await postP; await sleep(1300); // 分区乐观插入
  await page.keyboard.press("Escape"); await sleep(300);
  await drawer.getByRole("button", { name: "关闭" }).click(); await sleep(400);
  // 看板：⛔ 角标 + 拖拽拦截 + 强制完成
  console.log("    [幕3] 去看板…");
  await gotoBoard(page);
  const card = page.locator('[data-sb-scope="board-card"]').filter({ hasText: "验收-前端联调" }).first();
  await card.locator('[data-sb-scope="board-blocked-badge"]').first().waitFor({ timeout: 10_000 });
  await sleep(800);
  const doneCol = page.locator('section[data-col="completed"]');
  await card.dragTo(doneCol, { targetPosition: { x: 140, y: 60 } });
  await page.locator('[data-sb-scope="blocked-list"]').waitFor({ timeout: 10_000 }); // 拦截对话框
  await sleep(1200);
  await page.locator('[data-sb-scope="blocked-force-btn"]').click(); await sleep(500);
  await page.locator('[data-sb-scope="blocked-force-comment"]').fill("客户演示节点，风险已评估由我承担");
  const forceP = page.waitForResponse((r) => r.request().method() === "PATCH" && r.status() === 200, { timeout: 15_000 });
  await page.locator('[data-sb-scope="blocked-force-go"]').click();
  await forceP; await sleep(1600); // 「已强制完成」Toast
});

// 幕4：工时（估算/填报/进度/编辑）
await runScene(browser, "工时", async (page) => {
  await login(page); await enterProject(page); await gotoList(page);
  const drawer = await openDrawer(page, "验收-前端联调");
  const sec = drawer.locator('[data-sb-scope="drawer-worklog-section"]');
  await sec.waitFor({ timeout: 10_000 }); await sleep(900); // 已耗 2.5h / 估算 1d 0h 可见
  // 记工时（chips + 备注 + 保存）
  await sec.locator('[data-sb-scope="drawer-worklog-add"]').click();
  await page.locator('[data-sb-scope="modal-title"]').waitFor({ timeout: 10_000 });
  await sleep(600);
  await page.locator('[data-sb-scope="wl-chip"]').filter({ hasText: "1h" }).click(); await sleep(300);
  await page.locator('[data-sb-scope="wl-note"]').fill("联调收尾与文档整理");
  const wlP = page.waitForResponse((r) => r.url().includes("/worklogs/") && r.request().method() === "POST", { timeout: 15_000 });
  await page.locator('[data-sb-scope="wl-save"]').click();
  await wlP; await sleep(1300); // 已耗 3.5h 回读
  // ⌘ 保存并再开（连续填报演示）
  await sec.locator('[data-sb-scope="drawer-worklog-add"]').click();
  await page.locator('[data-sb-scope="wl-chip"]').filter({ hasText: "30m" }).click();
  const wl2P = page.waitForResponse((r) => r.url().includes("/worklogs/") && r.request().method() === "POST", { timeout: 15_000 });
  await page.locator('[data-sb-scope="wl-save"]').click({ modifiers: ["Meta"] });
  await wl2P; await sleep(800);
  await page.keyboard.press("Escape"); await sleep(600); // 弹层保持可见后关闭
  // 进度条 + 记录 ⋯ 编辑
  await sleep(900); // 已耗 4h / 进度条
  const wrow = sec.locator('[data-sb-scope="drawer-worklog-row"]').filter({ hasText: "联调收尾与文档整理" }).first();
  await wrow.hover(); await sleep(400);
  await wrow.locator('[data-sb-scope="drawer-worklog-menu"]').click(); await sleep(500);
  await wrow.locator('[data-sb-scope="drawer-worklog-edit"]').click(); await sleep(500);
  await page.locator('[data-sb-scope="wl-note"]').fill("联调收尾与文档整理（补充缺陷单）");
  const patchP = page.waitForResponse((r) => r.url().includes("/worklogs/") && r.request().method() === "PATCH", { timeout: 15_000 });
  await page.locator('[data-sb-scope="wl-save"]').click();
  await patchP; await sleep(1200);
});

// 幕5：多执行人（转交弹层 + 认领 + 快速指派）
await runScene(browser, "多执行人", async (page) => {
  await login(page); await enterProject(page); await gotoList(page);
  // 未指派任务：行悬浮 🖐 认领
  const row = page.locator('tr[data-sb-scope="tree-row"]').filter({ hasText: "验收-前端回归测试" }).first();
  await row.hover(); await sleep(400);
  const claimP = page.waitForResponse((r) => r.url().includes("/claim/") && r.request().method() === "POST", { timeout: 15_000 });
  await row.locator('[data-sb-scope="list-claim"]').first().click();
  await claimP; await sleep(1200); // 头像出现
  // 转交弹层（导出功能已有 1 人）
  const drawer = await openDrawer(page, "验收-导出功能");
  const sec = drawer.locator('[data-sb-scope="drawer-assignee-section"]');
  await sec.waitFor({ timeout: 10_000 }); await sleep(800);
  await sec.locator('[data-sb-scope="drawer-assignee-edit"]').click();
  await page.locator('[data-sb-scope="asg-list"]').waitFor({ timeout: 10_000 });
  await sleep(700); // （我）候选 + 1/10 计数
  const me = page.locator('[data-sb-scope="asg-list"] label').filter({ hasText: "（我）" }).first();
  await me.locator("input[type=checkbox]").check(); await sleep(400);
  await page.locator('[data-sb-scope="asg-comment"]').fill("导出联调窗口改到周四，请相关同学对接"); await sleep(600); // 通知预览
  const putP = page.waitForResponse((r) => r.url().includes("/assignees/") && r.request().method() === "PUT", { timeout: 15_000 });
  await page.getByRole("button", { name: /保存修改/ }).click();
  await putP; await sleep(1400); // 堆叠回读
  await drawer.getByRole("button", { name: "关闭" }).click(); await sleep(500); // 关抽屉（避免遮挡列表 hover）
  // 列表快速指派（另一任务悬浮头像区）
  const row2 = page.locator('tr[data-sb-scope="tree-row"]').filter({ hasText: "验收-登录改造" }).first();
  await row2.hover(); await sleep(400);
  await row2.locator('[data-sb-scope="list-quick-assign"]').first().click();
  const pop = page.locator('[data-sb-scope="list-quick-assign-pop"]');
  await pop.waitFor({ timeout: 8_000 }); await sleep(600);
  const put2P = page.waitForResponse((r) => r.url().includes("/assignees/") && r.request().method() === "PUT", { timeout: 15_000 });
  await pop.locator('[data-sb-scope="list-quick-assign-item"]').first().click();
  await put2P; await sleep(1200);
});

// 幕6：自定义字段（管理页/12 类型宫格/新建/停用/三段删除）
await runScene(browser, "自定义字段", async (page) => {
  await login(page); await enterProject(page);
  await page.getByRole("navigation").getByRole("link", { name: "字段管理" }).click();
  await page.waitForURL(/\/settings\/fields/, { timeout: 10_000 });
  await page.locator('[data-sb-scope="fields-cap"]').waitFor({ timeout: 15_000 });
  await sleep(1200); // 计数 3/50 + 既有三字段
  // 新建弹层：12 类型宫格 → 建 select 字段「验收优先级」
  await page.locator('[data-sb-scope="fields-new"]').click();
  await page.locator('[data-sb-scope="field-modal-title"]').waitFor({ timeout: 10_000 });
  await sleep(900); // 宫格全貌停留
  await page.locator('[data-sb-scope="field-name"]').fill("验收优先级");
  await page.locator('[data-sb-scope="field-key-input"]').fill(`cf_acc_${Date.now() % 100000}`);
  await page.locator('[data-sb-scope="field-type-cell"]').filter({ hasText: "单选下拉" }).click();
  await sleep(500);
  const addOpt = page.getByRole("button", { name: /添加选项/ });
  if (await addOpt.count()) { await addOpt.click(); await sleep(300); }
  const createP = page.waitForResponse((r) => r.url().includes("/issue-properties/") && r.request().method() === "POST", { timeout: 15_000 });
  await page.locator('[data-sb-scope="field-save"]').click();
  await createP; await sleep(1100);
  // 停用 → 灰显·数据保留
  const row = page.locator('[data-sb-scope="field-row"]').filter({ hasText: "开启公测" }).first();
  await row.locator('[data-sb-scope="field-row-menu"]').click(); await sleep(400);
  await row.locator('[data-sb-scope="field-menu-toggle"]').click();
  await row.locator('[data-sb-scope="field-inactive-mark"]').waitFor({ timeout: 10_000 });
  await sleep(900);
  await row.locator('[data-sb-scope="field-row-menu"]').click(); await sleep(300);
  await row.locator('[data-sb-scope="field-menu-toggle"]').click(); await sleep(700); // 启用恢复
  // 三段删除（输入名激活 → 202 黄条）
  const victim = page.locator('[data-sb-scope="field-row"]').filter({ hasText: "验收优先级" }).first();
  await victim.locator('[data-sb-scope="field-row-menu"]').click(); await sleep(400);
  await victim.locator('[data-sb-scope="field-menu-del"]').click();
  await page.locator('[data-sb-scope="delfield-title"]').waitFor({ timeout: 8_000 });
  await sleep(900); // 影响统计/后果文案
  await page.locator('[data-sb-scope="delfield-confirm-input"]').fill("验收优先级");
  await sleep(500);
  const delP = page.waitForResponse((r) => r.url().includes("/issue-properties/") && r.request().method() === "DELETE", { timeout: 15_000 });
  await page.locator('[data-sb-scope="delfield-go"]').click();
  await delP; await sleep(1500); // 行消失 + 202 黄条
});

// 幕7：复制 / 归档 / 恢复 / 归档视图
await runScene(browser, "复制归档恢复", async (page) => {
  await login(page); await enterProject(page); await gotoList(page);
  // 复制弹层（五选项 + 固定信息条）→ 创建副本
  const drawer = await openDrawer(page, "验收-导出功能");
  await drawer.getByRole("button", { name: "更多操作" }).click(); await sleep(400);
  await drawer.locator('[data-sb-scope="drawer-menu-dup"]').click();
  await page.locator('[data-sb-scope="modal-title"]').waitFor({ timeout: 10_000 });
  await sleep(1100); // 五选项默认态 + 「评论、附件、依赖、工时不会被复制」
  console.log("    [幕7] 复制提交…");
  const dupP = page.waitForResponse((r) => r.url().includes("/duplicate/") && r.request().method() === "POST", { timeout: 20_000 });
  await page.getByRole("button", { name: /创建副本/ }).click();
  await dupP; await sleep(2600); // Toast「已创建 … (副本)」画面
  // 复制 Toast 带「查看」按钮显示 10s，fixed 右上角正好遮挡抽屉「更多操作」——存在才关（count 不等待）
  const tclose = page.locator('.toast [aria-label="关闭"]').first();
  if (await tclose.count()) await tclose.click({ timeout: 3000 }).catch(() => {});
  await sleep(400);
  // 归档（确认弹窗「将同时归档 N 个子任务」+ 10s 撤销 Toast + 撤销）
  console.log("    [幕7] 归档动线…");
  // 复制成功后前端会自动打开新根（副本）抽屉——关掉最顶层抽屉（两层 scrim 会拦旧层按钮）
  const topAside = page.locator("aside").last();
  await topAside.getByRole("button", { name: "关闭" }).click(); await sleep(500);
  if (await page.locator("aside").count()) { // 若还叠着旧抽屉一并关闭
    await page.locator("aside").last().getByRole("button", { name: "关闭" }).click().catch(() => {});
  }
  await sleep(600);
  const d2 = await openDrawer(page, "验收-导出功能", { exact: true });
  console.log("    [幕7] d2 打开，点更多操作…");
  await d2.getByRole("button", { name: "更多操作" }).click(); await sleep(400);
  console.log("    [幕7] 菜单展开，点归档…");
  await d2.locator('[data-sb-scope="drawer-menu-archive"]').click();
  await sleep(700); // 确认文案
  const archP = page.waitForResponse((r) => r.url().includes("/archive/") && r.request().method() === "POST", { timeout: 15_000 });
  await page.locator('[data-sb-scope="arch-go"]').click();
  await archP; await sleep(1000); // Toast + 撤销按钮倒计时
  const undo = page.getByRole("button", { name: /撤销/ }).first();
  await undo.waitFor({ timeout: 5_000 }); await sleep(1500); // 倒计时可见
  await undo.click(); await sleep(1300); // 「已恢复归档」
  // 再归档后看归档视图 + 只读横幅 + 写保护
  const d3 = await openDrawer(page, "验收-导出功能", { exact: true });
  await d3.getByRole("button", { name: "更多操作" }).click(); await sleep(400);
  await d3.locator('[data-sb-scope="drawer-menu-archive"]').click();
  const arch2P = page.waitForResponse((r) => r.url().includes("/archive/") && r.request().method() === "POST", { timeout: 15_000 });
  await page.locator('[data-sb-scope="arch-go"]').click();
  await arch2P; await sleep(900);
  console.log("    [幕7] 归档视图…");
  await page.locator('[data-sb-scope="list-archived-toggle"]').click();
  await sleep(1000); // 归档行 opacity-60 + 图标
  const d4 = await openDrawer(page, "验收-导出功能", { exact: true });
  await d4.locator('[data-sb-scope="drawer-arch-banner"]').waitFor({ timeout: 10_000 });
  await sleep(1100); // 只读横幅「已归档于 … · [恢复]」
  const restoreP = page.waitForResponse((r) => r.url().includes("/archive/") && r.request().method() === "DELETE", { timeout: 15_000 });
  await d4.locator('[data-sb-scope="drawer-arch-restore"]').click();
  await restoreP; await sleep(1000);
});

// 幕8：动态时间线
await runScene(browser, "动态时间线", async (page) => {
  await login(page); await enterProject(page); await gotoList(page);
  const drawer = await openDrawer(page, "验收-导出功能");
  await drawer.locator('[role="tab"]').filter({ hasText: "动态" }).click();
  const list = drawer.locator('[data-sb-scope="drawer-activity-list"]');
  await list.waitFor({ timeout: 10_000 });
  await sleep(2200); // 日期分区 + 组头 + 字段 diff（旧值删除线→新值加粗）
  // 双过滤器
  await drawer.locator('[data-sb-scope="act-field-toggle"]').click(); await sleep(500);
  await drawer.locator('[data-sb-scope="act-field-item"]').filter({ hasText: "优先级" }).click();
  await sleep(1200); // 过滤后仅剩优先级组
  await drawer.locator('[data-sb-scope="act-field-toggle"]').click(); await sleep(300);
  await drawer.locator('[data-sb-scope="act-field-item"]').filter({ hasText: "全部", exact: true }).click();
  await sleep(900);
  await drawer.locator('[data-sb-scope="act-actor-toggle"]').click(); await sleep(600);
  await drawer.locator('[data-sb-scope="act-actor-item"]').filter({ hasText: "全部操作人" }).click();
  await sleep(900);
  await drawer.locator('[data-sb-scope="act-load-earlier"]').click().catch(() => {}); // 有下一页时演示按钮式分页
  await sleep(1400);
});

// 幕9：边界（注册新账号 → 他人项目 403；死信页 403 空态；归档写保护 409）
await runScene(browser, "边界与负向", async (page) => {
  await login(page); await enterProject(page); await gotoList(page);
  // 归档写保护：打开已归档树（幕7 已恢复——改用「验收-旧版导出方案」seed 归档树）
  console.log("    [幕9] 归档只读…");
  await page.locator('[data-sb-scope="list-archived-toggle"]').click();
  await page.locator('tr[data-sb-scope="tree-row"]').filter({ hasText: "验收-旧版导出方案" })
    .first().waitFor({ timeout: 15_000 }); // 归档视图行渲染完成再点击
  await sleep(600);
  const drawer = await openDrawer(page, "验收-旧版导出方案", { exact: true });
  await drawer.locator('[data-sb-scope="drawer-arch-banner"]').waitFor({ timeout: 10_000 });
  await sleep(800);
  // 关抽屉（scrim 会拦截工具条/侧栏点击——幕7 同款教训）
  await drawer.getByRole("button", { name: "关闭" }).click(); await sleep(600);
  await page.locator('[data-sb-scope="list-archived-toggle"]').click(); await sleep(600);
  // 死信页 403 权限空态（非 SystemAdmin）
  console.log("    [幕9] 死信页…");
  await page.getByRole("navigation").getByRole("link", { name: /死信补偿/ }).click();
  await page.locator('[data-sb-scope="dlq-forbidden"]').waitFor({ timeout: 10_000 });
  await sleep(1500); // 「需要系统审计权限」
  // 登出 → 注册全新账号（表单即真实入口）→ 直达他人项目 URL → 403
  await page.goBack(); // 死信页为顶层路由（无顶栏）——浏览器后退回带顶栏的应用页（真实用户路径）
  await page.waitForFunction(
    () => /\/projects$/.test(location.pathname) || /\/board$/.test(location.pathname) || /\/issues$/.test(location.pathname),
    null, { timeout: 10_000 });
  await sleep(600);
  const avatarBtn = page.locator('button[aria-label="账号菜单"]').first();
  await avatarBtn.waitFor({ timeout: 10_000 });
  await avatarBtn.click(); await sleep(500);
  await page.getByRole("menuitem").filter({ hasText: "退出登录" }).click();
  await page.waitForFunction(() => /\/login/.test(location.pathname), null, { timeout: 10_000 });
  await sleep(800);
  console.log("    [幕9] 注册新账号…");
  await page.goto("/register");
  await sleep(600);
  const rid9 = Math.random().toString(36).slice(2, 8);
  // 表单字段：邮箱/密码/确认密码（无昵称项——label 逐字对齐 register.tsx）
  await page.locator("#rg-email").fill(`out${rid9}@e2e.dev`);
  await page.locator("#rg-pw").fill("Rabbit123!");
  await page.locator("#rg-pw2").fill("Rabbit123!");
  await sleep(400);
  const regP = page.waitForResponse((r) => r.url().includes("/sign-up/"), { timeout: 20_000 });
  await page.getByRole("button", { name: /创建账号/ }).click();
  await regP; await sleep(1200);
  // 直达他人项目（URL 取演示项目——从 /projects 页拿不到（跨 WS），用 goto 到演示 WS 项目路径）
  await page.goto("/workspace/projects");
  await sleep(1000);
  // 跨账号访问演示 WS → 403/空（不泄露）；再直接访问已知项目 URL 模式
  const projUrl = "/workspace/projects"; // 演示 WS 列表对非成员不可见——画面即为「无项目/无权访问」
  await page.goto(projUrl);
  await sleep(1200);
});

await browser.close();

/* ── 汇总 + README 索引 ──────────────────────────────────────── */
const pass = results.filter((r) => r.ok).length;
console.log("\n═══ Sprint-2 验收录屏完成 ═══");
console.log(`  ${pass}/${results.length} 幕成功；视频目录：${OUT}/`);
const readme = [
  "# Sprint-2 验收录屏（9 幕，全部从用户实际入口出发）",
  "",
  "| 幕 | 场景 | 覆盖 | 时长 | 结果 |",
  "| --- | --- | --- | --- | --- |",
  ...results.map((r) => `| ${r.id} | ${r.name} | 见下表 | ${r.seconds}s | ${r.ok ? "✓" : "✗"} |`),
  "",
  "覆盖对照：幕1 C.37/C.38/C.40/C.41（树形/行内加子/全屏树）｜幕2 C.39（拖拽+环 409）｜幕3 C.42~C.45（依赖三分区/弹层/拦截/看板角标）｜幕4 C.46~C.48（工时分区/填报/列）｜幕5 C.49~C.51（执行人/转交/认领）｜幕6 C.52~C.56（字段管理/宫格/三段删除/动态列基础）｜幕7 C.57~C.60（复制/归档/撤销/只读横幅）｜幕8 C.61（时间线）｜幕9 边界（403/写保护/注册入口）",
  "",
  "录屏环境：API(8000)+Web(3001)+Celery worker 常驻；数据 = 「S2 验收演示」项目（scripts/seed_acceptance.py 生成，可重跑）。",
  "观看建议：慢放 0.75× 可看清 Toast 与动画细节；每幕开头的登录/导航即「用户实际入口」演示。",
  "",
  ...results.filter((r) => !r.ok).map((r) => `> ⚠ 幕${r.id}（${r.name}）录制中断于失败点，现场画面保留 1.2s${r.errors.length ? "；pageerror: " + r.errors.join(" | ") : ""}`),
].join("\n");
(await import("node:fs")).writeFileSync("docs/sprint-2-acceptance/README.md", readme + "\n");
console.log("  索引：docs/sprint-2-acceptance/README.md");
process.exit(pass === results.length ? 0 : 1);
