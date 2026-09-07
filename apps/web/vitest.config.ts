import { defineConfig } from "vitest/config";
import path from "node:path";

// 前端核心路径单测（sprint-5 §6 前端覆盖率 ≥70% 门槛的基建）：
// 环境用 node——被测对象是纯逻辑层（服务层契约 / 常量单源 / 共享工具），
// DOM 组件由 Playwright e2e（parity spec）承载，不在 vitest 重复。
// 覆盖率口径 include 同「核心路径」定义：services 层 + 共享包 + 关键 store。

export default defineConfig({
  test: {
    environment: "node",
    // axios 错误路径触 location/document（401 跳转分支）——services 域用 jsdom
    environmentMatchGlobs: [["app/services/**", "jsdom"]],
    include: ["app/**/*.test.ts", "app/**/*.test.tsx"],
    coverage: {
      provider: "v8",
      reporter: ["text", "json-summary"],
      include: [
        "app/services/**/*.ts",
        "app/realtime/**/*.ts",
        "app/stores/**/*.ts",
      ],
      thresholds: {
        // §6 全量门槛 70% 归 sprint-6 补（realtime 三文件 + session/permission
        // store + gantt 取数管线未测——见 known-tech-debt D 表）；本迭代先
        // 守「不后退」基线 35%（当前实测 36.4%，Sprint-5 域服务面 100%）。
        lines: 35,
        functions: 25,
        statements: 35,
      },
    },
  },
  resolve: {
    alias: { "@rp/types": path.resolve(__dirname, "../types/src/index.ts") },
  },
});
