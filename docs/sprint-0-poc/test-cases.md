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

---

## 附录 C（续）· Sprint 2 新增表面 C.37~C.63

> 依据 ADR-0010 纪律 ①：Sprint-2（TASK-004~010）新增/变更 UI 表面先行登记，每行标规格出处；跨文档裁决 R1~R5 与原型开放点 O1~O4 已定稿（ADR-0013，原型 FROZEN 2026-09-05）——R2/R3 所涉 C.27/C.28/C.24 的旧形态行以本批 C.37/C.41 行为最终基线。视觉/交互验收基准 = 该冻结原型。

### C.37 任务列表·树形展示【变更 · 基线=C.28 七列】（归属：TASK-004 §3.1）
| 懒加载失败态 | 【条件态】展开请求失败：该层行内「加载失败 · 重试」按钮（TASK-004 §3.6，反扫 M1 补） | 条件断言 | TASK-004 §3.6 |
| 树形响应式 | 【条件态】768~1279 隐藏「负责人/截止」列、缩进 16px/层；<768 树降级卡片+缩进横条、折叠默认全收起（反扫 M2 补） | 条件断言 | TASK-004 §3.7 |

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 树形行结构 | 【默认态】行 = 折叠箭头(16px，有子级才渲染，无子级占位对齐) + 编号(96px mono，▮类型色条，缩进发生在整行：每层 20px) + 标题(树形引导线 border-l neutral-200；顶层 text-sm font-medium、子行 text-sm) + 其余列沿用 C.28 | 断言 | TASK-004 §3.1 |
| 折叠箭头 | 【默认态】ChevronRight 展开时旋转 90°（150ms ease）；点击懒加载 `?parent_id=&order_by=sort_order&per_page=50`；请求期间箭头转圈 | 交互断言 | TASK-004 §3.1 |
| 子任务进度列 | 【默认态】独立列 88px：SubtaskProgress 16px 圆环 + `1/2` 文本（直接子级口径，排除 cancelled 分子分母）；全完成圆环实心绿 `2/2`；无子级显示 `—`【R2 裁决：替代 C.27 标题列内联徽标】 | 断言 | TASK-004 §3.1 |
| 「⊕ 展开 ▾」下拉 | 【默认态】工具条三项：「全部展开（≤500 节点）」/「收起到第 1 层」/「收起到第 2 层」 | 交互断言 | TASK-004 §3.1 |
| 折叠记忆 | 【条件态】折叠状态存 localStorage key `issue-tree:collapsed:{projectId}`，刷新还原 | 条件断言 | TASK-004 §3.1 |
| 排序语义 | 【条件态】树形模式下 order_by 仅作用于同层兄弟；全局排序（如 -created_at）自动切换平铺模式并顶部提示 | 条件断言 | TASK-004 §3.1 |
| 行悬浮 | 【默认态】出现「＋」（行下方插入快速行入口）与拖拽把手 grip-vertical | 断言 | TASK-004 §3.1 |
| 键盘/无障碍 | 【默认态】树容器 role=tree、行 role=treeitem、aria-expanded 绑定箭头、aria-level=depth；← 折叠 / → 展开 / ↑↓ 同层移动 / Home/End 首末 / Enter 打开详情；进度徽标 aria-label「子任务 2 个，已完成 1 个」 | 断言 | TASK-004 §3.1 |

### C.38 行内快速加子任务（归属：TASK-004 §3.2）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 插入输入行 | 【默认态】目标行下方插入：`＋ 输入子任务标题后按回车…` + 提示「Enter 保存 · Esc 取消」；缩进对齐目标行子级 | 断言 | TASK-004 §3.2 |
| 提交语义 | 【默认态】Enter 创建（仅 name，类型继承父任务，状态落默认）；清空保持 focus 可连续录入；Esc 取消 | 交互断言 | TASK-004 §3.2 |
| 乐观插入 | 【默认态】回车瞬间插入 opacity-60 临时行（编号 `…`、temp- 前缀）；成功后替换真实行，父徽标 +1；失败移除临时行 + 内容恢复输入框 + Toast error.message | 交互断言 | TASK-004 §3.2 |
| 深度上限 | 【条件态】第 5 层行的悬浮「＋」不渲染（前端预判）；直连 API 由后端 409 DEPTH 兜底 | 条件断言 | TASK-004 §3.2/§2 |

### C.39 拖拽移动子树 + 确认弹层（归属：TASK-004 §3.5；技术参数 §4.4.2）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 起拖 | 【默认态】按住把手 150ms；行 opacity-50；光标 grabbing | 交互断言 | TASK-004 §3.5 |
| 双区判定 | 【条件态】目标行上沿 25%（DROP_EDGE_RATIO=0.25）= 排到它上面（同级排序，复用 BOARD-001 sort_order 插值）；中部 75% = 成为它的子任务 | 交互断言 | TASK-004 §3.5/§4.4.2 |
| 缩进预览 | 【条件态】中部悬停 400ms 后目标行展开一级缩进虚线框，提示「松开移入」 | 交互断言 | TASK-004 §3.5 |
| 确认弹层 | 【默认态】文案「将 “X”（含 N 个后代）移动到 “Y” 之下？」按钮 移动/取消；聚焦陷阱，Esc 取消并还原 | 断言 | TASK-004 §3.5 |
| 成环反馈 | 【条件态】409 响应 Toast 环路径（error.details[0].message 直出）；树中环上节点红色高亮 2s（flashCyclePath） | 条件断言 | TASK-004 §3.5 |
| 自拖自 | 【条件态】拖到自身行 = 无操作（前端判定） | 条件断言 | TASK-004 §3.5 |
| 移动端禁用 | 【条件态】<768px 拖拽移动禁用，改行菜单「移动到…」（见 C.63） | 条件断言 | TASK-004 §3.7 |

### C.40 全屏树抽屉（归属：TASK-004 §3.3）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 入口/宽度 | 【默认态】详情子任务分区「查看整棵树 →」/「查看全部 N 个 →」打开；宽 min(960px, 100vw-48px)；<768~1279 全宽 | 断言 | TASK-004 §3.3 |
| 头部统计 | 【默认态】`N 个任务（含自身）· M 已完成 · 最深 K 层`（stats 含根口径）；font-mono | 断言 | TASK-004 §3.3 |
| 节点行 | 【默认态】状态圆点(state.color) + 编号 + 标题(truncate) + 负责人头像 + 子任务圆环；缩进 24px/层；节点点击 → 打开该任务详情 Drawer（返回时树状态保留）；hover tooltip 含工时行（与 C.48 联动） | 交互断言 | TASK-004 §3.3 + TASK-006 §3.3 |
| 数据源 | 【默认态】GET …/issues/{id}/subtree/ 一次 CTE（root + nodes + stats） | — | TASK-004 §4.2 |
| 截断黄条 | 【条件态】meta.truncated=true 时底部黄条 + 建议按状态/负责人筛选 | 条件断言 | TASK-004 §3.3 |
| 空态/加载/失败 | 【空态】「暂无子任务」+「添加第一个子任务」按钮（focus 到隐藏快速行）；【加载】3 层×5 行骨架树；【条件态】失败 alert-circle + error.message + 重试（样式 O4 原型先行定义） | 空态断言 | TASK-004 §3.3/§3.6 |

### C.41 任务详情抽屉·子任务分区升级【变更 · 基线=C.24】（归属：TASK-004 §3.4；R3 裁决）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 分区头 | 【默认态】「子任务 ◔ 1/2」圆环计数（替代 C.24 微条）+ 折叠开关 +「＋」 | 断言 | TASK-004 §3.4 |
| 列表 | 【默认态】直接子级前 20 条（标题 + 状态圆点 + 复选完成）；尾部「查看全部 N 个 →」进全屏树 | 断言 | TASK-004 §3.4 |
| 完成勾选 | 【默认态】点复选 = PATCH state 落项目「已完成」；父徽标乐观 +1 | 交互断言 | TASK-004 §3.4 |
| 拖拽重排 | 【默认态】分区内拖拽把手重排（更新 sort_order，复用 BOARD-001 插值） | 交互断言 | TASK-004 §3.4 |
| 添加行/空态 | 【默认态】沿用 C.24 文案：「＋ 添加子任务，回车保存…」「暂无子任务，添加一个开始拆解」【R3】 | 断言 | C.24 + TASK-004 §3.4 |
| 归档只读态 | 【条件态】已归档任务：子任务分区只读渲染直接子级（取数 ?archived=true 口径），无「＋」与完成勾选；「查看整棵树」入口隐藏（subtree 对归档根 404） | 条件断言 | TASK-004 §3.4 + TASK-009 §3.2 |

### C.42 任务详情抽屉·关联分区（新增区块）（归属：TASK-005 §3.1）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 三分组 | 【默认态】固定顺序：阻塞于此（is_blocked_by 前置）/ 阻塞（blocks 后置）/ 相关（relates_to+duplicates 合并，duplicates 项加「重复于」角标）；空组不渲染；分组标题 role=group + aria-label | 断言 | TASK-005 §3.1 |
| 关联行 | 【默认态】图标 + 编号(font-mono text-xs) + 标题(truncate) + 状态圆点 + 跳转箭头(→ 点击跳目标详情，保留返回栈) + 删除 ⓧ（悬浮显现，二次确认） | 交互断言 | TASK-005 §3.1 |
| 阻塞语义强化 | 【条件态】未完成前置行前置 alert-triangle(amber)；已完成为 check(green) 视觉降级 | 条件断言 | TASK-005 §3.1 |
| 计数与上限提示 | 【默认态】分区标题 (N) 为三组合计；≥40 时计数变 amber（50 上限预警） | 条件断言 | TASK-005 §3.1/BR-08 |
| 空态/加载/失败 | 【空态】无任何关联收缩为一行「关联 — [+ 添加]」；【加载】3 行骨架；【条件态】失败行内「加载失败 · 重试」 | 空态断言 | TASK-005 §3.5 |
| 响应式 | 【条件态】≥1280 全量；768~1279 关联行隐藏状态圆点（保留 8px 色点 + tooltip）；<768 分组折叠手风琴、弹层全屏 | 条件断言 | TASK-005 §3.6 |

### C.43 添加关联弹层（归属：TASK-005 §3.2）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 类型下拉 | 【默认态】四项固定（图标+中文名+代码角标）：⚡ 阻塞了… blocks / ⛔ 被…阻塞 is_blocked_by / 🔗 相关于… relates_to / 👥 重复于… duplicates | 断言 | TASK-005 §3.2 |
| 语义提示行 | 【默认态】选中类型后底部 ⓘ 提示：blocks/is_blocked_by →「目标完成前，当前任务将无法流转到已完成」；其余 →「仅建立关联，不阻塞流转」 | 条件断言 | TASK-005 §3.2 |
| 目标搜索 | 【默认态】防抖 300ms GET ?search=&per_page=20；排除自身；行显状态圆点；【空态】「未找到匹配任务，换个关键词」 | 交互断言 | TASK-005 §3.2/§3.5 |
| 已关联预判 | 【条件态】目标已被当前任务关联时搜索行内灰字「已关联」且不可选 | 条件断言 | TASK-005 §3.2 |
| 提交/失败 | 【默认态】创建关联 loading → 关闭弹层 + 分区乐观插入对应分组；409 环路径 → 弹层内红条完整依赖链；其他失败 toast | 交互断言 | TASK-005 §3.2 |
| 键盘 | 【默认态】Tab 顺序 类型 → 搜索 → 结果方向键 → 提交；<768 全屏 | 断言 | TASK-005 §3.2/§3.6 |

### C.44 完成被拦截对话框（归属：TASK-005 §3.3；看板/详情双入口）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 结构 | 【默认态】⛔ 无法完成「X」+ 阻塞项列表（圆点+编号+标题+状态+跳转→）+ ⓘ「已取消（cancelled）的前置任务不会阻塞完成」+ [我知道了] [强制完成（管理员）]；role=alertdialog + aria-describedby 指向阻塞列表 | 断言 | TASK-005 §3.3 |
| 触发 | 【条件态】拖拽 409 RESOURCE_TRANSITION_BLOCKED 后：卡片 300ms 弹回原列 + 列头 shake 一次 + 弹本对话框；详情改状态同样触发 | 条件断言 | TASK-005 §3.3/§4.4.2 |
| 阻塞项跳转 | 【默认态】点击 → 跳转该任务详情（返回后对话框已关闭、原任务留在原地） | 交互断言 | TASK-005 §3.3 |
| 强制完成 | 【条件态】仅 PROJ_ADMIN 可见（与后端 403 同口径）；点击展开必填 comment 输入（≥5 字符）后重发 force=true | 条件断言 | TASK-005 §3.3 |
| 环依赖 toast | 【条件态】RESOURCE_CIRCULAR_DEPENDENCY → toast.error 6000ms 完整依赖链 | 条件断言 | TASK-005 §4.4.2 |

### C.45 列表/看板依赖可见性（归属：TASK-005 §3.4）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 列表行依赖图标 | 【默认态】标题左侧 12px link 图标（存在任意关联时） | 断言 | TASK-005 §3.4 |
| ?blocked 筛选 | 【默认态】列表筛选只看被阻塞任务（Chip 回显） | 交互断言 | TASK-005 §3.4/§4.2 |
| 看板 ⛔ 角标 | 【条件态】被未完成前置阻塞的卡片右上角 ⛔(amber)；tooltip 列阻塞项前 3 个 +「等 N 项」；角标带文本 tooltip（无障碍） | 条件断言 | TASK-005 §3.4 |
| 列头计数 | 【默认态】「进行中 · 5（2 被阻塞）」次级文本（amber） | 断言 | TASK-005 §3.4 |

### C.46 任务详情抽屉·工时分区（新增区块）（归属：TASK-006 §3.1）
| 记录加载骨架 | 【加载态】记录列表加载：3 行骨架（反扫 M3 补） | 空态断言 | TASK-006 §3.4 |

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 估算输入 | 【默认态】下拉常用值 0.5h/1h/2h/4h/8h/16h/24h +「自定义」（分钟粒度数字输入）；onBlur 提交 PATCH estimate_minutes | 交互断言 | TASK-006 §3.1 |
| 已耗主数字 | 【默认态】formatMinutes（<60→45m；<480→5.5h；≥480→1d 2.5h，1d=8h）；子树口径为次级行（⊕含子任务开关，默认本任务口径，切换显「本任务 X · 子树估算 Y」） | 断言 | TASK-006 §3.1/§4.4.2 |
| 进度条 | 【默认态】spent/estimate；estimate 空时隐藏；>100% 红色 + 显示「128%」+「超出 N%」；role=progressbar + aria-valuenow（分钟） | 条件断言 | TASK-006 §3.1 |
| 超耗红显 | 【条件态】spent>estimate 时已耗数字与进度条红 text-red-600 +「超耗 +X」+ aria-label「超出估算 N%」 | 条件断言 | TASK-006 §3.1 |
| 记录列表 | 【默认态】日期(yyyy-MM-dd)/人(头像+名)/时长(0.5h 粒度展示分钟精确)/备注(truncate 悬浮全文)；⋯ 菜单 编辑/删除（仅本人或 PROJ_ADMIN，他人行无菜单）；分页 20/页 | 断言 | TASK-006 §3.1 |
| 空态/不一致 | 【空态】无估算无记录 → 分区一行「工时 — [⏱ 记工时]」（估算 placeholder「设估算」）；【条件态】汇总与列表短暂不一致 → 侧栏以 spent annotate 为准，SWR 30s 收敛 | 空态断言 | TASK-006 §3.4 |
| 响应式/无障碍 | 【条件态】768~1279 隐藏子树口径行；<768 记录折叠「N 条记录 ▾」、弹层全屏底部抽屉；时长输入 inputmode=numeric；记录 ⋯ 键盘可达 | 条件断言 | TASK-006 §3.6/§3.7 |

### C.47 工时填报弹层 WorkLogDialog（归属：TASK-006 §3.2）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 标题/时长 | 【默认态】「记工时 · 任务名」；时长下拉 0.5h 步进 + 自定义分钟输入；快捷 chips [30m][1h][2h][4h][8h] 选中即填 | 断言 | TASK-006 §3.2 |
| 日期 | 【默认态】max=today / min=today-30d / 默认今天；提示「可补填最近 30 天」 | 断言 | TASK-006 §3.2 |
| 备注 | 【默认态】备注（可选）placeholder「做了什么…」 | 断言 | TASK-006 §3.2 |
| 提交/连续填报 | 【默认态】乐观更新侧栏已耗（+N 分钟），成功关闭、失败回滚 + Toast；⌘+点击保存 = 保存并再开（清空时长保留日期）；⌘/Ctrl+Enter 提交 | 交互断言 | TASK-006 §3.2 |
| 复用两态 | 【条件态】「填报」「编辑」复用同一弹层（编辑预填原值，对新值重校验窗口） | 条件断言 | TASK-006 §3.2/§4.4.2 |

### C.48 列表工时列 + 全屏树工时 tooltip（归属：TASK-006 §3.3）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 工时列（可选开启） | 【默认态】`⏱ 5.5/8h`；超耗红；无估算仅 `⏱ 5.5h`；无记录灰显 `—`；列选择器开关 | 断言 | TASK-006 §3.3 |
| 看板卡片排除 | 【条件态】看板卡片不展示工时（信息密度，非流转语义）【R5 确认】 | 条件断言 | TASK-006 §3.3 |
| 全屏树 tooltip | 【条件态】节点 hover tooltip 增加工时行 | 条件断言 | TASK-006 §3.3 |

