import { defineConfig, devices } from "@playwright/test";

/** Playwright E2E 配置 —— Sprint 0 端到端 UI 流。
 *  启动要求：
 *  1) API 服务在 http://localhost:8000（连接真实 PG 容器）
 *  2) Web dev server 在 http://localhost:3001
 *  推荐：用 `pnpm dev:all` + `pnpm exec playwright test` 一起跑 */
export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,            // 注册类测试有副作用
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,                       // 端到端共享同一测试账号，按顺序
  reporter: process.env.CI ? "github" : "list",

  use: {
    // INT-G1 编号复制：headless 默认无剪贴板权限，writeText 会 reject
    permissions: ["clipboard-read", "clipboard-write"],
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3001",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    actionTimeout: 10_000,
    navigationTimeout: 15_000,
  },

  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],

  webServer: process.env.E2E_NO_SERVER ? undefined : [
    {
      command: "pnpm dev:web",
      url: "http://localhost:3001",
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
      stdout: "ignore",
      stderr: "pipe",
    },
  ],
});
