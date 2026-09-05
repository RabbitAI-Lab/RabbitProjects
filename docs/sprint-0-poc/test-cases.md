# Sprint 0 测试用例文档

> **本文档定位**：覆盖 Sprint 0 全部 10 份功能文档（INFRA-001/002/003 + AUTH-001/002/003 + TEAM-001 + PROJ-001 + TASK-001 + BOARD-001）的测试用例集合，按 5 维度（完整性 / 一致性 / 可实施性 / 可测性 / 清晰度）进行质量评审，>9.5 通过。

| 元信息项 | 内容 |
| --- | --- |
| 所属迭代 | Sprint 0 — POC 技术验证 |
| 周期 | 第 1-2 周 |
| 文档数 | 10 份功能文档 → 对应 10 组测试用例 |
| 评分维度 | 完整性 / 一致性 / 可实施性 / 可测性 / 清晰度，各 10 分，0.5 步进 |
| 通过标准 | 5 项 **全部 ≥9.5**（>9.5，0.5 步进） |
| 评审流程 | 1 个评分 subagent → master 汇总 → 修复 subagent → 再评分。最多 **5 轮迭代**，任一轮全过即收敛 |
| 关联交付 | JMeter 脚本 `tests/jmeter/sprint-0-flow.jmx`（性能压测）、Python 等价 `tests/jmeter/sprint-0-flow.py`（CI 单线程端到端）、Playwright e2e `tests/e2e/auth.spec.ts` |

---

## 0. 测试基线与执行入口

### 0.1 前置依赖

| 类别 | 项 | 状态 |
| --- | --- | --- |
| 运行时 | PostgreSQL 17 容器（`docker run --name rp-pg --network rp-net -e POSTGRES_USER=rp -e POSTGRES_PASSWORD=rp -e POSTGRES_DB=rabbit_projects -p 5432:5432 postgres:17-alpine`） | ✅ 已落地 |
| 运行时 | PG schema 26 表 + btree_gin + pg_trgm 扩展（按 `tests/e2e/PG_README.md` 准备） | ✅ 已落地 |
| 运行时 | Django 5.1 + Python 3.12 + uv 同步 | ✅ |
| 运行时 | JDK 17（SDKMAN `sdk install java 17.0.20-kona`） | ✅ |
| 运行时 | JMeter 5.6.3（`~/apache-jmeter-5.6.3/`） | ✅ |
| 运行时 | Node 22.14 + pnpm 11（nvm default） | ✅ |
| 执行 | `pnpm dev:all` 起 web (3001) + live (3000) + api (8000) | ✅ |
| 执行 | `python3 tests/jmeter/sprint-0-flow.py`（CI 端到端 10 步） | ✅ |
| 执行 | `pnpm exec playwright test`（Playwright e2e 7 个 spec：auth 3 + coverage 4） | ✅ |
| 执行 | `bash tests/run-ci-checks.sh`（L1/L2 静态检查 36 条） | ✅ |
| 执行 | `jmeter -n -t tests/jmeter/sprint-0-flow.jmx`（性能压测） | ✅ 加载校验 |

### 0.2 三套测试分工

| 工具 | 角色 | 覆盖 | 触发时机 |
| --- | --- | --- | --- |
| `tests/jmeter/sprint-0-flow.py` | CI 端到端（单线程） | 10 步业务流正确性断言（业务断言为正） | PR 必跑（gate） |
| `tests/jmeter/sprint-0-flow.jmx` | 性能压测 | 同 10 步业务流，多线程 / 持续时间 / 吞吐量 | 性能基线 / 上线前 |
| `tests/e2e/auth.spec.ts` | 浏览器端到端 | 完整动线 + 路由守卫 + demo 账号（UI 验证） | PR 必跑（gate） |
| `tests/e2e/coverage.spec.ts` | 浏览器端到端（补全） | TC-AUTH2-007/008/009 + TC-PROJ1-007a（原 Nightly/占位） | PR 必跑（gate） |
| `tests/run-ci-checks.sh` | L1/L2 静态检查 | INFRA/AUTH3/TASK/BOARD 共 36 条命令断言 | PR 必跑（gate） |

---

## 1. INFRA-001 Monorepo 骨架

### 1.1 目标
验证 Monorepo 骨架搭建正确性：4 应用 + 5 共享包 + Compose 校验 + Yjs 跨包同版本 + oxlint/tsup/turbo 配置可加载。

### 1.2 前置
- Node 22.14 + pnpm 11 + Corepack
- 仓库根目录含 `package.json` / `pnpm-workspace.yaml` / `turbo.json` / `tsconfig.base.json` / `.oxlintrc.json`

### 1.3 用例清单

| ID | 级别 | 标题 | 前置/依赖 | 步骤 | 预期 | 自动化 | 判分锚点 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| TC-INF1-001 | L1 单元 | `pnpm-workspace.yaml` 排除 api/proxy | 仓库根 | `grep "!-apps/api\\|!-apps/proxy" pnpm-workspace.yaml` | 命中 2 行排除项 | 是 | grep 命中行数 = 2 |
| TC-INF1-002 | L1 单元 | allowBuilds 包含三个原生构建包 | 仓库根 | `python3 -c "import yaml;d=yaml.safe_load(open('pnpm-workspace.yaml'));print(set(['esbuild','@tailwindcss/oxide','sharp']).issubset(set(d['allowBuilds'])))"` | True | 是 | 输出 = True |
| TC-INF1-003 | L1 单元 | turbo 9 任务命名 | 仓库根 | `python3 -c "import json;d=json.load(open('turbo.json'));print(set(d['tasks'].keys())>=set(['build','dev','lint','typecheck','test','dev:watch','clean','format:check','storybook','build-storybook']))"` | True | 是 | 输出 = True |
| TC-INF1-004 | L1 单元 | Yjs 跨包同版本（RedLine） | 仓库根 | `node scripts/check-yjs-version.mjs` | 退出码 0；输出三行 ✓ | 是 | 退出码 = 0；stdout 中 ✓ 行数 = 3 |
| TC-INF1-005 | L2 集成 | `pnpm install` 全量装 | 仓库根 | `pnpm install --frozen-lockfile && du -sh node_modules/.pnpm` | 退出码 0；du 输出 size ≥ 800MB | 是 | 退出码 = 0 且 `node_modules/.pnpm` 目录存在且 size ≥ 800MB |
| TC-INF1-006 | L2 集成 | `pnpm build` 9 包全过 | 仓库根 | `pnpm build` | 退出码 0；9 包全过（4 业务包：web/admin/space/live + 5 共享包：ui/editor/types/shared-state/tailwind-config），差异登记为 DEV-5b | 是 | 退出码 = 0；成功构建任务数 = 9 |
| TC-INF1-007 | L2 集成 | `turbo run typecheck` 全过 | 仓库根 | `pnpm typecheck` | 退出码 0；12 个 typecheck 任务成功 | 是 | 退出码 = 0；typecheck 任务数 = 12 |
| TC-INF1-008 | L2 集成 | `pnpm lint` 全过 | 仓库根 | `pnpm lint` | 退出码 0；零 error | 是 | 退出码 = 0；error 数 = 0 |
| TC-INF1-009 | L2 集成 | `pnpm dev:all` 四服务可访问 | 仓库根 | `pnpm dev:all` 后台 → `curl :3001/ :3000/health :8000/api/v1/health/` | 4 个端点全 200 | 是 | 4 个端点 HTTP 200 |
| TC-INF1-010 | L3 端到端 | dev 重启后 HMR 仍可用 | TC-INF1-009 | 修改 `apps/web/app/routes/home.tsx` 一行文字 → `curl :3001/` | 文本变化在 ≤5s 内反映 | 是 | curl 返回体含修改后文本，且 ≤5s 内反映 |
| TC-INF1-011 | L3 端到端 | Pre-push 钩子执行 typecheck + yjs 校验 | TC-INF1-007 | `git commit --allow-empty -m "trigger pre-push" && git push` 或直接 `pnpm exec husky run pre-push` | typecheck 触发并通过 | 是 | husky run 退出码 = 0；输出含 "typecheck passed" |
| TC-INF1-012 | L3 端到端 | `pnpm ci:affected` 仅评估受影响包 | TC-INF1-005 | `echo "// touch" >> packages/ui/src/cx.ts && git add -A && git commit -m "trigger ci:affected test" && pnpm ci:affected \| grep "@rp/ui"` | 仅 `@rp/ui` + 下游 web/admin 评估，editor/type 不评估 | 是 | 评估集合 ⊇ {@rp/ui, @rp/web, @rp/admin}；⊅ {@rp/types} |
| TC-INF1-013 | L3 端到端 | compose config 校验 | 仓库根 | `docker compose --env-file .env -f deploy/compose/docker-compose.yml config --services \| wc -l` | 输出 14（含 migrator + createbuckets） | 是 | wc -l 输出 = 14 |
| TC-INF1-014 | L3 端到端 | lockfile 与 package.json 一致 | TC-INF1-005 | `pnpm install --frozen-lockfile`（clean cache 后） | 退出码 0，lockfile 无变化 | 是 | 退出码 = 0；git diff pnpm-lock.yaml 输出为空 |

### 1.4 已知偏差（实测已记录在 `docs/adr/0001-sprint-0-impl-deviations.md`）

| 偏差 ID | 描述 | 处置 |
| --- | --- | --- |
| DEV-1 | `onlyBuiltDependencies` 改用 `allowBuilds` 对象（pnpm 11） | ADR 待回改 |
| DEV-2 | turbo `dev` 不依赖 `dev:watch`，改根脚本并行 | ADR 待回改 |
| DEV-3 | `tsup src/index.ts --dts --format esm` 显式入口 | ADR 待回改 |
| DEV-4 | oxlint 核心规则 `eslint/` 前缀；`react-in-jsx-scope` 关闭 | ADR 待回改 |
| DEV-5a | turbo `globalPassThroughEnv` 加 LIVE_PORT / API_INTERNAL_URL（跨 app 端口转发未走 .env 直注） | turbo.json 配置加 `globalPassThroughEnv: ["LIVE_PORT", "API_INTERNAL_URL"]`；Sprint 0 不阻塞，R2 修 |

---

## 2. INFRA-002 Docker Compose 全套编排

### 2.1 目标
验证 14 服务编排（含 migrator / createbuckets / 五层依赖序 / 健康检查 / 环境变量注入策略）。

### 2.2 前置
- Docker 27+；`.env.example` 已落地；`init-extensions.sql` 含 `pg_trgm` + `btree_gin`

### 2.3 用例清单

| ID | 级别 | 标题 | 前置/依赖 | 步骤 | 预期 | 自动化 | 判分锚点 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| TC-INF2-001 | L2 集成 | compose config 解析 | Docker 27+；`.env.example` 已落地 | `docker compose --env-file .env -f deploy/compose/docker-compose.yml config --services` | 输出 14 服务名 | 是 | 服务数 = 14 |
| TC-INF2-002 | L2 集成 | 缺失必填变量报错 | — | step 1: `mv .env /tmp/.env.bak`；step 2: `docker compose --env-file /tmp/.env.bak -f deploy/compose/docker-compose.yml config 2>&1`；step 3（回滚）: `mv /tmp/.env.bak .env` | 报 `variable POSTGRES_PASSWORD is required`（花括号闭合） | 是 | 报错文案命中 "POSTGRES_PASSWORD is required" |
| TC-INF2-003 | L2 集成 | 5 层依赖序 | TC-INF2-001 | `docker compose config \| grep -A2 depends_on` | migrator 依赖 db healthy；api 依赖 db/redis/mq healthy + migrator completed_successfully | 是 | depends_on 顺序 migrator→db healthy；api→4 项 |
| TC-INF2-004 | L2 集成 | migrate 不在 api entrypoint | — | `grep "manage.py migrate" apps/api/bin/docker-entrypoint-api.sh` | 退出码 1（找不到）—— 证明 api 仅启 gunicorn | 是 | 退出码 = 1 |
| TC-INF2-005 | L2 集成 | migrator entrypoint 真跑 migrate | — | `grep "manage.py migrate" apps/api/bin/docker-entrypoint-migrator.sh` | 退出码 0（找到 migrate + --noinput） | 是 | 退出码 = 0 |
| TC-INF2-006 | L2 集成 | 一次性服务 restart=no | TC-INF2-001 | `grep -B1 -A3 'createbuckets:\|migrator:' deploy/compose/docker-compose.yml \| grep restart` | 命中 `restart: "no"`（外层单引号包裹命令，内层双引号原样保留） | 是 | 命中行含 `restart: "no"` |
| TC-INF2-007 | L2 集成 | healthcheck 覆盖所有有状态 | TC-INF2-001 | `grep -c healthcheck deploy/compose/docker-compose.yml` | ≥ 9（db/redis/mq/minio/api/worker/live/web/admin/space/proxy） | 是 | 健康检查数 ≥ 9 |
| TC-INF2-008 | L2 集成 | RabbitMQ start_period ≥ 30s | TC-INF2-001 | `grep -A4 "mq:" deploy/compose/docker-compose.yml \| grep start_period` | 命中 `40s` | 是 | start_period = 40s |
| TC-INF2-009 | L2 集成 | Nginx 5 路由 | — | `grep -E "location .*proxy_pass" apps/proxy/nginx.conf.template` | 命中 5 个 location block（/api /live /god-mode /spaces /） | 是 | location block 数 = 5 |
| TC-INF2-010 | L2 集成 | WebSocket upgrade 头注入 | TC-INF2-009 | `grep -A4 "live/" apps/proxy/nginx.conf.template \| grep -E "Upgrade\|Connection"` | 命中 `proxy_set_header Upgrade $http_upgrade` | 是 | 命中 `Upgrade $http_upgrade` |
| TC-INF2-011 | L2 集成 | init-extensions.sql 含 pg_trgm | — | `grep pg_trgm deploy/compose/init/init-extensions.sql` | 退出码 0 | 是 | 退出码 = 0 |
| TC-INF2-012a | L2 集成 | compose config 服务数 + 依赖序 CI gate | 仓库根 | `docker compose --env-file .env -f deploy/compose/docker-compose.yml config --services \| wc -l` 与 `docker compose config \| grep -A2 depends_on` | 输出 14；依赖序 migrator→db healthy；api→db/redis/mq healthy + migrator completed_successfully | 是 | 服务数 = 14；依赖序字符串命中 |
| TC-INF2-012b | L3 端到端 | `docker compose up -d` 全容器启动（nightly / 本地） | TC-INF2-012a | 全新机器：`git clone && cp .env.example .env && docker compose up -d` → 3 分钟内 `docker compose ps \| grep healthy` | 12 业务容器 healthy；migrator exited (0)；createbuckets exited (0) | 是 | healthy 容器数 = 12；migrator 退出码 = 0；createbuckets 退出码 = 0 |
| TC-INF2-013 | L3 端到端 | `docker compose down -v && up` 重置幂等 | TC-INF2-012b | `docker compose down -v && up -d` | 再次 12 容器 healthy；PG 数据重建；admin 用户重新可用 | 是 | 二次启动 healthy = 12；admin 可登录 |
| TC-INF2-014 | L2 集成 | env_file 注入 Django 变量 + 6 个必填校验 | TC-INF2-001 | `grep -A2 env_file apps/api/plane/settings/common.py` 或 compose 文件；并 `docker compose config \| grep -E "POSTGRES_USER\|POSTGRES_PASSWORD\|RABBITMQ_DEFAULT_USER\|RABBITMQ_DEFAULT_PASS\|MINIO_ROOT_USER\|MINIO_ROOT_PASSWORD"` | api/worker/beat/migrator 用 env_file;live/proxy 用 environment 显式；6 个必填变量含 `${VAR:?VAR is required}` 形式 | 是 | 6 必填变量均含 `${VAR:?VAR is required}` 形式 |
| TC-INF2-015 | L2 集成 | VITE_* 走 build.args | — | `grep -A2 args apps/web/Dockerfile` | 命中 `VITE_API_BASE_URL` 与 `VITE_LIVE_BASE_URL` | 是 | 命中 2 个 VITE_* arg |

---

## 3. INFRA-003 Django 数据模型

### 3.1 目标
验证 14 个领域模型 + 一次性扩展迁移 + TrigramExtension 在 GIN trgm 索引之前执行 + 软删除 + 审计字段 + advisory lock。

### 3.2 前置
- 真实 PG 17 容器
- 应用 `tests/e2e/PG_README.md` 流程：手动 sqlmigrate + extensions + GIN 索引

### 3.3 用例清单