### C.49 任务详情抽屉·执行人区升级【变更 · 基线=C.23 负责人行】（归属：TASK-007 §3.1）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 头像堆叠 AvatarGroup | 【默认态】24px 圆形、重叠 -8px、边框 2px 白；>3 显 +N（neutral 底，按钮展开浮层列全部：头像+名+在线状态）；role=group aria-label「执行人 N 人：…」 | 断言 | TASK-007 §3.1/§4.4.2 |
| 名称行 | 【默认态】text-xs text-neutral-500 truncate；hover 浮层列全量 | 断言 | TASK-007 §3.1 |
| 空态/认领 | 【空态】👤 未指派 虚线框 +「🖐 认领」按钮（无执行人且自己 ≥CONTRIBUTOR；点击即 POST 无需确认，乐观插入自己头像；409 STATE 已被认领 → Toast + mutate）；aria-label「认领该任务」 | 空态断言 | TASK-007 §3.1/§3.4 |
| 编辑入口 | 【默认态】[＋ 编辑] 打开转交弹层（C.50） | 交互断言 | TASK-007 §3.1 |
| 退出任务 | 【条件态】+N 浮层中自己行的次级动作「退出任务」；最后一人退出时二次确认加强 | 条件断言 | TASK-007 §3.1 |
| 失败回滚 | 【条件态】转交失败头像堆叠回滚 + Toast；成员列表加载弹层 5 行骨架 | 条件断言 | TASK-007 §3.4 |
| 响应式 | 【条件态】≥1280 堆叠+名称行+编辑；768~1279 仅堆叠+ +N；<768 单头像+计数徽标 | 条件断言 | TASK-007 §3.6 |

### C.50 转交弹层 AssigneePicker（归属：TASK-007 §3.2；§4.4.1 组件）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 成员列表 | 【默认态】active 项目成员；CONTRIBUTOR+ 可勾选；COMMENTER/VIEWER 灰显标注「（评论者/查看者，不可指派）」；自己带「（我）」；显示在线点 + 邮箱 | 断言 | TASK-007 §3.2 |
| 搜索/勾选 | 【默认态】🔍 搜索成员…；勾选为 checkbox 组（方向键+空格）；已选 chips 顺序即提交顺序（去重保序），× 移除 | 交互断言 | TASK-007 §3.2 |
| 计数 n/10 | 【条件态】已选 2/10；达 10 后未选项禁用（MAX_ASSIGNEES=10） | 条件断言 | TASK-007 §3.2 |
| 转交说明 | 【默认态】（可选，将随通知发送）maxLength 500；通知预览灰字「将通知：新增 N 人、移除 M 人」aria-live=polite | 断言 | TASK-007 §3.2 |
| 保存 | 【默认态】PUT 全量集合 + comment；乐观更新头像堆叠；失败回滚；提交结果以服务端 assignee_ids 整体替换 | 交互断言 | TASK-007 §3.2 |
| <768 | 【条件态】弹层全屏 | 条件断言 | TASK-007 §3.2 |

### C.51 列表/看板执行人呈现 + 快速指派/认领【变更 · 基线=C.27/C.29 负责人位】（归属：TASK-007 §3.3）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 列表负责人列 | 【默认态】头像堆叠 20px；空显示「未指派」neutral 徽标（可点开筛选）；项目级「未指派」内置筛选一键收拢 | 断言 | TASK-007 §3.3 |
| 快速指派 | 【默认态】列表行悬浮头像区 → 单人快速选择浮层 | 交互断言 | TASK-007 §3.3 |
| 认领入口 | 【默认态】列表行悬浮（未指派时）「🖐」按钮；未指派任务 → 虚线人形 + 认领按钮 | 交互断言 | TASK-007 §3.3 |
| 看板执行人 | 【默认态】卡片头像堆叠右上角；未指派卡片左上虚线人形占位；沿用 C.29 20px AvatarGroup 前叠 2+计数 | 断言 | TASK-007 §3.3 |

### C.52 字段管理页（项目设置 → 字段）（归属：TASK-008 §3.1）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 列表列 | 【默认态】拖拽把手 / 名称 / 类型中文 / 作用域（适用类型 chips）/ 必填·默认值标记 / 索引标记（● 索引优化）/ 操作 [⋯ 编辑·停用启用·删除] | 断言 | TASK-008 §3.1 |
| 顶部提示条 | 【默认态】「Workspace 全局字段 N 个 · 项目私有字段 M 个」+ 字段计数 x/50（≥45 变 amber） | 断言 | TASK-008 §3.1/BR-10 |
| 继承折叠区 | 【默认态】「继承自 Workspace」折叠区只读 +「在 Workspace 设置中管理」跳转（权限不足时提示条） | 断言 | TASK-008 §3.1 |
| 拖拽排序 | 【默认态】拖拽把手落位即 PATCH sort-order（浮点插值复用 BOARD-001）；键盘替代：行菜单「上移/下移」 | 交互断言 | TASK-008 §3.1/§3.7 |
| 停用/启用 | 【条件态】停用行 opacity-50 +「数据保留」角标；启用一键恢复 | 条件断言 | TASK-008 §3.1 |
| 空态 | 【空态】插画 +「创建第一个字段」+ 3 个场景模板按钮（严重等级/需求来源/影响版本一键创建） | 空态断言 | TASK-008 §3.6 |
| 响应式 | 【条件态】≥1280 全列；768~1279 隐藏作用域列；<768 卡片化 | 条件断言 | TASK-008 §3.7 |

### C.53 新建/编辑字段弹层（归属：TASK-008 §3.2）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 基本字段 | 【默认态】字段名称 / 字段标识 cf_ 前缀（按名称自动拼音转写生成，可改，失焦校验格式，保存后置灰）/ 适用任务类型（全部默认勾选） | 断言 | TASK-008 §3.2/BR-01 |
| 12 类型宫格 | 【默认态】12 类型图标+名称（text/textarea/select/multi_select/number/currency/date/checkbox/member/url/member_multi/auto_increment（ADR-0015 A-8 勘误：原型宫格的 email/phone 系笔误，后端 P2 白名单拒绝））；选定后表单下段按类型变形（选项区/日期格式/币种）；auto_increment 选项区隐藏、必填强制关 | 断言 | TASK-008 §3.2/§4.4.2 |
| 选项行 | 【默认态】色块（ColorPicker 12 预设，对比度 ≥4.5:1）+ 显示名 + 存储值（自动转写可改）+ 拖拽 + 删除；「＋ 添加选项」；ⓘ「存储值创建后不可改，显示名可改」；删除选项值时提示「N 个任务仍在使用该值」 | 交互断言 | TASK-008 §3.2 |
| 必填/默认/索引 | 【默认态】☐ 必填 / 默认值（可选）/ ☑ 建立索引优化（帮助气泡「加速该字段的排序与范围查询；每个工作空间最多 10 个」）/ 帮助说明 | 断言 | TASK-008 §3.2 |
| 编辑态差异 | 【条件态】名称/说明/选项颜色/排序/必填/默认值可改；类型与 key 置灰不可改 | 条件断言 | TASK-008 §3.2/BR-06 |

### C.54 删除字段三段式确认（归属：TASK-008 §3.3）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 影响统计 | 【默认态】⚠ 删除字段「X」？+ 服务端回传统计：「被 1,284 个任务填写」+「被 3 个视图引用（TASK-011 上线后）」 | 断言 | TASK-008 §3.3 |
| 删除后果 | 【默认态】「全部任务的该字段值将被异步清除（不可恢复）」+「引用它的视图将自动移除该条件」 | 断言 | TASK-008 §3.3 |
| 输入名激活 | 【条件态】请输入字段名确认：输入 == 字段名才激活 [确认删除] | 条件断言 | TASK-008 §3.3 |
| 202 进度 | 【条件态】删除返回 202 → 行消失 + 顶部黄条显示后台任务进度链接（INFRA-004 §13.1 系统任务） | 条件断言 | TASK-008 §3.3/§4.4.2 |

### C.55 动态表单渲染器 DynamicFieldForm（详情侧栏 + 新建弹窗共用）（归属：TASK-008 §3.4）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 渲染顺序/折叠 | 【默认态】按 sort_order 渲染；超出首屏折叠为「更多属性 ▾」 | 断言 | TASK-008 §3.4 |
| 控件映射 | 【默认态】CONTROL_REGISTRY 类型→控件唯一映射（text→Input / select→色块下拉 / member→成员选择 / checkbox→Switch / auto_increment→只读徽标「№ 128」）；零字段硬编码 | 断言 | TASK-008 §3.4/§4.4.1 |
| 必填/校验 | 【条件态】名称后 * 红；缺失提交拦截并滚动聚焦第一个错误；错误映射 details[].field=key → 控件 error slot；aria-describedby 关联 | 条件断言 | TASK-008 §3.4/§3.7 |
| 帮助说明 | 【默认态】名称旁 ? 图标 hover tooltip（description） | 断言 | TASK-008 §3.4 |
| 默认值预填 | 【默认态】新建弹窗预填（member 默认 @me 可配置）；切换 issue_type 表单区即时重渲染 | 条件断言 | TASK-008 §3.4 |
| 停用字段 | 【条件态】字段停用但值存在：详情页该字段不渲染（数据在响应中，UI 过滤） | 条件断言 | TASK-008 §3.4 |
| Schema 加载态 | 【条件态】加载 → 表单区 3 行骨架（不阻塞内置字段）；失败 → 内置字段 +「自定义字段加载失败 · 重试」条 | 条件断言 | TASK-008 §3.6 |

### C.56 动态列表列 + 列选择器（归属：TASK-008 §3.5）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 列选择器 | 【默认态】「+ 添加列」列出全部字段（内置 + 自定义无差别） | 交互断言 | TASK-008 §3.5 |
| 值渲染 | 【默认态】select=色块文本 / member=头像 / date=yyyy-MM-dd / currency=¥1,234.00 / multi=chips 前 2+N | 断言 | TASK-008 §3.5 |
| 排序/筛选 | 【默认态】列头点击 order_by=±cf_xxx（类型感知：number→numeric、currency→amount 键、select→选项配置序）；列头筛选图标按类型弹控件（?property.<id>= 参数化） | 交互断言 | TASK-008 §3.5/§4.3.5 |

### C.57 复制选项弹层 DuplicateDialog（归属：TASK-009 §3.1）
| 响应式/键盘 | 【条件态】<768 全屏底部抽屉；checkbox 键盘可达（反扫 M4 补） | 条件断言 | TASK-009 §3.4 |

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 五选项 | 【默认态】☑包含子任务（N 个，将一并复制结构）/ ☐包含执行人 / ☑包含标签 / ☑包含自定义字段值（X 项）/ ☐包含起止日期；默认 子✓执行人✗标签✓字段✓日期✗ | 断言 | TASK-009 §3.1 |
| 固定信息条 | 【默认态】ⓘ「新任务将进入「待办」状态，编号重新分配；评论、附件、依赖、工时不会被复制」 | 断言 | TASK-009 §3.1 |
| 归档子任务提示 | 【条件态】源树含已归档子任务追加「N 个已归档子任务将复制为活跃副本」 | 条件断言 | TASK-009 §3.1/BR-15 |
| 副本预览 | 【默认态】「导出功能 (副本) + 3 个子任务」随选项实时更新 | 断言 | TASK-009 §3.1 |
| 提交/loading | 【默认态】按钮 loading + >1s 进度文案「正在复制 N 个任务…」；201 → Toast「已创建 RBT-31 导出功能 (副本)」+「查看」跳转 + 滚动定位新根；失败 Toast + request_id（无半成品残留） | 交互断言 | TASK-009 §3.1/§3.3 |
| 入口 | 【默认态】详情 ⋯ 菜单 + 列表行菜单 | 断言 | TASK-009 §3.1 |

### C.58 归档确认 + 撤销 Toast（归属：TASK-009 §3.2）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 确认弹窗 | 【默认态】含子任务时提示「将同时归档 N 个子任务」；入口 = 详情 ⋯ → 归档任务 / 行菜单 | 断言 | TASK-009 §3.2 |
| 成功动线 | 【默认态】详情关闭 + 列表移除 + Toast 带「撤销」按钮（10s 内一键恢复）+ 倒计时可见文本；aria-label | 交互断言 | TASK-009 §3.2/§4.4 |
| 看板联动 | 【条件态】归档任务不占列、列计数即时扣减（默认板查询排除 archived_at IS NOT NULL） | 条件断言 | TASK-009 §3.2/BR-11 |

### C.59 归档视图（列表模式）（归属：TASK-009 §3.2）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 入口 | 【默认态】列表工具条「显示已归档」→ ?archived=true（URL 同源） | 交互断言 | TASK-009 §3.2 |
| 归档行 | 【默认态】opacity-60 + archive 图标（带文本 tooltip）+ 菜单仅「恢复/删除」 | 断言 | TASK-009 §3.2 |
| 空态 | 【空态】「没有已归档的任务」插画 | 空态断言 | TASK-009 §3.2 |

### C.60 归档详情只读横幅（归属：TASK-009 §3.2 + TASK-004 §3.4 交叉）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 只读横幅 | 【条件态】「已归档于 2026-09-01 · [恢复]」顶部，role=status；编辑控件全部禁用 | 条件断言 | TASK-009 §3.2 |
| 子任务只读 | 【条件态】子任务分区只读渲染、无「＋」/完成勾选、「查看整棵树」入口隐藏（与 C.41 归档行一致） | 条件断言 | TASK-004 §3.4 |

### C.61 任务详情抽屉·动态 Tab 时间线升级【变更 · 基线=C.25】（归属：TASK-010 §3.1；R1/R4 裁决）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| Tab 结构 | 【默认态】四 Tab「描述｜评论｜动态｜附件」沿用 C.25/ADR-0011 #1/#20【R1：T010 §3.1 mockup 三 Tab 为笔误】 | 断言 | C.25 + TASK-010 §3.1 |
| 日期分区 | 【默认态】「今天 / 昨天 / M月d日」sticky 分区头（text-xs text-neutral-400） | 断言 | TASK-010 §3.1 |
| epoch 组 | 【默认态】组头行（时间 mono + 头像 + 操作者 + 动作摘要「更新了 3 个字段」）+ 缩进字段行（`字段 旧值 → 新值`，旧值删除线、新值加粗）；每组 aria-label 摘要 | 断言 | TASK-010 §3.1 |
| 系统事件 | 【默认态】⚙系统 头像（复制/归档/自动化来源）；Sprint 3 起显示自动化规则名 | 断言 | TASK-010 §3.1 |
| 值渲染 | 【默认态】FK 值显示名+色点（ID 不展示）；M2M added/removed 合并「（原：…）」对比；新旧值用文本 → 分隔不依赖颜色 | 断言 | TASK-010 §3.1/BR-03 |
| 过滤器 | 【默认态】字段（全部▾ 多选）与操作人（⋯▾）双下拉；过滤态 URL 同源 | 交互断言 | TASK-010 §3.1 |
| 空态/加载/分页 | 【空态】「暂无动态——第一次修改将出现在这里」；【加载】8 行骨架；【默认态】按钮式「[加载更早的动态]」（审计翻阅场景，不做无限滚动）【R4】 | 空态断言 | TASK-010 §3.1 |
| 响应式 | 【条件态】≥1280 双栏（左竖线+右内容）；<768 单栏字段行换行 | 条件断言 | TASK-010 §3.6 |

### C.62 管理端死信补偿页（admin）（归属：TASK-010 §3.2；布局 O2 原型先行定义）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 死信列表 | 【默认态】数据源 Redis hash 元数据（队列 activity.dlq）：列 = 时间 / event_key / 错误摘要 / 重试次数（3/3 耗尽红显）；权限码 system.audit.read；堆积计数（>100 触发 SERVER_QUEUE_ERROR 告警） | 断言 | TASK-010 §3.2 |
| 操作 | 【默认态】单条重放（幂等：event_key 命中 dedup_skipped）/ 批量重放（≤100）/ 丢弃（二次确认 + 留痕） | 交互断言 | TASK-010 §3.2/§4.2 |
| 空态 | 【空态】无死信 → 队列健康空态 | 空态断言 | TASK-010 §3.2 |

### C.63 移动端「移动到…」弹窗（归属：TASK-004 §3.7 + §3.5/§4.4.2）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 触发条件 | 【条件态】仅 <768px（拖拽禁用的键盘/触屏替代路径）；行菜单「移动到…」 | 条件断言 | TASK-004 §3.7 |
| 父任务搜索选择器 | 【默认态】搜索候选（排除自身与后代）；选择后走与拖拽相同的 PATCH parent_id + 确认链路（C.39 确认弹层复用） | 交互断言 | TASK-004 §3.5/§4.4.2 |

---

## 附录 C（续 2）· Sprint 3 Phase 3-A 新增表面 C.64~C.76（BOARD-003 / TASK-011）

> 依据 ADR-0010 纪律 ①：Sprint-3 视图与筛选体系（BOARD-003 §3.1~§3.7 + TASK-011 §3.1~§3.4）新增/变更 UI 表面先行登记，每行标规格出处；跨文档裁决 R1~R5 与原型开放点 O1~O5 已定稿（原型 FROZEN 2026-09-06，docs/design/sprint-3-hifi-prototype.html 头部注释）。视觉/交互验收基准 = 该冻结原型。COLLAB-002/003/004 表面（C 批）与 BOARD-004 批量表面（B 批）不在本批登记。本批涉及的前端写端点：`POST/PATCH/DELETE …/views/`（BOARD-003 §4.2-1/2/4/5）、`PATCH /users/me/settings/`（§4.2 注 BR-10）、分组信封 `GET …/issues/?group_by=&view_id=&filters=`（BOARD-003 §4.2-6 + TASK-011 §4.2.2）。

