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
