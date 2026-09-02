/** 交互元素全覆盖（goal：每一个可交互元素都需要有相关测试和断言）。
 *  与 parity.spec（字段存在性）互补：本 spec 逐个「可操作」元素验证交互行为。
 *  覆盖：登录（眼睛/记住我/错误 Alert/禁用解锁）、注册（强度/不一致/409 链接/邮箱携带/欢迎条）、
 *  顶栏（切换团队/建团队/退出）、项目（自动建议/409 采纳/设置保存/删除 confirm）、
 *  任务弹窗（状态 4 态下拉/负责人/快捷日期/⌘Enter/续创建）、看板（列内快速建/
 *  卡片→抽屉 peekIssue/标题编辑/⋯ 删除）、列表（快速建焦点保持/行点抽屉/编号复制）。
 *  运行：E2E_NO_SERVER=1 pnpm exec playwright test tests/e2e/interactions.spec.ts */
import { test, expect, type Page } from "@playwright/test";
import { attachConsoleGuard, freshEmail } from "./no-console-errors";

const PW = "Rabbit123";
/** 演示账号持久：identifier/团队名必须每次运行唯一，否则 409 连锁失败 */
const rid = (n = 5) => Array.from({ length: n }, () => String.fromCharCode(65 + Math.floor(Math.random() * 26))).join("");
const uname = (pfx: string) => `${pfx} ${Date.now() % 100000}`;

async function loginDemo(page: Page) {
  await page.goto("/login");
  await page.getByRole("button", { name: /一键进入演示账号/ }).click();
  await page.waitForURL(/\/workspace\/projects/, { timeout: 8000 });
}

async function createProject(page: Page, name: string, id: string) {
  await page.getByRole("button", { name: /创建项目/ }).first().click();
  await page.getByLabel("项目名称 *").fill(name);
  await page.getByLabel("项目标识符 *").fill(id);
  await page.getByRole("button", { name: "创建项目", exact: true }).click();
  await page.waitForURL(/\/projects\/.+\/board/, { timeout: 10_000 });
}