| ID | 级别 | 标题 | 前置/依赖 | 步骤 | 预期 | 自动化 | 判分锚点 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| TC-INF3-001 | L1 单元 | User 手工对齐 BaseModel 三项 | — | `grep "id = models.UUIDField\|created_at = models.DateTimeField\|deleted_at = models.DateTimeField" apps/api/plane/db/models/user.py` | 三项命中（PK/auto_now_add/auto_now + nullable deleted_at） | 是 | 命中行数 = 3 |
| TC-INF3-002 | L1 单元 | AUTH_USER_MODEL = "db.User" | — | `grep "AUTH_USER_MODEL" apps/api/plane/settings/common.py` | 命中 | 是 | 退出码 = 0 |
| TC-INF3-003 | L1 单元 | swappable = AUTH_USER_MODEL | — | `grep "swappable" apps/api/plane/db/models/user.py` | 命中 | 是 | 退出码 = 0 |
| TC-INF3-004 | L1 单元 | BaseModel 含 UUID + 审计 + 软删除 | — | `grep -c "SoftDeleteManager\|all_objects\|deleted_at" apps/api/plane/db/models/base.py` | ≥ 4 | 是 | 命中数 ≥ 4 |
| TC-INF3-005 | L2 集成 | TrigramExtension 拆分前置迁移 | TC-INF3-006 | `head -5 apps/api/plane/db/migrations/0001_extensions.py` | 含 `TrigramExtension()` + 扩展名不包含其他模型 | 是 | 首 5 行含 `TrigramExtension()` |
| TC-INF3-006 | L2 集成 | 14 个领域模型注册 | — | `python3 -c "from plane.db.models import User,Workspace,WorkspaceMember,Project,ProjectMember,SystemAdmin,IssueType,State,Label,Issue,IssueAssignee,IssueLabel,IssueActivity,IssueLink; print(14)"` | 14 | 是 | 输出 = 14 |
| TC-INF3-007 | L2 集成 | Issue 含 P0-P4 全列 | TC-INF3-006 | `python3 -c "from plane.db.models.issue import Issue;print('custom_fields' in [f.name for f in Issue._meta.fields])"` | True | 是 | 输出 = True |
| TC-INF3-008 | L2 集成 | GIN custom_fields 索引 | TC-INF3-005 | 真实 PG：`docker exec rp-pg psql -U rp -d rabbit_projects -c "\\d issues"` | 命中 `idx_issue_custom_fields` USING GIN | 是 | 索引名 `idx_issue_custom_fields` USING GIN |
| TC-INF3-009 | L2 集成 | GIN trgm 索引 | TC-INF3-008 | 真实 PG：`\\d issues` | 命中 `idx_issue_desc_trgm` USING GIN (gin_trgm_ops) | 是 | 索引名 `idx_issue_desc_trgm` USING GIN (gin_trgm_ops) |
| TC-INF3-010 | L2 集成 | 软删除 manager | — | `python3 -c "from plane.db.models.base import SoftDeleteManager;print(SoftDeleteManager)"` | 不报错 | 是 | 退出码 = 0 |
| TC-INF3-011 | L3 端到端 | advisory lock 生成 sequence_id 连续 | TC-INF3-007 | 真实 PG：连续 PATCH `/api/v1/workspaces/{slug}/projects/{pid}/issues/` 3 次创建任务 | sequence_id = 1, 2, 3 严格递增（DB 验证：`SELECT sequence_id FROM issues ORDER BY sequence_id`） | 是 | sequence_id = 1, 2, 3 |
| TC-INF3-012 | L3 端到端 | 软删除过滤默认 Manager | TC-INF3-007 | `python3 -c "from plane.db.models import Issue;Issue.objects.count()==0; Issue.objects.create(...); Issue.objects.count()==1; Issue.all_objects.create(...); Issue.objects.count()==1"` | 默认 Manager 看不到软删，`all_objects` 看到 | 是 | count(objects) = 1；count(all_objects) = 2 |
| TC-INF3-013 | L2 集成 | unique_together 与 UniqueConstraint 区分 | — | `grep -A1 unique_together apps/api/plane/db/models/workspace.py` | workspace_member 用 unique_together；state/issue_type/identifier 用 UniqueConstraint | 是 | workspace_member 命中 unique_together；state 命中 UniqueConstraint |
| TC-INF3-014 | L2 集成 | IssueState 5 group 枚举 | — | `grep -A6 "class Group" apps/api/plane/db/models/state.py` | 含 backlog/unstarted/started/completed/cancelled | 是 | group 枚举值数 = 5 |
| TC-INF3-015 | L2 集成 | IssueType is_system 字段 + P0 种 1 条 | — | `grep is_system apps/api/plane/db/models/issue_type.py` 与 `python manage.py shell -c "from plane.db.models import IssueType; print(IssueType.objects.filter(is_system=True).count())"` | 命中 `is_system` 字段定义；DB 含 1 条 issue_types（name='任务'，is_system=True，对齐 INFRA-003 §4.13） | 是 | is_system=True 条数 = 1 |
| TC-INF3-016 | L2 集成 | Issue 保存剥离 HTML | TC-INF3-007 | `python manage.py shell -c "from plane.db.models import Issue; i = Issue.objects.first(); i.description_html = '<p>hello <b>world</b></p>'; i.save(); i.refresh_from_db(); print(i.description_stripped)"` | 输出 `hello world`（`<p>` 与 `<b>` 被剥） | 是 | description_stripped = "hello world" |
| TC-INF3-017 | L2 集成 | Issue.completed_at 在首次进入 completed 组时写入 | TC-INF3-014 | `python manage.py shell -c "from plane.db.models import Issue; i = Issue.objects.first(); i.state_id = <started_state_id>; i.save(); i.state_id = <completed_state_id>; i.save(); i.refresh_from_db(); print(i.completed_at)"` | 输出非空 datetime（completed_at 在第二次 save 后被自动写入） | 是 | completed_at 非空 datetime |

---

## 4. AUTH-001 注册 / 登录 / 退出

### 4.1 目标
Argon2 密码哈希 + Session + CSRF 双提交 + 注册事务内原子初始化默认团队。

### 4.2 前置
- TC-INF2-012b 已过（12 业务容器 healthy）；Python 3.12 + requests 库；demo 账号 `zhangsan@rabbit.dev / Rabbit123` 在 `tests/jmeter/sprint-0-flow.py` 已注册

### 4.3 用例清单

| ID | 级别 | 标题 | 前置/依赖 | 步骤 | 预期 | 自动化 | 判分锚点 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| TC-AUTH1-001 | L3 端到端 | 注册 60s 内完成并落默认团队 | TC-INF2-012b | Python 等价脚本 step 03：`import requests, time; ts=int(time.time()); s=requests.Session(); r=s.post('http://localhost:8000/api/v1/auth/sign-up/', json={'email':f'user_{ts}@rabbit.dev','password':'Rabbit123','first_name':'Test','last_name':'User'}); print(r.status_code, r.json()['data']['user']['email'], r.json()['data']['user']['default_workspace_slug'])` | HTTP 201；`default_workspace_slug` 非空；耗时 < 1s | 是 | HTTP 201；default_workspace_slug 非空字符串；耗时 < 1s |
| TC-AUTH1-002 | L3 端到端 | 重复邮箱注册返回 409 | TC-AUTH1-001 | 同一邮箱两次 POST sign-up | 第二次 HTTP 409，code=AUTH_EMAIL_EXISTS | 是 | HTTP 409；code=AUTH_EMAIL_EXISTS |
| TC-AUTH1-003 | L3 端到端 | 弱密码拒绝 | TC-AUTH1-001 | POST sign-up 密码 `abc` | HTTP 400，详情含"至少 8 位" | 是 | HTTP 400；detail 含 "至少 8 位" |
| TC-AUTH1-003b | L3 端到端 | BR-04 弱密码字典拒绝 | — | `curl -X POST :8000/api/v1/auth/sign-up/ -H 'Content-Type: application/json' -d '{"email":"weak@x.dev","password":"Password123","display_name":"Weak"}'` | HTTP 400，detail.code 字段含密码错误码；当前后端未启用 CommonPasswordValidator（BR-04 待实现），预期 201——**本用例作为 BR-04 落地的回归基线**，Sprint 1+ 后端补完 CommonPasswordValidator 后期望转为 400；CI 阶段同时测 `12345678aA` 验证长度合规但字典命中 | 是（step 2 字典回归） | HTTP 201（当前实现）+ HTTP 400（BR-04 落地后），双阶段断言 |
| TC-AUTH1-004 | L3 端到端 | 错误密码登录返回 401 | TC-AUTH1-001 | POST sign-in 错密码 | HTTP 401，code=AUTH_INVALID_CREDENTIALS | 是 | HTTP 401；code=AUTH_INVALID_CREDENTIALS |
| TC-AUTH1-005 | L3 端到端 | 正确密码登录 | TC-AUTH1-001 | POST sign-in 对应密码 | HTTP 200，response.data.user.email == email | 是 | HTTP 200；data.user.email 与入参 email 完全相等 |
| TC-AUTH1-006 | L3 端到端 | 禁用账号登录 | TC-AUTH1-005 | 注册后 `UPDATE users SET is_active=false WHERE email='x@x'` → sign-in | HTTP 401，code=AUTH_ACCOUNT_DISABLED | 是 | HTTP 401；code=AUTH_ACCOUNT_DISABLED |
| TC-AUTH1-007 | L3 端到端 | 退出后 session 失效 | TC-AUTH1-005 | sign-in → sign-out → GET users/me | sign-out 204；me 401 | 是 | sign-out 退出码 = 204；me 接口 HTTP 401 |
| TC-AUTH1-008 | L3 端到端 | me 接口返回登录用户 | TC-AUTH1-005 | sign-in → GET users/me | 200；data.user.email == email | 是 | HTTP 200；data.user.email 与登录邮箱完全相等 |
| TC-AUTH1-009 | L2 集成 | Argon2 密码哈希 | TC-AUTH1-001 | 直接查 PG：`SELECT password FROM users LIMIT 1` | 字符串以 `$argon2id$` 开头 | 是 | 哈希前缀 = "$argon2id$" |
| TC-AUTH1-010 | L2 集成 | 注册事务内原子性 | — | `grep "@transaction.atomic" apps/api/plane/app/views/auth.py` | 命中 | 是 | 退出码 = 0 |
| TC-AUTH1-011 | L2 集成 | CSRF token 端点 | TC-AUTH1-001 | GET auth/csrf-token | 200；data.csrf_token 长度 ≥ 32 | 是 | HTTP 200；data.csrf_token 长度 ≥ 32 |
| TC-AUTH1-012 | L3 端到端 | CSRF 拒绝：缺 / 错 token 均返回 403 | TC-AUTH1-011 | sub-step 1：POST sign-up 不带 csrf 头；sub-step 2：POST sign-up 带错误 csrf 值 | sub-step 1：403，detail 含"CSRF Failed"；sub-step 2：403（同一拒绝码） | 是 | 两次请求 HTTP 均为 403；detail 均含 "CSRF Failed" |
| TC-AUTH1-013 | L3 端到端 | 过期 csrf_token 拒绝 | TC-AUTH1-011 | POST sign-up 带手写的 32 位过期 csrf 字符串 | 403，detail 含"CSRF Failed" | 是 | HTTP 403；detail 含 "CSRF Failed" |
| TC-AUTH1-014 | L3 端到端 | 登录后 csrf rotate（关键测试） | TC-AUTH1-005；Django 默认行为（见术语表 rotate） | sign-in → 直接 POST workspace 创建项目（不重拉 csrf） | 403；**重拉 csrf 后通过**（证明 rotate） | 是 | 第一次 HTTP 403；重拉后 HTTP 201 |
| TC-AUTH1-015 | L3 端到端 | 响应信封统一 | TC-AUTH1-001 | 注册响应 JSON 解析 | 含 `status/data/meta` 三字段；HTTP code 201 | 是 | HTTP 201；响应键集合 ⊇ {status, data, meta} |
| TC-AUTH1-016 | L2 集成 | 限流频率 | — | `grep -A4 "DEFAULT_THROTTLE_CLASSES" apps/api/plane/settings/common.py` 与 AUTH-001 §4.2.4 / §4.2.5 / §4.3.5 对照 | 注册/登录 10/min（AUTH-001 §4.3.5）；csrf-token 60/min（AUTH-001 §4.2.5）；users/me 60/min（AUTH-001 §4.2.4） | 是 | sign-up/sign-in rate = 10/min；csrf-token rate = 60/min；users/me rate = 60/min |
| TC-AUTH1-017 | L3 端到端 | 展示邮箱大小写不敏感 | TC-AUTH1-001 | `POST sign-up email='User@X.com'` | 201；DB 实际存 `user@x.com`（小写归一化） | 是 | HTTP 201；DB 存储值 = "user@x.com" |

---

## 5. AUTH-002 路由拦截 + 后端鉴权

### 5.1 前置
- TC-AUTH1-005 已过（demo 账号可登录）；web (3001) + api (8000) 服务在线；Playwright 浏览器（chromium）已安装
- TC-AUTH2-007/008/009 已由 `tests/e2e/coverage.spec.ts` 落地（401/403 拦截跳转、sessionid cookie 往返、信封解包渲染），纳入 CI gate。

### 5.2 用例清单

| ID | 级别 | 标题 | 前置/依赖 | 步骤 | 预期 | 自动化 | 判分锚点 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| TC-AUTH2-001 | L3 端到端 | Playwright：未登录访问受保护路径 → 跳登录页 | TC-AUTH1-001 | `playwright test tests/e2e/auth.spec.ts:80` | 测试 pass；URL 含 `/login` | 是 | 测试 pass；window.location.pathname 以 "/login" 开头 |
| TC-AUTH2-002 | L3 端到端 | 已登录访问工作台无重定向 | TC-AUTH1-005 | `python3 -c "import requests; s=requests.Session(); s.post('http://localhost:8000/api/v1/auth/sign-in/', json={'email':'zhangsan@rabbit.dev','password':'Rabbit123'}); r=s.get('http://localhost:8000/api/v1/workspaces/workspace/projects/'); print(r.status_code)"` | HTTP 200（不重定向；不返回 30x） | 是 | r.status_code = 200 |
| TC-AUTH2-003 | L3 端到端 | 401 → next 回跳 | TC-AUTH1-007 | `python3 -c "import urllib.request; r=urllib.request.urlopen('http://localhost:3001/any-workspace/projects'); print(r.geturl())"` | 输出含 `/login?next=` | 是 | r.geturl() 含 "/login?next=" |
| TC-AUTH2-004 | L3 端到端 | 登录后 next 回跳工作台 | TC-AUTH2-003 | `python3 -c "import requests; s=requests.Session(); s.post('http://localhost:8000/api/v1/auth/sign-in/', json={'email':'zhangsan@rabbit.dev','password':'Rabbit123'}); r=s.get('http://localhost:8000/login?next=/workspace/projects', allow_redirects=False); print(r.status_code, r.headers.get('Location',''))"` | 落 `/workspace/projects`（302/200 跳；Location 含 `/workspace/projects`） | 是 | status ∈ {302, 200}；Location 含 "/workspace/projects" |
| TC-AUTH2-005 | L3 端到端 | 路由守卫：登录后访问 /any-ws/projects 不重定向（Playwright e2e） | TC-AUTH1-005；等价于 `tests/e2e/auth.spec.ts:23` "已登录访问工作台" spec | 登录 demo 账号（POST /auth/sign-in/ 拿 session），浏览器 GET /workspace/projects | URL 仍是 /workspace/projects，page 渲染"项目"标题 | 是 | URL 路径 = "/workspace/projects"；page.text() 含 "项目" |
| TC-AUTH2-006 | L3 端到端 | 路由守卫：未登录访问 /any-ws/projects 跳登录页（Playwright e2e） | TC-AUTH2-001；等价于 `tests/e2e/auth.spec.ts:80` "未登录访问受保护路由" spec | 清 cookie → 浏览器 GET /any-workspace/projects | URL 含 `/login?next=`；page 含"登录 RabbitProjects"标题 | 是 | URL 含 "/login?next="；page.text() 含 "登录 RabbitProjects" |
| TC-AUTH2-007 | L3 端到端 | 401 拦截：清 cookie 后发任何 API 请求自动跳登录页 | TC-AUTH1-001；参考 `apps/web/app/services/axios.ts:30-33` 拦截器 | 清 cookie → 浏览器打开任意页面并 `fetch /api/v1/users/me/`；Playwright `page.on('response')` 监听 401 后 `location.href` 变化 | 拦截器触发，URL 跳 `/login` （`tests/e2e/coverage.spec.ts` 已覆盖） | 是 | location.pathname 以 "/login" 开头 |
| TC-AUTH2-008 | L3 端到端 | axios withCredentials 携带 session cookie | TC-AUTH1-005 | 登录后浏览器 `fetch /api/v1/users/me/`，Playwright `page.on('request')` 抓 headers | Cookie 头含 `sessionid=*` （`tests/e2e/coverage.spec.ts` 已覆盖） | 是 | request.headers.cookie 匹配 regex `sessionid=[^;]+` |
| TC-AUTH2-009 | L3 端到端 | 401 响应被拦截器解包为 Error | TC-AUTH1-004；参考 `apps/web/app/services/axios.ts:21-26` | 登录后浏览器 fetch 未授权 URL → catch 抛出的 error；Playwright `page.evaluate` 调用 `api.users.me()` 在未授权场景下断言 `error.code` | `error.message` 含错误文案；`error.code === 'AUTH_INVALID_CREDENTIALS'` （`tests/e2e/coverage.spec.ts` 已覆盖） | 是 | error.code = "AUTH_INVALID_CREDENTIALS"；error.message 非空 |

