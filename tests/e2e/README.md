# Playwright E2E 测试

## 前置
- PG 17 容器 + migrations 已建（`tests/e2e/PG_README.md` 指引）
- `pnpm install` 已装好 `@playwright/test` + Chromium headless
- `pnpm exec playwright install --with-deps chromium` 已下载浏览器

## 跑（默认会启动 web dev server）
```bash
# 一键：API 已在 8000（用户手动起）+ playwright 自启 web dev server
pnpm exec playwright test

# 跑单个 spec
pnpm exec playwright test tests/e2e/auth.spec.ts

# 跳 web 自动启动（如果 web 已起来）
E2E_NO_SERVER=1 pnpm exec playwright test

# 调试模式
pnpm exec playwright test --debug

# 用真浏览器看（需 headed）
pnpm exec playwright test --headed

# 看 trace
pnpm exec playwright show-report
```

## 覆盖
- `tests/e2e/auth.spec.ts`：
  1. **完整动线**：注册 → 工作台 → 建项目 → 建任务 → 看板验证（包含拖拽前序）
  2. **演示账号一键登录**：验 happy path
  3. **路由守卫**：未登录访问 `/ws/projects` → 自动跳 `/login`

## 验收口径（设计基线 §1.1）
- 视觉与交互以冻结稿 `docs/design/sprint-0-hifi-prototype.html` 为准
- 端到端跑通核心动线（注册→建项目→建任务→看板）
- 守卫拦截正确（未登录跳登录）

## 文件说明
- `playwright.config.ts`：root 配置（baseURL / dev server 自动启动 / Chrome）
- `tests/e2e/auth.spec.ts`：核心动线 e2e
- 截图 / trace 自动保存到 `test-results/` 与 `playwright-report/`
