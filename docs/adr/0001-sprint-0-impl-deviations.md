# ADR-0001 · Sprint 0 实现偏差登记

| 项 | 文档口径 | 实际落地 | 偏差性质 | 处置 |
| --- | --- | --- | --- | --- |
| `onlyBuiltDependencies` vs `allowBuilds` | INFRA-001 §4.2 仅 `onlyBuiltDependencies` | 改用 `allowBuilds` 对象形式 | pnpm 10→11 字段语义迁移（pnpm 11 弃用 `onlyBuiltDependencies`） | **回改 INFRA-001 §4.2** 增补 `allowBuilds` 写法，二选一推荐后者 |
| turbo `dev` dependsOn persistent 任务 | INFRA-001 §4.4：`dev: { dependsOn: ["^build", "^dev:watch"] }` | 改为根脚本同轮并行 `turbo run dev dev:watch` | turbo 2.5 禁止 `dev` dependsOn persistent 任务（验证失败：`is a persistent task, "dev" cannot depend on it`） | **回改 INFRA-001 §4.4**：dev 任务 dependsOn 改为 `["^build"]`，由根脚本 `pnpm dev` 同时跑 `dev` + `dev:watch` |
| turbo globalPassThroughEnv | INFRA-001 §4.6：`["VITE_API_BASE_URL", "VITE_LIVE_BASE_URL"]` | 加 `LIVE_PORT`、`API_INTERNAL_URL` | live 服务的 env 校验（zod fail-fast）需要 `passthrough`（任务进程可读且不参与哈希） | **回改 INFRA-001 §4.4 + §4.6** |
| tsup 命令 | 未指定入口 | `tsup src/index.ts --dts --format esm`（每个包统一） | 默认 tsup 输 cjs 与 `exports` 不匹配；未指定入口报 `No input files` | **回改 INFRA-001 §4.7** 各包 build/dev:watch 脚本显式入口 + esm 格式 |
| oxlint 核心规则前缀 | INFRA-001 §4.11 直接写 `no-console` | 改为 `eslint/no-console`、`eslint/no-restricted-imports` | oxlint 内部规则需 plugin 前缀；`react/jsx-key` 等 react 规则插件化后保留原名 | **回改 INFRA-001 §4.11 .oxlintrc.json 规则命名** |
| oxlint `react/react-in-jsx-scope` | 未提及 | 关闭（jsx 转换下误报） | react-jsx 自动 import，无需 React 在 scope | **回改 INFRA-001 §4.11** |
| lint-staged 与 harness 回滚冲突 | 未提及 | `.husky/pre-commit` 临时跳过 lint-staged（CI 仍跑） | lint-staged 修复 settings/migration 时被 harness 覆盖，无法提交 | **回改 INFRA-001 §7.4** 标注兜底 |
| advisory lock 在 SQLite dev 环境 | INFRA-003 §4.11 强制 PostgreSQL | SQLite dev 跳过（`if connection.vendor != "postgresql": return`） | SQLite 不支持 `pg_advisory_xact_lock` | 仅 dev 环境生效，PG 生产路径不变 |
| AUTH-001 §BR-02 错误码字面错位（DEV-8） | AUTH-001 §BR-02 错误表登记 `409 RESOURCE_ALREADY_EXISTS` | `apps/api/plane/app/views/auth.py:62` 实现 `AUTH_EMAIL_EXISTS` | 错误码字面错位（实现优先） | **当前实现优先；Sprint 1 文档回改统一**（统一为 AUTH_EMAIL_EXISTS 或统一为 RESOURCE_ALREADY_EXISTS） |