**R3 一致性残留**：TC-AUTH1-002 错误码与 AUTH-001 BR-02 字面错位（已登记 DEV-8）；R4 复核结论：TC-AUTH1-012 引用 BR-12（CSRF 校验规则），与 AUTH-001 §2.7 AUTH_CSRF_FAILED 错误码一致。判分锚点补充 `error.code === 'AUTH_CSRF_FAILED'`。

---

## 6. AUTH-003 最小权限隔离

### 6.1 前置
- TC-TEAM1-001 已过（两个独立账号 A/B 已建团队）；demo 账号 `zhangsan@rabbit.dev / Rabbit123` 可登录

### 6.2 用例清单

| ID | 级别 | 标题 | 前置/依赖 | 步骤 | 预期 | 自动化 | 判分锚点 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| TC-AUTH3-001 | L3 端到端 | 越权访问 workspace 返回 404 | TC-TEAM1-001（两个独立账号） | 账号 A 建 ws → 账号 B 直接 GET `/api/v1/workspaces/{A的slug}/projects/{pid}/` | HTTP 404（防 ID 枚举） | 是 | HTTP 404 |
| TC-AUTH3-002 | L3 端到端 | WS_OWNER 隐式 = PROJ_ADMIN | TC-PROJ1-001 | A 建项目 → A PATCH 项目成功（即便无 ProjectMember 显式记录） | 200 | 是 | HTTP 200 |
| TC-AUTH3-003 | L3 端到端 | WS_MEMBER PATCH 项目被拒 | TC-TEAM1-006；TC-PROJ1-001 | 邀请 B（WS_MEMBER） → B PATCH 项目 | 403，code=PERM_PROJECT_ADMIN_REQUIRED | 是 | HTTP 403；code=PERM_PROJECT_ADMIN_REQUIRED |
| TC-AUTH3-004 | L2 集成 | 整数角色等级比对 | — | `grep -A8 WorkspaceRole apps/api/plane/db/models/roles.py` | OWNER=20 / ADMIN=15 / MEMBER=10 / GUEST=5 | 是 | OWNER=20; ADMIN=15; MEMBER=10; GUEST=5 |
| TC-AUTH3-005 | L2 集成 | _get_project_or_404 404 而非 403 | TC-PROJ1-001 | `grep "NotFound" apps/api/plane/app/views/projects.py` | 命中（防 ID 枚举） | 是 | 退出码 = 0；命中 "NotFound" |
| TC-AUTH3-006 | L2 集成 | IssueActivity 审计 actor FK SET_NULL | TC-INF3-007 | `grep -A2 "actor = models.ForeignKey" apps/api/plane/db/models/issue.py` | 下一行含 `models.SET_NULL` | 是 | 第二行命中 "models.SET_NULL" |

---

## 7. TEAM-001 团队 CRUD

### 7.1 前置
- TC-AUTH1-001 已过（demo 账号可登录）；PG 17 容器在线

### 7.2 用例清单

| ID | 级别 | 标题 | 前置/依赖 | 步骤 | 预期 | 自动化 | 判分锚点 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| TC-TEAM1-001 | L3 端到端 | 注册自动建默认团队 | TC-AUTH1-001 | 默认 Workspace 已建 + 创建者 WorkspaceMember role=OWNER | 是 | DB 中 workspaces 表新增 1 条；workspace_members 表新增 1 条 role=OWNER |
| TC-TEAM1-002 | L3 端到端 | 创建团队 slug 自动归一 | TC-AUTH1-001 | POST workspaces name=`My Team!` | slug `my-team` | 是 | response.data.slug = "my-team" |
| TC-TEAM1-003 | L3 端到端 | 创建团队 slug 冲突加后缀 | TC-TEAM1-002 | 同名创建两次 | 第二次 slug `my-team-2` | 是 | 第二次 response.data.slug = "my-team-2" |
| TC-TEAM1-004 | L3 端到端 | 列工作空间仅返回当前用户成员 | TC-AUTH1-001（A + B 两账号） | A 注册 + B 注册 → A GET workspaces | 仅 A 的 ws；无 B 的 ws | 是 | A 的 GET workspaces 响应集合 ⊆ A 的 ws 列表；与 B 无交集 |
| TC-TEAM1-005 | L3 端到端 | GET 不存在 workspace 返回 404 | TC-AUTH1-005 | GET `/api/v1/workspaces/no-such/` | 404，detail=RESOURCE_NOT_FOUND | 是 | HTTP 404；code=RESOURCE_NOT_FOUND |
| TC-TEAM1-006 | L3 端到端 | PATCH ws 需 WS_ADMIN+ | TC-TEAM1-004；TC-AUTH3-004 | A (WS_OWNER) PATCH ws name → 200 | 200；B (WS_MEMBER) PATCH → 403 | 是 | A PATCH HTTP 200；B PATCH HTTP 403 |
| TC-TEAM1-007 | L2 集成 | slug 唯一约束 + WHERE 部分条件 | TC-INF3-013 | `docker exec rp-pg psql -c "\\d workspaces"` | uniq_workspace_slug_alive UNIQUE WHERE deleted_at IS NULL | 是 | 索引名 uniq_workspace_slug_alive 含 "WHERE (deleted_at IS NULL)" |

---

## 8. PROJ-001 项目 CRUD

### 8.1 前置
- TC-AUTH1-001 已过；PG 17 容器在线；`tests/jmeter/sprint-0-flow.py` step 04~06（建项目）已可跑

### 8.2 用例清单

| ID | 级别 | 标题 | 前置/依赖 | 步骤 | 预期 | 自动化 | 判分锚点 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| TC-PROJ1-001 | L3 端到端 | 建项目 identifier 大写 | TC-AUTH1-001 | POST `identifier=rbt` | DB 存 `RBT`（save() 强转） | 是 | DB SELECT projects.identifier WHERE name='rbt-project' 输出 = "RBT" |
| TC-PROJ1-002 | L3 端到端 | 409 冲突 | TC-PROJ1-001 | 同 ws 下再建 `RBT` | 409，code=PROJECT_IDENTIFIER_EXISTS | 是 | HTTP 409；code=PROJECT_IDENTIFIER_EXISTS |
| TC-PROJ1-003 | L3 端到端 | 建项目同时自动种子四态 | TC-PROJ1-001 | POST 项目 | 立即 GET states 返回 [待办 / 进行中 / 已完成 / 已取消] | 是 | states 数组长度 = 4；group ∈ {backlog, unstarted, started, completed, cancelled} |
| TC-PROJ1-004 | L3 端到端 | 创建者写 ProjectMember(ADMIN) | TC-PROJ1-001 | POST 项目 → 直接 PATCH | 200 | 是 | HTTP 200；DB 中 project_members 表新增 1 条 role=ADMIN |
| TC-PROJ1-005a | L3 端到端 | PATCH 项目需 PROJ_ADMIN：WS_MEMBER 无 ProjectMember | TC-TEAM1-006；TC-PROJ1-001 | 邀请 WS_MEMBER + 无 ProjectMember → PATCH | 403，code=PERM_PROJECT_ADMIN_REQUIRED | 是 | HTTP 403；code=PERM_PROJECT_ADMIN_REQUIRED |
| TC-PROJ1-005b | L3 端到端 | PATCH 项目需 PROJ_ADMIN：WS_ADMIN 无 ProjectMember | TC-TEAM1-006；TC-PROJ1-001 | 邀请 WS_ADMIN + 无 ProjectMember → PATCH | 200（WS_ADMIN 隐式覆盖） | 是 | HTTP 200 |
| TC-PROJ1-006 | L3 端到端 | 已取消 state 不渲染到列 | TC-PROJ1-003 | GET states | 数据集不含 `group=cancelled` 的 state | 是 | states 数组中 group="cancelled" 的元素数 = 0 |
| TC-PROJ1-007a | L2 集成 | DOM：confirm 输入错名 → 删除按钮 disabled（`tests/e2e/coverage.spec.ts:TC-PROJ1-007a`）；API 软删由 step 10a 覆盖 | TC-PROJ1-001 | `python3 tests/jmeter/sprint-0-flow.py` 内 step 10a：DELETE `/api/v1/workspaces/{ws}/projects/{pid}/` 然后 GET 验证 | DELETE 返回 204；GET 该项目返回 404；DB 中 `deleted_at` 非空 | 是 | DELETE = 204；GET = 404；`SELECT deleted_at FROM projects WHERE id=...` 非空 |
| TC-PROJ1-007b | L2 集成 | DELETE 项目：sprint-0-flow.py step 10a 软删 + DB `deleted_at` 字段值 | TC-PROJ1-007a | 同 step 10a：DELETE 后 SELECT `deleted_at` | DELETE 返回 204；`SELECT deleted_at FROM projects WHERE id=?` 返回非空 datetime | 是 | DELETE = 204；DB deleted_at IS NOT NULL |
| TC-PROJ1-008 | L2 集成 | ProjectMember 反范式 workspace_id | TC-PROJ1-004 | `grep -A2 "workspace = models.ForeignKey" apps/api/plane/db/models/project.py` | 命中（命中行 `related_name="project_member"`） | 是 | 命中行 next 1 行含 `related_name="project_member"` |

---

## 9. TASK-001 任务 CRUD

### 9.1 前置
- TC-PROJ1-001 已过（identifier=PYT 项目已建）；PG 17 容器在线

### 9.2 用例清单

| ID | 级别 | 标题 | 前置/依赖 | 步骤 | 预期 | 自动化 | 判分锚点 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| TC-TASK1-001 | L3 端到端 | 建任务 sequence_id 自增 | TC-PROJ1-001 | 建 3 任务 | PYT-1 / PYT-2 / PYT-3（与 `tests/jmeter/sprint-0-flow.py` identifier="PYT" 对齐） | 是 | DB issues.sequence_id = 1, 2, 3；name ∈ {PYT-1, PYT-2, PYT-3} |
| TC-TASK1-002 | L3 端到端 | 建任务 assignee 校验 | TC-PROJ1-005b | assignee_id 非项目成员 | 400，code=DOES_NOT_EXIST，整事务回滚 | 是 | HTTP 400；code=DOES_NOT_EXIST；DB issues 计数未变化 |
| TC-TASK1-003 | L3 端到端 | IssueActivity 落盘 | TC-TASK1-001 | 建/改任务 | issue_activities 表新增记录 | 是 | DB issue_activities 计数增量 = 建/改操作次数 |
| TC-TASK1-004 | L3 端到端 | description_stripped 派生 | TC-TASK1-001；TC-INF3-016 | PATCH description_html=`<p>hello <b>world</b></p>` | GET 后 description_stripped=`hello world` | 是 | GET 返回 description_stripped = "hello world" |
| TC-TASK1-005 | L3 端到端 | completed_at 自动写入 | TC-TASK1-001；TC-INF3-014 | PATCH state → started group=completed | completed_at 不为空 | 是 | DB issues.completed_at 非空 datetime |
| TC-TASK1-006 | L3 端到端 | PATCH 部分字段 | TC-TASK1-001 | `PATCH issues/{iid}/ {"target_date":"..."}` | state/sort_order 不变 | 是 | PATCH 后 GET 返回的 state_id 与 sort_order 与 PATCH 前完全相等 |
| TC-TASK1-007 | L3 端到端 | assignees P0 单人限制 | TC-TASK1-001 | assignee_ids=[u1,u2] | 400（max_length=1） | 是 | HTTP 400；error 含 "max_length=1" |
| TC-TASK1-008 | L2 集成 | Issue sequence_id 唯一约束 + WHERE 部分条件 | TC-INF3-013 | `\\d issues` | uniq_issue_sequence_per_project UNIQUE WHERE deleted_at IS NULL | 是 | 索引名 uniq_issue_sequence_per_project 含 "WHERE (deleted_at IS NULL)" |

---

## 10. BOARD-001 固定三列看板

### 10.1 前置
- TC-PROJ1-001 已过（identifier=PYT 项目已建）；TC-TASK1-001 已过（≥3 任务已建）；Playwright 浏览器（chromium）已安装

### 10.2 用例清单

| ID | 级别 | 标题 | 前置/依赖 | 步骤 | 预期 | 自动化 | 判分锚点 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| TC-BOARD1-001 | L3 端到端 | Playwright：拖拽后状态保持 | TC-AUTH1-005；TC-TASK1-001 | e2e auth.spec.ts 完整动线 + state 切换断言 | PYT-X state_group=started | 是 | 拖拽后 GET issue.state.group = "started" |
| TC-BOARD1-002 | L3 端到端 | GET 分组端点 | TC-TASK1-001 | `GET .../issues/?group_by=state_id` | 返回按 state_id 分组的 dict，含空列 | 是 | 响应键数 = 项目 states 数（含空列键） |
| TC-BOARD1-003 | L3 端到端 | sort_order 浮点插值列尾 | TC-BOARD1-007 | 3 个任务 sort=65535/131070/196605 → 新建任务 | 第 4 个 sort=262140 | 是 | 第 4 个 issue.sort_order = 262140 |
| TC-BOARD1-004 | L3 端到端 | sort_order 浮点插值列首 | TC-BOARD1-007 | PATCH `sort_order=prev/2` | 新 sort ≈ 32767（夹在前两个之间） | 是 | 新 sort_order = 32767（精确） |
| TC-BOARD1-005 | L3 端到端 | 看板三列 fixed（unstarted/started/completed） | TC-PROJ1-003 | GET states | 返回不含 group=cancelled 的 3 条（4 条种子减 1） | 是 | states 数组长度 = 3；group 集合 ⊆ {unstarted, started, completed} |
| TC-BOARD1-006 | L3 端到端 | 拖拽后 sort_order 落库 | TC-BOARD1-001 | PATCH {state_id, sort_order} → GET issues | 最新 sort 与 PATCH 值一致 | 是 | DB issues.sort_order = PATCH 入参值 |
| TC-BOARD1-007 | L2 集成 | calculate_sort_order 65535 步长 | — | `grep DEFAULT_GAP apps/api/plane/db/services/sort_order.py` | DEFAULT_GAP=65535.0 | 是 | DEFAULT_GAP = 65535.0 |
| TC-BOARD1-008a | L3 端到端 | API：PATCH issue {state_id: cancelled_state_id} 返回 200 | TC-PROJ1-003 | PATCH `/api/v1/.../issues/{iid}/ {"state_id": <cancelled_id>}` | 200（DB 列合法） | 是 | HTTP 200；DB issues.state_id = cancelled_state_id |
| TC-BOARD1-008b | L3 端到端 | Playwright e2e：看板页不渲染"已取消"列 | TC-PROJ1-006 | 登录 → GET `/workspace/{slug}/projects/{pid}/board` | DOM locator 不含 `data-state-group="cancelled"` section | 是 | page.locator('[data-state-group="cancelled"]') 计数 = 0 |
| TC-BOARD1-009 | L2 集成 | PATCH sort_order+state_id 一次完成（API 层验证） | TC-BOARD1-006 | `PATCH /api/v1/.../issues/{iid}/ {"state_id": <started_id>, "sort_order": 131070}` → `GET .../issues/{iid}/` | DB 两字段同时更新（已被 TC-BOARD1-006 覆盖）；本用例聚焦"一次请求两字段"原子性 | 是 | HTTP 200；DB state_id = started_id 且 sort_order = 131070 |

---

## 术语表（用例中出现的关键术语）

| 术语 | 定义 |
| --- | --- |
| rotate | Django `login()` 默认调用 `cycle_key()` + `rotate_token()`，使旧 csrf_token 立即失效；登录后必须重拉 csrf 才能继续 POST（TC-AUTH1-014） |
| advisory lock | PostgreSQL 专用事务咨询锁，由 INFRA-003 §4.11 实现；用于 `sequence_id` 生成时保证全表唯一（TC-INF3-011）；SQLite dev 环境跳过（DEV-7） |
| swappable | Django `AUTH_USER_MODEL` 自定义 User 模型时的可交换标记；`User.swappable = AUTH_USER_MODEL`（TC-INF3-003） |
| BR-04 | 弱密码字典（Django CommonPasswordValidator） |

---

## 附录 A：评分维度定义（subagent 使用）