test.describe("交互元素全覆盖", () => {
  let getErrs: () => string[] = () => [];
  test.beforeEach(async ({ page }) => { getErrs = attachConsoleGuard(page); });
  test.afterEach(async () => { expect(getErrs(), "console errors").toEqual([]); });

  /* ── 登录页 ── */
  test("INT-A1 眼睛切换 password↔text", async ({ page }) => {
    await page.goto("/login");
    const pw = page.getByLabel("密码", { exact: true });
    await pw.fill("secret");
    await expect(pw).toHaveAttribute("type", "password");
    await page.getByRole("button", { name: "显示密码" }).click();
    await expect(pw).toHaveAttribute("type", "text");
  });

  test("INT-A2 记住我 checkbox", async ({ page }) => {
    await page.goto("/login");
    const cb = page.getByRole("checkbox");
    await expect(cb).not.toBeChecked();
    await cb.check();
    await expect(cb).toBeChecked();
  });

  test("INT-A3 错误密码 → 表单 Alert", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel("邮箱").fill("zhangsan@rabbit.dev");
    await page.getByLabel("密码", { exact: true }).fill("Wrong123");
    await page.getByRole("button", { name: "登录", exact: true }).click();
    await expect(page.getByText("邮箱或密码错误").first()).toBeVisible({ timeout: 5000 });
  });

  test("INT-A4 ?disabled=1 → Alert + 改邮箱解锁", async ({ page }) => {
    await page.goto("/login?disabled=1");
    await expect(page.getByText("账号已被禁用，请联系管理员")).toBeVisible();
    const submit = page.getByRole("button", { name: "登录", exact: true });
    await expect(submit).toBeDisabled();
    await page.getByLabel("邮箱").fill("other@rabbit.dev");
    await expect(submit).toBeEnabled();
  });

  /* ── 注册页 ── */
  test("INT-B1 强度条 弱→中", async ({ page }) => {
    await page.goto("/register");
    await page.getByLabel("密码", { exact: true }).fill("abc");
    await expect(page.getByText("强度：弱")).toBeVisible();
    await page.getByLabel("密码", { exact: true }).fill("Rabbit123");
    await expect(page.getByText("强度：中")).toBeVisible();
  });

  test("INT-B2 确认密码不一致提示", async ({ page }) => {
    await page.goto("/register");
    await page.getByLabel("密码", { exact: true }).fill("Rabbit123");
    await page.getByLabel("确认密码").fill("Different1");
    await page.getByRole("button", { name: "创建账号" }).click();
    await expect(page.getByText("两次输入的密码不一致")).toBeVisible();
  });

  test("INT-B3 重复邮箱 409 → 直接登录链接", async ({ page }) => {
    await page.goto("/register");
    await page.getByLabel("邮箱").fill("zhangsan@rabbit.dev");
    await page.getByLabel("密码", { exact: true }).fill("Rabbit123");
    await page.getByLabel("确认密码").fill("Rabbit123");
    await page.getByRole("button", { name: "创建账号" }).click();
    await expect(page.getByText("该邮箱已注册").first()).toBeVisible({ timeout: 5000 });
    await expect(page.getByRole("link", { name: "直接登录" })).toBeVisible();
  });

  test("INT-B4 登录→注册邮箱携带", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel("邮箱").fill("carry@rabbit.dev");
    await page.getByRole("link", { name: "立即注册" }).click();
    await expect(page.getByLabel("邮箱")).toHaveValue("carry@rabbit.dev");
  });

  test("INT-B5 注册成功 → 欢迎条 + 关闭", async ({ page }) => {
    await page.goto("/register");
    await page.getByLabel("邮箱").fill(freshEmail("welcome"));
    await page.getByLabel("密码", { exact: true }).fill(PW);
    await page.getByLabel("确认密码").fill(PW);
    await page.getByRole("button", { name: "创建账号" }).click();
    await page.waitForURL(/\/[^/]+\/projects/, { timeout: 8000 });
    await expect(page.getByText("欢迎使用 RabbitProjects")).toBeVisible({ timeout: 5000 });
    await page.locator('[data-testid="welcome-banner"] button').click();
    await expect(page.getByText("欢迎使用 RabbitProjects")).toHaveCount(0, { timeout: 2000 });
  });

  /* ── 顶栏 ── */
  test("INT-C1 建团队 → 落新团队页 → 切回", async ({ page }) => {
    await loginDemo(page);
    await page.locator("header button").filter({ hasText: /的工作空间/ }).click();
    await page.getByText("创建新团队").click();
    const tname = uname("Switch Team"); await page.getByLabel("团队名称 *").fill(tname);
    await expect(page.getByText(/访问地址预览：/)).toBeVisible();
    await page.getByRole("button", { name: "创建团队", exact: true }).click();
    const tslug = tname.toLowerCase().replace(/\s+/g, "-"); await page.waitForURL(new RegExp(`/${tslug}/projects`), { timeout: 8000 });
    await expect(page.locator("header button").first()).toContainText(tname, { timeout: 5000 });
    await page.locator("header button").filter({ hasText: tname }).click();
    await page.locator('[role="option"]').filter({ hasText: "张三 的工作空间" }).click();
    await page.waitForURL(/\/workspace\/projects/, { timeout: 5000 });
    await expect(page.locator("header button").first()).toContainText("张三 的工作空间");
  });

  test("INT-C2 头像菜单退出 → /login", async ({ page }) => {
    await loginDemo(page);
    await page.getByRole("button", { name: "账号菜单" }).click();
    await page.getByRole("menuitem", { name: "退出登录" }).click();
    await page.waitForFunction(() => location.pathname === "/login", null, { timeout: 6000 });
  });

  /* ── 项目 ── */
  test("INT-D1 identifier 大写 + 409 试试建议采纳", async ({ page }) => {
    await loginDemo(page);
    const ida = rid(); await createProject(page, uname("Int Proj"), ida);
    await page.getByRole("navigation").getByRole("link", { name: "返回项目列表" }).click();
    await page.waitForURL(/\/workspace\/projects/);
    await page.getByRole("button", { name: /创建项目/ }).first().click();
    await page.getByLabel("项目名称 *").fill("Int Proj B");
    await page.getByLabel("项目标识符 *").fill(ida);
    await page.getByRole("button", { name: "创建项目", exact: true }).click();
    await expect(page.getByText(`标识符 ${ida} 已被占用`).first()).toBeVisible({ timeout: 5000 });
    await expect(page.getByRole("button", { name: /试试/ })).toBeVisible();
    await page.getByRole("button", { name: /试试/ }).click();
    await expect(page.getByLabel("项目标识符 *")).toHaveValue(new RegExp(`^${ida}`));
  });

  test("INT-D2 设置保存 chip + 删除 confirm 流程", async ({ page }) => {
    await loginDemo(page);
    await createProject(page, uname("Settings Proj"), rid());
    await page.goto(page.url().replace(/\/board$/, "/settings"));
    const sname = uname("Settings Renamed"); await page.getByLabel("项目名称").fill(sname);
    await page.getByRole("button", { name: "保存更改" }).click();
    await expect(page.getByText("已保存").first()).toBeVisible({ timeout: 5000 });
    await page.getByRole("button", { name: "删除项目" }).first().click();
    const confirmInput = page.locator("input").nth(2);
    await confirmInput.fill("错误的名字");
    await expect(page.locator("button[disabled]", { hasText: "删除项目" }).last()).toBeVisible();
    await confirmInput.fill(sname);
    await page.locator(".fixed button", { hasText: "删除项目" }).last().click();
    await page.waitForURL(/\/workspace\/projects/, { timeout: 8000 });
    await expect(page.getByText(sname)).toHaveCount(0);
  });

  /* ── 任务弹窗 ── */
  test("INT-E1 弹窗：状态 4 态下拉 / 负责人 / 快捷日期 / ⌘Enter", async ({ page }) => {
    await loginDemo(page);
    await createProject(page, uname("Modal Proj"), rid());
    await page.getByRole("button", { name: /\+ 创建任务/ }).click();
    await page.locator("button").filter({ hasText: /待办/ }).nth(0).click();
    for (const st of ["待办", "进行中", "已完成", "已取消"]) {
      await expect(page.getByRole("button", { name: new RegExp(`^${st}$`) }).first()).toBeVisible();
    }
    await page.locator("button").filter({ hasText: /待办/ }).nth(0).click();
    await page.locator("button").filter({ hasText: /未分配/ }).first().click();
    await expect(page.getByText(/指派给我/)).toBeVisible();
    await page.locator("button").filter({ hasText: /未分配/ }).first().click();
    await page.getByRole("button", { name: "今天", exact: true }).click();
    await page.getByPlaceholder("任务标题").fill("Modal Task");
    await page.keyboard.press("Meta+Enter");
    await expect(page.getByText("Modal Task").first()).toBeVisible({ timeout: 5000 });
  });

  test("INT-E2 续创建 checkbox 连续 2 条", async ({ page }) => {
    await loginDemo(page);
    await createProject(page, uname("Keep Open"), rid());
    await page.getByRole("button", { name: /\+ 创建任务/ }).click();
    await page.getByText("创建后继续创建下一个").click();
    await page.getByPlaceholder("任务标题").fill("Keep One");
    await page.locator('[data-testid="create-task-submit"]').click();
    await expect(page.getByPlaceholder("任务标题")).toBeVisible({ timeout: 3000 });
    await page.getByPlaceholder("任务标题").fill("Keep Two");
    await page.locator('[data-testid="create-task-submit"]').click();
    await expect(page.getByPlaceholder("任务标题")).toBeVisible();
    await expect(page.getByText("2 个任务")).toBeVisible({ timeout: 5000 });
  });

  /* ── 看板 ── */
  test("INT-F1 列内快速创建落该列", async ({ page }) => {
    await createProject(page, uname("F1"), rid());
    await page.getByRole("button", { name: /添加任务/ }).nth(1).click();
    const input = page.locator("input[id^='qi-started']");
    await expect(input).toBeVisible();
    await input.fill("列内快速任务");
    await input.press("Enter");
    await expect(page.getByText("列内快速任务").first()).toBeVisible({ timeout: 5000 });
    const startedCol = page.locator("section").filter({ hasText: "进行中" }).first();
    await expect(startedCol.locator("article").filter({ hasText: "列内快速任务" })).toBeVisible();
  });

  test("INT-F2 卡片→抽屉 peekIssue + 标题编辑保存", async ({ page }) => {
    await createProject(page, uname("F2"), rid());
    await page.getByRole("button", { name: /\+ 创建任务/ }).click();
    await page.getByPlaceholder("任务标题").fill("Peek Task");
    await page.locator('[data-testid="create-task-submit"]').click();
    await page.locator("article").filter({ hasText: "Peek Task" }).first().click();
    await expect(page.locator("aside")).toBeVisible();
    await expect(page.url()).toContain("peekIssue=");
    await page.locator("aside h2").click();
    const editor = page.locator("aside input").last();
    await editor.fill("Peek Task Edited");
    await editor.press("Enter");
    await expect(page.getByText("已保存").first()).toBeVisible({ timeout: 5000 });
    await expect(page.locator("aside h2")).toContainText("Peek Task Edited");
  });

  test("INT-F3 抽屉 ⋯ 菜单删除任务 → 卡片消失", async ({ page }) => {
    await createProject(page, uname("F3"), rid());
    await page.getByRole("button", { name: /\+ 创建任务/ }).click();
    await page.getByPlaceholder("任务标题").fill("Doomed Task");
    await page.locator('[data-testid="create-task-submit"]').click();
    await page.locator("article").filter({ hasText: "Doomed Task" }).first().click();
    await page.getByRole("button", { name: "更多操作" }).click();
    await page.getByRole("button", { name: "删除任务" }).last().click();
    await page.getByRole("button", { name: "删除", exact: true }).click();
    await expect(page.getByText(/已删除/)).toBeVisible({ timeout: 5000 });
    await expect(page.locator("article").filter({ hasText: "Doomed Task" })).toHaveCount(0, { timeout: 8000 });
  });

  /* ── 列表 ── */
  test("INT-G1 列表：快速建+焦点保持 / 行点抽屉 / 编号复制", async ({ page }) => {
    const lid = rid();
    await createProject(page, uname("G1"), lid);
    await loginDemo(page);
    await page.getByRole("navigation").getByRole("link", { name: "任务列表" }).click();
    await page.waitForURL(/\/issues/);
    const quick = page.getByPlaceholder(/按回车快速创建/);
    await quick.fill("List Item One");
    await quick.press("Enter");
    await expect(page.getByText("List Item One").first()).toBeVisible({ timeout: 5000 });
    await expect(page.locator("#tq-input")).toBeFocused();
    await expect(page.getByText("1 个任务")).toBeVisible();
    await page.locator("tbody tr").first().click();
    await expect(page.locator("aside")).toBeVisible();
    await expect(page.url()).toContain("peekIssue=");
    await page.getByRole("button", { name: "关闭" }).click();
    await page.locator("td button").first().click();
    await expect(page.getByText(new RegExp(`已复制 ${lid}-\\d+`))).toBeVisible({ timeout: 3000 });
  });
});