### C.64 视图切换器工具条【升级 · 承载于看板/列表/表格三布局】（归属：BOARD-003 §3.1；R1 布局器最左 / R2 ?view_id=）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 布局四段器 | 【默认态】工具条最左 list/kanban/table/gantt 四段（R1 裁决：BOARD-003 画法为准）；当前布局高亮；点击 = 路由段变化 + PATCH 视图 `layout` 单字段（BR-04，筛选/分组保持）；gantt 禁用态 + tooltip「甘特视图即将上线（GANTT-001）」 | 断言+交互断言 | BOARD-003 §3.1 / §2.5 BR-04 / TASK-011 §3.2 |
| 视图 Tabs | 【默认态】「全部」固定首项（前端入口不入库，filters={} 非种子）+ 内置五视图（🔒 角标=口径锁定）+ 本人个人视图（含 icon emoji + ★ 默认角标）；当前视图高亮下划线；超出 6 个折叠进「＋ ▾」下拉；role=tablist/tab + aria-selected | 断言 | BOARD-003 §3.1/§3.6 / TASK-011 §3.2 / R4 |
| 默认星标 | 【条件态】当前视图 hover 出现空心 ★（非默认时）可点设默认；默认视图实心 ★ 常显；写入 `PATCH /users/me/settings/` 偏好键 board.default_view_id（值按项目记）；进项目无 ?view_id 时直达默认视图 | 交互断言 | BOARD-003 §3.1 / §2.5 BR-10 / §4.2 注 |
| 「＋ ▾」折叠下拉 | 【默认态】超出 6 个的视图入下拉；下拉含「新建视图」「从当前筛选另存」；「管理视图（P3 共享设置）」禁用项 | 断言 | BOARD-003 §3.1 |
| 视图右键菜单 | 【条件态】Tab 右键：重命名 / 复制视图 / 设为默认 / 删除（内置视图无删除项）；重命名走 PATCH name；复制走「另存为」弹层 | 交互断言 | BOARD-003 §3.1 / TASK-011 §3.2（Tab ⋯ 菜单行） |
| ⚙ 显示入口 | 【默认态】工具条右侧「⚙ 显示 ▾」按钮打开显示配置 Drawer（C.69） | 断言 | BOARD-003 §3.1 |
| 分组切换器 | 【条件态】仅 kanban 布局显示「分组：<维度> ▾」；候选 = 状态（置顶）/优先级/负责人/标签 + groupable 自定义 select 字段（Schema 白名单同源）；切换 = 列重建（配置生成零 DISTINCT）+ 属视图修改（黄条出现） | 交互断言 | BOARD-003 §3.2 分组切换器行 / §2.5 BR-05 |
| presence 空容器 | 【条件态】工具条右端预留 presence 位（COLLAB-004 C 批接线；本批空容器不渲染头像） | 条件断言 | 原型 O1（COLLAB-004 §3.1，B/C 批交付） |

### C.65 视图已修改黄条 + 未保存离开确认（归属：BOARD-003 §3.1 / §3.5）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 黄条出现条件 | 【条件态】筛选/分组/显示配置与视图存档不一致时出现（数据写——拖拽改卡片字段——≠ 视图修改，不触发） | 条件断言 | BOARD-003 §3.1 / §3.5 |
| 黄条动作 | 【默认态】「视图已修改」+ [保存]（就地 PATCH display_props；内置视图允许、toast 注明 filters 锁定）+ [另存为]（弹层 C.70）+ [放弃]（还原存档） | 交互断言 | BOARD-003 §3.1 / §2.5 BR-03 |
| 未保存离开确认 | 【条件态】dirty 状态下切视图/切布局 → 确认弹层（保存并离开/放弃修改/取消）；Esc 取消 | 条件断言 | BOARD-003 §3.1 |

### C.66 分组看板五维列头 + __none__ 哨兵列 + 空列（归属：BOARD-003 §3.2 / §2.3；基线=C.29）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 列头（五维配置源） | 【默认态】列头 = 维度色点/头像 + 列名 + 计数徽章；列集合由前端配置源渲染（State API / 优先级五档固定色表 / 成员表 / 标签表 / Schema options——分组响应不内嵌列元数据）；列宽 280px、scroll-snap | 断言 | BOARD-003 §3.2 / §2.5 BR-16 / §2.3 表 |
| state_id 维 | 【默认态】State UUID 裸键列；无 __none__（状态必填兜底）；组内 sort_order | 断言 | BOARD-003 §2.3 |
| priority 维 | 【默认态】urgent/high/medium/low/none 枚举键五列（none 为合法值列非哨兵）；组内按语义权重 | 断言 | BOARD-003 §2.3 |
| assignee_id 维 | 【默认态】成员 UUID 列（头像 24px 列头）+ __none__「未指派」哨兵列恒在且排最末 | 断言 | BOARD-003 §2.3 / BR-14 |
| label_id 维 | 【默认态】Label UUID 列（Label.color 色点）+ __none__「无标签」哨兵列 | 断言 | BOARD-003 §2.3 / BR-14 |
| cf_* 维 | 【默认态】groupable select 的 options 选项值列 + __none__「未填值」列 | 断言 | BOARD-003 §2.3 |
| __none__ 列样式 | 【条件态】虚线列头样式 + 中文哨兵名；assignee/label 列头 hint「可拖出 · 不可拖入」；cf_* 列头 hint「可拖入=删值」；aria-label 用中文哨兵名（不用 __none__ 字面量） | 条件断言 | BOARD-003 §3.2/§3.7 / BR-14 |
| 空列恒在 | 【条件态】0 计数列恒渲染（空提示「空分组（恒在展示 · 债务可见）」）；show_empty_groups=false 时折叠为列头胶囊 | 条件断言 | BOARD-003 §3.2 / §2.3 铁律 1/3 |
| 列头 aria | 【默认态】bcol-head aria-label 含维度名与计数（如「优先级 紧急，2 个任务」） | 断言 | BOARD-003 §3.7 |
| 视图切换重排 | 【条件态】切换视图/分组 120ms 卡片渐隐重排；数据按视图 filters 重拉（SWR key 变化） | 条件断言 | BOARD-003 §3.5 交互表 |
| 看板多选 ring | 【条件态】B/C 批（BOARD-004 §3.1）——本批卡片不含多选 ring/角标 | 条件断言 | BOARD-004 §3.1（批外登记） |

### C.67 跨维度拖拽写路径 + 多值替换确认 + 哨兵拦截（归属：BOARD-003 §2.4 / §3.5）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| state_id 拖拽 | 【交互】PATCH {state_id, sort_order}（P0 语义）；完成被前置阻塞 → 409 拦截弹回 + M-BLOCKED（复用 C.44） | 交互断言 | BOARD-003 §2.4 表 |
| priority 拖拽 | 【交互】PATCH {priority, sort_order}；组内序不变 | 交互断言 | BOARD-003 §2.4 表 / §2.3 |
| assignee_id 拖拽 | 【交互】PUT …/assignees/ 全量替换 [M]；多执行人卡片先弹替换确认（BR-15）；拖入 __none__ 被拦：落点红 outline + toast「不能拖入未指派列」+ 弹回 | 交互断言 | BOARD-003 §2.4 表 / BR-14/BR-15 |
| label_id 拖拽 | 【交互】PUT …/labels/ 替换 [L]；多标签卡片先弹确认；拖入 __none__ 拦截同上 | 交互断言 | BOARD-003 §2.4 表 / BR-14/BR-15 |
| cf_* 拖拽 | 【交互】PATCH {custom_fields:{cf_x:V}}；拖入 __none__ = 删该键传 null（正常写路径，toast「已清空」） | 交互断言 | BOARD-003 §2.4 表 / BR-14 |
| 替换确认弹层 | 【默认态】「替换{执行人|标签}确认」：该任务有多个 X：<旧值>。跨列拖拽将以 <新值> 全量替换（PUT 语义）[取消][替换] | 断言 | BOARD-003 §2.5 BR-15 |
| 失败回滚 | 【条件态】写失败 → 弹回原列（bounce-back 动画）+ toast；权限不足（VIEWER）toast「当前角色无编辑权限」 | 条件断言 | BOARD-003 §3.5 / 原型 handleDrop |

### C.68 显示配置面板 Drawer 320px（归属：BOARD-003 §3.3）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 面板形态 | 【默认态】右侧 Drawer 320px / role=dialog「显示配置」；改动即预览（乐观应用到看板） | 断言 | BOARD-003 §3.3 |
| 分组区 | 【默认态】候选 = Schema groupable 字段（「按状态」置顶）；当前值停用则红字提示 | 断言 | BOARD-003 §3.3 / BR-05 |
| 排序区 | 【默认态】拖拽顺序 / 按优先级 等下拉 + hint「仅优先级维度与拖拽序解耦（组内语义权重）」 | 断言 | BOARD-003 §3.3 / §2.3 铁律 2 |
| 卡片显示开关 | 【默认态】固定 7 项（标签/子任务/附件数/工时/优先级/计时器/截止时间）+ 自定义 cf_* 双列网格开关 | 断言 | BOARD-003 §3.3 / BR-09 |
| 空组显隐 | 【默认态】开关（默认开）+ hint「债务可见原则：空组恒在（默认开）」；联动看板空列折叠 | 断言 | BOARD-003 §3.3 |
| 列配置区 | 【条件态】仅 list/table 布局显示；列 chips 点击显隐（隐藏=删除线+降透明）；hint「拖拽排序 · 点击显隐」 | 条件断言 | BOARD-003 §3.3 |
| 底部动作 | 【默认态】[重置]（恢复默认）+ [保存到视图]（写回当前视图 display_props；内置视图允许——BR-03）；【条件态·「全部」裸态】[保存到视图] 换为可点 [另存为视图]（打开 C.69 弹层；2026-09-08 体验优化），首次改显示配置 toast「『全部』不入库：修改仅本次生效」；未保存关闭 = 仅本会话生效 + 已修改条 | 交互断言 | BOARD-003 §3.3 / BR-03 / §3.6 |

### C.69 保存/另存为弹层（归属：BOARD-003 §3.4）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 名称输入 | 【默认态】另存为默认「当前视图名 (副本)」；128 字上限；项目内重名允许但同名时警告条（不阻断创建） | 断言 | BOARD-003 §3.4 / §2.5 BR-09（TASK-011） |
| 图标 8 选 1 | 【默认态】✨ 📦 🐛 👤 📅 🧪 🔥 🚒 预设（存 display_props.icon；R3 裁决） | 断言 | BOARD-003 §3.4 / R3 |
| 布局四选 | 【默认态】当前布局预选；gantt 禁用；创建后切换选中（URL ?view_id= 分享直达） | 断言 | BOARD-003 §3.4 |
| 提示条 | 【默认态】「将保存当前筛选、分组与显示配置（P2 仅个人视图 · 共享归 P3）」 | 断言 | BOARD-003 §3.4 |
| 动作 | 【交互】[取消][创建视图]；另存 = 合并树（视图 filters AND 临时树，TASK-011 §4.4.1 mergeTrees）；创建成功新 Tab 出现并选中 + toast | 交互断言 | BOARD-003 §3.4 / TASK-011 §4.4.1 |

### C.70 空状态/首次引导/删除回退/停用降级（归属：BOARD-003 §3.6 / §2.6）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 首次进入引导 | 【条件态】项目无自定义视图时 Tabs 引导气泡「保存你的第一块看板：筛选 + 分组 + 显示列可存为个人视图」+ [知道了]（localStorage 记忆不再弹） | 条件断言 | BOARD-003 §3.6 |
| 视图结果为空 | 【条件态】分组结构保留 + 中央空态（C.29 同款「无匹配卡片 + 清空筛选」） | 空态断言 | BOARD-003 §3.6 |
| 视图列表加载 | 【加载态】Tabs 骨架条 | 断言 | BOARD-003 §3.6 |
| 视图已删除（旧 URL） | 【条件态】黄条「视图已删除，已切换默认视图」+ 自动跳默认 | 条件断言 | BOARD-003 §3.6 / §2.5 BR-12 |
| 停用字段降级 | 【条件态】黄条「分组字段『X』已停用，已回退状态分组」（meta.degraded.group_by）；数据仍按视图 filters 过滤 | 条件断言 | BOARD-003 §3.6 / §2.6 / BR-05 |
| 他人视图 URL | 【条件态】?view_id= 他人个人视图 → 列表端点 404 → 前端回退默认视图 + toast（存在性隐藏） | 条件断言 | BOARD-003 §2.6 / §2.5 BR-11/12 |

### C.71 筛选面板·布尔树编辑（640px Drawer）（归属：TASK-011 §3.1）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 面板形态 | 【默认态】右侧 640px 抽屉；头部「筛选」+ [最近 ▾] [清空全部] [保存视图] | 断言 | TASK-011 §3.1 / §3.4（≥1280 640px） |
| 组节点 | 【默认态】圆角虚线框；头部「满足 全部（AND）/任一（OR）」切换下拉 + [+ 条件] [+ 组] + 组删除 ⨯（根组不可删）；嵌套 ≤3 层、条件 ≤20；role=group + aria-label「条件组：满足任一，共 N 条」 | 断言 | TASK-011 §3.1 / §2.5 BR-01 |
| 配额 | 【默认态】「条件 n/20 · 层级 d/3」；达限禁用 [+条件]/[+组] + 红色配额提示 + toast「层级已达 3 层上限」 | 断言 | TASK-011 §3.1 / §2.5/§2.6 |
| 条件行·字段选择器 | 【默认态】字段下拉（内置+自定义混排、类型色点、自定义标注）；新行默认「状态组 in [待办]」 | 断言 | TASK-011 §3.1 / §2.3 |
| 条件行·操作符下拉 | 【默认态】按字段类型出 §2.3 操作符集（priority 仅 in/not_in 等）；切换清空值 | 断言 | TASK-011 §3.1 / §2.3 |
| 条件行·值控件 | 【默认态】类型匹配：select/multi/member=多选 chips+[+值▾]；date=日期输入（between 双值）；checkbox=是/否（序列化 true/false）；text=文本框；number=数字；is_empty 族=「（无需值）」 | 断言 | TASK-011 §3.1/§2.3 |
| 值占位符回显 | 【默认态】chips 显示「活」解析（@me→@姓名、today→今天、this_week→本周、__requirement__→需求） | 断言 | TASK-011 §2.5 BR-05 / 原型 resolvedChip |
| 条件行删除 | 【默认态】行尾 ⨯（aria-label「删除条件」） | 断言 | TASK-011 §3.1 |

### C.72 筛选面板·快捷 chips / 最近使用 / 底部状态栏（归属：TASK-011 §3.1/§3.3）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 快捷 chips | 【默认态】@我 / 未指派 / 今天到期 / 已逾期 / 本周 一键成条件（超 20 上限 toast 拒绝） | 交互断言 | TASK-011 §3.1 |
| 实时命中数 | 【条件态】任意条件变更 500ms 防抖预估：GET issues/?view_id&filters&per_page=1 读 meta.total_count；aria-live=polite；失败显示 — | 条件断言 | TASK-011 §3.1/§3.3 / §4.4.1 |
| 应用/取消 | 【交互】[应用] → 列表刷新 + URL 同步 ?filters=（替换不入栈）；[取消] 还原打开前状态 | 交互断言 | TASK-011 §3.1/§3.3 / §4.4.2 |
| 最近使用 | 【默认态】[最近 ▾] 本地 localStorage recent-filters:{projectId} 最近 5 条 DSL 摘要；点击载入为临时 | 断言 | TASK-011 §3.3 / §2.5 BR-16 |
| 保存视图 | 【交互】底部 [保存视图] 打开 C.69 弹层（含当前树） | 交互断言 | TASK-011 §3.1/§3.2 |

### C.73 筛选面板·视图段（只读 chips + 叠加区 + 另存/脱离）（归属：TASK-011 §3.1）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 视图段 | 【条件态】选中视图时显示：「视图：<icon> <名>（内置 · 只读 / 个人）」+ 只读条件 chips；内置视图 filters 锁定仅可临时叠加 | 条件断言 | TASK-011 §3.1 / §2.5 BR-11 |
| 叠加语义 | 【默认态】新增条件进③临时层（view_id 与 filters 恒 AND，BR-12）；面板「视图条件 + 叠加条件」双段 | 断言 | TASK-011 §3.1 / §2.5 BR-12 |
| [另存] | 【交互】把「视图 filters AND 临时树」合并树存为新视图（弹 C.69 命名） | 交互断言 | TASK-011 §3.1 / §4.4.1 |
| [脱离] | 【交互】切回「全部」裸态，视图条件转为纯临时层 | 交互断言 | TASK-011 §3.1 |

### C.74 高级筛选入口 + 视图条件 chips 行【升级 · 承载 C.28/C.29 filterbar】（归属：TASK-011 §3.1 + 原型 O2）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| ⊞ 筛选按钮 | 【默认态】filterbar「⊞ 筛选 ▾」按钮打开 C.71 面板；有临时条件时高亮（brand 边框）+ 条件数徽章 n | 断言 | TASK-011 §3.1 / 原型 O2 |
| 视图条件 chips | 【条件态】选中视图（非「全部」）时 chiprow 显示视图条件只读 chips（title「视图条件（内置·只读/个人视图）」） | 条件断言 | TASK-011 §3.1 / 原型 filterbar |
| 叠加 chip | 【条件态】临时树存在时「⊞ 叠加 N 条临时条件」chip + ✕ 清空（tree-clear） | 条件断言 | TASK-011 §3.1 / 原型 chiprow |
| 命中计数 | 【默认态】filterbar 右侧「N 个任务 · 视图 <名>」总计数 | 断言 | 原型 updateCounts / TASK-011 §3.1 |

### C.75 表格布局（紧凑斑马纹 + 列配置全生效）（归属：BOARD-003 §3.3 列配置行 + 原型 O3）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 表格形态 | 【默认态】紧凑行高 + 斑马纹（偶数行 #fbfbfb）；与列表布局同构（同一数据源） | 断言 | 原型 O3 / BOARD-003 §1.2 |
| 列配置生效 | 【默认态】列集合 = display_props.columns（编号/标题/状态/负责人/截止/优先级/标签；隐藏列删除线在 C.68 配置）；筛选与分组条件保持（四布局共用一份视图配置） | 断言 | 原型 O3 / BOARD-003 §1.2/§3.3 |
| 行点击 | 【交互】行点击打开任务详情抽屉（C.24 承载） | 交互断言 | BOARD-003 §1.2（四布局共用） |

### C.76 URL ?view_id / ?filters 同步与还原（归属：BOARD-003 §3.5 R2 裁决 / TASK-011 §3.3/§4.4.2）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| view_id 双向绑定 | 【默认态】切视图 → URL ?view_id= 更新（R2：与 API 参数同名）；初始化从 query 反序列化；刷新/分享还原 | 断言 | BOARD-003 §3.5（R2 裁决）/ TASK-011 §4.4.2 |
| filters 同步 | 【默认态】应用临时筛选 → URL ?filters=<urlencode JSON>；初始化解析；损坏 JSON → 静默回无条件 + Toast | 断言 | TASK-011 §3.3 / §2.5 |
| 跨布局保留 | 【条件态】list/kanban/table 三布局路由段切换时 ?view_id&?filters 保留（布局切换仅 PATCH layout） | 条件断言 | BOARD-003 §1.2/§4.4 / TASK-011 §2.5 BR-13 |