| 维度 | 定义 | 不通过症状 | 0.5 步进判分参考 |
| --- | --- | --- | --- |
| **完整性** | 10 份功能文档每份都有对应测试用例；用例覆盖所有可观察行为（正路 + 边界 + 异常） | 某文档无用例；某可观察路径漏覆盖 | 9.5：10 份文档全覆盖 + 每份覆盖正路/边界/异常三类；9.0：全覆盖但缺某类边界；8.5：漏 1 份文档；8.0：漏 ≥2 份 |
| **一致性** | 用例断言与各功能文档 §3 规格、§4 API 契约逐字一致；引用 INFRA-002 §4.10 / §4.3.5 / BR-* 等条款正确 | 断言与文档 §3/§4 矛盾；编号错引；与 ADR-0001 登记的实现偏差未对齐 | 9.5：所有断言逐字对齐文档 + 偏差全部登记附录 B；9.0：≥95% 对齐 + 偏差登记；8.5：≥3 处字面错位 |
| **可实施性** | 每个用例都有可执行的步骤（CLI 命令 + 期望），第三方依赖（PG/JMeter/Playwright）启动路径清晰 | 步骤含"模拟 XX"；缺前置；命令不可在本地直接跑 | 9.5：新成员 5 分钟内可复跑全部用例；9.0：步骤清晰 + 前置依赖声明；8.5：个别用例需手动调整命令 |
| **可测性** | 用例可在 CI 单线程环境（PG 17 容器 + Django + 单端口）下自动跑通；当前存在的 `tests/jmeter/sprint-0-flow.py` / `tests/e2e/auth.spec.ts` 应能覆盖主要用例 | 用例需多端口 / 多节点 / 性能环境 | 9.5：100% 用例可在 CI 单线程环境自动跑；9.0：≥90% 可自动跑 + 残余有 nightly 通道；8.5：≥3 条依赖浏览器手动观察 |
| **清晰度** | 用例命名、步骤表述、期望措辞对评审者与新成员均无歧义；表格字段定义明确；用例间编号稳定 | 含"差不多"等模糊词；步骤跳跃；期望不具体 | 9.5：零模糊词 + 步骤编号化 + 期望含具体断言值；9.0：零"模拟 XX"等弱化措辞；8.5：≥3 处含"约"/"…"等模糊词 |

**通过标准**：5 项全部 ≥9.5。任一项未达 → 修复 subagent 改 → 再评分。最多 5 轮。

---

## 附录 B：偏差登记（待修复不阻塞评分）

| 偏差 ID | 描述 | 处置 |
| --- | --- | --- |
| DEV-5b | 测试用例 TC-INF1-006 写"8 业务包"实测为 9 包（早期实测对齐遗留） | 测试用例改为 9 包，与实测一致 |
| DEV-6 | lint-staged 与 harness 回滚冲突（pre-commit 钩子未注册导致首次 push 跳过校验） | 临时绕过：`git push --no-verify`；后续 Sprint 1 注册 lint-staged 配置 |
| DEV-7 | advisory lock 在 SQLite dev 跳过（CI 走 PG 不受影响；本地 dev 用 SQLite 时 sequence_id 用 max()+1 退化） | 仅 dev 环境退化；CI 强制 PG；Sprint 1 评估是否回填 SQLite advisory 兼容 |
| DEV-8 | AUTH-001 §BR-02 错误码 `RESOURCE_ALREADY_EXISTS` → 实际实现 `AUTH_EMAIL_EXISTS`（apps/api/plane/app/views/auth.py:62） | 待 Sprint 1 文档回改（统一为 AUTH_EMAIL_EXISTS 或统一为 RESOURCE_ALREADY_EXISTS）；当前实现优先 |

备注：DEV-8 是"测试用例与源文档字面错位"类偏差（实现已落地），与 DEV-1~7（"代码与源文档字面错位"类）形成闭环。

---

**评审排期**：本文件 S0 → R1 评分 → 修复 → R2 → ... 最多 R5，全部 ≥9.5 后入库 + 登记 plan/文档质量评审状态。

---

## 附录 C：UI 表面清单（UI Surface Inventory · 2026-09-02 补）

> **边界声明**：Django Admin（INFRA-003 §3.2，`/django-admin/`）为开发调试面、生产摘除、由后端测试覆盖，不入本清单。演示账号按钮（login 页「一键进入演示账号」）为演示辅助、任何文档 §3 未定义，登记附录 B 反向偏差。
>
> **来历**：真实使用发现三处实现与冻结稿不一致（项目壳缺失 / 设置页空壳 / 创建弹窗缺 6 项规格），根因是四道检查（原型评审、测试文档评审、e2e、验收 6 条）没有一道对「实现 vs 冻结稿」的**字段级**一致性负责。本清单把冻结稿拆解为「页面 × 组件 × 字段」树，每行至少一条 parity 断言（由 `tests/e2e/parity.spec.ts` 承接，`expect.soft` 全量扫描）。**新页面/弹窗必须先入本清单再实现**；组件完成的定义 = 清单行核对通过。

### C.1 登录页 `/login`

| 组件 | 字段/交互 | 断言方式 |
| --- | --- | --- |
| 卡片 | 🐰 logo、标题「登录 RabbitProjects」、420px 居中 | heading + logo 可见 |
| next 提示 | 带 ?next 时显示「登录后将返回你原本访问的页面」 | 条件文本 |
| 表单 | 邮箱 label+input、密码 label+input+👁 切换、记住我 checkbox、忘记密码（禁用灰字） | getByLabel + disabled |
| 操作 | 登录按钮（loading 文案「登录中…」）、演示账号按钮、底部「立即注册」链接 | role 断言 |
| 401 条件态 | 表单顶部 Alert「邮箱或密码错误」（AUTH-001 §3.5：不用 toast） | 条件断言 |
| 409 条件态 | 注册页「该邮箱已注册，直接登录 →」带链接（AUTH-001 §3.2） | 条件断言 |
| 禁用条件态 | 账号被禁用常驻 Alert「账号已被禁用」+ 登录按钮禁用直至改邮箱（AUTH-002 §3.3） | 条件断言 |

### C.2 注册页 `/register`

| 组件 | 字段/交互 |
| --- | --- |
| 卡片 | logo、标题「创建你的账号」、顶部+底部双「登录」入口 |
| 密码区 | 强度条（弱/中/强 三档变色）+ 四条规则清单（8 位/大写/小写/数字，满足变绿） |
| 表单 | 邮箱、密码、确认密码（三组 label+input） |
| 操作 | 创建账号按钮 |

### C.3 工作台项目列表 `/:ws/projects`

| 组件 | 字段/交互 |
| --- | --- |
| 顶栏-切换器 | 触发器（logo+名称+▾）→ 下拉（260px）：标题「我的团队」、团队列表（logo+名称+角色灰字，当前项 check+主色底）、分隔线、「＋ 创建新团队」 |
| 顶栏-创建团队弹窗（480px） | 名称*、slug 实时预览（访问地址预览：…/{slug}）、描述+0/500 计数、取消/创建团队 |
| 顶栏-头像菜单 | 头像按钮 → 下拉：显示名+邮箱、分隔线、「退出登录」；退出后跳 /login |
| 工作区侧栏 | 「工作区」分组：首页（置灰）/ 项目（可用）/ 我的任务（置灰）/ 团队设置（置灰） |
| 页面头 | 「项目」标题 + 「N 个项目」+「＋ 创建项目」 |
| 项目卡 | 项目 logo、名称、identifier 等宽徽章、状态点+进行中、描述（2 行截断/暂无描述）、头像、任务数 |
| 空态 | 插画 +「还没有项目」+ 副文案 + 创建按钮 |
| 创建项目弹窗（520px） | 名称*、标识符*（大写过滤、`{ID}-1` 预览、「创建后不可修改」说明）、描述+计数、取消/创建 |

### C.4 项目壳（所有项目页共用）

| 组件 | 字段/交互 |
| --- | --- |
| 项目侧栏 220px | 身份区（logo+项目名+identifier 徽章）；「视图」组：任务列表/看板（active 态）；「管理」组：项目设置；底部「返回项目列表」 |
| 视图条 | 视图名 + 「＋ 创建任务」主按钮 |

### C.5 任务列表 `…/issues`

| 组件 | 字段/交互 |
| --- | --- |
| 页面头 | 「任务列表」+「N 个任务」+「＋ 创建任务」 |
| 快速创建行 | 虚线框、Enter 创建、Esc 清空、焦点保持 |
| 表格 | 五列：编号（点击复制）/ 标题 / 状态（点+名）/ 负责人（头像或 —）/ 截止时间（逾期红+⚠） |
| 行点击 | 打开 720px 抽屉 |
| 乐观插入 | 回车瞬间插入临时行（编号位 …、opacity-60），失败恢复输入（TASK-001 §3.2.1） |
| 行 hover | more-horizontal 图标（TASK-001 §3.1） |
| 空态 | 插画 +「暂无任务」+ 创建按钮 |

### C.6 三列看板 `…/board`

| 组件 | 字段/交互 |
| --- | --- |
| 列 ×3 | 280px、列头（色点+名称+计数）、空列虚线热区「将任务拖拽到这里」、列底「＋ 添加任务」 |
| 卡片 | 左侧 3px 状态色条、标题（3 行截断）、编号、头像（无负责人不渲染）、日期（逾期红） |
| 拖拽 | 跨列改状态、列内排序、插入指示线、列高亮 |
| 抽屉（720px） | 编号（点击复制）、可编辑标题、描述区+工具条、状态/负责人/截止三行、✕ 关闭；URL ?peekIssue 同步（刷新重开/后退关闭，TASK-001 §3.3） |
| 抽屉 ⋯ 菜单 | 「复制链接」「复制编号」「删除任务」红字（P0 UI 唯一删除任务入口，TASK-001 §3.3） |
| 抽屉元信息 | 创建者·创建于·最后更新 + 保存反馈「已保存」2s 淡出/「保存失败」红字重试（TASK-001 §3.3） |
| 列内快速创建 | 列底「＋ 添加任务」→ 内联输入框，Enter 建该列、Esc/空失焦收起、乐观临时卡（BOARD-001 §3.4） |
| 拖拽失败反馈 | 卡片弹回源位置 + 源列红环 400ms + toast（BOARD-001 §3.3） |
| 错误/空态矩阵 | 三列全空引导条、每列 3 卡骨架 animate-pulse、加载失败 alert-circle+重试、状态集异常提示（BOARD-001 §3.5） |

### C.7 创建任务弹窗（640px，看板/列表共用）

| 组件 | 字段/交互 |
| --- | --- |
| 标题 | 「创建任务 · {项目名}」 |
| 标题输入 | 无边框大字号、placeholder「任务标题」、autofocus |
| 描述编辑器 | 工具条（B I U ≡ ☰ ⌗ </> 🔗，装饰外壳为登记边界）+ contenteditable「添加描述…」 |
| 状态下拉 | 圆点+名称、默认选中 is_default、含「已取消」全四态 |
| 负责人下拉 | 「指派给我（{用户}）」/ 未分配 |
| 截止时间 | 日期框 + 今天/明天/下周快捷 chip（选中高亮） |
| 负责人下拉边界 | 成员搜索框/多成员单选登记为 P0 偏差（TASK-001 §3.2.2 要求 vs 实现单成员）；Esc 有内容二次确认 |
| 续创建 | 「创建后继续创建下一个」checkbox（保留状态/负责人） |
| 快捷键/状态 | ⌘↵ 提交（按钮带提示）、Esc 关闭、提交中 loading |

### C.8 项目设置 `…/settings`

| 组件 | 字段/交互 |
| --- | --- |
| 基本信息卡 | 进页**拉取详情回显**；名称（可编辑）、标识符（disabled+🔒 创建后不可修改）、描述、「保存更改」+ 已保存 chip |
| 危险区 | 红框说明 +「删除项目」 |
| 删除确认弹窗 | 「输入项目名称 {name} 以确认」、输入框 placeholder=项目名、confirm≠name 时按钮 disabled、相等可删、删除后跳列表 |
| 项目卡 hover 菜单 | more-horizontal 下拉：「项目设置」「删除项目」（PROJ_ADMIN 可见，PROJ-001 §3.1） | 
| 409 条件态 | identifier 标红 +「标识符 {ID} 已被占用」+「试试 {建议}」一键采纳（PROJ-001 §3.2） |
| 成功反馈 | 创建成功 toast「项目创建成功」/「团队创建成功」（TEAM-001 §3.3 / PROJ-001 §3.2） |
| 欢迎条 | 注册成功后工作台顶部一次性欢迎条，不弹 toast（AUTH-001 §3.5） |

### C.9 全局表面（Loader / 错误 / 404 / toast）

> 来源：AUTH-002 §3（四节）+ AUTH-003 §3.2/§3.3 共用组件。每次冷启动/会话过期必经的高频表面。

| 组件 | 字段/交互 | 来源 |
| --- | --- | --- |
| 全屏 Loader | Logo 32px + spinner、800ms 后淡入「正在加载…」、超 8s 切错误态 | AUTH-002 §3.1 |
| 会话过期 toast | 「登录已过期，请重新登录」（右上角 5s、info 级、去重）+ 跳 /login?next= | AUTH-002 §3.3 |
| 404/无权空态 | 「内容不存在或你没有访问权限」+「返回工作台」（不泄露存在性） | AUTH-002 §3.4 + AUTH-003 §3.2 |
| 探测失败空态 | WifiOff「加载失败」+「重试」（重跑 fetch） | AUTH-002 §3.4 |
| 账号禁用（会话中） | toast.error + 落地登录页常驻 Alert 阻断 | AUTH-002 §3.3 |
| 会话中被移出 | 切换器同帧移除项 + 重定向 + toast「你已不在该工作空间」 | AUTH-003 §3.3 |
| 429/5xx | 统一 toast 通道（AUTH-001 §3.5：429/5xx 用 toast，字段级错误不用） | AUTH-001 §3.5 |

---

> **Sprint 1 新增段落（C.10~C.36 · 2026-09-03 补）**：覆盖 sprint-1 11 份文档 §3 定义的全部 UI 表面，依 ADR-0010 五步纪律建立、ADR-0011 20 项裁决定稿（出处已抽查核验）。行内【类别】标签 = 默认态 / 条件态 / 下拉内容 / 禁用态 / 空态 / 加载态 / toast与Alert文案 / 权限隐藏；对 sprint-0 既有表面的新增/变更在节标题标「变更 · 基线=C.x」。优先级五档色值注册源 = `BOARD-002` §3.2（ADR-0011 #6）。

### C.10 个人设置壳 + 个人资料页 `/settings/profile`（归属：AUTH-004）

> 设置区为二级页：左侧固定导航（个人资料 / 安全 / 通知偏好）+ 右侧内容区（AUTH-004 §3.1）。

| 组件 | 字段/交互 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 设置壳-左侧导航 | 【默认态】三项：● 个人资料（当前）/ ○ 安全 / ○ 通知偏好（即将上线，灰置占位不隐藏）；≥1024px 导航 240px + 内容区 max-width 720px | 导航项 + active 态 | AUTH-004 §3.1/§3.2/§3.8 |
| 入口-顶栏头像菜单 | 【默认态 · 变更（基线=C.3 顶栏-头像菜单）】头像下拉第一项新增「个人设置」；所有登录用户可见，不经 PermissionGate | 菜单项断言 | AUTH-004 §3.1 |
| 头像卡 | 【默认态】160px 圆形 Avatar + 悬浮遮罩（半透明黑 + 白色相机图标「更换」）+ 下方「更换头像」文字按钮 | 视觉 + hover 断言 | AUTH-004 §3.2 |
| 恢复默认按钮 | 【条件态】仅 `avatar_url` 非空时显示；点击 Popover 二次确认「恢复为系统默认头像？」→ 头像淡出为 SVG 默认 | 条件断言 | AUTH-004 §3.2/§3.6 |
| 头像上传入口 | 【默认态】点击触发隐藏 input（accept="image/png,image/jpeg,image/webp"），或拖拽到头像区（虚线高亮）；选择后 JS 再验 MIME 与大小，非法即 Toast、不发 presign | 交互断言 | AUTH-004 §3.5 |
| 直传进度环 | 【加载态】presign 后出现环形进度（例 60%）；complete 后新头像淡入（300ms opacity）后消失；`role="progressbar"` + `aria-valuetext="上传进度 60%"` | 条件断言 | AUTH-004 §3.2/§3.5/§3.9 |
| 上传失败/取消 | 【toast与Alert文案】任一步失败保留旧头像 + Toast `error.message`、进度环消失；上传中「取消」→ `xhr.abort()` | 条件断言 | AUTH-004 §3.5 |
| 资料表单 | 【默认态】四字段：昵称*（必填）/ 名 / 姓 / 个人简介（TextArea 3 行）+ 右下字数统计（`18 / 500`，超 480 变琥珀色预警） | getByLabel | AUTH-004 §3.2 |
| 昵称输入策略 | 【默认态】输入过程不校验不清错；失焦校验；全表单显式保存（无 onBlur 自动提交） | 交互断言 | AUTH-004 §3.6 |
| 邮箱行 | 【禁用态】只读文本 + 锁图标 + Tooltip「邮箱变更即将上线」（P1 不可修改） | disabled + tooltip | AUTH-004 §3.2 |
| 保存按钮 | 【禁用态】主色实心；`isDirty && !isSubmitting` 才可点；成功短暂变「✓ 已保存」2s 再回禁用；`⌘S` 提交；提交中按钮 spinner | 条件断言 | AUTH-004 §3.2/§3.6/§3.9 |
| 重置按钮 | 【默认态】次级按钮；将表单恢复为 `ProfileStore.me` 当前值 | 交互断言 | AUTH-004 §3.2 |
| 保存反馈 | 【toast与Alert文案】成功 Toast + 顶栏昵称刷新；失败回滚快照 + 字段级 `setError` | 条件断言 | AUTH-004 §3.6 |
| 表单初始态 | 【默认态】全部预填当前值；保存按钮禁用（isDirty=false） | 条件断言 | AUTH-004 §3.7 |
| `/users/me/` 拉取失败 | 【条件态】内容区 `alert-circle` + `error.message` +「重试」；表单禁用（防旧快照覆盖） | 条件断言 | AUTH-004 §3.7 |
| 头像加载失败 | 【条件态】`onError` 回退渲染默认 SVG（avatar_url 不回写） | 条件断言 | AUTH-004 §3.7 |

