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
# 1) 接口端到端 —— 两条流程并列，都是 CI gate
python3 tests/jmeter/sprint-0-flow.py [http://localhost:8000]   # 10 步（10a 删除 + 10b 越权）
python3 tests/jmeter/sprint-1-flow.py [http://localhost:8000]   # 信封 C1 / 权限快照 / sort_order / 搜索收藏归档 / 隔离
python3 tests/jmeter/api-full-coverage.py                       # 端点 × 方法 × 正负例 契约矩阵

# 2) L1/L2 静态检查（含 api-ci 平价三件套：与 .github/workflows/api-ci.yml 同 cwd 同命令）
bash tests/run-ci-checks.sh   # ruff/mypy/pytest 必须在 apps/api 目录跑（uv run --project 不变 cwd）

# 3) Playwright e2e（web dev server 需已在 3001；API 在 8000）
E2E_NO_SERVER=1 pnpm exec playwright test   # auth.spec.ts + coverage.spec.ts + interactions.spec.ts + parity.spec.ts

# 4) JMeter 性能压测
export JAVA_HOME=~/.sdkman/candidates/java/17.0.20-kona
jmeter -n -t tests/jmeter/sprint-0-flow.jmx -l result.jtl -e -o report
```

**契约常量唯一定义点：`tests/jmeter/_contract.py`**（HTTP 状态码表 / 错误码 / 信封字段路径 / `Client` / 断言辅助）。sprint-0 把状态码表写在 `sprint-0-flow.py` 顶部，但该脚本无 `__main__` 守卫、一 import 就跑完整条流程，导致「唯一真相源」实际无法复用；新脚本一律 import `_contract`，禁止各自硬编码（ADR-0012 E4）。

PG schema 准备（Django migrate 在 PG 上有已知问题，见下面"坑"）：按 `tests/e2e/PG_README.md` 走 sqlmigrate + 手工建扩展/索引 + `migrate --fake`。

测试用例文档：`docs/sprint-0-poc/test-cases.md`（114 条用例 + **附录 C UI 表面清单 C.1~C.36**，全迭代共用）｜`docs/sprint-1-mvp/test-cases.md`（sprint-1 用例，含回归锚点附录）。

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
11. **禁止用 `document.cookie` 嗅探 `sessionid`**：Django 默认 `SESSION_COOKIE_HTTPONLY=True`，前端 JS **恒读不到**，判据永远为 false —— 曾让 `labels-admin` 的 `load()` 直接短路（标签列表永远空、新建后也不刷新）。要判断「有无会话」请用 `apps/web/app/services/session-probe.ts` 的 `rp_session` 探针 cookie（SessionStore 在登录/bootstrap 成功时写、登出或 bootstrap 失败时清）。
12. **Django `reverse()` 必须带 `app:` 命名空间**：`plane/urls.py` 是 `include((..., "app"), namespace="app")`，裸名 reverse 必抛 `NoReverseMatch`；一旦外层有 `except Exception: return ""`，错误就被吞成静默空串（附件 `download_url` 曾因此为空 → 下载按钮跳 `/undefined`）。捕获要收窄到 `NoReverseMatch`。
13. **网关 `/uploads/` 反代是附件直传的必经环节**：presign 返回的是同源 `/uploads/<bucket>/<key>?X-Amz-…`，缺 Nginx `location ^~ /uploads/`（生产）或 Vite `/uploads` proxy（dev）就会静默 404；且必须 `Host $proxy_host` / `changeOrigin: true`，因为 SigV4 把 host 纳入签名，透传浏览器 Host 会让 MinIO 判签名不匹配。

## 文档体系（改代码前先读对应文档）

- `docs/architecture/`（7 份）：技术栈/目录/API 规范/数据模型/权限——全局约束
- `docs/sprint-N-*/`：每个功能一份规格文档（含 §3 UI 规格、§4 API 契约）；sprint-0 有 `test-cases.md`
- `docs/adr/`：实现偏差登记——实现与文档不一致时**先登记再继续**
  - ADR-0001（Sprint 0 偏差，8 项）｜ADR-0010（UI parity 五步纪律）
  - ADR-0011（Sprint 1 跨文档 UI 矛盾裁决，20 项定稿）｜ADR-0012（Sprint 1 实现偏差，A~E 五类）
- `docs/design/sprint-0-hifi-prototype.html`：Sprint 0 冻结原型
- `docs/design/sprint-1-hifi-prototype.html`：**Sprint 1 冻结原型（FROZEN 2026-09-04）**，前端实现的视觉/交互验收基准；头部含冻结记录（审计范围 / 误报复核 / 实补 8 项缺口）
- `docs/plan/文档质量评审状态.md`：文档评审 master 记录
- 文档质量门槛：5 维度（完整性/一致性/可实施性/可测性/清晰度）全部 ≥9.5

## 工作流约定

- 有 UI 交互的迭代：开发前必须先有高保真可交互设计稿并评审冻结（需求文档 §8.3 修订）
- **UI parity 五步纪律（ADR-0010，强制）**：① 新页面/弹窗先入 UI 表面清单（test-cases.md 附录 C）再实现，**每行必须标来源（文档 §x.x），禁止凭记忆概括**——首版清单就因顶栏一行概括成"切换器（logo+名称+▾）"漏掉整个下拉/建团队/头像菜单；② 组件完成的定义 = 清单行核对通过 + `tests/e2e/parity.spec.ts` 补对应字段断言（带出处注释）；③ e2e 断言由清单生成，不由实现反推（防自我印证）；④ 迭代收口前由**未参与实现的 subagent** 反向扫全部文档 §3 核对清单覆盖率（文档 vs 清单，不是实现 vs 文档）；⑤ 条件态/下拉内容/禁用态/空态/加载态/toast 文案是漏项高发区，逐类过
- 实现偏差 → ADR 登记 → 后续 Sprint 回改文档
- GateGuard 事实陈述、lint/commitlint 钩子是刻意保留的纪律，不要绕过

### 测试脚本规范（写 e2e/接口/静态检查时强制）

- **API 真相源唯一**：`tests/jmeter/_contract.py` 是后端契约的事实来源（HTTP 状态码 + 错误码 + 信封字段路径）；`tests/e2e/no-console-errors.ts` 的 `API_TRUTH` 镜像同一份契约。**所有接口脚本、e2e 与静态断言必须 import 这两份，禁止各自硬编码**——双源必漂。（sprint-0 曾把该表放在 `sprint-0-flow.py` 顶部，但那个脚本没有 `__main__` 守卫、一 import 就跑完整条流程，实际无法被复用，见 ADR-0012 E4）
- **Playwright spec 必装 console guard**：`attachConsoleGuard(page)` + `expect.soft(errors).toEqual([])`（见 `no-console-errors.ts` 白名单示例：vite HMR / DevTools 下载提示）
- **跨 test 状态边界**：每个 `test.describe` 必须自带 `beforeEach`/`afterEach`——`signOut/clearCookies/重置 store` 显式调用，**禁止依赖 Worker 复用 page 自动清理**（实测根因：跨 spec 缓存 `isBootstrapped=true`，下一个 spec 守卫直接 return 不重检）
- **Playwright ≥ 1.62 + reporter = line**：1.54/1.56 的 list reporter 在 `expect.soft` 失败时 NPE（`base.js:320 undefined.startsWith`），CI/本地都改用 line 避开；CI 环境 reporter 切 `github`
- **性能压测 vs 端到端分清**：`tests/jmeter/sprint-0-flow.py` 是 CI gate（10 步单线程）；`tests/jmeter/sprint-0-flow.jmx` 是性能压测（多线程 / 持续时间 / 报告），不要混用
- **加/移 SerializerMethodField 必加 Meta.fields**：GET 路径不触发 `get_field_names` assert，PATCH/POST 路径触发 500（教训 #3）
- **dropdown 全局点击监听禁 document click**：改 `mousedown` 阶段 + `target.closest('[data-sb-scope="..."]')` 判范围，破坏性操作（登出/删账户）继续用 `location.href` 全量重载（教训 #4）
- **行为断言三件套（sprint-1 验收教训）**：`toBeVisible` 只证明渲染、不证明接线。任何交互控件的测试覆盖 = ①点击/输入 → ②`waitForResponse` 断言对应 PATCH/POST 发出且 2xx → ③UI 回读确认新值（重开抽屉/刷新后仍在更好）。存在性断言只能算 parity，不算行为测试。
- **禁止空转（vacuous）断言**：不得用不存在的资源做被测路径的断言（如 `goto("/__no_such_ws__/")` 后断 URL 不变——真实代码路径根本没执行）；需要登录态的页面必须显式登录（先 `clearCookies`），禁止依赖前序 spec 泄漏的会话。CI 用 grep 扫 `__no_such` / `__bogus` 类资源在**非错误分支测试**里的使用（TC-INF4-016）。
- **演示数据健康检查**：演示账号是验收环境也是测试环境。`scripts/seed_demo_history.py` 保证 stats 非零 + 趋势非平；C.35 golden-path spec 断言真实数据非空。改演示数据结构时两者必须同步。
