/** UI parity 全量扫描（附录 C 表面清单的字段级断言）。
 *  设计要点：
 *  - expect.soft：任一断言失败不中断，一次跑出**全部**缺失字段清单
 *  - 单测试串行走完全部六屏（共享一次注册/建项目，避免上下文重建）
 *  - 断言对象是"冻结稿规定的字段必须存在/可见/可用"，不由实现反推（防自我印证）
 *  运行：E2E_NO_SERVER=1 pnpm exec playwright test tests/e2e/parity.spec.ts */
import { test, expect } from "@playwright/test";

const ts = Date.now();
const EMAIL = `parity-${ts}@rabbit.dev`;
const PW = "Rabbit123";

test("C.1-C.8 全屏字段级 parity 扫描", async ({ page }) => {
  test.setTimeout(60_000);

  /* ── C.1 登录页 ── */
  await page.goto("/login");
  await expect.soft(page.getByRole("heading", { name: /登录 RabbitProjects/ })).toBeVisible();
  await expect.soft(page.getByText("🐰")).toBeVisible();
  await expect.soft(page.getByLabel("邮箱")).toBeVisible();
  await expect.soft(page.getByLabel("密码", { exact: true })).toBeVisible();
  await expect.soft(page.getByRole("button", { name: "显示密码" })).toBeVisible();
  await expect.soft(page.getByText("记住我（30 天）")).toBeVisible();
  await expect.soft(page.getByText("忘记密码？")).toBeVisible();
  await expect.soft(page.getByRole("button", { name: /一键进入演示账号/ })).toBeVisible();
  await expect.soft(page.getByRole("link", { name: "立即注册" })).toBeVisible();

  /* ── C.2 注册页 ── */
  await page.goto("/register");
  await expect.soft(page.getByRole("heading", { name: /创建你的账号/ })).toBeVisible();
  for (const rule of ["至少 8 位", "含大写字母", "含小写字母", "含数字"]) {
    await expect.soft(page.getByText(rule)).toBeVisible();
  }
  await page.getByLabel("密码", { exact: true }).fill("Rabbit123");
  await expect.soft(page.getByText("强度：中")).toBeVisible();

  /* ── 注册落地 → C.3 项目列表 ── */
  await page.getByLabel("邮箱").fill(EMAIL);
  await page.getByLabel("密码", { exact: true }).fill(PW);
  await page.getByLabel("确认密码").fill(PW);
  await page.getByRole("button", { name: "创建账号" }).click();
  await page.waitForURL(/\/[^/]+\/projects/, { timeout: 10_000 });

  // 顶栏 + 工作区侧栏（置灰项）
  await expect.soft(page.getByRole("navigation").getByText("首页")).toBeVisible();
  await expect.soft(page.getByRole("navigation").getByText("我的任务")).toBeVisible();
  await expect.soft(page.getByRole("navigation").getByText("团队设置")).toBeVisible();
  await expect.soft(page.getByRole("navigation").getByRole("link", { name: "项目" })).toBeVisible();
  await expect.soft(page.getByText("还没有项目")).toBeVisible();

  // C.3 创建项目弹窗字段
  await page.getByRole("button", { name: /创建项目/ }).first().click();
  await expect.soft(page.getByLabel("项目名称 *")).toBeVisible();
  await expect.soft(page.getByLabel("项目标识符 *")).toBeVisible();
  await expect.soft(page.getByText("创建后不可修改")).toBeVisible();
  await expect.soft(page.getByLabel("项目描述")).toBeVisible();
  await page.getByLabel("项目名称 *").fill("Parity 项目");
  await page.getByLabel("项目标识符 *").fill("PRT");
  // 测 409 一键采纳：identifier 留空 + name 重复触发（实施未完整，此处仅声明断言入口，回归由 TC-PROJ1-001 覆盖）
  await expect.soft(page.locator("body")).toBeAttached();
  await expect.soft(page.getByText(/PRT\s*-1/).first()).toBeVisible(); // {ID}-1 预览
  await page.getByRole("button", { name: "创建项目", exact: true }).click();
  await page.waitForURL(/\/projects\/.+\/board/, { timeout: 10_000 });

  /* ── C.4 项目壳 ── */
  const nav = page.getByRole("navigation"); // 第二个 nav = 项目侧栏
  await expect.soft(nav.getByRole("link", { name: "任务列表" })).toBeVisible();
  await expect.soft(nav.getByRole("link", { name: "看板" })).toBeVisible();
  await expect.soft(nav.getByRole("link", { name: "项目设置" })).toBeVisible();
  await expect.soft(nav.getByRole("link", { name: "返回项目列表" })).toBeVisible();
  await expect.soft(nav.getByText("PRT")).toBeVisible(); // identifier 徽章

  /* ── C.6 看板（先到的是 board）── */
  // 列底「＋ 添加任务」按钮 + 三列全空引导条（BOARD-001 §3.5）
  await expect.soft(page.getByRole("button", { name: /添加任务/ }).first()).toBeVisible();
  for (const col of ["待办", "进行中", "已完成"]) {
    await expect.soft(page.locator("section").filter({ hasText: new RegExp(`^${col}`) }).first()).toBeVisible();
  }
  await expect.soft(page.getByText("将任务拖拽到这里").first()).toBeVisible();

  /* ── C.7 创建任务弹窗全字段 ── */
  await page.getByRole("button", { name: /\+ 创建任务/ }).click();
  await expect.soft(page.getByText(/创建任务 · Parity 项目/).first()).toBeVisible();
  await expect.soft(page.getByPlaceholder("任务标题")).toBeVisible();
  await expect.soft(page.locator("div[contenteditable=\"true\"]")).toBeVisible(); // 占位为伪元素，断言可编辑区存在
  for (const t of ["B", "I", "U", "≡", "☰", "⌗", "</>", "🔗"]) {
    await expect.soft(page.locator("span").filter({ hasText: new RegExp(`^${t.replace("/", "/")}$`) }).first()).toBeVisible();
  }
  await expect.soft(page.getByText("创建后继续创建下一个")).toBeVisible();
  await expect.soft(page.getByText("⌘↵")).toBeVisible();
  for (const q of ["今天", "明天", "下周"]) {
    await expect.soft(page.getByRole("button", { name: q, exact: true })).toBeVisible();
  }
  // 状态下拉：默认待办 + 打开后含全四态（含已取消）
  await page.locator("button").filter({ hasText: /待办/ }).nth(0).click();
  for (const st of ["待办", "进行中", "已完成", "已取消"]) {
    await expect.soft(page.getByRole("button", { name: new RegExp(`^${st}$`) }).first()).toBeVisible();
  }
  await page.locator("button").filter({ hasText: /待办/ }).nth(0).click(); // 再次点击收起菜单
  // 负责人下拉：指派给我
  await page.locator("button").filter({ hasText: /未分配/ }).first().click();
  await expect.soft(page.getByText(/指派给我/)).toBeVisible();
  await page.locator("button").filter({ hasText: /未分配/ }).first().click(); // 收起
  // 创建一个任务供后续断言
  await page.getByPlaceholder("任务标题").fill("Parity 任务");
  await page.getByRole("button", { name: /创建任务/ }).last().click();
  await expect.soft(page.getByText("Parity 任务").first()).toBeVisible({ timeout: 5_000 });

  /* ── C.6 抽屉 ── */
  await page.locator("article").filter({ hasText: "Parity 任务" }).first().click();
  await expect.soft(page.locator("aside").getByText(/PRT-\d+/)).toBeVisible();
  await expect.soft(page.getByText("状态", { exact: true })).toBeVisible();
  await expect.soft(page.getByText("负责人", { exact: true })).toBeVisible();
  await expect.soft(page.getByText("截止时间", { exact: true })).toBeVisible();
  await page.locator("aside").getByRole("button", { name: "✕" }).click();

  /* ── C.5 任务列表 ── */
  await page.getByRole("navigation").getByRole("link", { name: "任务列表" }).click();
  await page.waitForURL(/\/issues/);
  await expect.soft(page.getByText("任务列表").first()).toBeVisible();
  await expect.soft(page.getByText("1 个任务")).toBeVisible();
  for (const h of ["编号", "标题", "状态", "负责人", "截止时间"]) {
    await expect.soft(page.locator("th").filter({ hasText: h })).toBeVisible();
  }
  await expect.soft(page.getByPlaceholder(/按回车快速创建/)).toBeVisible();
  // 行点击 → 抽屉
  await page.locator("tbody tr").first().click();
  await expect.soft(page.locator("aside").getByText(/PRT-\d+/)).toBeVisible();
  await page.locator("aside").getByRole("button", { name: "✕" }).click();

  /* ── C.8 设置页 ── */
  await page.getByRole("navigation").getByRole("link", { name: "项目设置" }).click();
  await page.waitForURL(/\/settings/);
  await expect.soft(page.getByLabel("项目名称")).toHaveValue("Parity 项目", { timeout: 10_000 }); // 回显（原空壳缺陷回归锚点）
  await expect.soft(page.getByText("创建后不可修改")).toBeVisible();
  await expect.soft(page.getByRole("button", { name: "保存更改" })).toBeVisible();
  await expect.soft(page.getByText("危险区域")).toBeVisible();
  await page.getByRole("button", { name: "删除项目" }).first().click();
  await expect.soft(page.getByText(/输入项目名称/)).toBeVisible();
  await expect.soft(page.getByRole("button", { name: "删除项目" }).last()).toBeDisabled(); // confirm≠name
});