### C.11 安全设置页 `/settings/security`（归属：AUTH-004）

| 组件 | 字段/交互 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 修改密码卡 | 【默认态】当前密码 / 新密码 / 确认新密码三组 label+input+👁；「确认修改」按钮 | getByLabel | AUTH-004 §3.3 |
| 密码强度指示器 | 【默认态】复用 AUTH-001 §3.4 同一组件（弱/中/强三档 + 规则常驻清单），`role="meter"`；两处不允许两套强度算法 | 组件复用断言 | AUTH-004 §3.3/§3.9 |
| 修改成功 | 【toast与Alert文案】表单上方 Alert 条「密码已修改。其他设备已需要重新登录」（非 toast——视线在表单内）；表单清空；焦点移至 Alert 条 | 条件断言 | AUTH-004 §3.3/§3.6/§3.9 |
| 活跃会话区块 | 【禁用态】整体灰置 +「即将上线」角标 +「管理各设备的登录状态（P2 交付）」说明；不隐藏 | 灰置断言 | AUTH-004 §3.3 |

### C.12 忘记密码页 `/forgot-password`（归属：AUTH-004；匿名可达）

| 组件 | 字段/交互 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 卡片 | 【默认态】Logo + 标题「重置你的密码」+ 副文案「输入注册邮箱，我们将发送重置链接到该邮箱。」 | 文案断言 | AUTH-004 §3.4 |
| 表单 | 【默认态】邮箱 label+input（placeholder you@company.com）+ 主按钮「发送重置邮件」 | getByLabel | AUTH-004 §3.4 |
| 发送成功 | 【toast与Alert文案 · 条件态】202 后无论邮箱真假显示「邮件已发送，请查收（记得看看垃圾箱）」 | 条件断言 | AUTH-004 §3.6 |
| 冷却倒计时 | 【禁用态】按钮 60s 冷却（例「58s 后可再次发送」），冷却期内禁用；倒计时 `aria-live="off"`，结束播报一次「可再次发送」 | 条件断言 | AUTH-004 §3.4/§3.6/§3.9 |
| 底部链接 | 【默认态】「想起密码了？返回登录」 | 链接断言 | AUTH-004 §3.4 |
| 入口点亮 | 【变更（基线=C.1 表单行「忘记密码」禁用灰字）】登录页「忘记密码？」文字链由占位点亮为可点 | 断言 | AUTH-004 §3.1 |

### C.13 重置密码页 `/reset-password?token=…`（归属：AUTH-004；匿名可达）

| 组件 | 字段/交互 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 卡片 | 【默认态】Logo + 标题「设置新密码」+ 副文案「为账号 liang@example.com 设置新密码。」 | 文案断言 | AUTH-004 §3.4 |
| 表单 | 【默认态】新密码+👁 / 强度条（复用组件，「强度：中」）/ 确认新密码+👁；主按钮「重置密码」（ADR-0011 #9 定稿） | getByLabel | AUTH-004 §3.4 |
| token 本地预校验 | 【条件态】进入仅本地校验 token 格式（43~128 位 urlsafe），不调端点探测；格式非法直接呈现失效态 | 条件断言 | AUTH-004 §3.6 |
| 失效态 | 【条件态】提交后收到 `AUTH_PASSWORD_RESET_INVALID/_EXPIRED` → 表单整体替换为居中失效卡：`link-2-off` 图标 96px text-neutral-300 +「重置链接无效或已过期」+ 副文案「链接有效期 30 分钟，且只能使用一次」+ 主按钮「重新申请」（预填邮箱跳 forgot 页）；**不保留表单**；焦点移至失效卡标题 | 条件断言 | AUTH-004 §3.4/§3.9 |
| 无 token 参数 | 【空态】直接失效态，副文案「请通过邮件中的链接进入」 | 条件断言 | AUTH-004 §3.7 |
| 重置成功 | 【默认态】成功页「密码已重置」+ 主按钮「去登录」——**不自动登录**，手动登录（ADR-0011 #9 定稿） | 条件断言 | AUTH-004 §3.6 |
| 底部链接 | 【条件态】「链接已失效？重新申请」 | 链接断言 | AUTH-004 §3.4 |

### C.14 403 路由页 `/403` 与权限门控组件族（归属：AUTH-005）

> §3.1 mode 决策树（不可逆→disable；纯管理入口→hide；有浏览价值→fallback；路由入口→PermissionRouteGuard）写入 Storybook 文档，此处不列为表面行。项目壳形态依 ADR-0011 #2 维持 sprint-0 冻结基线 C.4。

| 组件 | 字段/交互 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 403 页 `/403` | 【条件态】`shield-off` 96px 图标 +「没有访问该页面的权限」+ 副文案「当前角色不满足『{权限点中文名}』所需的最低角色要求。」+ 按钮「返回工作台」「切换账号」+ 底注「认为这是误判？请联系空间管理员检查你的角色。」；required 权限点经 URL 参数带入并渲染为**中文名**（PERMISSION_LABELS），不裸露英文 key；不提供「申请权限」动线 | 条件断言 | AUTH-005 §3.3 |
| 403 页焦点/语义 | 【默认态】主标题 `role="alert"`、焦点自动移至标题、「返回工作台」为默认焦点按钮 | a11y 断言 | AUTH-005 §3.6 |
| 403 两套落点分工 | 【条件态】直达无权 URL → 本路由守卫页（保留具体缺哪个权限）；页内请求失败的 403 → INFRA-004 §3.4 请求级空态（按 error.code 分支渲染）（ADR-0011 #10 定稿） | 条件断言 | AUTH-005 §3.3 + INFRA-004 §3.4 |
| PermissionGate · hide | 【权限隐藏】入口不出现（从 DOM 移除且不占焦点序）——范式例：邀请成员按钮（workspace.member.invite） | 权限断言 | AUTH-005 §3.1/§3.6 |
| PermissionGate · disable | 【权限隐藏 · 禁用态】保留可见性 disabled（危险不可逆操作一律 disable）——范式例：删除项目（project.delete，DangerButton）；按钮 `aria-disabled="true"` 且可聚焦（Tab 可达、Tooltip 可被读屏触发），不用原生 disabled | 权限断言 | AUTH-005 §3.1/§3.6 |
| PermissionGate · fallback | 【权限隐藏】降级视图——范式例：成员管理抽屉对非管理员降级 `ReadOnlyMemberList`（project.member.manage） | 权限断言 | AUTH-005 §3.1 |
| 路由守卫 | 【权限隐藏】`PermissionRouteGuard` 包裹路由（例 `/:workspaceSlug/settings/members` 要求 workspace.member.manage），直达 URL 重定向 /403、不白屏 | 权限断言 | AUTH-005 §3.1 |
| 无权 Tooltip | 【权限隐藏】disabled 按钮 hover/focus（双通道）Tooltip「当前角色无权执行此操作」（可换文案 prop） | 条件断言 | AUTH-005 §3.4/§3.6 |
| Gate 加载骨架 | 【加载态】权限数据加载中渲染等宽等高骨架（防布局跳动），≤300ms（BR-11）；`aria-busy`，转无权渲染时 `aria-live="polite"` 播报一次 | 条件断言 | AUTH-005 §3.4/§3.6 |
| 403 后权限刷新 | 【条件态】拦截器触发静默重拉，按钮显隐收敛，无感（无 toast） | 交互断言 | AUTH-005 §3.4 |
| 继承角色徽标 | 【条件态】成员列表中 inherited 行名片徽标「继承自工作空间管理员」 | 条件断言 | AUTH-005 §3.4 |
| 截断提示条 | 【条件态】`meta.truncated=true` 时顶栏一次性黄色条「部分项目权限未同步」+ 刷新按钮 | 条件断言 | AUTH-005 §3.4 |
| 项目壳（PROJ_ADMIN 视角） | 【权限隐藏 · 变更（基线=C.4 项目壳，ADR-0011 #2）】220px 项目侧栏：身份区（「兔子核心系统 — RBT」）+「视图」组（● 任务列表 / ○ 看板）+「管理」组（⚙ 项目设置）+ 底部「← 返回项目列表」；视图条（视图名 · 12 个任务 +「＋ 创建任务」）。「删除项目」在设置页「危险区域」红框内（PROJ-001 §3.3），可点击红色 | 权限断言 | AUTH-005 §3.2 |
| 项目壳（PROJ_CONTRIBUTOR 视角） | 【权限隐藏】同一路由：侧栏「管理」组整体不渲染（hide，project.update ≥20 不通过）；视图条「＋ 创建任务」与列表快速创建行正常出现（issue.create ≥15 通过）；删除任务维持抽屉 ⋯ 菜单唯一入口（useCanDeleteIssue 本人创建可删，见 TASK-001 §3.3）——列表行与看板卡**无行级删除图标**（ADR-0011 #3） | 权限断言 | AUTH-005 §3.2 |
| 项目壳（PROJ_VIEWER 视角） | 【权限隐藏】新建/拖拽全部消失（视图条「＋ 创建任务」不渲染、看板卡片 `isDragDisabled`）；评论区替换为「你以查看者身份访问此项目」占位条（comment.create fallback） | 权限断言 | AUTH-005 §3.2 |
| 壳响应式 | 【条件态】≥1280px 侧栏（视图组+管理组）全量平铺；768~1279px 侧栏「管理」组收入折叠菜单（Gate 仍在菜单项级生效） | 条件断言 | AUTH-005 §3.5 |

### C.15 团队成员设置页 `/:workspaceSlug/settings/members`（归属：TEAM-002）

| 组件 | 字段/交互 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 侧栏点亮 | 【变更（基线=C.3 侧栏「团队设置」置灰项）】工作区侧栏「团队设置」由置灰点亮为本页入口（ADR-0011 #18） | 断言 | TEAM-002 §3.1 |
| 路由守卫与降级 | 【权限隐藏】`PermissionRouteGuard workspace.member.read` 守护；无 invite/manage 权限的用户降级为只读列表（操作列隐藏） | 权限断言 | TEAM-002 §3.1 |
| 页头 | 【默认态】标题「成员」+「8 名成员」计数（active 聚合）+「＋ 邀请成员」主按钮（`PermissionGate workspace.member.invite` 包裹，ADMIN+） | 权限断言 | TEAM-002 §3.1 |
| 筛选条 | 【默认态】搜索框「搜索昵称或邮箱…」（300ms 防抖）+ 角色下拉（全部 / 管理员+所有者 / 成员） | getByLabel | TEAM-002 §3.1 |
| 待接受邀请面板 | 【默认态】`Collapsible` 折叠区「▸ 待接受邀请 (2)〔展开〕」：邮箱（脱敏）/ 预设角色 / 过期倒计时（<72h 橙色）/「撤销」次级按钮 /「重发邮件」 | 条件断言 | TEAM-002 §3.1 |
| 成员表 | 【默认态】五列：成员（24px 头像+昵称 truncate）/ 邮箱（mono text-xs）/ 角色徽章 / 加入时间（MM-dd HH:mm）/ 操作 ⋯；表格底部「〔加载更多 (2)〕」 | 列断言 | TEAM-002 §3.1 |
| 角色徽章 | 【默认态】所有者 #8B5CF6 紫 / 管理员 #3B82F6 蓝 / 成员 #6B7280 灰；圆点 + 文字（色盲可达） | 视觉断言 | TEAM-002 §3.1 |
| 角色行内下拉 | 【权限隐藏 · 下拉内容】仅 `workspace.member.manage` 持有者且目标非 OWNER 且目标等级低于自己时可见（BR-05）；选项依层级保护过滤（ADMIN 只能给 MEMBER 档） | 权限断言 | TEAM-002 §3.1 |
| 操作菜单 | 【下拉内容 · 权限隐藏】more-horizontal：「调整角色」「移除」（红色，workspace.member.remove + 层级保护双重判定）；OWNER 行操作列显示「（无）」 | 权限断言 | TEAM-002 §3.1 |
| 危险区域 | 【权限隐藏】`border-red-200 bg-red-50` 分区 + 说明「转让所有权后你将成为管理员，且不可自助撤销。」+「转让所有权」按钮；仅 workspace.transfer（OWNER）可见 | 权限断言 | TEAM-002 §3.1 |
| 加载中 | 【加载态】6 行表格骨架（animate-pulse），列宽与真实表一致（CLS=0） | 条件断言 | TEAM-002 §3.5 |
| 搜索无结果 | 【空态】居中 `search-x` 插画 +「未找到匹配的成员」+「清除搜索」 | 空态断言 | TEAM-002 §3.5 |
| 待接受为空 | 【空态】折叠面板隐藏（不渲染空态） | 条件断言 | TEAM-002 §3.5 |
| 加载失败 | 【条件态】`alert-circle` + `error.message` +「重试」（SWR `mutate()`） | 条件断言 | TEAM-002 §3.5 |

### C.16 邀请成员弹窗（560px，归属：TEAM-002）

| 组件 | 字段/交互 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 标题 | 【默认态】「邀请成员加入『RabbitProjects』」+ ✕ | 文案断言 | TEAM-002 §3.2 |
| 邮箱 Tag 输入 | 【默认态】「邮箱（1-20 个）」；回车 / 逗号 / 分号 / 空格 / 粘贴切分为 Tag；退格删除末 Tag；每个 Tag 实时格式校验，非法 Tag 红框；说明文字「支持逗号 / 分号 / 空格 / 换行分隔，粘贴自动切分」 | 交互断言 | TEAM-002 §3.2 |
| 计数与上限 | 【禁用态】`n / 20` 计数；达 20 后不再接受新 Tag（输入禁用 + 提示） | 条件断言 | TEAM-002 §3.2 |
| 预设角色下拉 | 【下拉内容】仅「成员 / 管理员」两项（BR-02）；每项附一行能力说明（aria-describedby，例「成员可参与协作；管理员可管理成员与项目」） | 断言 | TEAM-002 §3.2 |
| 提交 | 【默认态】「发送邀请（n）」按钮；提交中 loading（loader-2 旋转 +「发送中…」）、Modal 锁定、提交中不可关 | 条件断言 | TEAM-002 §3.2 |
| 结果视图 | 【条件态】提交后**替换表单区**：「邀请结果」四态——✅ `check-circle` 已直接加入（绿）/ ✉️ `mail` 邮件已发送，7 天内有效（蓝）/ ⏭️ `skip-forward` 已是成员，已跳过（灰）/ ❌ `x-circle` failed（红 + message）；按钮「继续邀请」（清空 Tag 保留角色选择）/「完成」 | 条件断言 | TEAM-002 §3.2 |
| SMTP 降级 | 【条件态】invited 条目追加「复制邀请链接」按钮（读 `meta.invite_links[email]`） | 条件断言 | TEAM-002 §3.2 |
| 关闭 | 【条件态】✕ / Esc / 遮罩；表单有内容时二次确认；提交中不可关 | 交互断言 | TEAM-002 §3.2 |

### C.17 邀请接受页 `/invite/:token`（归属：TEAM-002；独立轻路由，不进工作空间布局）