---

## 附录 C（续 3）· Sprint 3 Phase 3-B 新增表面 C.77~C.88（BOARD-004 批量操作 / COLLAB-002 评论协作升级）

> 依据 ADR-0010 纪律 ①：本批登记 BOARD-004（§3.1~§3.7 批量工具条/多选/浮层/确认/失败定位）与 COLLAB-002（§3.1~§3.5 楼中楼/表情/图片评论）全部 UI 表面，每行标规格出处；视觉/交互基准 = 冻结原型（FROZEN 2026-09-06）。本批前端写端点：`PATCH/DELETE …/issues/bulk/`、`POST …/issues/bulk/archive/`、`POST …/issues/bulk/preview/`（BOARD-004 §4.2）、`POST …/comments/`（含 parent_id，COLLAB-002 §4.2-1）、`POST/DELETE …/comments/{id}/reactions/`（§4.2-3/4）、`GET …/comments/?expand=reactions`（§4.2-5）、presign `entity_type=comment_image` + `?variant=thumb`（§4.2 注 / asset.py 收紧口径）。

### C.77 看板卡片多选态【升级 · 承载 C.66 分组看板】（归属：BOARD-004 §3.1）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 选中视觉 | 【条件态】选中卡片 `ring-2 ring-brand` 双环高亮 + border-brand；`aria-selected="true"`（容器 role=listbox / 卡片 role=option） | 条件断言 | BOARD-004 §3.1 选中视觉行 / §3.7 |
| 左上角标复选框 | 【条件态】18px 复选角标 absolute 左上；hover 或已选或已有选中（bulk-on）时显示（opacity 过渡）；点击 = toggle 不打开详情 | 交互断言 | BOARD-004 §3.1 / 原型 .cardcb |
| ⌘ 点选 | 【交互】⌘/Ctrl + 点击卡片 = 追加/移除（不打开详情）；role=option + aria-selected 翻转 | 交互断言 | BOARD-004 §3.1 / §3.5 交互表 |
| Shift 区间 | 【交互】Shift + 点击 = 当前视图可见顺序区间全选（受上限 100 截断） | 交互断言 | BOARD-004 §3.5 交互表 / §2.3 状态机 |
| 空白处框选 | 【交互】看板空白处 pointerdown 拖出 1.5px 虚线矩形（brand 色 8% 填充），触及卡片即实时高亮入选；起点在卡片/按钮/输入框上不触发（与 HTML5 拖拽互斥，R5） | 交互断言 | BOARD-004 §3.1/§4.4 / 原型 bindMarquee |
| 拖拽互斥 | 【条件态】卡片拖拽进行中框选禁用（多选拖拽期间不触发框选；批量动作仅工具条入口无批量拖拽） | 条件断言 | BOARD-004 §2.3 / R5 裁决 |

### C.78 列表/表格行多选态【升级 · 承载 C.27 列表 / C.75 表格】（归属：BOARD-004 §3.1）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 行首复选框列 | 【默认态】34px 复选列（列表含列头全选框；表格行首 hover 浮现 ⬜）；点击 toggle 不打开详情 | 断言+交互断言 | BOARD-004 §3.1 列表/表格布局图 |
| 选中行高亮 | 【条件态】已选行 `bg-brand-50`（hover 加深）；aria-selected | 条件断言 | BOARD-004 §3.1 选中视觉行 |
| ⌘/Shift 点选 | 【交互】行上 ⌘ 点选追加/移除；Shift 点「A→B」= 可见顺序区间全选；普通点击仍打开详情 | 交互断言 | BOARD-004 §3.1 / §3.5 |
| 空白处框选 | 【交互】表格容器空白处拖出虚线矩形触及行即选（Shift 按住 = 在既有选中上追加） | 交互断言 | BOARD-004 §3.5 交互表 / 原型 bindMarquee |
| 列头全选 | 【交互】列表列头全选框 = 全选当前视图结果集（同 ⌘A，截断 100） | 交互断言 | BOARD-004 §3.1 表格布局图「⬚ 列头全选」 |

### C.79 全选视图结果集 + ⌘A 截断黄条（归属：BOARD-004 §3.1 / §3.5 / BR-08）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| ⌘A 全选 | 【交互】列表/表格/看板路由下 ⌘A（输入框聚焦时不拦截）= 当前视图结果集全部入选；前端显式展开 id 列表（服务端不隐式展开视图，BR-08）；屏幕阅读器播报 | 交互断言 | BOARD-004 §3.5 / §2.4 BR-08 / §3.7 |
| 截断黄条 | 【条件态】结果集 >100 时选中前 100 + 黄条「已选前 100 / 共 N —— 批量上限 100（BR-01）」+ [知道了]；计数 tabular-nums | 条件断言 | BOARD-004 §2.5 视图全选超 100 行 / §2.4 BR-01 / 原型 .trunc-strip |
| 视图为空全选 | 【条件态】当前视图无任务 → toast「当前视图无任务」（无黄条） | 条件断言 | BOARD-004 §3.6 空态表 |
| 上限阻止追加 | 【条件态】选中池达 100 后再点选/框选/区间不追加（toggle 已选项仍可移除） | 条件断言 | BOARD-004 §2.6 边界表 / §4.4 SelectionStore |
| Esc/✕ 清空 | 【交互】Esc 或工具条 ✕ = 清空选中 + 关闭浮层；Esc 优先级：灯箱 > 弹窗 > 浮层 > 选中 | 交互断言 | BOARD-004 §3.5 交互表 |
| 跨页/切视图保留 | 【条件态】翻页/切视图选中池保留（工具条计数持续）；切项目清空 | 条件断言 | BOARD-004 §2.3 状态机注 / §3.5 翻页保选 |

### C.80 批量工具条（底部居中浮动）（归属：BOARD-004 §3.1）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 形态与浮现 | 【条件态】选中 ≥1 浮现：fixed bottom-4 居中、白底 border rounded-[14px] shadow-lg h-[52px]，160ms 滑入动画；`role="toolbar"` + `aria-label="批量操作，已选 N 项"`；选中 0 隐藏（无空态） | 条件断言 | BOARD-004 §3.1 工具条行 / §3.6 / §3.7 |
| 计数 | 【默认态】「已选 N 项」tabular-nums + 勾选 icon；选中态变化即时更新 | 断言 | BOARD-004 §3.1 计数行 |
| 状态/优先级下拉 | 【交互】[状态 ▾]（项目状态表）/[优先级 ▾]（五档）即选即发：PATCH …/issues/bulk/ `{issue_ids, patch:{state_id|priority}}`；成功 Toast「已更新 N 项」+ 选中清空 + 列表 revalidate | 交互断言 | BOARD-004 §3.1 动作组行 / §4.2.1 / §3.5 |
| 指派…/标签… | 【交互】打开 C.81 浮层（模式三选 + 多选 + 备注） | 交互断言 | BOARD-004 §3.1 动作组行 / §3.2 |
| 归档 | 【交互】打开 C.83 归档确认（可逆无需输入） | 交互断言 | BOARD-004 §3.1 / §3.3 |
| 删除 | 【交互】红色文字；先 POST bulk/preview/ 级联统计再弹 C.83 删除确认（数量输入激活） | 交互断言 | BOARD-004 §3.1 动作组行 / §3.3 / §4.2.4 |
| ✕ 清空 | 【交互】尾部 ✕ 清空选中（aria-label「清空选中（Esc）」） | 交互断言 | BOARD-004 §3.1 |
| 请求中锁定 | 【条件态】请求未返回：整条 opacity-75 + 禁点 + 动作区 animate-pulse；选中冻结（BR-13：期间点选/区间无效）；完成（成功/失败）后解锁 | 条件断言 | BOARD-004 §3.1 请求中行 / §2.4 BR-13 |
| 成功反馈 | 【交互】成功 Toast「已更新 N 项」（归档/删除另有口径）+ 选中清空 + 数据 revalidate（切视图/翻页均生效） | 交互断言 | BOARD-004 §3.5 动作即发行 |
| 权限门槛 | 【条件态】无写权限角色（VIEWER/COMMENTER）不浮现工具条（点选入口 toast「当前角色无多选权限」） | 条件断言 | BOARD-004 §2.4 BR 批级门槛 / 原型 canEdit |

### C.81 批量指派/标签浮层（模式三选）（归属：BOARD-004 §3.2）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 浮层形态 | 【默认态】工具条上方 400px 浮层（fixed bottom-78px 居中）；标题「批量指派/标签 · 将应用到 N 个任务」+ ✕ | 断言 | BOARD-004 §3.2 浮层图 / 原型 .bulk-pop |
| 模式三选 | 【默认态】radiogroup：替换为（PUT 语义·全量替换）/ 添加到（并集）/ 从中移除（差集）；默认替换；选中 brand 边框+浅底 | 断言+交互断言 | BOARD-004 §3.2 模式三选行 |
| 成员/标签多选 | 【默认态】🔍 搜索 + 滚动列表（成员：头像+姓名+在线态，CONTRIBUTOR+ 可选；标签：色点+名）；已选 chips 回显区（✕ 逐个移除） | 断言 | BOARD-004 §3.2 浮层图 |
| 备注 | 【默认态】「备注（可选，将随动态记录 · ≤500 字）」textarea；超限提交时 400 TOO_LONG 由后端拦截 | 断言 | BOARD-004 §3.2 / §2.4 BR-12 |
| 应用按钮 | 【交互】[取消] / [应用到 N 项]（恒显数量防误配范围）；未选至少 1 个 → toast「请先选择至少 1 个成员/标签」；应用 → PATCH …/issues/bulk/ `{issue_ids, assignees|labels:{mode, *_ids}, comment}` → 2xx Toast + 选中清空 | 交互断言 | BOARD-004 §3.2 应用数恒显行 / §4.2.1 |

### C.82 失败项定位弹层（role=alertdialog）（归属：BOARD-004 §3.4）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 触发与形态 | 【条件态】批量写返回 400（项级失败）→ 弹层 `role="alertdialog"`；标题「N 项中 M 项未通过校验，本次未执行任何修改」；副文「单事务全成败：任一条目失败整批 ROLLBACK」 | 条件断言 | BOARD-004 §3.4 触发行 / §2.5 |
| 失败项清单 | 【默认态】列出全部失败项：`#<索引>` + issue_key + 标题 + 原因行（红字）；解析 details[].field `issue_ids[i]` 0 基索引映射回选中列表 | 断言 | BOARD-004 §3.4 定位行 / §2.4 BR-02 |
| 点击跳详情 | 【交互】点击失败项关闭弹层并打开该任务详情（选中保留） | 交互断言 | BOARD-004 §3.4 定位行 |
| 移除该项并重试 | 【交互】[移除该项并重试（N-M 项）] 剔除全部失败 id 重发同动作（仍是显式列表，BR-08）；成功后 Toast + 清空 | 交互断言 | BOARD-004 §3.4 一键重试行 |
| 保留选中返回 | 【交互】[保留选中返回] 仅关弹层（选中与失败高亮保留） | 交互断言 | BOARD-004 §3.4 |
| 非项级错误 | 【条件态】载荷级错误（枚举/超限）→ 普通 Toast，不弹失败定位层 | 条件断言 | BOARD-004 §2.5 异常表 |

### C.83 批量归档确认 + 批量删除确认（级联统计 + 数量输入）（归属：BOARD-004 §3.3）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 归档确认形态 | 【条件态】「批量归档 N 个任务？」+「归档可逆（任务列表『已归档』可恢复）」+ 含子树数（preview/ 统计：with_subtree/cascade）+ [归档 N 项] | 条件断言 | BOARD-004 §3.3 归档确认行 |
| 归档提交 | 【交互】POST …/issues/bulk/archive/ `{issue_ids}` → 200 `{archived_count, affected_total}`；Toast「已归档 N 项」；选中清空 + 列表 revalidate | 交互断言 | BOARD-004 §4.2.2 / §3.3 |
| 删除确认·级联统计 | 【条件态】红色标题「批量删除 N 个任务？」+ 红底级联统计块（POST …/issues/bulk/preview/ `{issue_ids, action:"delete"}`：selected/with_subtree/cascade_total/affected_total + links/worklogs/comments + denied 预检失败项）；`aria-describedby` 指向统计块 | 条件断言 | BOARD-004 §3.3 级联统计行 / §4.2.4 / §3.7 |
| 删除确认·数量输入 | 【交互】数字输入框（90px 居中）：值 == 选中数才激活 [确认删除 N 项]（BR-10 confirm_count）；激活时绿边框 | 交互断言 | BOARD-004 §3.3 输入确认行 / §4.2.3 |
| 删除提交 | 【交互】DELETE …/issues/bulk/ `{issue_ids, confirm_count:N}`（默认带 Idempotency-Key，BR-14）→ 200 `{deleted, affected_total}`；Toast；选中清空 + revalidate | 交互断言 | BOARD-004 §4.2.3 / §2.4 BR-14 |
| 回收站说明 | 【默认态】「删除后任务进入回收站（管理端可恢复），关联的评论、工时、依赖将被保留但随任务隐藏」文案 | 断言 | BOARD-004 §3.3 删除弹层图 |
| preview denied 前移 | 【条件态】预检 denied[] 非空时确认层列出（如「RBT-x 仅创建者可删除」），用户可先剔除 | 条件断言 | BOARD-004 §4.2.4 注（失败发现前移） |

### C.84 评论 Tab 线程化·两层结构与回复行【升级 · 基线=C.32】（归属：COLLAB-002 §3.1）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 评论计数头 | 【默认态】「💬 评论 N · 回复 M」双计数（顶层 + 全部回复） | 断言 | COLLAB-002 §3.1 mockup 头行 |
| 线程容器 | 【默认态】顶层评论全宽 32px 头像；replies 区左缩进 32px + 左 2px neutral-200 引导线 + 圆角浅底 bg-neutral-50 行卡 | 断言 | COLLAB-002 §3.1 线程容器行 |
| 回复行 | 【默认态】24px 小头像 +「回复」徽标（brand 浅底 10.5px）+ 正文 text-sm；被归并回复显示「回复 @<被回复人>」徽标 + sr-only 归并提示「已归并至本线程」 | 断言 | COLLAB-002 §3.1 回复行行 / §1.3 / §3.5 |
| 线程加载骨架 | 【条件态】顶层骨架行 + replies 区合并骨架块 | 条件断言 | COLLAB-002 §3.4 线程加载行 |
| 折叠条 | 【条件态】线程 >3 条回复默认显示前 2 条 + 「⊕ 查看另外 N 条回复…」（阈值 3）；展开后「⊖ 收起」；会话内记忆展开/折叠态；aria-expanded | 条件断言+交互断言 | COLLAB-002 §3.1 折叠条行 / §2.6 折叠阈值行 |
| 父删子留占位 | 【条件态】is_deleted 父行 = 灰底占位「该评论已删除（回复 N 条保留 ↓）」；replies 区保留渲染（引导线延续） | 条件断言 | COLLAB-002 §3.1 父删子留行 / §2.4 BR-06 |
| 行尾 ↩ 回复按钮 | 【交互】顶层/回复行尾「↩ 回复」进入回复态 Composer + 焦点；删除父占位行无回复按钮 | 交互断言 | COLLAB-002 §3.3 交互表 / §2.5 已删父行 |

### C.85 反应栏 + 表情选择器 + 名单浮层（归属：COLLAB-002 §3.1 / §3.2）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 反应 chips | 【默认态】emoji + 计数 chips（reacted_by_me=true 蓝底高亮 + aria-pressed）；`aria-label="emoji，N 人，含你"`；无聚合数据时隐藏反应栏 | 断言 | COLLAB-002 §3.1 反应栏行 / §3.5 |
| chip toggle | 【交互】点 chip = POST/DELETE …/reactions/（body 带 emoji）：乐观 count ±1 + 高亮切换，失败静默回滚重试一次；换 emoji = 前端串联 DELETE 旧 + POST 新 | 交互断言 | COLLAB-002 §3.2 toggle 反馈行 / §2.2 / §4.4 |
| ➕ 选择器 | 【交互】虚线圆角 ➕ 展开 Popover：24 枚白名单 emoji 网格 8 列（常驻 8 + 展开全量展示）、role=menu、键盘方向键 + Enter；已点过的 emoji 灰显禁点 | 交互断言 | COLLAB-002 §3.2 选择器行 / §2.6 白名单行 / §4.3.1 EMOJI_WHITELIST |
| 名单浮层 | 【交互】悬浮 chip 弹点名人列表：`?expand=reactions` 拉 user_ids → 前 5 人 + 「等 N 人」+ 自己标「（你）」 | 交互断言 | COLLAB-002 §3.2 名单浮层行 / §4.2-5 |
| 权限 | 【条件态】无 comment.create 权限角色不渲染 ➕ 与 ↩（只读）；归档项目 403 只读 | 条件断言 | COLLAB-002 §2.4 BR-01 / §2.5 归档项目行 |

### C.86 图片评论·缩略网格 + GIF 角标（归属：COLLAB-002 §3.1）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 缩略网格 | 【默认态】96px 方形 object-cover；2 列（≤2 张）/ 3 列（≥3 张）；hover 半透明遮罩 + ⤢ 放大提示；img src = …/attachments/{asset_id}/download/?variant=thumb；alt = 文件名 | 断言 | COLLAB-002 §3.1 图片缩略图行 / §4.2.1 图片评论请求注 |
| GIF 角标 | 【条件态】GIF 图右下黑底白字「▶ GIF」角标 | 条件断言 | COLLAB-002 §3.1 / 原型 .gif-badge |
| 点击灯箱 | 【交互】点缩略图打开 C.87 灯箱 | 交互断言 | COLLAB-002 §3.1 灯箱行 |
| 失效占位 | 【条件态】asset 失效格「图片不可用」灰底占位（cursor-not-allowed） | 条件断言 | COLLAB-002 §2.5 asset 盗链行 / §3.4 |

