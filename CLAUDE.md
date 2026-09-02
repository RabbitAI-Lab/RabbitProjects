# RabbitProjects — Claude Code 项目指南

企业级项目管理系统（对标 Ones / Plane）。pnpm + Turborepo Monorepo：`apps/{web,admin,space,api,live,proxy}` + `packages/{ui,editor,types,shared-state,tailwind-config}`。

## 环境要求（版本锁死，见 `docs/architecture/tech-stack.md`）

| 依赖 | 版本 | 来源 |
| --- | --- | --- |
| Node | 22.14.x（`>=22.14 <23`） | nvm（`nvm use 22.14.0`） |
| pnpm | 11.x | Corepack（`corepack enable && corepack prepare pnpm@11.0.0 --activate`） |
| Python | 3.12.x | uv（`apps/api/.venv`，`uv sync --project apps/api`） |
| JDK | 17（JMeter 用） | SDKMAN `~/.sdkman/candidates/java/17.0.20-kona` |
| JMeter | 5.6.3 | `~/apache-jmeter-5.6.3/` |
| PostgreSQL | 17-alpine（容器） | `docker run --name rp-pg --network rp-net -e POSTGRES_USER=rp -e POSTGRES_PASSWORD=rp -e POSTGRES_DB=rabbit_projects -p 5432:5432 postgres:17-alpine` |

**注意**：每个新 shell 需 `source ~/.nvm/nvm.sh && nvm use 22.14.0 --silent`，否则 corepack 会用错 Node 版本（PATH 里可能有 v24）。JMeter 需要 `export JAVA_HOME=~/.sdkman/candidates/java/17.0.20-kona`。

## 常用命令

```bash
pnpm dev                  # web(3001) + live(3000) + 依赖包 watch
pnpm build / typecheck / lint / check:yjs
pnpm compose:up           # 14 服务全套（deploy/compose/）
# Django（在 apps/api 目录或用 uv run --project apps/api）
uv run --project apps/api python apps/api/manage.py runserver 0.0.0.0:8000
```

API 启动需要环境变量：`DATABASE_URL=postgresql://rp:rp@localhost:5432/rabbit_projects SECRET_KEY=dev`。

## 测试（本地全部跑通后才入库）

```bash
# 1) 接口端到端（10a 删除 + 10b 越权，覆盖 TC-PROJ1-007/AUTH3-001）
python3 tests/jmeter/sprint-0-flow.py [http://localhost:8000]

# 2) Playwright e2e（web dev server 需已在 3001；API 在 8000）
E2E_NO_SERVER=1 pnpm exec playwright test

# 3) JMeter 性能压测
export JAVA_HOME=~/.sdkman/candidates/java/17.0.20-kona
jmeter -n -t tests/jmeter/sprint-0-flow.jmx -l result.jtl -e -o report
```

PG schema 准备（Django migrate 在 PG 上有已知问题，见下面"坑"）：按 `tests/e2e/PG_README.md` 走 sqlmigrate + 手工建扩展/索引 + `migrate --fake`。

测试用例文档：`docs/sprint-0-poc/test-cases.md`（114 条用例，5 轮评分 ≥9.5 收口）。

## 已知坑（踩过的，别再踩）

1. **Django `migrate` 在 PG 上失败**（`Related model 'db.user' cannot be resolved`，Django 5.1 + 自定义 User + swappable 的已知问题）。解法：`sqlmigrate` 导出 DDL 手工灌 + `migrate --fake`。扩展（pg_trgm/btree_gin）必须建在 GIN 索引**之前**（已拆为独立的 `0001_extensions` migration）。
2. **登录后 CSRF token 轮换**：Django `login()` 会 rotate CSRF。登录/任何状态变更后的下一个 POST 前必须重新 GET `/api/v1/auth/csrf-token/`（`sprint-0-flow.py` 的 `fresh_csrf()`）。
3. **HTML 禁止 `<button>` 嵌套**：解析器自动闭合外层 button 导致 DOM 塌陷（高保真原型踩过）。React/JSX 不报错，靠 review 防护。
4. **pre-commit 钩子当前跳过 lint-staged**（与 harness 文件回滚冲突）。lint 由 CI 全量兜底。不要用 `--no-verify`（被 hook 拦）。
5. **commitlint**：subject 不能 sentence-case 开头大写（中文开头最稳）；type 必须 `feat/fix/docs/test/chore` 等；scope 白名单见 `commitlint.config.js`。
6. **pnpm 11**：构建脚本白名单字段是 `allowBuilds`（对象），不是文档写的 `onlyBuiltDependencies`（pnpm 10 字段）。ADR-0001 已登记。
7. **turbo 2.5**：`dev` 任务不能 dependsOn persistent 任务（`dev:watch`）；改根脚本 `turbo run dev dev:watch` 并行。
8. **tsup**：必须显式入口 `tsup src/index.ts --dts --format esm`（默认 cjs 与 exports 不符）。
9. **advisory lock**：`acquire_project_lock` 在非 PG 后端（SQLite dev）直接跳过；CI/生产走 PG 不受影响。
10. **GateGuard hook**：本仓库 Bash/Edit/Write 首次调用会要求陈述事实，按提示输出后重试即可。

## 文档体系（改代码前先读对应文档）

- `docs/architecture/`（7 份）：技术栈/目录/API 规范/数据模型/权限——全局约束
- `docs/sprint-N-*/`：每个功能一份规格文档（含 §3 UI 规格、§4 API 契约）；sprint-0 有 `test-cases.md`
- `docs/adr/`：实现偏差登记（ADR-0001，8 项）——实现与文档不一致时**先登记再继续**
- `docs/design/sprint-0-hifi-prototype.html`：冻结的高保真原型，前端实现的视觉/交互验收基准
- `docs/plan/文档质量评审状态.md`：文档评审 master 记录
- 文档质量门槛：5 维度（完整性/一致性/可实施性/可测性/清晰度）全部 ≥9.5

## 工作流约定

- 有 UI 交互的迭代：开发前必须先有高保真可交互设计稿并评审冻结（需求文档 §8.3 修订）
- 实现偏差 → ADR 登记 → 后续 Sprint 回改文档
- GateGuard 事实陈述、lint/commitlint 钩子是刻意保留的纪律，不要绕过