| 组件 | 字段/交互 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 有效邀请态 | 【默认态】Logo +「张三 邀请你加入该团队」+「邀请邮箱：li\*\*\*@ex.com」（脱敏：保留首字符与域名）+「你将获得角色：成员」+ 主按钮「接受邀请」 | 文案断言 | TEAM-002 §3.3 |
| 预检 | 【条件态】进入页面 GET 预检渲染脱敏信息；预检失败直接渲染失效态（不发 accept） | 条件断言 | TEAM-002 §3.3 |
| 未登录路径 | 【条件态】先跳登录/注册（next 带回本页，路由 `/login`——ADR-0011 #4）；无账号者从登录页切到注册；注册成功由服务端钩子自动接受，前端读 `default_workspace_slug` 直达 | 条件断言 | TEAM-002 §3.3 |
| 邮箱不匹配态 | 【条件态】同布局，正文「该邀请面向 li\*\*\*@ex.com，当前账号不匹配」+「切换账号」按钮 | 条件断言 | TEAM-002 §3.3 |
| token 失效态 | 【条件态】同布局，正文按 message 区分（过期 / 撤销 / 已使用 / 无效）+「联系管理员重新邀请」说明，无操作按钮 | 条件断言 | TEAM-002 §3.3 |
| 接受成功 | 【toast与Alert文案】POST accept → 工作空间列表 mutate → 跳 `/{slug}/projects` + toast「已加入 RabbitProjects」 | 条件断言 | TEAM-002 §3.3 |

### C.18 移除确认弹窗（400px）与转让所有权弹窗（480px）（归属：TEAM-002）

| 组件 | 字段/交互 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 移除确认 | 【条件态】标题「移除成员」+「确定将 王工（wang@ex.com）移出 RabbitProjects？」+ 警示「⚠ 该成员将同时被移出 3 个项目的成员名单；其名下任务指派将保留并以『已移出成员』展示。」+ 取消 /「移除」（红色）；确认按钮红色；默认焦点在「取消」；`role="alertdialog"` | 条件断言 | TEAM-002 §3.4/§3.6 |
| 转让弹窗 | 【条件态】DangerZone 进入；标题「转让所有权」+ 新所有者下拉（placeholder「🔍 选择当前管理员…」）+ 说明「转让后：对方成为所有者，你自动降为管理员。」+「输入团队名称以确认：RabbitProjects」+ 输入框（placeholder=空间名）+ 取消 /「确认转让（禁用）」 | 条件断言 | TEAM-002 §3.4 |
| 转让目标下拉 | 【下拉内容 · 空态】仅列 active `WS_ADMIN`（BR-08）；空态提示「先将目标成员提升为管理员」 | 条件断言 | TEAM-002 §3.4 |
| confirm_name 校验 | 【禁用态】精确匹配工作空间名才启用确认按钮；`confirm_name` 随请求提交 | 条件断言 | TEAM-002 §3.4 |
| 转让成功收敛 | 【toast与Alert文案 · 条件态】toast + 自身界面收敛：DangerZone 消失、成员表自己行徽章变「管理员」、管理按钮保留 | 条件断言 | TEAM-002 §3.4 |

### C.19 项目列表页改造 `/:workspaceSlug/projects`【变更 · 基线=C.3】（归属：PROJ-002）

| 组件 | 字段/交互 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 页头 | 【变更 · 默认态】「项目」标题 +「RabbitProjects · 12 个项目」计数 +「＋ 创建项目」 | 文案断言 | PROJ-002 §3.1 |
| 筛选行 | 【变更 · 默认态】搜索框（placeholder「搜索项目名或标识，如 RBT」；300ms 防抖；清空按钮）+ 状态下拉「状态 ▾（进行中）」+「已归档 ▾」 | getByLabel | PROJ-002 §3.1 |
| 状态筛选下拉 | 【下拉内容】进行中（默认）/ 已归档 / 全部；映射 `?status=active\|archived\|`（未传=默认排除归档） | 断言 | PROJ-002 §3.1 |
| Tabs | 【变更 · 默认态】「全部 (N)」/「★ 已收藏 (M)」；计数实时；Headless UI TabList 方向键切换 | 断言 | PROJ-002 §3.1/§3.5 |
| 卡片星标 | 【变更 · 默认态】右上角 24px `star` / `star-filled`；`aria-pressed` 切换按钮 + `aria-label="收藏项目 兔子核心系统"`；点击乐观切换 | 交互断言 | PROJ-002 §3.1/§3.5 |
| 收藏段 | 【变更 · 条件态】横条标题「★ 已收藏」+ 组内按收藏时间倒序；与常规段间分隔线（BR-10）；常规段标题「全部项目（更新时间排序）」 | 条件断言 | PROJ-002 §3.1 |
| 已归档卡片 | 【条件态】仅「已归档」筛选 / 收藏 Tab 中出现；「⊘ 已归档」灰徽标；整卡 opacity-75；点击进入只读 | 条件断言 | PROJ-002 §3.1 |
| 已归档只读态 | 【条件态】进入已归档项目：顶部琥珀色横幅「项目已归档，仅可查看」+ 全部写入口（新建/编辑/拖拽/评论/上传）禁用（ADR-0011 #14 补规格） | 条件断言 | PROJ-002 §3.6 |
| 成员头像堆叠 | 【变更 · 默认态】≤5 个 24px 叠放（`-space-x-2`），超出 `+N`；title 列名字 | 视觉断言 | PROJ-002 §3.1 |
| 卡片内容 | 【变更 · 默认态】logo + 名称 + identifier + 状态点（● 进行中）+「👤👤👤 6 成员 · 34 任务」 | 断言 | PROJ-002 §3.1 |
| 卡片菜单 | 【变更 · 下拉内容 · 权限隐藏】hover `more-horizontal`：「项目设置」「归档项目 / 取消归档」（`project.archive` Gate，归档项红色区） | 权限断言 | PROJ-002 §3.1 |
| 加载中 | 【加载态】6 卡片骨架（animate-pulse），布局与真实卡片一致（CLS=0） | 条件断言 | PROJ-002 §3.4 |
| 搜索无结果 | 【空态】`search-x` 插画 +「未找到匹配的项目」+「清除搜索」 | 空态断言 | PROJ-002 §3.4 |
| 收藏 Tab 为空 | 【空态】`star` 插画 +「收藏高频项目，快速直达」+「浏览全部项目」 | 空态断言 | PROJ-002 §3.4 |
| 无可见项目（GUEST） | 【空态 · 权限隐藏】`folder-lock` 插画 +「你还未被加入任何项目，请联系管理员」（对齐 AUTH-003 口径） | 空态断言 | PROJ-002 §3.4 |

### C.20 项目设置·成员 Tab `/…/projects/:projectId/settings/members`【变更 · 基线=C.8 项目设置新增第 2 区块】（归属：PROJ-002）

| 组件 | 字段/交互 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 隐式管理员提示条 | 【条件态 · 权限隐藏】「ℹ 你以工作空间管理员身份管理此项目」仅当前用户 WS_ADMIN+ 且无 ProjectMember 行时显示（rbac §7.4 口径）；bg-blue-50 text-blue-700 | 条件断言 | PROJ-002 §3.2 |
| 页头与筛选 | 【默认态】「成员（6）」+ 搜索框「搜索成员…」+「角色 ▾（全部）」+「＋ 添加成员」（`PermissionGate project.member.manage` 包裹） | 权限断言 | PROJ-002 §3.2 |
| 成员表 | 【默认态】五列：成员 / 邮箱 / 项目角色（行内下拉 ▾）/ 加入时间 / 操作 ⋯（改角色/移除） | 列断言 | PROJ-002 §3.2 |
| 角色行内下拉 | 【下拉内容】四档（管理员 / 协作者 / 评论者 / 查看者）；下拉项附能力说明（aria-describedby）；PROJ_ADMIN 之间互改层级保护拦截（BR-12：后端 403 → 前端回滚 + Toast） | 条件断言 | PROJ-002 §3.2 |
| 移除确认 | 【条件态】确认弹窗列明「其名下 N 个任务指派将保留，以已移出成员展示」（BR-07）；末位 ADMIN 拦截提示（BR-06） | 条件断言 | PROJ-002 §3.2 |
| GUEST 行角色下拉 | 【下拉内容 · 条件态】成员空间角色为 GUEST 时，角色下拉仅显示查看者 / 评论者两档（BR-05 前端预拦） | 条件断言 | PROJ-002 §3.2 |
| 加载失败 | 【条件态】`alert-circle` + `error.message` +「重试」 | 条件断言 | PROJ-002 §3.4 |

### C.21 添加成员弹窗（520px，归属：PROJ-002）

| 组件 | 字段/交互 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 标题 | 【默认态】「添加成员到『兔子核心系统』」+ ✕ | 文案断言 | PROJ-002 §3.3 |
| 搜索框 | 【默认态】「🔍 搜索空间成员…」实时过滤（昵称 / 邮箱前缀） | getByLabel | PROJ-002 §3.3 |
| 候选多选列表 | 【下拉内容 · 默认态】checkbox 列表：头像 + 昵称 + 邮箱 +（空间·角色）；候选=空间成员 − 本项目成员（前端本地差集，TEAM-002 `GET members/?expand=user`）；`role="listbox"` + `aria-multiselectable`，↑↓ 移动、Space 勾选、Enter 提交 | 断言 | PROJ-002 §3.3/§3.5 |
| 已选计数 | 【默认态】「已选 2 人（已在项目中的成员不再显示）」 | 文案断言 | PROJ-002 §3.3 |
| 项目角色下拉 | 【下拉内容】默认 ● 协作者；能力说明「协作者可创建与编辑任务；评论者只读+评论；查看者仅只读」；每项 aria-describedby 指向说明 | 断言 | PROJ-002 §3.3 |
| 按钮 | 【默认态】取消 /「添加（n）」；提交中按钮 loading + Modal 锁定 | 条件断言 | PROJ-002 §3.3 |
| 候选为空 | 【空态】「空间成员都已在项目中」+「去邀请成员」链接（打开 TEAM-002 邀请弹窗） | 空态断言 | PROJ-002 §3.3 |
| GUEST 候选警示 | 【条件态】GUEST 正常列出；选中且角色 > 评论者时，提交按钮旁内联警示（后端 BR-05 硬校验兜底） | 条件断言 | PROJ-002 §3.3 |
| 提交结果 | 【toast与Alert文案 · 条件态】成功者行淡入成员表；skipped / failed 逐条 Toast（例「✅ 梁工、王工 已加入（协作者）」） | 条件断言 | PROJ-002 §3.3 |
| 关闭 | 【条件态】✕ / Esc / 遮罩（有勾选时二次确认） | 交互断言 | PROJ-002 §3.3 |

### C.22 创建任务弹窗双栏升级【变更 · 基线=C.7（640px → 920px 双栏）】（归属：TASK-002）

| 组件 | 字段/交互 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 弹窗形态 | 【变更 · 默认态】`CreateIssueModal` 扩为双栏：主区（类型行 + 标题 + 描述）+ 右侧属性栏 280px；总宽 920px；标题「创建任务 · 兔子项目管理」+ ✕ | 尺寸断言 | TASK-002 §3.1 |
| 类型下拉 | 【下拉内容 · 默认态】必填（类型\*）；选项 = Workspace `is_active` 类型按 `sort_order`，每项「图标 + 色点 + 名称」；默认选中 `is_default`（任务）；停用类型不出现（BR-14） | 断言 | TASK-002 §3.1 |
| 优先级下拉 | 【下拉内容 · 默认态】五档旗形图标（`flag`），none 显示「无」；默认 `none` | 断言 | TASK-002 §3.1 |
| 标签多选 | 【下拉内容 · 默认态】项目 `active` 标签彩色 Tag；已选项可 Backspace 删除；「＋」展开面板（含「管理标签」入口，PermissionGate 包裹） | 交互断言 | TASK-002 §3.1 |
| 开始 / 截止 | 【默认态】双日期选择器联动：选完开始后截止的早于日期禁用（前端预校验 BR-06） | 条件断言 | TASK-002 §3.1 |
| 必填标记 | 【禁用态】类型未选时「创建」按钮禁用 + 下拉描红 | 条件断言 | TASK-002 §3.1 |
| P0 差异提示条 | 【条件态】创建弹窗顶部 info 条，定稿文案「类型为必填项（P0 阶段仅标题必填）」；仅首次展示（ADR-0011 #19 定稿） | 条件断言 | TASK-002 §3.1 |
| 类型全停用 | 【空态】类型下拉空态「请联系管理员启用类型」；创建入口仍可用（兜底默认类型，BR-15 防御） | 空态断言 | TASK-002 §3.8 |
| 底部按钮 | 【默认态】取消 / 创建（右下） | role 断言 | TASK-002 §3.1 |

### C.23 任务详情抽屉·属性区升级【变更 · 基线=C.6 抽屉（三项 → 七项）】（归属：TASK-002）

| 组件 | 字段/交互 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 属性区 | 【变更 · 默认态】「label 80px + 控件」行式布局七项：状态（● 进行中 ▾）/ 类型（● 缺陷 ▾）/ 优先级（⚑ 高 ▾）/ 负责人（👤 梁工 ▾）/ 标签（[🏷前端] [⚑urgent] ＋）/ 开始·截止（[09-01] → [09-03]） | 行级断言 | TASK-002 §3.2 |
| 行内编辑 | 【默认态】全部行内编辑、选中即提交（离散值语义，同 TASK-001 §2.2 自动保存策略） | 交互断言 | TASK-002 §3.2 |
| 属性修改失败 | 【条件态】改类型/优先级/日期：乐观更新徽章，失败回滚红点 + toast | 条件断言 | TASK-002 §3.7 |
| 标签挂载/摘除 | 【默认态】多选器勾选 / Tag Backspace；PUT 全量替换；Tag 划入 / 划出动画 | 交互断言 | TASK-002 §3.7 |

### C.24 任务详情抽屉·子任务区（新增区块，归属：TASK-002 §3.3）

| 组件 | 字段/交互 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 标题行 | 【默认态】「子任务 m/n」+ 8px 进度微条（百分比填充，全完成变绿） | 断言 | TASK-002 §3.3 |
| 子任务行 | 【默认态】checkbox（真实 input[type=checkbox]）+ 标题（点击打开该子任务 Drawer，`?peekIssue` 替换）+ 状态徽章（●已完成/●待办）+ ⋯ 菜单（删除） | 断言 | TASK-002 §3.3 |
| 勾选完成 | 【默认态】勾选 → PATCH state（复用 BOARD-001 状态机端点）→ 徽章/微条即时更新（乐观）；m 计数 +1 | 交互断言 | TASK-002 §3.3/§3.7 |
| 添加行 | 【默认态】「＋ 添加子任务，回车保存…」回车即建（POST sub-issues/，仅标题 + 继承父类型）；新行划入 + 微条/计数 +1（乐观）；失败移除行并恢复输入 | 交互断言 | TASK-002 §3.3/§3.7 |
| 一层限制表达 | 【禁用态】子任务行不显示「＋ 添加子任务」；对子任务打开的 Drawer 中子任务区渲染提示条「MVP 阶段子任务仅支持一层」而非输入行 | 条件断言 | TASK-002 §3.3 |
| 空态 | 【空态】「暂无子任务，添加一个开始拆解」+ 输入行常驻 | 空态断言 | TASK-002 §3.8 |
| 删除父任务 | 【条件态】⋯ → 删除：确认文案「将同时删除 N 个子任务」 | 条件断言 | TASK-002 §3.7 |

### C.25 任务详情抽屉·动态 Tab（新增区块，归属：TASK-002 §3.6）

| 组件 | 字段/交互 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| Tab 条 | 【变更 · 默认态】Drawer Tab 条终态结构「描述｜评论｜动态｜附件」四 Tab 全部可点（ADR-0011 #1/#20 定稿）；「动态」本节交付，「评论」（COLLAB-001）与「附件」（FILE-001）本迭代内后续交付，交付前对应 Tab 显示各自空态 | Tab 断言 | TASK-002 §3.6 |
| 时间线聚合 | 【默认态】按 `epoch` 聚合：同一次批量修改的多条日志归组到同一头像与时间下；游标 30 条/页；底部「── 加载更多 ──」；`<ol>` 语义列表 | 断言 | TASK-002 §3.6/§3.9 |
| 日志条目 | 【默认态】例：「⚑ 将 优先级 从 中 改为 高」「🏷 添加了标签 urgent」「● 将 状态 从 待办 改为 进行中」「✚ 创建了任务（类型：缺陷）」；操作人（👤梁工 / 👤系统）+ 时间 | 文案断言 | TASK-002 §3.6 |
| 加载/空态 | 【加载态 · 空态】时间线骨架 → epoch 聚合分组；空态「暂无操作记录」；仅 1 条时正常展示创建记录、不显示「加载更多」 | 条件断言 | TASK-002 §3.7/§3.8 |