### C.87 图片灯箱（归属：COLLAB-002 §3.1）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 全屏形态 | 【条件态】全屏 88% 黑遮罩 + 居中舞台（原图无参 download/ 直取，懒加载）；`role="dialog"` + 关闭按钮（Esc） | 条件断言 | COLLAB-002 §3.1 灯箱行 / §3.5 |
| ←→ 翻页 | 【交互】左右箭头按钮 + 键盘 ←→ 在同评论图片间循环切换 | 交互断言 | COLLAB-002 §3.1 灯箱行 |
| Esc 关闭 | 【交互】Esc / 点击遮罩 / ✕ 关闭（优先级最高） | 交互断言 | COLLAB-002 §3.3 交互表 |
| 底部信息 | 【默认态】「i / N · 文件名」元信息胶囊（底部居中，半透明黑底） | 断言 | COLLAB-002 §3.1 灯箱行 / 原型 .lightbox-meta |

### C.88 回复态 Composer + 图片上传【升级 · 基线=C.32 输入区】（归属：COLLAB-002 §3.1 / §2.3）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 回复态顶部条 | 【条件态】brand 浅底「↩ 回复 @<被回复人> ▾（切换目标/清除）」+ sr-only「将回复到该线程（归并对用户透明）」；▾ 菜单：线程内目标切换 + 「清除 · 转为顶层评论」 | 条件断言 | COLLAB-002 §3.1「回复 @xx ▾」行 / §4.4 ReplyContext |
| @ 预填可删 | 【交互】进入回复态自动预填 `@被回复人 ` 锚点（可手动删除；删除则不触发 mentioned，BR-04）；光标置于末尾 | 交互断言 | COLLAB-002 §2.1 时序 / §2.4 BR-04 |
| ⌘Enter 乐观插入 | 【交互】回复：POST comments/ `{parent_id, comment_html, comment_json}` → 201 乐观划入线程底部 + 计数 +1 + Composer 复位；失败回滚 + 草稿保留 + Toast | 交互断言 | COLLAB-002 §3.3 发表回复行 / §4.2.1 |
| 回复上限 | 【条件态】目标线程已有 100 条回复 → toast「该评论回复已达上限，请直接发表新评论」（409 前端预判） | 条件断言 | COLLAB-002 §2.6 单评论回复数行 / §2.5 |
| 字数计数 | 【默认态】0/5000 计数（textarea 右下）；maxlength 5000 | 断言 | COLLAB-002 §3.1 Composer 图 |
| 图片上传入口 | 【交互】🖼 工具按钮选文件 / 粘贴 / 拖入（≤5MB png/jpg/jpeg/gif/webp，超限行内提示不入队）；presign `entity_type=comment_image` → 直传 → complete | 交互断言 | COLLAB-002 §2.3 时序图 / §2.4 BR-08 |
| 上传进度节点 | 【条件态】Composer 内嵌进度条（文件名 + 百分比 + brand 填充条）；完成变缩略图；失败「重试」按钮 + 节点移除 | 条件断言 | COLLAB-002 §3.3 贴图行 / §2.3 失败分支 |
| 发表含图评论 | 【交互】发表时正文含 image 节点（comment_html 内 `<img src=…download/?variant=thumb>`；comment_json image 节点 attrs.asset_id）；单条 ≤9 张（第 10 张拒绝入队） | 交互断言 | COLLAB-002 §4.2.1 图片评论请求 / §2.4 BR-08 |

## 附录 C（续 4）· Sprint 3 Phase 3-C 新增表面 C.89~C.97（COLLAB-003 动态流 / COLLAB-004 实时协作）

> 归属与冻结基准：COLLAB-003 §3.1~§3.4（动态流页/批量抽屉/交互/响应式）+ COLLAB-004 §3.1~§3.5
> （presence/实时看板/通知与动态流实时化/交互细节/无障碍）；视觉基准 docs/design/sprint-3-hifi-prototype.html
> （FROZEN，V-ACT 表面 21/22/23/26 + O1/O4/O5 裁决）。

### C.89 动态流页·视图条 + 侧栏入口（归属：COLLAB-003 §3.1；原型 O5 pulse 图标）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 侧栏「动态」入口 | 【默认态】项目侧栏视图组内固定入口（pulse 图标 O5；活跃态高亮） | 断言 | COLLAB-003 §3.1 路由行 / 原型 O5 |
| 视图条标题 | 【默认态】「动态」+ 项目 identifier；右侧 presence 头像列 + 连接指示（O1 消费位） | 断言 | 原型 renderViewBar('动态 · RBT 标准版') |
| 过滤条·所有人 | 【交互】成员下拉（含头像名）；选中后按钮 brand 高亮；URL ?actor= 同源还原 | 交互断言 | COLLAB-003 §3.1 过滤条行 / §3.3 过滤行 |
| 过滤条·全部类型 | 【交互】§2.3 语义组下拉（13 组：创建/状态/指派/优先级/日期/工时/字段/关联/父子/工时/归档/删除/评论）；URL ?event= | 交互断言 | COLLAB-003 §2.3 表 / §3.1 |
| 过滤组合 AND | 【交互】actor × event 组合恒 AND；无结果「该过滤条件下暂无动态」 | 交互断言 | COLLAB-003 §2.6 组合行 / §3.3 |
| 自动刷新提示 | 【默认态】过滤条右端 hint「60s 自动刷新 · 页面可见时」 | 断言 | COLLAB-003 §3.1 自动刷新行 / 原型 hint |

### C.90 动态流页·三态行 + 日期分区（归属：COLLAB-003 §3.1 / §3.4）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 日期分区头 | 【默认态】今天/昨天/M月d日 sticky（role=heading aria-level=3 + 日期副标 MM-DD）；与任务时间线 C.61 同款口径（独立组件复用登记：StreamList 新文件，未改 Sprint-2 冻结表面） | 断言 | COLLAB-003 §3.1 日期分区行 / §3.4 |
| activity 行 | 【默认态】时间列 + 头像 + 「操作者 + 动作文案（activity comment 摘要，缺失按 verb/field 兜底组句）」+ 任务 chip | 断言 | COLLAB-003 §3.1 行结构 / §4.2.1 kind=activity |
| comment 合流行 | 【默认态】💬 图标 + 「评论了/回复了 RBT-xxx：「摘要」」+ 回复 @某人 徽标（root_id/reply_to_actor） | 断言 | COLLAB-003 §1.2 #3 / §4.2.1 要点 1 |
| batch 汇总行 | 【默认态】bg-brand-50/40 圆角卡；batch_count 加粗 tabular-nums；change_brief 后缀「· 状态 待办 → 已完成」 | 断言 | COLLAB-003 §3.1 批量汇总行行 |
| 系统行兜底 | 【条件态】actor 为空 → ⚙ 灰头像 + 「系统」（复制/归档等用户动作以操作者头像展示，BR-13） | 条件断言 | COLLAB-003 §3.1 系统行行 / BR-13 |
| 任务 chip | 【交互】issue_key ↗ 点击打开任务详情 Drawer（不离开流页）；软删 chip 置灰 line-through + ✕ disabled；归档可跳转只读态 | 交互断言 | COLLAB-003 §3.1 行结构 / BR-06/07 |
| 软删点击 Toast | 【交互】软删行/chip 点击 Toast「该任务已删除」，无路由跳转 | 交互断言 | COLLAB-003 §2.5 软删任务链接行 / IT-07 |
| 相对时间 | 【默认态】刚刚/N 分钟前/今天 HH:MM/昨天 HH:MM/M月d日；hover title 绝对时间；每分钟文案刷新（无请求） | 断言 | COLLAB-003 §3.1 时间显示行 / §3.3 相对时间行 |
| 加载更早 | 【交互】[加载更早的动态] 按钮 → GET ?cursor=（组感知游标）追加；到底替换为「没有更早了」 | 交互断言 | COLLAB-003 §2.6 历史深度行 / §3.3 |
| 空态 | 【条件态】新项目「项目还没有动静」；过滤无结果「该过滤条件下暂无动态」（pulse 插画） | 条件断言 | COLLAB-003 §2.5 流暂时为空行 / §3.3 |
| 加载骨架 | 【条件态】首屏 6 行骨架（animate-pulse） | 条件断言 | COLLAB-003 §3.3 打开页面行 |
| 语义列表 | 【默认态】`<ol aria-label="项目动态流">`；每行 aria-label 完整朗读（人，时间，动作，任务） | 断言 | COLLAB-003 §3.4 无障碍行 |

### C.91 批量明细抽屉（归属：COLLAB-003 §3.2）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 头部 | 【默认态】「操作者 · 汇总文案 · 绝对时间」+ epoch 副标 + ✕ 关闭（Esc 可关） | 断言 | COLLAB-003 §3.2 头部行 |
| 变更摘要 | 【默认态】「变更摘要：<全同直出 brief / 不同→多种变更>」灰底条 | 断言 | COLLAB-003 §3.2 变更摘要行 / UT-05 |
| 明细行 | 【交互】issue_key（mono）+ 标题 + ↗ → 打开任务详情 Drawer；?epoch= 轻量拉取（fields=id,issue_id,field,old,new 语义） | 交互断言 | COLLAB-003 §3.2 明细行行 / BR-05 |
| 100 截断提示 | 【条件态】meta.truncated → 「100 条上限，剩余 N 条已截断（BR-11 truncated）」黄条 | 条件断言 | COLLAB-003 §2.6 明细抽屉行 / §4.2.2 meta 豁免 |
| 失败/空态 | 【条件态】明细失败抽屉内重试；epoch 无匹配「明细已不可用」 | 条件断言 | COLLAB-003 §2.5 明细抽屉空行 / §3.3 |

### C.92 动态流实时化 + 新动态浮条（归属：COLLAB-003 §3.3 / COLLAB-004 §3.3）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 60s revalidate | 【交互】页面可见时每 60s 增量拉取（visibility hidden 暂停） | 交互断言 | COLLAB-003 §3.1 自动刷新行 / §3.3 |
| activity.created 增量 | 【交互】推送到达 → 按 stream_cursor 水位增量拉取（幂等；水位仅首页响应携带 BR-12） | 交互断言 | COLLAB-004 §3.3 动态流行 / §4.4.2 |
| ≤5 条顶部划入 | 【条件态】页面在顶且可见、新条 ≤5 → 顶部划入动画（slide-in） | 条件断言 | COLLAB-003 §3.3 新动态到达行 |
| 「N 条新动态 ↑」浮条 | 【条件态】>5 条或不在顶 → 顶部居中 brand 胶囊浮条（aria-live=polite）；点击才刷新 + 回顶 | 条件断言 | COLLAB-003 §3.3 / §4.4（避免阅读位置跳动） |
| 重连补偿 | 【交互】断线重连成功 → 增量拉取一次（拉取负责最终一致） | 交互断言 | COLLAB-004 §2.2 补偿语义 / §4.4.1 |

### C.93 presence 头像列（归属：COLLAB-004 §3.1 / BR-11；点亮 C.64 预留容器）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 头像列 | 【默认态】项目房间 presence 集 ≤7 枚重叠头像（-ml 交叠 + 白描边）；presence.joined/left 维护 | 断言 | COLLAB-004 §3.1 头像列行 / BR-11 |
| 绿点/灰度 | 【条件态】在线（25s 心跳内）绿点角标；离线灰度保留 5 分钟缓存位后清理 | 条件断言 | COLLAB-004 §3.1 ● 绿点行 |
| 「+N」 | 【条件态】离线缓存人数 → 26px 圆片「+N」（title「N 人近期在线」） | 条件断言 | COLLAB-004 §3.1 ≤7+「+N」 / 原型 .pmore |
| hover 成员卡 | 【交互】hover 头像弹成员卡（姓名 + 在线·正在看板 / 离线·5 分钟缓存位 + 项目房间 N 人在线） | 交互断言 | COLLAB-004 §3.1 hover 弹成员卡行 |
| 自己的影子位 | 【条件态】本人已连接 → 本地合成在线头像（presence 帧不回显本人 BR-08，本地补） | 条件断言 | COLLAB-004 BR-08 / §3.1 |
| 响应式 | 【条件态】<768px 收为 +N 单入口 | 条件断言 | COLLAB-004 §3.5 |

### C.94 连接指示三态 + 详情弹层（归属：COLLAB-004 §3.1 / §3.4）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 三态圆点 | 【默认态】wifi 图标 + 圆点：绿=已连接（隐藏文案）/ 黄=重连中（pulse 动画 + 文案）/ 灰=已降级（+文案）；role=status + aria-label | 断言 | COLLAB-004 §3.1 连接指示行 / §3.5 |
| 详情弹层 | 【交互】点击弹层四行：状态/房间（rooms 列表）/延迟（ping RTT ms）/重连次数 + [重连] 按钮 | 交互断言 | COLLAB-004 §3.1 点击弹连接详情行 |
| 手动重连 | 【交互】[重连] → 跳过退避立即重连 + 全量补偿（§3.4 手动刷新行） | 交互断言 | COLLAB-004 §3.4 手动刷新行 |
| online 跳退避 | 【交互】浏览器 online 事件 → 立即重连（跳过退避等待） | 交互断言 | COLLAB-004 §3.4 断网恢复行 |

### C.95 降级横幅 + 轮询降级（归属：COLLAB-004 §3.1 / BR-10）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 横幅 | 【条件态】连接失败累计 30s 或 /health 探测失败 → 常驻黄条「实时同步暂停 · 已切换为定时刷新」 | 条件断言 | COLLAB-004 §3.1 降级横幅行 / BR-10 |
| 立即重连 | 【交互】横幅 [立即重连]（跳过退避）；[关闭] 本次会话内隐藏 | 交互断言 | COLLAB-004 §3.1 / 原型 .rt-banner |
| 恢复撤除 | 【条件态】重连成功 → 横幅自动撤除 + 补偿拉取（动态流/看板/铃铛）；关闭记忆复位 | 条件断言 | COLLAB-004 §2.2 / BR-10 恢复自动切回 |
| /health 探测 | 【交互】降级期每 30s 探测 /live/health；恢复 → 立即重连 | 交互断言 | COLLAB-004 BR-10 / §2.2 H 节点 |

### C.96 实时看板同步 + 拖拽保护（归属：COLLAB-004 §3.2；接线于 C.66 分组看板）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 远端卡片迁移 | 【交互】对端 issue.state.changed/board.moved → 目标列定向更新 + 卡片 300ms 淡入（rp-remote-in）+ 列计数 bump 动画；不自动滚动视口 | 交互断言 | COLLAB-004 §3.2 远端卡片迁移行 |
| 定向更新 | 【交互】仅回写受影响列（from/to group 命中列）；其余列 DOM 不重排 | 交互断言 | COLLAB-004 §4.4.2 事件→patch 映射 |
| 本地拖拽保护 | 【交互】本地拖拽中 → 远端事件入队（不重排 DOM），松手（dragEnd）后合并应用 | 交互断言 | COLLAB-004 §3.2 本地拖拽保护行 |
| version 过滤 | 【条件态】事件 version（=updated_at）旧于等于本地忽略（BR-07 乱序免疫）；board.moved 按 column_version 列粒度比对 | 条件断言 | COLLAB-004 BR-07 / §2.3 注 3 |
| 自己操作不回显 | 【条件态】actor_id === me 的事件忽略（BR-08；live 已滤 + 前端双保险） | 条件断言 | COLLAB-004 BR-08 / §3.2 自己的操作行 |
| batch 聚合 Toast | 【条件态】batch_id 同批事件 800ms 窗口聚合 → 单条「X 批量更新了 N 个任务（batch）」 | 条件断言 | COLLAB-004 §2.3 注 2 |
| sr-only 播报 | 【条件态】远端迁移给屏幕阅读器一条 sr-only 播报语境（列头 aria-label 计数随刷新） | 条件断言 | COLLAB-004 §3.5 |

### C.97 铃铛/评论流/任务详情实时化（归属：COLLAB-004 §3.3；承载 C.34/C.84/C.23）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 铃铛徽标 +1 | 【交互】notification.created（user 房间）→ 徽标 +unread_delta + 弹跳一次（notifbounce 0.5s）；按 notification_id 去重 | 交互断言 | COLLAB-004 §3.3 铃铛行 / §4.4.2 |
| 评论流实时补齐 | 【交互】comment.created 非本人 → 静默拉取补齐正文（乐观语义=数据即时刷新） | 交互断言 | COLLAB-004 §3.3 评论流行 / §4.4.2 |
| 「N 条新回复 ↓」浮条 | 【条件态】非本人新回复 → 计数头右端 brand 胶囊（aria-live=polite，不抢滚动位置）；点击滚到线程底部 + 清零 | 条件断言 | COLLAB-004 §3.3 评论流行 |
| 详情区轻闪 | 【交互】issue.updated（brief 命中）→ 属性区 150ms 背景 pulse（rp-live-flash）+ 头部「✦ 已更新（brief）」角标（4s 消隐）；version/actor 过滤同 C.96 | 交互断言 | COLLAB-004 §3.3 任务详情 Drawer 行 / BR-07/08 |

### C.98 甘特视图页框架与甘特控制条（归属：GANTT-001 §3.1 / 原型 O1）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 侧栏「甘特」入口 | 【默认态】项目侧栏视图组入口；路由 /:ws/projects/:pid/gantt（替换 table 路由的 gantt 占位） | 断言 | GANTT-001 §3.1 视图切换条行 / BOARD-003 §4.4 |
| 布局四段器 | 【默认态】列表｜看板｜甘特｜表格（BOARD-003 框架 layout=gantt 高亮） | 断言 | GANTT-001 §3.1 / BOARD-003 §3.1 |
| 工具条分层 | 【默认态】视图条 → 甘特控制条（粒度/今天/缩放/⋯）→ 延期概览条 → 图表区（O1 分层定稿） | 断言 | 原型 O1 / GANTT-002 §3.2「工具条下方」 |
| 粒度三段器 | 【交互】日｜周｜月 Segmented；快捷键 1/2/3；中心日期锚定不变，列宽 150ms 过渡 | 交互断言 | GANTT-001 §3.1 粒度切换行 / §3.4 / 原型 O3 |
| 今天导航 | 【交互】←今天→；平移动画到今日居中（300ms）；T 键 | 交互断言 | GANTT-001 §3.1 今天导航行 / §3.4 |
| 缩放 | 【交互】⤢ + Ctrl+滚轮；缩放锚点 = 光标处日期 | 交互断言 | GANTT-001 §3.1 缩放行 / §3.4 |
| 甘特 ⋯ 菜单 | 【交互】甘特控制条右端 ⋯（与 BOARD-003 视图 ⋯ 并存，O2）→ 导出 PNG（GANTT-002 §3.3）/ 全屏、重置缩放（原型 ⋯ 菜单实现，规格无条款——ADR-0022 登记） | 交互断言 | GANTT-002 §3.3 入口行 / 原型 O2 |