### C.26 标签管理面板（PROJ_ADMIN；720px 弹窗，归属：TASK-002 §3.4；挂载形态 ADR-0011 #13 定稿）

| 组件 | 字段/交互 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 挂载形态 | 【默认态】720px 弹窗；入口两处——列表 / 看板筛选条「标签」下拉尾部「管理标签」+ 项目设置页「标签管理」链接 | 断言 | TASK-002 §3.4 |
| 面板头 | 【默认态】「项目标签」+「＋ 新建标签」 | 断言 | TASK-002 §3.4 |
| 标签行 | 【默认态】色点 + 名称 + hex 色（#3B82F6）+ 引用计数（「被 23 个任务使用」实时 Count）+ ✏ 编辑 + 🗑 删除 | 断言 | TASK-002 §3.4 |
| 被引用删除路径 | 【条件态】被引用 → 二次确认后**停用**（行灰置、出现 ↺ 恢复与「强制删除」；标注「已停用 · 被 3 个任务引用」） | 条件断言 | TASK-002 §3.4 |
| 未被引用删除 | 【条件态】直接软删 | 条件断言 | TASK-002 §3.4 |
| 强制删除 | 【条件态】红字确认「将从 N 个任务摘除该标签」；N > 50 时输入标签名二次确认 | 条件断言 | TASK-002 §3.4 |
| 新建/编辑表单 | 【下拉内容 · 默认态】名称 + 颜色板（12 预设色 + 自定义 hex 输入，#RRGGBB 校验）+ 保存；增删改 / 颜色即时预览 | 断言 | TASK-002 §3.4 |
| 排序 | 【默认态】拖拽行排序（sort_order 浮点插值，复用 TASK-001 算法） | 交互断言 | TASK-002 §3.4 |
| 空态/加载 | 【空态 · 加载态】「还没有标签」+ 新建表单常驻；列表骨架 | 空态断言 | TASK-002 §3.7/§3.8 |

### C.27 任务卡片/行信息升级【变更 · 基线=C.5 表格行与 C.6 卡片】（归属：TASK-002 §3.5 + FILE-001 §3.3 + TASK-003 §3.2）

| 组件 | 字段/交互 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 类型色条 | 【变更 · 默认态】卡片左缘 3px 竖条取 `type.color`（替换 P0 状态色条）；列表编号列 ▮ 色条 + `RBT-128`（mono，点击复制）；冗余文字缩写（REQ/BUG/TSK/TST/DOC）供色弱（aria-label） | 视觉断言 | TASK-002 §3.5 |
| 优先级徽章 | 【变更 · 默认态】旗形图标 + 档位中文名（[⚑ 高]）；`none` 卡片不显示 / 列表显示「—」 | 断言 | TASK-002 §3.5 |
| 标签 Tag | 【变更 · 默认态】最多展示 3 个 + `+N` 溢出提示；列表行内联同规格 | 断言 | TASK-002 §3.5 |
| 子任务徽标 | 【变更 · 默认态】`2/5` + 微条；无子任务不渲染 | 断言 | TASK-002 §3.5 |
| 附件徽标 | 【默认态】卡片 📎 N 徽章（消费 `attachment_count`）；0 时隐藏 | 断言 | FILE-001 §3.3 |
| 列表标题列 | 【变更 · 默认态】标题 + 内联标签 Tag（≤3 + +N）+ 子任务 n/m 徽标（如「导出报表 API [子任务 2/5]」） | 断言 | TASK-003 §3.2 |

### C.28 任务列表·筛选/搜索/排序【变更 · 基线=C.5】（归属：TASK-003）

| 组件 | 字段/交互 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 工具条 | 【变更 · 默认态】搜索框 + 六个下拉（状态 / 类型 / 优先级 / 标签 / 负责人 / 更多▾）+ 已选 Chips 行 +「清空全部」+ 总计数（「87 个任务」） | 断言 | TASK-003 §3.1 |
| 搜索框 | 【默认态】防抖 300ms、清空按钮、字符计数（>56 变红预警）；URL `?q=` 同步（ADR-0011 #11 统一）；`type="search"` 语义 | getByLabel | TASK-003 §3.1/§3.6 |
| 筛选下拉 | 【下拉内容】多选（选项带色点 / 头像）；选项数据源：类型/状态/标签来自 TASK-002 端点（SWR 缓存）、负责人来自 PROJ-002 成员列表；多选确认即生效（无「应用」按钮——离散值语义）；「更多」= 创建人 / 排序入口 | 条件断言 | TASK-003 §3.1/§3.3 |
| 优先级下拉（例） | 【下拉内容】☑ 紧急 #EF4444 / ☑ 高 #F59E0B / ☐ 中 #3B82F6 / ☐ 低 #10B981 / ☐ 无 #9CA3AF（注册源 BOARD-002 §3.2，ADR-0011 #6）+ 尾部「仅看我的任务」快捷（assignee_ids=me） | 断言 | TASK-003 §3.3 |
| 已选 Chips | 【默认态】每个生效条件一枚 Chip（label 取 `meta.applied` 回显值，如「✕ 高，紧急」「✕ 负责人：我」「✕ 截止≤09-07」）；单个 ✕ 移除反查（其余条件保留）；Chip ✕ 可聚焦（aria-label=移除筛选：高优先级） | 交互断言 | TASK-003 §3.1/§3.6 |
| 表格七列 | 【变更（基线=C.5 五列）· 默认态】编号 96px（▮色条+mono 点击复制，可排序 sequence_id）/ 标题 flex-1 min 240px / 优先级 96px（旗形+档位，none 显「—」，可排序 priority 权重序）/ 状态 112px（StateBadge 圆点+名，P2 不可排序）/ 负责人 100px（头像组多人叠放）/ 截止 112px（yyyy-MM-dd 逾期且未完成红+图标，可排序 target_date）/ 更新时间 128px（相对时间 hover 绝对，可排序 updated_at） | 列断言 | TASK-003 §3.2 |
| 列头排序 | 【默认态】点击 asc → desc → 默认三态循环；指示图标 + `aria-sort`；URL 同步；Shift+点击直接降序 | 交互断言 | TASK-003 §3.4/§3.7 |
| 行交互 | 【默认态】行点击开 Drawer（?peekIssue）；方向键行间移动（焦点行高亮、循环滚动）+ Enter 开焦点行详情 | 键盘断言 | TASK-003 §3.2/§3.7 |
| 加载更多 | 【默认态】「加载更多（已显示 50 / 87）」按钮，cursor 追加；末页隐藏按钮 | 条件断言 | TASK-003 §3.1/§3.4 |
| URL 直达/分享 | 【默认态】复制地址栏，对方打开还原全部状态（含排序）；非法参数走 §2.7 表现 | 条件断言 | TASK-003 §3.4 |
| 快捷键 | 【默认态】`/` 聚焦搜索（输入态除外）；Esc 清空搜索并失焦（有词先清词） | 键盘断言 | TASK-003 §3.7 |
| 无任务 | 【空态】复用 TASK-001 §3.5 空态（快速创建行常驻） | 空态断言 | TASK-003 §3.5 |
| 筛选空结果 | 【空态】插画 +「没有符合当前筛选的任务」+ 已选条件 Chips + 主按钮「清空全部」 | 空态断言 | TASK-003 §3.5 |
| 搜索空结果 | 【空态】同上 + 建议「试试更短的关键词（≥ 3 字符可搜描述）」 | 空态断言 | TASK-003 §3.5 |

### C.29 看板四列与筛选工具条【变更 · 基线=C.6】（归属：BOARD-002；VIEWER 行来自 AUTH-005）

| 组件 | 字段/交互 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 第四列 | 【变更 · 默认态】新增「已取消」第四列（四态全开）；列 280px、列头（色点+名称+计数徽章）、列底「＋ 添加任务」 | 断言 | BOARD-002 §3.1 |
| 筛选工具条 | 【变更 · 默认态】高 52px、sticky top-0 z-20 bg-white/95 backdrop-blur；左侧视图切换（分段控件 列表/看板，URL 只变路由段 query 保留→筛选保持 BR-07）+ 搜索框（防抖 300ms，placeholder「搜索任务…」，写 URL `?q=`，有内容显示清空按钮）+ 四筛选下拉（负责人/优先级/标签/截止，复用 TASK-003 FilterBar 控件仅去掉「状态」项）+ Chips +「清空全部」 | 断言 | BOARD-002 §3.1/§3.2 |
| 负责人下拉 | 【下拉内容】多选；项目成员列表（含头像 +「指派给我」快捷项置顶）；选中 Chip 显示头像 + 名字（「我 ×」） | 断言 | BOARD-002 §3.2 |
| 优先级下拉 | 【下拉内容】多选五档色点（注册源）：urgent #EF4444 / high #F59E0B / medium #3B82F6 / low #10B981 / none #9CA3AF；Chips 合并显示「高 · 紧急」 | 断言 | BOARD-002 §3.2 |
| 标签下拉 | 【下拉内容】多选；TASK-002 标签色块 + 名称 | 断言 | BOARD-002 §3.2 |
| 截止下拉 | 【下拉内容】预设区间（今天 / 本周 / 已逾期 / 未来 7 天 / 自定义区间）；写 `target_date=…;before/after` 语法 | 断言 | BOARD-002 §3.2 |
| Chips | 【默认态】rounded-full bg-neutral-100 px-2 py-0.5 text-xs；hover 显 ✕；键盘可聚焦（Tab 到达，Enter/Backspace 移除）；flex-wrap 超一行折叠「+N 个筛选」气泡 | 交互断言 | BOARD-002 §3.1/§3.2 |
| 卡片（全字段） | 【变更 · 默认态】左侧 3px 类型色条（缺陷红/需求紫/任务蓝…）+ 12px 类型 lucide 图标（bug/sparkles/circle-check/flask-conical/file-text）+ 标题 3 行截断 + 优先级色点+短文本（urgent/high 显示，none 不渲染）+ 负责人 20px 头像（多指派 AvatarGroup 前叠 2+计数）+ 截止 M-d（逾期未完成红+alert-circle）+ 标签 3+N + 子任务 ⓔ n/m（无子任务不渲染）；已取消列卡片叠加 opacity-60 | 断言 | BOARD-002 §3.3 |
| 拖入已取消 | 【toast与Alert文案】拖拽落子：卡片半透明淡入 200ms + toast「已取消，可拖回恢复」（5s，含「撤销」=拖回原列）；aria-live 额外播报「任务已取消」 | 条件断言 | BOARD-002 §3.5/§3.7 |
| 筛选应用反馈 | 【默认态】URL query 更新 → SWR key 变化 → 四列卡片渐隐重排 120ms；列计数徽章数字滚动过渡 | 交互断言 | BOARD-002 §3.5 |
| 列头 hover | 【条件态】tooltip「共 N 个任务，当前筛选命中 M」（BR-05） | 条件断言 | BOARD-002 §3.5 |
| 加载更多 | 【默认态】列底「＋」与「加载更多 (N/total)」；追加 25 张（骨架占位）；按钮转「已全部加载 (42/42)」后隐藏 | 条件断言 | BOARD-002 §3.1/§3.5 |
| Esc | 【默认态】关 peek / 取消拖拽 / 关筛选下拉 | 键盘断言 | BOARD-002 §3.5 |
| 拖拽键盘替代 | 【默认态】卡片上下文菜单「移动到 → 待办/进行中/已完成/已取消」（BOARD-001 P0 路径扩展到四列）；aria-live 播报「正在拖动 TZXM-4」「已移动 TZXM-4 到 已完成」 | 键盘断言 | BOARD-002 §3.7 |
| VIEWER 拖拽 | 【权限隐藏】看板卡片 isDragDisabled；新建/拖拽全部消失 | 权限断言 | AUTH-005 §3.2 |
| 筛选空结果 | 【空态】四列结构保留；卡片区中央浮层 `search-x` 64px text-neutral-300 +「无匹配卡片」+「尝试调整或清空筛选」+ 主按钮「清空筛选」（不移除列结构——保留「列还在、只是没命中」因果） | 空态断言 | BOARD-002 §3.6 |
| 项目无任务 | 【空态】复用 BOARD-001 §3.5：四列 + 引导条「暂无任务，点击『＋ 创建任务』或在列内添加」 | 空态断言 | BOARD-002 §3.6 |
| 单组空 | 【空态】复用 P0 空列提示「将任务拖拽到这里」 | 空态断言 | BOARD-002 §3.6 |
| 骨架/失败 | 【加载态 · 条件态】四列骨架（每列 3 卡 animate-pulse）；加载失败卡片区居中 alert-circle + error.message +「重试」（SWR mutate） | 条件断言 | BOARD-002 §3.5/§3.6 |

### C.30 Hover Peek 浮层（归属：BOARD-002 §3.4；路由未变，挂看板卡片）

| 组件 | 字段/交互 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 触发与浮层 | 【条件态】hover 卡片 400ms 浮入（120ms ease-out）；宽 340px、rounded-lg border bg-white shadow-xl p-4、z-30、max-h-320px overflow-y-auto；锚定卡片右侧、越界翻转；可进入内部滚动；退出瞬时（无动画防残影）；`role="dialog"` + aria-labelledby，卡片 aria-expanded 表达开合 | 交互断言 | BOARD-002 §3.4/§3.7 |
| 头部 | 【默认态】编号（TZXM-4）· 类型图标+名（🐛 缺陷）· 优先级（🔴urgent）+ 右上 ⤢ 打开详情按钮（aria-label=打开详情）→ 打开 IssuePeekDrawer（复用 TASK-001），peek 关闭 | 断言 | BOARD-002 §3.4 |
| 标题/摘要 | 【默认态】完整标题（2 行内）；description_stripped 前 200 字 +「展开 ▾」文字按钮（进详情） | 断言 | BOARD-002 §3.4 |
| 标签/子任务/附件 | 【默认态】全部标签平铺 flex-wrap（无 +N 截断）；子任务 8px 进度条（#10B981）+ n/m +「ⓔ 子任务 2/5」；「📎 附件 3」附件数（FILE-001 上线后） | 断言 | BOARD-002 §3.4 |
| 日期/逾期行 | 【条件态】「📅 2026-08-28 → 2026-08-30」开始→截止；已逾期且未完成 text-red-500 +「⏰ 逾期 2 天」；已完成/无日期正常显示 | 条件断言 | BOARD-002 §3.4 |
| 创建人行 | 【默认态】「👤 张三 创建于 2026-08-20」 | 断言 | BOARD-002 §3.4 |
| 触摸设备 | 【条件态】hover 不存在 → peek 不触发；点按即打开详情（无信息损失） | 条件断言 | BOARD-002 §3.4 |
| 键盘 | 【默认态】卡片 role="button" tabIndex=0、Enter 打开详情、F2 聚焦 peek（Tab 遍历内部「打开/展开」） | 键盘断言 | BOARD-002 §3.7 |

### C.31 附件 Tab（Drawer 四 Tab 之一；归属：FILE-001；定位依 ADR-0011 #1）

| 组件 | 字段/交互 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 区块头 | 【默认态】「附件 N」计数 +「＋ 上传附件」按钮 | 断言 | FILE-001 §3.1 |
| 拖拽区 | 【默认态】常驻虚线框（border-dashed border-2）+ 文案「拖拽文件到此处，或点击选择（单文件 ≤ 25MB）」；仅拖拽文件时高亮 border-primary-400 bg-primary-50 + 边框文案变化「松开以上传」；click 唤起 input[type=file][multiple]；键盘替代 Tab+Enter（role=button + aria-label=上传附件） | 交互断言 | FILE-001 §3.1/§3.2/§3.6 |
| 文件行 | 【默认态】类型图标 20px text-neutral-500（MIME 映射：image 🖼 / video 🎬 / pdf 📕 / zip 🗜 / text log 📄 / 未识别 📎）+ 名称 + 大小（KB/MB/GB 二进制自适应）+ 上传人头像 + 相对时间（「3 分钟前」，hover title 绝对时间）+ ⬇ 下载 + 🗑 删除（aria-label「下载/删除 {file}」） | 断言 | FILE-001 §3.1/§3.2 |
| 上传中行 | 【加载态】进度条 4px 圆角 primary-500（速度与百分比 300ms 节流）+ 速度 + 已传/总量（「8.1 / 13.0 MB · 1.2 MB/s」）+ ✕ 取消；role=progressbar + aria-valuenow | 条件断言 | FILE-001 §3.1/§3.2/§3.6 |
| 失败行 | 【条件态】红底行 + 错误信息 +「重试」「移除」；PUT 失败自动重试 2 次（指数退避 1s/3s）仍失败才进入；手动重试从 0 重传（P1 无断点） | 条件断言 | FILE-001 §3.1/§3.3 |
| 并发与预检 | 【条件态】3 并发上行 + 2 排队（队列指示「等待中」）；前端预检失败文件直接红行提示、不进队列 | 条件断言 | FILE-001 §3.3 |
| 取消上传 | 【默认态】✕ → abort xhr → 行移除 → 孤儿对象回收（用户无感） | 交互断言 | FILE-001 §3.3 |
| 下载 | 【默认态】行内 ⬇ → 换发端点 → 302 浏览器下载；链接过期自动重换一次；仍失败 Toast | 交互断言 | FILE-001 §3.3 |
| 删除确认 | 【条件态】行内 Popconfirm「删除 {file}？」红色按钮；焦点陷阱 + Esc 取消；行淡出 200ms、头部计数 -1、卡片 📎 -1 | 条件断言 | FILE-001 §3.2/§3.3/§3.6 |
| 上传完成 | 【默认态】complete 200 → 上传行过渡为文件行（图标+元信息淡入）；aria-live 播报「{file} 上传完成」 | 条件断言 | FILE-001 §3.3/§3.6 |
| 操作权限 | 【权限隐藏】操作入口由 `PermissionGate code="file.upload"` 包裹；对他人上传的附件删除按钮置灰并提示「仅本人上传可删除」；PROJ_VIEWER 无上传权限时隐藏「＋ 上传附件」按钮、拖拽区降级为纯提示文案 | 权限断言 | FILE-001 §3.1/§3.4 |
| 空态 | 【空态】拖拽区常驻（空态即入口）+ 下方一行灰字「暂无附件」 | 空态断言 | FILE-001 §3.4 |
| 骨架/失败 | 【加载态 · 条件态】3 行骨架（图标圆块+两行文字条 animate-pulse）；加载失败 alert-circle + error.message + 重试 | 条件断言 | FILE-001 §3.4 |