### C.99 甘特左栏行树与栏宽（归属：GANTT-001 §3.1 / §3.6 断点表）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 左栏宽度 | 【交互】默认 320px，拖拽调整 240~480px；行高固定 36px 不可拖 | 交互断言 | GANTT-001 §3.1 左栏行 / 概览 §2 |
| 行树结构 | 【默认态】编号 + 标题（truncate）+ 折叠箭头 ▸/▾；缩进 20px/层（原型 16px/层同义）；role=tree（TASK-004 沿用） | 断言 | GANTT-001 §3.1 左栏行树行 / §3.6 |
| 行折叠 | 【交互】父行折叠子行收起；连线按 BR-07 收拢为锚点 | 交互断言 | GANTT-001 §3.4 行折叠行 / BR-07 |
| 行选择 | 【交互】↑↓ 行选择高亮；Enter 打开选中任务详情 | 交互断言 | GANTT-001 §3.6 键盘行 |
| 任务计数 | 【默认态】左栏头部「任务 (N)」 | 断言 | GANTT-001 §3.1 ASCII |
| 响应式（1024~1439px） | 【条件态】左栏 240px；条内文本仅编号 | 条件断言 | GANTT-001 §3.6 断点表 |

### C.100 时间轴表头/底纹/今日线（归属：GANTT-001 §3.1 / §3.4）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 列头 | 【默认态】日：日期数字（+节假日名）；周：起–止区间（与 §1.4「周一日期+周数」冲突，按冻结原型 O3 裁决、规格待回改）；月：年月；role=application + aria-roledescription=甘特图 | 断言 | GANTT-001 §3.1 / §3.6 / 原型 O3 |
| 周末底纹 | 【默认态】6% 灰；中国法定节假日 10% 灰 + 节假名列头标注（列头标注为原型承载，§3.1 仅底纹条款） | 断言 | GANTT-001 §3.1 周末/节假日底纹行 / 原型 |
| 图例 | 【默认态】图表底部图例（今日线/空条/部分/完成/逾期五义；导出产物含图例——GANTT-002 BR-11 交叉证明为交付元素） | 断言 | GANTT-001 §3.1 ASCII 底部 / GANTT-002 BR-11 |
| 今日线 | 【默认态】2px 红（#EF4444）竖线 + 顶部角标「今天」；平移跟随 | 断言 | GANTT-001 §3.1 今日线行 |
| 水平平移 | 【交互】拖拽时间轴区/触控板横向滚动；视窗跟随（rAF 节流）；150ms 防抖后预取；预取中列头右侧 spinner | 交互断言 | GANTT-001 §3.4 水平平移行 |
| 垂直滚动 | 【交互】行虚拟滚动；80% 处预取下行页；行骨架 200ms | 交互断言 | GANTT-001 §3.4 垂直滚动行 |

### C.101 任务条样式矩阵（归属：GANTT-001 §3.2）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 条体基础 | 【默认态】圆角 4px、高 20px（行内垂直居中） | 断言 | GANTT-001 §3.1 任务条行 |
| 未开始 0% | 【条件态】10% 灰底无填充、1px 中性边框 | 条件断言 | GANTT-001 §3.2 行 1 |
| 进行中 1~99% | 【默认态】状态色 15% 底 + 状态色实心填充（宽度=进度%）+ 1px 状态色边框 | 断言 | GANTT-001 §3.2 行 2 |
| 已完成 | 【默认态】绿 15% 底 + 绿实心 100% + 条体划线 | 断言 | GANTT-001 §3.2 行 3 |
| 已取消 | 【条件态】虚线边框灰底、无填充 | 条件断言 | GANTT-001 §3.2 行 4 |
| 逾期 | 【条件态】红 15% 底 + 1.5px 红边 + 右端 ⚠ 图标（未完成且 target < 今天；口径与列表/看板 is_overdue 同源） | 条件断言 | GANTT-001 §3.2 行 5 / GANTT-002 §2.3 |
| 聚合条 | 【条件态】父无日期：渐变半透明 + 虚线边框 + 子树整体进度填充 | 条件断言 | GANTT-001 §3.2 行 6 |
| 开放端条 | 【条件态】单边 NULL：缺省端无圆角 + 渐隐边缘；悬浮提示「未设置开始/截止日期」（BR-02） | 条件断言 | GANTT-001 §3.2 行 7 / BR-02 |
| 今日线跨越条 | 【条件态】条体左缘 2px 亮色高亮 | 条件断言 | GANTT-001 §3.2 行 8 |
| 条内文本 | 【条件态】宽度 ≥80px 显示编号+标题（truncate）；不足隐藏 | 条件断言 | GANTT-001 §3.2 条内文本行 |
| 条悬浮 tooltip | 【交互】编号/标题/起止/进度/执行人头像/工时对照 spent/estimate（超耗红显；estimate 空不显示） | 交互断言 | GANTT-001 §3.2 条内文本行 / TASK-006 §3 |
| 条点击 | 【交互】单击打开任务详情 Drawer（复用 TASK-001，URL ?peekIssue=） | 交互断言 | GANTT-001 §3.4 条点击行 |

### C.102 依赖连线样式矩阵（归属：GANTT-001 §3.3）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| blocks 实线箭头 | 【默认态】A 尾圆点 → B 头箭头（A 完成 B 才可完成）；状态色 | 断言 | GANTT-001 §3.3 行 1 |
| is_blocked_by 合并 | 【默认态】渲染合并为 blocks 正向 | 断言 | GANTT-001 §3.3 行 2 |
| relates_to 虚线 | 【默认态】灰虚线无箭头、圆点双向 | 断言 | GANTT-001 §3.3 行 3 |
| duplicates 点划线 | 【默认态】灰点划线 + 菱形终 | 断言 | GANTT-001 §3.3 行 4 |
| 悬浮连线 | 【交互】tooltip 两端正倒名称与关系语义；点击高亮两端行 | 交互断言 | GANTT-001 §3.3 悬浮行 |
| 排期冲突红点 | 【条件态】B 起期早于 A 终期 → 连线中段红点（仅提示不阻止，P2 连线只读） | 条件断言 | GANTT-001 §3.3 冲突提示行 / GANTT-002 §2.4 |

### C.103 未排期折叠区（归属：GANTT-001 §3.1 / §3.4）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 折叠区头部 | 【默认态】「📥 未排期 (N)」正确计数；空则不显示 | 断言 | GANTT-001 §3.1 ASCII / §3.4 |
| 展开 | 【交互】点击列出任务行；点击行跳转列表设置日期；回到甘特即时出现 | 交互断言 | GANTT-001 §3.4 未排期展开行 |
| 全未排期引导 | 【条件态】任务全未排期：时间轴空网格 + 未排期区置顶展开 + 「去列表设置日期」引导 | 条件断言 | GANTT-001 §3.5 |

### C.104 甘特空态/骨架/失败（归属：GANTT-001 §3.5）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 项目无任务 | 【条件态】甘特区插画 + 「创建或导入任务后排期将在此展示」 | 条件断言 | GANTT-001 §3.5 |
| 首屏骨架 | 【条件态】表头骨架 + 12 行条形骨架（占位宽度随机，CLS=0） | 条件断言 | GANTT-001 §3.5 |
| 预取失败 | 【条件态】黄条提示 + 保持既有渲染 | 条件断言 | GANTT-001 §3.5 |
| <1024px 降级 | 【条件态】「建议在桌面端使用」+ 只读简化时间轴（行上限 200，无虚拟滚动） | 条件断言 | GANTT-001 §3.6 断点表 |

### C.105 甘特键盘导航与无障碍（归属：GANTT-001 §3.6）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 行树键盘 | 【交互】↑↓ 行选择、←→ 平移一天（周/月平移一列）、Home/End 视窗首尾、Enter 打开选中任务 | 交互断言 | GANTT-001 §3.6 键盘行 |
| 粒度快捷键 | 【交互】1/2/3 粒度、T 今天 | 交互断言 | GANTT-001 §3.6 |
| 行 aria-label | 【默认态】每行完整摘要（「RBT-13 后端导出 API，9 月 1 日至 9 月 5 日，进度 100%，已完成」）可线性消费 | 断言 | GANTT-001 §3.6 |
| 颜色语义冗余 | 【默认态】逾期 ⚠ 图标、完成划线、取消虚线（不依赖色相） | 断言 | GANTT-001 §3.6 |

### C.106 三手势拖拽反馈（归属：GANTT-002 §3.1）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 悬停可拖区 | 【条件态】条体 cursor grab；边缘热区 ew-resize + 4px 竖条手柄浮现 | 条件断言 | GANTT-002 §3.1 |
| 拖起态 | 【交互】条 60% 透明 + 原位虚线占位；顶部跟随徽标 Δ +5d / 工期 3d → 8d（aria-live=polite 播报） | 交互断言 | GANTT-002 §3.1 / §3.4 |
| 钳制触发 | 【条件态】条缘抖动一次（100ms）+ 徽标红闪（start ≤ target 约束） | 条件断言 | GANTT-002 §3.1 / §2.4 |
| 拖动中冲突 | 【条件态】相关连线红点脉冲 + 徽标追加 ⚠ 依赖冲突 | 条件断言 | GANTT-002 §3.1 |
| 松手提交 | 【交互】spinner 角标 → 成功就位 / 失败弹回（300ms ease-back）；MobX 乐观更新 + 失败回滚 | 交互断言 | GANTT-002 §3.1 / §4.4 |
| 仅写两字段 | 【交互】三手势仅映射 start_date/target_date（Issue PATCH 唯一写通道；尊重 chk_issue_start_before_target） | 交互断言 | GANTT-002 §2.4 / 概览 §5 |
| VIEWER 只读 | 【条件态】访客拖拽禁用（PATCH 403 提示）；<1024px 拖拽仅桌面；完成/取消/归档条不可拖（光标 default + tooltip「已归档」） | 条件断言 | GANTT-002 §2.4 BR-05 / §3.4 / §2.5 |

### C.107 依赖冲突确认弹层（归属：GANTT-002 §3.1）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 弹层文案 | 【条件态】「新排期使该任务早于其前置 RBT-13 的完成日。仍按此排期？」 | 条件断言 | GANTT-002 §3.1 |
| role | 【默认态】role=alertdialog | 断言 | GANTT-002 §3.4 |
| 确认语义 | 【交互】「仍按此排期」保存成功 + 连线红点持续提示；不自动顺延（P2 连线只读） | 交互断言 | GANTT-002 §2.4 / §3.1 |

### C.108 未排期拖入时间轴（归属：GANTT-002 §3.1）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 拖入成条 | 【交互】未排期行拖入时间轴：3 天默认工期成条；计数减一；可继续再拖 | 交互断言 | GANTT-002 §7.2-3 / 原型 O6 |
| 跟随徽标 | 【交互】拖拽中实时「排期至 09-14（3 天）」（O6） | 交互断言 | 原型 O6 |

### C.109 延期概览条与明细（归属：GANTT-002 §3.2）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 概览条 | 【默认态】⚠ 逾期 N 个任务 · 最长逾期 N 天 · 按人分布（张三(3)…）；无逾期整条隐藏；role=status | 断言 | GANTT-002 §3.2 / §3.4 |
| 统计口径 | 【默认态】三数字为完整集口径（含 start_date 为空、仅 target 逾期的开放端条——与 GANTT-001 is_overdue 同源） | 断言 | GANTT-002 §2.3 / §7.2-4 |
| [查看▾] 明细 | 【交互】展开逾期任务列表（编号/标题/逾期天数/执行人，点击跳行） | 交互断言 | GANTT-002 §3.2 |
| 20 截断 | 【条件态】明细上限 20 条按逾期天数降序；超出尾提示「已展示前 20 条，完整清单见任务列表 overdue 筛选（TASK-011）」 | 条件断言 | GANTT-002 §3.2 |

### C.110 PNG 导出（归属：GANTT-002 §3.3）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 入口 | 【交互】甘特 ⋯ →「导出 PNG」；⌘/Ctrl+E（O2） | 交互断言 | GANTT-002 §3.3 / 原型 O2 |
| 导出中 | 【交互】Toast「正在渲染…」（大视窗 < 2s） | 交互断言 | GANTT-002 §3.3 |
| 产物 | 【默认态】自动下载；右下角水印三行（项目名 / 时间 / 用户）；2x；含表头/条/连线/今日线；拖拽中的条不入产物 | 断言 | GANTT-002 §3.3 / §7.2-5 |
| 范围提示 | 【条件态】可见行 >200 时确认「导出仅包含当前滚动视窗的 200 行」 | 条件断言 | GANTT-002 §3.3 |

### C.111 键盘改期（归属：GANTT-002 §3.4）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 组合键 | 【交互】选中行 Shift+←/→ 平移一天、Alt+←/→ 调起点、Alt+Shift+←/→ 调终点 | 交互断言 | GANTT-002 §3.4 |
| 合并提交 | 【交互】键击 300ms 合并为一次 PATCH（连续键击仅一次提交） | 交互断言 | GANTT-002 §3.4 / §7.2-6 |
| 播报 | 【条件态】拖拽徽标 aria-live=polite 播报「向后移动 5 天」 | 条件断言 | GANTT-002 §3.4 |

### C.112 文件库页框架（归属：FILE-002 §3.1 / §3.5 断点表）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 侧栏「文件」入口 | 【默认态】项目导航「文件」；路由 /:ws/projects/:pid/files | 断言 | FILE-002 §3.1 |
| 左侧目录树 | 【默认态】缩进 16px/层（≤5 深）、当前高亮 ▌、悬浮 ⋯（改名/移动/删除/可见性）；role=tree | 断言 | FILE-002 §3.1 左树行 / §3.5 |
| 面包屑 | 【交互】路径可点击逐级返回；当前层级 + N 个文件计数 | 交互断言 | FILE-002 §3.1 |
| 工具条 | 【默认态】名称过滤（防抖 300ms）+ 类型/上传人/时间筛选 + 视图切换 + 上传按钮（VIEWER 禁用） | 断言 | FILE-002 §3.1 工具条行 / §2.4 BR-13 |
| 新建目录 | 【交互】左树底部「＋新建目录」 | 交互断言 | FILE-002 §3.1 |
| 回收站入口 | 【默认态】左树底部「🗑 回收站 (N)」计数徽标随请求者过滤口径（CONTRIBUTOR 仅本人，O7） | 断言 | FILE-002 §3.1 / BR-13 |
| 响应式（≥1280px） | 【条件态】左树 260px + 右列表 | 条件断言 | FILE-002 §3.5 断点表 |
| 响应式（768~1279px） | 【条件态】左树收起为目录下拉 | 条件断言 | FILE-002 §3.5 断点表 |
| 响应式（<768px） | 【条件态】单列列表 + 底部上传按钮；拖拽禁用改点选 | 条件断言 | FILE-002 §3.5 断点表 |

### C.113 文件列表/网格双视图（归属：FILE-002 §3.1）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 列表视图 | 【默认态】表列：类型图标/名称（大小人性化）/上传人/修改时间/⋯；行 hover 浅底；语义 <table> | 断言 | FILE-002 §3.1 列表视图行 / §3.5 |
| 网格视图 | 【默认态】卡片 120×140：类型大图标（缩略位 FILE-003）+ 名称两行 + 大小 | 断言 | FILE-002 §3.1 网格视图行 |
| 名称过滤 | 【交互】输入防抖 300ms 过滤 | 交互断言 | FILE-002 §3.1 工具条行 |
| 类型/人/时间筛选 | 【交互】三下拉过滤（含 has 高亮态） | 交互断言 | FILE-002 §3.1 工具条行 |
| 可见性角标 | 【条件态】🔒 仅管理员 / 👥 指定成员（FILE-002 三态）/ 🔗N 分享数（原型承载，规格 §3 未定义——ADR-0022 登记）；CONTRIBUTOR 列表不可见 admin 态文件 | 条件断言 | FILE-002 §3.3 / BR-07 / 原型 |
| 拖拽移动 | 【交互】文件/目录拖到左树目标（高亮落点）；环/深度前端预判 | 交互断言 | FILE-002 §3.1 拖拽移动行 |
| 图标冗余文本 | 【默认态】大小人性化 + 类型图标冗余文本（无障碍） | 断言 | FILE-002 §3.5 |

### C.114 拖拽上传 dropzone（归属：FILE-002 §3.1）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 右区 dropzone | 【交互】拖入蓝色虚线框 + 「松开上传到 {目录}」；多文件并行 | 交互断言 | FILE-002 §3.1 拖拽上传行 |
| 直传口径 | 【交互】上传到 MinIO（Django 零字节流）；>50MB 自动走分片（FILE-003）；<768px 拖拽禁用改点选 | 交互断言 | FILE-002 §4 / 概览 §5 / §3.5 |

### C.115 上传进度浮层与配额（归属：FILE-002 §3.2）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 在途浮层 | 【默认态】列表底部浮层：每文件进度条 + 速度 + 取消；完成即插入列表；role=status aria-live=polite 播报里程碑（25/50/75/100%） | 断言 | FILE-002 §3.2 / §3.5 |
| 失败重试 | 【条件态】浮层行红 + 重试（自动重申 presign） | 条件断言 | FILE-002 §3.2 |
| 取消 | 【交互】移除浮层行；30 分钟后标记 abandoned、残片次日物理回收（FILE-001 复用） | 交互断言 | FILE-002 §3.2 |
| 配额预检 | 【条件态】将满（≥95%）：上传前弹层用量/配额 + 「仍要上传」/「取消」 | 条件断言 | FILE-002 §3.2 / BR-11 / BR-03 |
| 配额条 | 【默认态】工作区存储用量条（warn/full 分色；定义于 §4.4 侧栏底部进度条 ≥95% 红） | 断言 | FILE-002 §4.4 / 原型 |

### C.116 文件 ⋯ 菜单与操作弹层（归属：FILE-002 §3.3）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| ⋯ 菜单 | 【交互】下载/重命名/移动/附加到任务/分享（FILE-004）/分享管理/可见性（仅 ADMIN）/删除——键盘可达 | 交互断言 | FILE-002 §3.3 / FILE-004 §3.1 |
| 重命名 | 【交互】行内编辑；同层同名即时校验（红框 + 提示） | 交互断言 | FILE-002 §3.3 |
| 移动弹层 | 【交互】目录树选择 + 新建快捷；禁选自身后代（置灰 + 提示）；显示目标路径；毫秒级元数据操作 | 交互断言 | FILE-002 §3.3 / §7.2-2 |
| 附加到任务 | 【交互】任务搜索弹层；建立 issue 双挂（任务附件区可见、互不复制） | 交互断言 | FILE-002 §3.3 / §1.2 / §7.2-6 |
| 可见性编辑器 | 【交互】三态单选（全员/指定成员/仅管理员）+ members 态成员多选；仅 ADMIN 可见此项；三层一致 | 交互断言 | FILE-002 §3.3 / §7.2-3 |
| 删除确认 | 【条件态】目录显示 N 文件；回收站 30 天提示；role=alertdialog | 条件断言 | FILE-002 §3.3 |

### C.117 回收站页（归属：FILE-002 §3.1 / §3.3）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 已删列表 | 【默认态】名称/原位置/删除人/删除时间/剩余天数（列清单为冻结原型承载，§3.1 仅「已删列表+还原/彻底删除」）；按 file.delete R1 口径过滤（CONTRIBUTOR 仅本人、ADMIN 全量） | 断言 | FILE-002 §3.1 回收站行 / BR-13 / 原型 |
| 还原 | 【交互】原位还原；同名冲突落根目录带「(恢复)」后缀；对象零拷贝 | 交互断言 | FILE-002 §3.3 / §7.2-5 |
| 彻底删除 | 【条件态】仅 ADMIN；二次确认；引用计数归零后清对象 | 条件断言 | FILE-002 §3.3 |
| 30 天自动清理 | 【条件态】期满无引用对象被清理（purge_deleted_assets 引用计数增强） | 条件断言 | FILE-002 §4 / §7.2-5 |

### C.118 文件库空态三式（归属：FILE-002 §3.4）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 空目录 | 【条件态】「拖拽文件到此处，或 [＋上传]」插画（含目标目录名） | 条件断言 | FILE-002 §3.4 |
| 空文件库 | 【条件态】首次引导卡：三示例目录模板（需求文档/设计稿/会议纪要）一键创建 | 条件断言 | FILE-002 §3.4 / §7.2-1 |
| 过滤无结果 | 【条件态】「未找到匹配文件」+ 清除筛选 | 条件断言 | FILE-002 §3.4 |
| 树加载 | 【条件态】3 级骨架 | 条件断言 | FILE-002 §3.4 |
| 下载失败 | 【交互】自动重申一次预签名，再失败 Toast | 交互断言 | FILE-002 §3.4 |

### C.119 分片上传器（归属：FILE-003 §3.1）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 大文件态卡片 | 【条件态】>50MB 强制分片：片级聚合百分比 + 速度 + 已传片数（1,024/2,150 MB · 128/269 片 · 并行 3） | 条件断言 | FILE-003 §3.1 / 概览 §5 |
| 暂停/继续 | 【交互】停止取新片（在途片完成后停）；从 uploaded_chunks 断点继续 | 交互断言 | FILE-003 §3.1 |
| 取消 | 【交互】abort 端点；会话 24h TTL 到期自动 Abort + 配额预留释放 | 交互断言 | FILE-003 §3.1 / BR-05 |
| 断点恢复 | 【条件态】重进上传器：「检测到未完成的上传 [继续] [放弃]」（session_id localStorage 探测；已传片零重传） | 条件断言 | FILE-003 §3.1 / §7.2-1 |
| MD5 核对 | 【交互】complete 时片级 ETag 与登记 MD5 全量核对（缺失/不符 400 拒绝落库，BR-04）；content_md5 入版本元数据 | 交互断言 | FILE-003 §7.2-1 / BR-04 |
| progressbar | 【默认态】上传进度 role=progressbar | 断言 | FILE-003 §3.5 |
| 断点提示 | 【默认态】「⏸ 断点续传已启用」hint | 断言 | FILE-003 §3.1 卡片尾注 |

### C.120 预览抽屉五通道（归属：FILE-003 §2.3 / §3.2 / §3.4）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 抽屉框架 | 【默认态】头部（名 + vN · 大小 · 人 · 时间 + [版本▾][下载] ✕）；960px；role=dialog + 焦点陷阱；Esc 关闭；预览区全屏按钮 | 断言 | FILE-003 §3.2 / §3.5 |
| 图片通道 | 【默认态】缩略图 + 原图（缩略位落位 §3.3） | 断言 | FILE-003 §2.3 / §3.2 |
| PDF 通道 | 【默认态】pdf.js 渲染 + 翻页；文本层可读 | 断言 | FILE-003 §2.3 / §3.5 |
| Office 通道 | 【条件态】转码 PDF（LibreOffice 异步） | 条件断言 | FILE-003 §2.3 |
| 文本/MD 通道 | 【默认态】Monaco / MD 渲染；≤2MB | 断言 | FILE-003 §2.3 |
| 视频通道 | 【默认态】边下边播（Range）+ 封面帧 + 原生控件（字幕轨有则加载） | 断言 | FILE-003 §2.3 / §3.5 |
| 不可预览类型 | 【条件态】元数据卡（类型/大小）+ 下载查看 | 条件断言 | FILE-003 §2.3 决策链 |
| 排队态 | 【条件态】⏳ 转码中 + 预计时长 + [先下载]；202 + 完成经 WebSocket 自动刷新 | 条件断言 | FILE-003 §3.4 / §7.2-3 |
| 失败态 | 【条件态】「预览生成失败 · 重试 / 下载」 | 条件断言 | FILE-003 §3.4 |
| 文本超限 | 【条件态】>2MB「文件较大，请下载查看」 | 条件断言 | FILE-003 §3.4 |
| 权限拦截 | 【条件态】「仅管理员」文件版本接口与预览调度均 404（三层一致） | 条件断言 | FILE-003 §7.2-5 |

### C.121 版本面板与对比/回滚（归属：FILE-003 §3.2 / §3.5）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 版本列表 | 【默认态】版本/时间/人/大小 + 当前 ● 标记；单版本隐藏版本区；[版本▾] 下拉链 | 断言 | FILE-003 §3.2 |
| 对比 | 【交互】文本类 [对比]：左右分栏 diff（新增绿/删除红）；aria-label「第 N 版与第 M 版差异：新增 X 行，删除 Y 行」 | 交互断言 | FILE-003 §3.2 / §3.5 / §7.2-6 |
| 回滚确认 | 【交互】「回滚到 v2？将创建新版本（内容同 v2）」；新版本指向旧对象不删除；版本链完整保留 | 交互断言 | FILE-003 §3.2 / §7.2-2 |
| 同名新版本 | 【交互】上传同名：不产生重复行、版本 +1、动态提示 | 交互断言 | FILE-003 §7.2-2 |
| 版本上限 | 【条件态】20/文件滚动淘汰最旧非当前版 | 条件断言 | FILE-003 / 概览 §5 |
| 回滚按钮门槛 | 【条件态】[回滚] 仅写权限可见（VIEWER 无按钮） | 条件断言 | FILE-003 §3.2 版本列表行 |
| 响应式（<768px） | 【条件态】全屏预览器；版本区折叠 | 条件断言 | FILE-003 §3.5 断点表 |

### C.122 缩略图落位（归属：FILE-003 §3.3）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 网格缩略 | 【默认态】网格卡片缩略图替换类型图标（兑现 FILE-002 占位；不可预览类型仍用大图标，O4） | 断言 | FILE-003 §3.3 / 原型 O4 |
| 列表悬浮小卡 | 【交互】列表行悬浮 200ms 出预览小卡 | 交互断言 | FILE-003 §3.3 / 原型 O4 |
| 衍生物生命周期 | 【条件态】转码产物 30 天未访问冷清理、按需重生成（BR-11） | 条件断言 | FILE-003 BR-11 / §7.2-4 |

### C.123 分享创建弹层（归属：FILE-004 §3.1）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 链接区 | 【默认态】https://{domain}/s/{22 位 slug} + [复制链接]（slug 不可枚举） | 断言 | FILE-004 §3.1 / BR slug |
| 权限单选 | 【交互】仅预览 ｜ 预览 + 下载 | 交互断言 | FILE-004 §3.1 |
| 密码开关 | 【交互】开启 + 掩码输入 + 👁 显示（Argon2id） | 交互断言 | FILE-004 §3.1 / §2 |
| 有效期 | 【交互】永久/1天/7天/30天/自定义（O5 日期选择） | 交互断言 | FILE-004 §3.1 / 原型 O5 |
| 警示行 | 【默认态】「⚠ 任何获得链接（与密码）的人都能访问该文件」 | 断言 | FILE-004 §3.1 |
| 入口权限 | 【条件态】VIEWER 无分享项（⋯ 菜单隐藏） | 条件断言 | FILE-004 BR-01 / §2.4 |

### C.124 分享管理弹层（归属：FILE-004 §3.2）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 分享列表 | 【默认态】「{文件} 的分享（N）」：链接（截断 + 复制）/权限/有效期/访问计数/操作 | 断言 | FILE-004 §3.2 |
| 过期行 | 【条件态】已过期标注（访问得失效页） | 条件断言 | FILE-004 §3.2 |
| 行 ⋯ 菜单 | 【交互】延期 30 天 / 吊销（二次确认）/ 复制链接 | 交互断言 | FILE-004 §3.2 |
| 吊销确认 | 【条件态】二次确认（规格仅「吊销（二次确认）」；具体文案为原型/清单自拟） | 条件断言 | FILE-004 §3.2 / 原型 |
| 跟随版本 | 【交互】上传同名新版本：匿名页刷新即见新内容（无需重建链接） | 交互断言 | FILE-004 §7.2-4 |

### C.125 space 匿名访问页三态（归属：FILE-004 §3.3）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 密码门 | 【默认态】极简不泄露文件信息；密码框 aria-label + 自动 focus；回车提交；错误抖动 + 剩余次数（role=alert）；「由 RabbitProjects 提供安全分享」 | 断言 | FILE-004 §3.3 / §3.4 |
| 防爆破锁定 | 【条件态】连续错 5 次第 6 次锁定 + 显示等待时间（10 分钟恢复） | 条件断言 | FILE-004 §7.2-5 / BR-07 |
| 文件页头部 | 【默认态】文件名 + 大小 + 过期倒计时（29 天后过期） | 断言 | FILE-004 §3.3 |
| 预览区 | 【默认态】复用 FILE-003 预览器（匿名只读变体） | 断言 | FILE-004 §3.3 |
| 下载按钮 | 【条件态】仅 preview+download 权限可见；aria-label 含文件名；点击 content/?download=1 → 302 至 5 分钟预签名 URL（不入前端可缓存载荷） | 条件断言 | FILE-004 §3.3 / §7.2-2 |
| 底部提示 | 【默认态】「⚠ 本链接由分享者创建，如需延期请联系分享者」 | 断言 | FILE-004 §3.3 |
| 失效页 | 【条件态】三因统一「链接不存在或已失效」（吊销/过期/源软删）；恢复文件后分享不自动复活 | 条件断言 | FILE-004 §3.3 / §7.2-3 |
| 移动优先 | 【默认态】匿名页移动优先响应式 | 断言 | FILE-004 §3.4 |


### C.126 批量角色分配（归属：AUTH-006 §3.1）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 复选列 | 【交互】成员行复选（OWNER/自己禁选）；已选 N 人浮条「批量改角色▾」「移出」 | 交互断言 | AUTH-006 §3.1 |
| 批量改角色弹层 | 【交互】目标角色 MEMBER/GUEST 单选 + [应用]；结果 Toast「已更新 X 人 · 跳过 Y 人（原因逐项）」role=status | 交互断言 | AUTH-006 §3.1 / §2.3 |
| 禁用行提示 | 【条件态】选中含已禁用账号时警示「角色变更将在其启用后生效（仍计入本次操作）」 | 条件断言 | AUTH-006 §3.1 |

### C.127 账号启停（归属：AUTH-006 §3.2/§3.3）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 禁用入口 | 【交互】成员行 ⋯ →「禁用账号」→ 危险确认框（三要点列表 + API Key 不恢复警示） | 交互断言 | AUTH-006 §3.2 |
| 吊销计数回显 | 【条件态】禁用成功 Toast 回显「会话 N · API Key M · WS 连接 K」 | 条件断言 | AUTH-006 §4.4.2 |
| 已禁用灰标 | 【默认态】成员列表头像 40% 透明 +「已禁用」灰徽章；筛选器仍列出 | 断言 | AUTH-006 §3.3 |
| 启用 | 【交互】⋯ →「启用账号」（幂等）；启用后需重新登录提示 | 交互断言 | AUTH-006 §2.4 |

### C.128 工作空间治理四区块（归属：TEAM-003 §3.1）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 归档区块 | 【交互】「归档工作空间」危险确认（只读影响说明）→ OWNER 专属；归档态横幅「已归档 · 只读 · [恢复]（OWNER）」 | 交互断言 | TEAM-003 §3.1/§3.2 |
| 全局标签区块 | 【交互】列表（≤100）+ 新建（名/#RRGGBB/描述）+ 行编辑/删除（二次确认显 affected_issues） | 交互断言 | TEAM-003 §3.1 / §4.2.3 |
| 状态模板区块 | 【交互】五组编辑（组名只读/状态名+顺序）+ [保存]（校验错逐字段提示）；version 回显 | 交互断言 | TEAM-003 §4.2.4 |
| 活跃度区块 | 【默认态】活跃成员 7d/30d + 总数三数卡；周贡献分桶条 + 登录天数直方图 + top_actions 占比；无个人明细 | 断言 | TEAM-003 §3.3 / §4.2.5 |

### C.129 项目生命周期操作（归属：PROJ-003 §3.2）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 状态卡 | 【默认态】当前态徽章（draft 灰/active 绿/archived 黄/closed 锁）+ 允许目标按钮（draft→启用；active→归档/关闭；archived→恢复/关闭） | 断言 | PROJ-003 §3.2 / §2.2 |
| 关闭向导 | 【条件态】开放任务 N>0 → 决策弹层（先去处理跳预过滤列表 / 强制关闭输入项目名确认） | 条件断言 | PROJ-003 §2.5 / BR-06 |
| 副本重开 | 【交互】closed 态「重开为副本」→ 新 draft 项目确认 | 交互断言 | PROJ-003 §2.5 |
| 状态历史 | 【默认态】status-logs 列表（from→to/操作人/时间/reason） | 断言 | PROJ-003 §4.2.2 |

### C.130 模板选择与实例化（归属：PROJ-003 §3.3）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 模板列表 | 【默认态】新建项目第一步：内置 3 套卡片（名/描述/状态数标签数）+ 空白项目 | 断言 | PROJ-003 §3.3 / §4.2.4 |
| 实例化回执 | 【交互】创建成功 Toast 回显四件套计数（状态/标签/字段/目录） | 交互断言 | PROJ-003 §4.2.5 |

### C.131 项目统计页（归属：RPT-002 §3.1）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 进度卡 | 【默认态】五组分布条 + 完成率大数 + 逾期红数；?days=7/14/30/90 切换 | 断言 | RPT-002 §3.1 / §4.2.1 |
| 工时卡 | 【默认态】估算/登记/剩余/超支四数 + 未估算计数 | 断言 | RPT-002 §4.2.1 |
| 趋势图 | 【默认态】created/completed 双折线（按日补零） | 断言 | RPT-002 §3.1 |
| 成员任务量表 | 【默认态】按人 open/done_30d/overdue/估算/登记列 + 未指派行 + 合计行；role 筛选 + 排序白名单 | 断言 | RPT-002 §3.2 / §4.2.2 |
| 已禁用成员 | 【条件态】行灰标（is_active=false），可正常过滤 | 条件断言 | RPT-002 §4.2.2 / AUTH-006 §3.3 |

### C.132 集成设置页（归属：INTG-001 §3.1/§3.2）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 集成卡 | 【默认态】GitHub 卡：连接态（org/仓库数/上次同步）+ [安装应用]/[管理] | 断言 | INTG-001 §3.1 |
| 绑定管理 | 【交互】绑定列表（仓库/状态 syncing|paused|stale）+ 绑定（≤5 上限提示）+ 暂停/恢复 + 解绑确认 | 交互断言 | INTG-001 §3.2 / §4.2 |
| secret 一次性展示 | 【条件态】绑定成功弹层展示 webhook secret（仅一次 + 复制） | 条件断言 | INTG-001 §4.2.2 |
| 同步冲突日志 | 【条件态】Tab 冲突列表（scope/胜方/败方快照对照/时间） | 条件断言 | INTG-001 §4.2 #7 |

### C.133 Webhook 管理页（归属：INTG-002 §3.1~§3.3）