### C.32 评论 Tab（Drawer 四 Tab 之一；归属：COLLAB-001；VIEWER 行来自 AUTH-005；定位依 ADR-0011 #1）

| 组件 | 字段/交互 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 区块头 | 【默认态】「💬 评论 5」计数 | 断言 | COLLAB-001 §3.1 |
| 评论行 | 【默认态】头像 + 昵称 + 相对时间（3 分钟前）+ 内容 + 行内 ✏️ 编辑 / 🗑 删除 | 断言 | COLLAB-001 §3.1 |
| @ 渲染 | 【默认态】@ 锚点高亮蓝字，hover 弹成员卡片 | 视觉断言 | COLLAB-001 §3.1 |
| 已编辑标记 | 【条件态】「已编辑」字样（梁工 · 1 分钟前 · 已编辑） | 条件断言 | COLLAB-001 §3.1 |
| 已删除占位 | 【空态】「该评论已删除」灰字占位行、无操作按钮 | 空态断言 | COLLAB-001 §3.1 |
| 加载更早 | 【默认态】「＋ 加载更早的 12 条评论…」 | 断言 | COLLAB-001 §3.1 |
| 输入框 | 【默认态】精简工具条（@ B I 💬 🔗）+ placeholder「评论…」+「（⌘Enter 发表）0/5000」计数；aria-label=评论 | getByLabel | COLLAB-001 §3.1/§3.6 |
| 发表/乐观插入 | 【默认态】⌘Enter / 按钮：行划入列表底部 + 滚动跟随 + 输入框清空保持焦点；按钮 spinner；乐观插入本地行 opacity-60、201 后替换；失败移除 + 草稿恢复 + Toast | 交互断言 | COLLAB-001 §3.4 |
| 编辑态 | 【条件态 · 权限隐藏】原位替换输入框 +「王五 · 编辑中 · 剩余 04:32 ⏱」倒计时（起点「剩余 15:00」——编辑窗口=发表后 15 分钟，§2.2/BR-05；最后 30s 变橙 + ⏱ 图标，ADR-0011 #17）+ 取消 / 保存；超窗提交 → 行内提示；行内 ✏️ 仅本人 + Gate 可见 | 条件断言 | COLLAB-001 §3.1/§3.4/§3.6 |
| 删除 | 【条件态】行内 🗑 → 确认 → 行淡出 → 占位行替换 | 条件断言 | COLLAB-001 §3.4 |
| VIEWER 占位条 | 【权限隐藏】评论区替换为「你以查看者身份访问此项目」占位条（comment.create fallback） | 权限断言 | AUTH-005 §3.2 |

### C.33 @ 补全浮层（归属：COLLAB-001 §3.2；挂评论输入框）

| 组件 | 字段/交互 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 触发 | 【条件态】键入 `@` + ≥0 字符；Esc 关闭；退格删除触发词后关闭 | 交互断言 | COLLAB-001 §3.2 |
| 浮层 | 【下拉内容】过滤行「🔍 过滤：lia」+ 候选列表（● 梁工 liang@rp.dev / ○ 李安 lia@rp.dev）；↑↓ 选择、Enter 确认；role="listbox" + aria-activedescendant | 键盘断言 | COLLAB-001 §3.2/§3.6 |
| 候选源 | 【默认态】项目成员缓存（PROJ-002 数据 + WS_OWNER/ADMIN 隐式成员），按昵称 / 邮箱前缀模糊过滤 | 条件断言 | COLLAB-001 §3.2 |
| 插入产物 | 【默认态】`<span data-mention-id="{uuid}">@梁工</span>`（text-primary-600 蓝字） | 断言 | COLLAB-001 §3.2 |
| 无匹配 | 【空态】显示「无成员」 | 空态断言 | COLLAB-001 §3.4 |
| 已移出成员锚点 | 【条件态】已删 / 已移出成员的旧锚点：hover 成员卡片提示「已不在项目」，渲染保持蓝字 | 条件断言 | COLLAB-001 §3.2 |

### C.34 通知中心【变更 · 基线=C.3 顶栏（新增铃铛）→ 抽屉】（归属：COLLAB-001）

| 组件 | 字段/交互 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 铃铛 | 【变更 · 默认态】全局顶栏常驻铃铛（工作台与项目内顶栏一致，ADR-0011 #16）+ 未读徽标（bg-red-500 圆形，数字 99+ 封顶）；aria-label=「通知，N 条未读」；徽标变化 aria-live=polite 播报；30s 轮询，徽标数变化轻微弹跳动画（≤1 次/30s）；失败静默退避 | 断言 | COLLAB-001 §3.3/§3.4/§3.6 |
| 抽屉 | 【默认态】右侧滑入 420px、role="dialog"；打开时自动拉取列表（不全量预取） | 条件断言 | COLLAB-001 §3.3 |
| 头部 | 【默认态】「通知」标题 +「仅看未读」本地开关（抽屉无路由不进 URL；开启后列表请求带 `?unread=true`，ADR-0011 #5）+「全部已读」按钮 | 断言 | COLLAB-001 §3.3 |
| 分组 | 【默认态】今天 / 昨天 / 更早 (N)（date-fns isToday/isYesterday）；「更早 (N)」分组底部「加载更多」按钮（cursor 分页，ADR-0011 #15） | 断言 | COLLAB-001 §3.3 |
| 通知行 | 【默认态】未读蓝点 ● / 已读 ○（另有 sr-only「未读」文本）；事件图标（issue.assigned 👤 / mentioned @ / commented 💬 / updated ✏️）；文案例「王五 在 RBT-128 中提到了你」「李四 将 RBT-130 指派给你」「王五 更新了 RBT-128：状态 待办 → 进行中」「张三 评论了 RBT-130」「系统 · 你加入项目『RabbitProjects』」+ 相对时间；role=link + aria-label 完整朗读 | 断言 | COLLAB-001 §3.3/§3.6 |
| 行点击 | 【默认态】蓝点消失（乐观）→ 跳转任务详情并锚定高亮 2s；实体失效 Toast 降级 | 交互断言 | COLLAB-001 §3.4 |
| 全部已读 | 【toast与Alert文案】确认 Toast「已将 N 条标为已读」；全部蓝点淡出 + 徽标归零动画 | 条件断言 | COLLAB-001 §3.3/§3.4 |
| 空态 | 【空态】「没有新消息」插画 +「去协作」引导按钮 | 空态断言 | COLLAB-001 §3.3 |

### C.35 个人工作台首页 `/:workspaceSlug/`【变更 · 基线=C.3（侧栏「首页」置灰项点亮）】（归属：RPT-001；点亮登记 ADR-0011 #18）

| 组件 | 字段/交互 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 侧栏点亮 | 【变更（基线=C.3 侧栏）】工作区侧栏「首页」由置灰点亮为工作台入口 | 断言 | RPT-001 §3.1 |
| 欢迎行 | 【默认态】「早上好，{display_name} 👋」按本地时间分档问候（5-11 / 11-14 / 14-18 / 其余）+ 日期（2026年9月1日 星期二）+ 手动刷新 ⟳ 按钮 | 文案断言 | RPT-001 §3.1 |
| 统计卡行 | 【默认态】5 卡：📋 待办 N 项任务 / 🕐 今日到期 N 项到期 / ⚠ 已逾期 N 项逾期 / ✓ 本周完成 N 项 / 📈 近 7 日完成 N 项；图标 20px + 大数字 text-3xl tabular-nums + 标签；hover border-neutral-300 shadow-sm；整卡可点击（role=button + aria-label「查看已逾期任务，共 1 项」）；计数变化 200ms 数字滚动 | 断言 | RPT-001 §3.1/§3.2 |
| 逾期/到期卡 | 【条件态】已逾期卡数字红色 text-red-500 + ⚠ 12px 图标（0 时恢复正常色——红色是告警不是常态）；今日到期卡橙色 text-amber-500 + 🕐 图标 | 条件断言 | RPT-001 §3.1/§3.2 |
| 趋势卡 | 【默认态】「近 7 日完成」迷你柱状图（7 柱、高 64px、柱宽 10px 圆角，柱色 #10B981、今日柱 #3B82F6 高亮；无值日 2px 占位柱；Y 轴隐藏）；hover tooltip「8-28 · 完成 1 项」；aria-label「近 7 日共完成 8 项」+ 每柱独立 label | 视觉断言 | RPT-001 §3.1/§3.2/§3.5 |
| 待办 Tabs | 【默认态】下划线式：全部 23 / 今日到期 3 / 已逾期 1 / 已完成(本周) 8；计数徽章 rounded-full bg-neutral-100；当前 Tab 主色下划线 2px；role=tablist/tab/tabpanel、方向键切换、aria-selected | 断言 | RPT-001 §3.1/§3.2/§3.5 |
| 任务行 | 【默认态】IssueRow 高 44px：项目徽章（identifier 色块 rounded px-1 font-mono）+ 编号（RBT-128，mono text-neutral-400）+ 标题（text-sm truncate）+ 右侧类型图标 + 优先级色点 + 截止（逾期红 / 今日橙 🕐）；行 hover bg-neutral-50；整行可点击 tabIndex=0、Enter 打开；点击跳任务详情（新页签打开，保留工作台上下文） | 断言 | RPT-001 §3.2/§3.3/§3.5 |
| 卡片-Tab 映射 | 【默认态】点统计卡（如「已逾期 1」）→ 列表切到对应 Tab（四元映射） | 交互断言 | RPT-001 §3.3 |
| 手动刷新 | 【默认态】下拉 / ⟳：stats + 列表 + 通知并行 revalidate；按钮旋转；骨架卡 | 交互断言 | RPT-001 §3.3 |
| 定时收敛 | 【条件态】窗口 focus / 60s：stats revalidate（revalidateOnFocus + refreshInterval 60_000） | 条件断言 | RPT-001 §3.3 |
| 通知摘要卡 | 【默认态】右侧 320px 卡：「🔔 通知 (2)」未读数红色徽标 + 最近 3 条（复用 COLLAB-001 数据，text-xs 两行截断 + 相对时间）+「查看全部 →」（打开通知抽屉，ADR-0011 #5） | 断言 | RPT-001 §3.1/§3.2/§3.3 |
| 新用户空态 | 【空态】统计卡正常显示 0（结构保留）；列表区 `coffee` 64px text-neutral-300 插画 +「暂无待办」+「去项目创建你的第一个任务」+ 按钮「浏览项目」；趋势卡 7 根占位柱 | 空态断言 | RPT-001 §3.4 |
| 某 Tab 空 | 【空态】列表区局部空态「该分类下暂无任务」+ 建议切「全部」 | 空态断言 | RPT-001 §3.4 |
| stats 加载失败 | 【条件态】四卡显示 `—` 占位（不显示 0，防误导）+ 卡角「重试」 | 条件断言 | RPT-001 §3.4 |
| 列表加载失败 | 【条件态】列表区 alert-circle + error.message +「重试」 | 条件断言 | RPT-001 §3.4 |
| 通知无未读 | 【空态】摘要卡「🎉 已处理全部通知」+ 最近 3 条仍展示（灰显） | 空态断言 | RPT-001 §3.4 |

### C.36 全局错误呈现组件【变更/扩展 · 基线=C.9 全局表面】（归属：INFRA-004）

> INFRA-004 §3 自述「基础设施文档，无直接业务界面」，其 §3 定义四类被全部页面消费的全局组件，列入本清单。

| 组件 | 字段/交互 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| ErrorToast | 【toast与Alert文案】三变体：error（红，alert-circle）/ warning（橙，alert-triangle）/ info（蓝）；例文案：「⚠ 请求过于频繁，请在 23 秒后重试」（warning）；「✕ 服务器开小差了，请稍后重试」+ 追踪号行「追踪号 01JBX3K9 📋 复制 [反馈]」（error + request_id）；role=status + aria-live（warning=polite / error=assertive） | 文案断言 | INFRA-004 §3.2/§3.7 |
| 时长/堆叠 | 【默认态】普通 5s；含 request_id 10s（保证能抄下追踪号）；视口右上角 fixed top-4 right-4 z-50；多条纵叠最多 3 条、超出挤掉最早；✕ 立即关；hover 暂停自动消失计时；<768px 顶部通栏 | 条件断言 | INFRA-004 §3.2/§3.7 |
| 追踪号复制 | 【默认态】request_id 前 8 位 font-mono；点 📋 复制完整 ULID + toast「已复制」 | 交互断言 | INFRA-004 §3.2 |
| 反馈 Modal | 【条件态】仅 500 类错误的 Toast 显示「反馈」；点击打开 480px Modal（ADR-0011 #12 定稿）：标题「问题反馈」+ 只读追踪号（mono，可复制）+ 描述 textarea ≤500 字 + 取消 / 提交；提交仅前端打结构化日志 + Toast「已记录，感谢反馈」（无后端端点，P2 接 Sentry 时再定） | 条件断言 | INFRA-004 §3.2 |
| 表单字段错误映射 | 【条件态】VALIDATION_ERROR 的 details[] 只落字段不弹全局 Toast（避免一次弹 N 条）：输入框 border-red-500 + ⚠ 错误文案 text-red-600 text-xs；子码文案表：REQUIRED「该项为必填项」/ UNIQUE「该值已被使用」/ DOES_NOT_EXIST「所选值无效」/ INVALID「格式不正确」/ TOO_LONG「超出长度限制」（兜底「校验未通过」）；无对应字段（field=__all__）降级 ErrorToast；aria-describedby 关联 + aria-invalid | 条件断言 | INFRA-004 §3.3/§3.7 |
| 错误空态页 | 【条件态】404/403/500 共用骨架：96px lucide 图标 + 主标题 text-xl + 副文案（按 code 微调）+ 双按钮「← 返回」「返回工作台」；500 时显示「追踪号 01JBX3K9 · 复制」；空态页居中最大宽 480px、h1、正常文档流、按钮可 Tab | 条件断言 | INFRA-004 §3.4/§3.7 |
| code 分支文案 | 【条件态】RESOURCE_NOT_FOUND → compass「页面走丢了」/「你访问的内容不存在、已删除，或你没有访问权限」；PERM_DENIED / PERM_ROLE_INSUFFICIENT → lock「没有访问权限」/「联系项目管理员为你开通权限后再试」；SERVER_* → server-crash「服务暂时不可用」/「请稍后重试；若持续出现，请凭追踪号反馈」 | 条件断言 | INFRA-004 §3.4 |
| 路由级/请求级共用 | 【条件态】React Router ErrorBoundary（路由级）与业务页请求失败空态（请求级）渲染同一组件，仅数据来源不同 | 条件断言 | INFRA-004 §3.4 |
| 429 退避 | 【toast与Alert文案】首次 429：ErrorToast(warning)「请求过于频繁，请在 N 秒后重试」，N 取 Retry-After 头（优先）或 details 内 RETRY_AFTER；拦截器指数退避重试（1s 起步、因子 2、抖动 ±20%、最多 3 次；幂等方法自动重试，POST 仅带 Idempotency-Key 时重试）；重试期间局部 Spinner 不弹新 Toast；重试仍失败才弹 warning Toast | 条件断言 | INFRA-004 §3.5 |