| 组件/字段/交互 | 规格 | 断言方式 | 来源 |
| --- | --- | --- | --- |
| 端点列表 | 【默认态】URL + 事件面 chips + 状态（active/disabled/auto_disabled 红标「50 连败」） | 断言 | INTG-002 §3.1 / §4.2.2 |
| 新建/编辑抽屉 | 【交互】URL + 事件复选（webhook.ping 免勾选固定提示）+ secret 一次性展示 | 交互断言 | INTG-002 §3.2 / §4.2.1 |
| 投递日志页 | 【默认态】事件/状态/尝试数/最后码列表 + status/event 过滤；死信 Tab 红点计数 | 断言 | INTG-002 §3.3 / §4.2.7 |
| 重放 | 【交互】死信行 [重放] → 新 pending 行（原行保留） | 交互断言 | INTG-002 §3.3 / BR-07 |
| ping | 【交互】[发送测试] → 202 Toast「已入队」 | 交互断言 | INTG-002 §4.2.6 |

### C.134 admin 限流监控页（归属：INFRA-005 §3.1；路由 /ops）
| UI 表面 | 内容 | 验证 | 来源 |
| --- | --- | --- | --- |
| 降级/L2 旗标 | 【默认态】「正常/降级中」胶囊 + 「L2 全局：启用/未启用（dev 口径）」 | 断言 | INFRA-005 §3.1 / §4.2 #5 |
| 配额快照表 | 【默认态】L1 三区（api/auth/public）+ L2/L3 八行配额，表尾注明 §7.2 冻结源 | 断言 | INFRA-005 §3.1 / api-conventions §7.2 |
| 无权限态 | 【边界】非 SystemAdmin：错误条「需系统管理员会话」 | 断言 | INFRA-005 §4.2 表注 / UT-14 |

### C.135 admin 备份管理页（归属：INFRA-005 §3.2；路由 /ops/backups）
| UI 表面 | 内容 | 验证 | 来源 |
| --- | --- | --- | --- |
| 立即备份 | 【交互】[立即备份] → 202「已入队」；10 分钟内重复 → 429 文案 | 交互断言 | INFRA-005 §3.2 / §4.2 #1 |
| 记录列表 | 【默认态】类型/状态（success 绿·failed 红·running 黄）/开始/大小/SHA-256/对象键 | 断言 | INFRA-005 §3.2 / §4.1 |
| 连败红线 | 【边界】连续 2 失败 → 红条「已通知 WS Admin，发布 checklist 阻塞」 | 断言 | INFRA-005 §2.3 BR-07 |
| 空态 | 【空态】「暂无备份记录——点『立即备份』创建第一份」 | 断言 | INFRA-005 §3.4 |

### C.136 admin 发布门禁页（归属：QA-001 §3.1 + INFRA-005 §3.3；路由 /ops/release）
| UI 表面 | 内容 | 验证 | 来源 |
| --- | --- | --- | --- |
| 创建发布尝试 | 【交互】版本号 + commit sha → 创建（幂等）；空列表态显示创建卡 | 交互断言 | QA-001 §3.1 / §4.4 #3 |
| 四门禁胶囊 | 【默认态】缺陷/压测/安全/兼容 × pending/running/passed/blocked 四色 | 断言 | QA-001 §3.1 / §4.1 |
| checklist 8 项 | 【交互】逐项 [签署]/[反签]（append-only）；已签显示签署人，反签后「已反签」 | 交互断言 | QA-001 §3.1 / INFRA-005 §4.5.4 |
| 裁决 | 【交互】[裁决放行] 门禁未全绿 → 红字 BLOCKED_BY_GATE；[打回] 无条件 | 交互断言 | QA-001 §2.6 / §4.4 #6 |
| 事件时间线 | 【默认态】折叠面板：时刻/类型/操作者（CI=机器） | 断言 | QA-001 §3.1 / §4.1 |

### C.140 审批中心（归属：WF-002 §3.1；路由 /:ws/approvals，Sprint-7）
| UI 表面 | 内容 | 验证 | 来源 |
| --- | --- | --- | --- |
| 三 Tab | 【默认态】待办 / 已办 / 我发起的；待办 Tab 含计数徽标 | 断言 | WF-002 §3.1 / §4.6 |
| 待办行 | 【默认态】任务编号+标题 / 审批流名+级号 / 状态徽标五态（pending/approved/rejected/withdrawn/terminated） | 断言 | WF-002 §3.1 |
| 已办行 | 【默认态】含「我的动作」标注（approve/reject） | 断言 | WF-002 §4.6 行级口径 |
| 详情抽屉 | 【交互】行点击开抽屉：任务链接 / 流程与状态 / L{level} 逐票时间线（审批人+动作+意见） | 交互断言 | WF-002 §3.2 |
| 审批动作 | 【交互】通过（确认）+ 驳回（意见必填，空则红字）；仅 pending 态显示动作区 | 交互断言 | WF-002 §3.2 / BR-05 |
| 空态 | 【空态】三 Tab 各自空文案（没有待处理/没有记录/没有发起过） | 断言 | WF-002 §3.5 |

### C.141 工作流画布编辑器（归属：WF-001 §3.1~§3.3；路由 /:ws/projects/:pid/workflows/:wid/canvas，Sprint-7）
| UI 表面 | 内容 | 验证 | 来源 |
| --- | --- | --- | --- |
| 顶栏 | 【默认态】工作流名 + 状态徽标（草稿/已发布 vN/已归档）+ 未保存圆点 + 自动布局/保存/发布按钮（draft-only 启用） | 断言 | WF-001 §3.1 |
| 状态节点 | 【默认态】名称 + group 色边框 + 初始态圆点 + 🔒字段锁定徽标（有锁时） | 断言 | WF-001 §3.1 |
| 边 | 【交互】拖拽连边（强制命名）；点击选中开侧栏（命名输入/删除） | 交互断言 | WF-001 §3.2 |
| 自动布局 | 【交互】dagre LR 分层重排 | 交互断言 | WF-001 §3.2 |
| 发布错误面板 | 【条件态】校验失败逐项列出（不可达/缺完成组/在用状态等） | 断言 | WF-001 §3.3 |
| 保存冲突 | 【条件态】If-Match 不匹配 → 「已被他人修改」提示 | 断言 | WF-001 §4.8④ |

### C.142 守卫补齐对话框（归属：WF-004 §3.1；组件 GuardDialog，挂流转入口，Sprint-7）
| UI 表面 | 内容 | 验证 | 来源 |
| --- | --- | --- | --- |
| 标题 | 【条件态】「无法完成『{边名}』——请补齐以下内容」 | 断言 | WF-004 §3.1 |
| 字段区 | 【交互】缺失必填字段逐项输入（meta 驱动 number/date/select/input 四控件） | 交互断言 | WF-004 §3.1 / §4.9 |
| 阻塞区 | 【默认态】前置任务清单（编号+标题+状态组）+ 管理员强制提示 | 断言 | WF-004 §3.1 |
| 补齐重试 | 【交互】[补齐并流转] guard_payload 单请求；新一轮缺口回填对话框 | 交互断言 | WF-004 BR-05 |

### C.143 工时周视图与台账（归属：TASK-013 §3.1~§3.3；路由 /:ws/projects/:pid/worklog，Sprint-7）
| UI 表面 | 内容 | 验证 | 来源 |
| --- | --- | --- | --- |
| 三 Tab | 【默认态】我的周 / 审批队列 / 团队台账 | 断言 | TASK-013 §3.1~3.3 |
| 周提交 | 【交互】周起始选择 + [提交本周]；无工时 → 提交失败提示 | 交互断言 | TASK-013 §2.2 BR-03 |
| 审批队列 | 【交互】成员×周行 + 驳回意见输入（必填红字）+ [通过]/[驳回] | 交互断言 | TASK-013 §3.2 / BR-05 |
| 台账矩阵 | 【默认态】成员×周（工时/任务数/超 8h†标红/冻结🔒） | 断言 | TASK-013 §3.3 / BR-11 |

### C.144 工作流列表页（归属：WF-001 §3.1 / WF-005 §3；路由 /:ws/projects/:pid/workflows，Sprint-7 补口轮）
| UI 表面 | 内容 | 验证 | 来源 |
| --- | --- | --- | --- |
| 列表 | 名称/状态徽标（草稿·已发布 vN·已归档）/状态数/边数/更新时间 | 断言 | WF-001 §3.1 |
| 新建草稿 | [＋新建草稿] 弹层名称 → 创建即进画布 | 交互断言 | WF-001 §4.2 |
| 归档 | 已归档态隐藏归档钮；确认弹层文案（回退兜底流警示） | 交互断言 | WF-001 §4.6 |
| 模板入口 | [从模板库下发] 链到 WS 模板库（C.145） | 断言 | WF-005 §3 |

### C.145 工作流模板库（归属：WF-005 §3；路由 /:ws/workflow-templates，Sprint-7 补口轮）
| UI 表面 | 内容 | 验证 | 来源 |
| --- | --- | --- | --- |
| 模板卡片 | 预设徽标 + 版本/态数 + 描述 | 断言 | WF-005 §2.2 |
| 两步下发 | [下发到项目…] → 选项目 → 预演（BR-05 状态映射 JSON）→ [确认下发] 进画布 | 交互断言 | WF-005 §3.2 / BR-05 |

### C.146 自动化规则页（归属：WF-003 §3/§3.4；路由 /:ws/projects/:pid/automation，Sprint-7 补口轮）
| UI 表面 | 内容 | 验证 | 来源 |
| --- | --- | --- | --- |
| 规则 Tab | 名称/触发器/条件数/动作链/启停开关（PATCH）/删除 | 断言 | WF-003 §3.1 |
| 三段式编辑器 | 触发器（含 config）+ 条件行（字段/操作符/值）+ 动作行（五类型按型变形） | 交互断言 | WF-003 §2.1/2.7 |
| Dry Run 弹层 | 任务样本搜索 → 选中预演 → 结果 JSON（0 写） | 交互断言 | WF-003 §2.4 / BR-11 |
| 运行日志 Tab | 时间/规则/任务/状态（skip_reason）/耗时/动作明细 | 断言 | WF-003 §3.4 / BR-12 |

### C.147 画布边配置增强（归属：WF-002 §3.3 + WF-004 §3.1；画布侧栏，Sprint-7 补口轮）
| UI 表面 | 内容 | 验证 | 来源 |
| --- | --- | --- | --- |
| 审批流挂接 | 边配置「审批流」下拉（无/项目审批流）→ 随保存写图 | 交互断言 | WF-002 §3.3 |
| 守卫编辑 | required_fields 字段 chips 多选 + estimate/blocker 勾选 + role 下拉 | 交互断言 | WF-004 §4.2 |
| 字段锁灰显 | 抽屉开始/截止在锁定态禁用 + 🔒（available.current_locks） | 断言 | WF-004 §3.2 |
| 审批中徽标 | 看板卡/列表行「审」角标（has_pending_approval annotate） | 断言 | WF-002 §3.5 |

### C.148 审批留痕审计页（归属：WF-006 §2/§3；路由 /:ws/projects/:pid/audit，Sprint-7 补口轮）
| UI 表面 | 内容 | 验证 | 来源 |
| --- | --- | --- | --- |
| 完整性徽标 | 🔒链完整·N 事件 / ⚠断裂（verify 端点） | 断言 | WF-006 §2.2 |
| 事件列表 | 时间/操作者/动作/实例/任务/prev→hash | 断言 | WF-006 §2.1 |
| 导出 | [导出 CSV] 确认 → blob 下载（文件名含标识符+日期） | 交互断言 | WF-006 §2.3 |

### C.149 字段权限矩阵（归属：TASK-012 §4.4；字段管理 ⋯ 菜单「字段权限」，Sprint-7 补口轮）
| UI 表面 | 内容 | 验证 | 来源 |
| --- | --- | --- | --- |
| 矩阵网格 | 角色×（可读/可写/必填）三列勾选；留空=全员 | 断言 | TASK-012 §4.4 |
| 保存 | PATCH permission_config（BR-08/BR-17 服务端校验回显） | 交互断言 | TASK-012 BR-08/17 |
| 高级类型控件 | date_range 双日期 / cascade 逐级联动 / relation·attachment 只读 | 交互断言 | TASK-012 §4.3 |
| dropped 提示 | 权限不足写入静默丢弃 → warning toast | 交互断言 | TASK-012 BR-16 |

### C.150 组织管理页（归属：AUTH-007 §3.1/§3.2；路由 /:ws/org，Sprint-8 R6）
| UI 表面 | 内容 | 验证 | 来源 |
| --- | --- | --- | --- |
| 部门树 | 平铺组树（缩进层级）+ 直属/含子级双计数 + 未分配/总数恒等式 | 断言 | AUTH-007 §3.1 / §1.2 |
| 新建部门 | 名称输入（父级=选中部门/根）→ 201；同级重名/超 6 层结构化报错 | 交互断言 | AUTH-007 §2.1 / BR-01/02 |
| 删除部门 | 空部门确认删除；非空 409 提示 | 交互断言 | AUTH-007 BR-04 |
| 成员归属表 | 成员×部门下拉（未分配=空）+ 岗位列 + 部门过滤（含子部门开关） | 交互断言 | AUTH-007 §3.3 / §2.2 |
| 批量授权弹窗 | 项目+角色选择 → 预览（added/role_changed/skipped/unchanged）→ 执行 | 交互断言 | AUTH-007 §3.2 / §2.2 |

### C.151 自定义角色页（归属：AUTH-008 §3.1/§3.2/§3.3；路由 /:ws/projects/:pid/roles，Sprint-8 R6）
| UI 表面 | 内容 | 验证 | 来源 |
| --- | --- | --- | --- |
| 角色列表 | 名称/码数/挂接人数；内置模板采用（测试/外包/只读干系人）+ 空白新建 | 断言 | AUTH-008 §3.1 / BR-14 |
| 权限矩阵编辑器 | 目录 42 码按域分组勾选（编辑/保存两态）；保存即时生效提示 | 交互断言 | AUTH-008 §3.2 / BR-12 |
| 成员挂接表 | 已挂角色 chips + 挂接/卸除（GUEST 越界 409 文案含越界码） | 交互断言 | AUTH-008 §3.2 / BR-16 |
| 我的权限面板 | 并集清单（fixed_role + permissions 全量码） | 断言 | AUTH-008 §3.3 |

### C.152 全站审计日志页（归属：AUTH-010 §3.1/§3.2；路由 /:ws/audit-logs，Sprint-8 R6）
| UI 表面 | 内容 | 验证 | 来源 |
| --- | --- | --- | --- |
| 筛选条 | category/action 级联 + actor + 对象名关键词 | 交互断言 | AUTH-010 §2.2 |
| 事件表 | 时间/分类.动作/操作者/对象/IP + 游标分页（上/下一页） | 断言 | AUTH-010 §3.1 |
| 详情抽屉 | actor/object 快照 + detail JSON（敏感键已滤）+ event_key | 断言 | AUTH-010 §2.3 / BR-02 |
| 导出对话框 | 密码二次确认 → CSV blob 下载；导出动作自入审计 toast | 交互断言 | AUTH-010 §3.2 / BR-04/08 |

### C.153 视图治理（归属：BOARD-005 §3.1/§3.2；ViewSwitchBar Tab 右键菜单 + 锁定横幅，Sprint-8 R6）
| UI 表面 | 内容 | 验证 | 来源 |
| --- | --- | --- | --- |
| Tab 标识 | 🔒锁定 ★项目默认 👤共享（无标识=personal） | 断言 | BOARD-005 §3.1 |
| 治理菜单 | 共享/收回、锁定/解锁、设/取消项目默认、订阅、另存副本、删除（锁定态禁删） | 交互断言 | BOARD-005 §2.1 / BR-02 |
| 锁定横幅 | 「组织标准视图，只读」+ [另存为副本] 主操作 + ★说明 | 断言 | BOARD-005 §3.1 / §2.5 |
| 两步解锁引导 | 默认视图解锁 → 409 提示「先显式取消项目默认」toast | 交互断言 | BOARD-005 BR-15 |
| 泳道维度选择 | 「泳道：无/状态/负责人…」（排除当前列维度） | 交互断言 | BOARD-005 §3.3 / BR-07 |

### C.154 二维泳道看板（归属：BOARD-005 §3.1 泳道区；?sub_group_by= 矩阵形态，Sprint-8 R6）
| UI 表面 | 内容 | 验证 | 来源 |
| --- | --- | --- | --- |
| 矩阵表 | 列头（group_by）× 行头（sub_group_by）交叉格；行头 sticky | 断言 | BOARD-005 §1.3 |
| 格计数 | 服务端聚合 count 徽章（99+ 截断）+ ≤8 样例短卡 | 断言 | BOARD-005 §2.3 / BR-13 |
| 空格 | 虚线框「拖拽任务到此」 | 断言 | BOARD-005 §3.4 |
| 维度说明 | 「状态 × 负责人 · 共 N 项」+ 计数对账（Σ格=N） | 断言 | BOARD-005 §7.2 |
| 降级黄条 | 维度停用/聚合超时 → 一维回退 + meta.degraded 提示 | 断言 | BOARD-005 BR-07 / §2.5 |

### C.155 SSO 配置与登录路由（归属：AUTH-009 §3.1/§3.2；/:ws/settings/sso + 登录页 + /sso/claim，Sprint-8 R6）
| UI 表面 | 内容 | 验证 | 来源 |
| --- | --- | --- | --- |
| 协议选择 | OIDC / SAML 2.0 切换（启用中锁定） | 交互断言 | AUTH-009 §3.1 |
| 元数据表单 | issuer/client_id/secret（或 EntityID/SSO URL/X509）；secret 永不回显（已设置提示） | 断言 | AUTH-009 §3.1 / BR-14 |
| 干跑与启用 | [测试连接]（通过 ✓ 状态/失败原因）+ 启用开关（未过干跑禁用） | 交互断言 | AUTH-009 BR-04 |
| 强制 SSO | 开/关按钮 + 前置说明（OWNER 绑定防自锁）+ 逃生名单说明 | 交互断言 | AUTH-009 BR-05/06 |
| 证书临期 | 剩余天数提示（<14 天提醒语义） | 断言 | AUTH-009 §2.6 |
| 登录邮箱路由 | 邮箱失焦 → route 发现：sso 模式蓝条 + [前往 SSO 登录] | 交互断言 | AUTH-009 §3.2 / §2.4 |
| 认领页 | 密码一次验证 + 事务 10 分钟说明 + 超时文案 | 断言 | AUTH-009 §3.2 / BR-02 |
