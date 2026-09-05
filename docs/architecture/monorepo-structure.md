# Monorepo 仓库结构规范

| 元信息项 | 内容 |
| --- | --- |
| 文档编号 | ARCH-002 |
| 所属层级 | 跨迭代架构决策（Cross-Iteration Architecture Decision） |
| 文档状态 | 已确认（Approved）· 目录结构变更需走 ADR 流程 |
| 最后更新日期 | 2026-08-31 |
| 适用范围 | 全仓库结构、包命名、依赖方向、构建管道、环境变量 |
| 上游依据 | `docs/需求文档.md` §1.2（pnpm workspace + Turborepo，apps/ + packages/）、§8.3 POC 交付项 |
| 对标基线 | Plane 开源版 master 分支仓库结构 |
| 关联文档 | `tech-stack.md`、`api-conventions.md` |

---

## 1. 设计目标与总体原则

### 1.1 目标

1. **一次 clone，一条命令起全栈**：`pnpm i && pnpm dev` 起前端与 live，`docker compose up` 起全套 11 个服务（POC 硬性交付项）。
2. **共享代码只有一份**：UI 组件、类型、编辑器、MobX store、Tailwind 配置全部收敛到 `packages/`，禁止跨 app 复制粘贴。
3. **增量构建**：改动 `apps/web` 不触发 `apps/admin` 的构建与测试；改动 `packages/ui` 精确触发其全部下游。
4. **异构语言共存**：Python 后端与 JS/TS 前端在同一仓库内，但**依赖管理体系完全隔离**，互不干扰。
5. **依赖方向单向**：`apps/*` → `packages/*`，禁止反向依赖，禁止 app 之间互相依赖。

### 1.2 命名约定

| 对象 | 约定 | 示例 |
| --- | --- | --- |
| npm 包名 | `@rp/<name>`（`rp` = RabbitProjects 组织 scope） | `@rp/ui`、`@rp/types` |
| 目录名 | kebab-case，与包名后缀一致 | `packages/tailwind-config` → `@rp/tailwind-config` |
| 内部包版本 | 统一 `0.1.0` + `private: true`，包间引用一律 `workspace:*` | `"@rp/ui": "workspace:*"` |
| 应用目录 | 单词小写，无前缀 | `apps/web`、`apps/api` |

---

## 2. 完整目录树

```
project-root/
├── apps/
│   ├── web/                          # 主工作台（React Router v7 + Vite）
│   │   ├── app/
│   │   │   ├── root.tsx              # 根布局：Provider 装配（MobX RootStore / SWR / i18n / Theme）
│   │   │   ├── routes.ts             # 路由表（Framework Mode 配置式路由，类型自动生成）
│   │   │   ├── entry.client.tsx      # 客户端入口（SPA 模式）
│   │   │   ├── routes/               # 路由模块，一文件一路由
│   │   │   │   ├── auth/             # 登录/注册/重置密码
│   │   │   │   ├── workspace/        # 工作空间层级（成员、设置、通知）
│   │   │   │   ├── projects/         # 项目列表与项目设置
│   │   │   │   ├── issues/           # 工作项：列表/看板/甘特/日历/表格视图 + 详情
│   │   │   │   ├── cycles/           # 迭代
│   │   │   │   ├── modules/          # 模块 / 项目集
│   │   │   │   ├── pages/            # 协作文档（接 live 服务）
│   │   │   │   ├── analytics/        # 报表与大屏
│   │   │   │   └── settings/         # 个人 / 团队 / 项目设置
│   │   │   ├── components/           # 仅本 app 使用的业务组件（可复用的须上移 packages/ui）
│   │   │   │   ├── issues/           # 看板列、卡片、筛选器、批量操作条
│   │   │   │   ├── gantt/            # 甘特图（自研，含时间轴与依赖连线）
│   │   │   │   └── command-palette/  # ⌘K 面板（cmdk）
│   │   │   ├── hooks/                # 业务 hooks（useIssues / usePermission / useRealtime）
│   │   │   ├── services/             # API 客户端层：axios 实例 + 按资源分文件的 service
│   │   │   ├── lib/                  # 纯函数工具（日期、排序键 lexorank、权限判定）
│   │   │   └── styles/
│   │   │       └── app.css           # Tailwind v4 入口（@import "tailwindcss" + @theme）
│   │   ├── public/
│   │   ├── react-router.config.ts    # ssr: false（SPA），prerender 配置
│   │   ├── vite.config.ts
│   │   ├── tsconfig.json
│   │   ├── Dockerfile                # 多阶段：build → nginx:alpine 静态托管（或由 proxy 统一托管）
│   │   ├── .env.example
│   │   └── package.json
│   │
│   ├── admin/                        # 实例管理后台（God Mode）
│   │   ├── app/
│   │   │   ├── routes/
│   │   │   │   ├── general/          # 实例基础信息、许可
│   │   │   │   ├── email/            # SMTP 配置与测试发信
│   │   │   │   ├── authentication/   # 各认证方式开关（密码/魔法链接/OAuth/SSO）
│   │   │   │   ├── ai/               # AI 能力配置（P4）
│   │   │   │   ├── storage/          # 对象存储策略、容量与生命周期
│   │   │   │   ├── users/            # 全站用户增删改查、禁用、角色调整
│   │   │   │   └── audit/            # 全站审计日志查询与导出（P3）
│   │   │   └── ...                   # 结构与 web 同构
│   │   ├── react-router.config.ts
│   │   ├── vite.config.ts            # base: '/god-mode/'
│   │   ├── Dockerfile
│   │   └── package.json
│   │
│   ├── space/                        # 对外公开空间（匿名可访问）
│   │   ├── app/
│   │   │   ├── routes/
│   │   │   │   ├── issues/           # 公开发布的工作项视图（只读 + 受控评论）
│   │   │   │   ├── views/            # 公开视图分享链接
│   │   │   │   ├── pages/            # 公开文档（只读渲染，复用 @rp/editor 只读模式）
│   │   │   │   └── intake/           # 对外需求/缺陷收集表单（P2）
│   │   │   └── ...
│   │   ├── react-router.config.ts    # 开启 prerender / SEO meta
│   │   ├── vite.config.ts            # base: '/spaces/'
│   │   ├── Dockerfile
│   │   └── package.json
│   │
│   ├── api/                          # Django + DRF 后端（★ 排除在 pnpm workspace 之外）
│   │   ├── plane/                    # Django 项目包（沿用 Plane 的包组织方式）
│   │   │   ├── settings/
│   │   │   │   ├── common.py         # 公共配置
│   │   │   │   ├── local.py          # 本地开发
│   │   │   │   ├── production.py     # 生产
│   │   │   │   └── test.py           # 测试（内存/独立库）
│   │   │   ├── db/                   # 数据层
│   │   │   │   ├── models/           # 全部 Django Model，按域拆文件
│   │   │   │   ├── migrations/       # Django migrations（提交入库，禁止手动改历史）
│   │   │   │   └── mixins/           # AuditModel（created_by/updated_by/时间戳）、SoftDelete
│   │   │   ├── app/                  # 面向 Web UI 的内部 API（Session 认证）
│   │   │   │   ├── views/            # ViewSet，按资源分文件
│   │   │   │   ├── serializers/
│   │   │   │   ├── permissions/      # Permission 类层级
│   │   │   │   └── urls/             # 路由，按资源分文件后聚合
│   │   │   ├── api/                  # 对外 Open API（API Key / OAuth 认证，独立版本节奏）
│   │   │   │   ├── views/
│   │   │   │   ├── serializers/
│   │   │   │   ├── rate_limit.py
│   │   │   │   └── urls.py
│   │   │   ├── space/                # 面向 apps/space 的匿名只读 API
│   │   │   ├── authentication/       # 自研认证：Session / Token / OAuth2 / SSO(P3)
│   │   │   ├── bgtasks/              # Celery 任务（通知、Webhook、导入导出、报表预聚合）
│   │   │   ├── workflow/             # 工作流引擎与审批（状态机、条件规则、流转校验）
│   │   │   ├── analytics/            # 报表聚合查询与缓存
│   │   │   ├── license/              # 企业版许可校验（P3）
│   │   │   ├── middleware/           # 请求 ID、审计上下文、限流（INFRA-001 §4.11）
│   │   │   ├── utils/                # 字段选择/展开 mixin（INFRA-001 §4.11）—— 异常处理/分页器见 base/
│   │   │   ├── base/                 # 框架层：error_codes 注册表 / handlers（异常处理）/ response / middleware（六件套）/ request_context（INFRA-004 §1.3，sprint-1 命名收口；详见 ADR-0012 A2）
│   │   │   ├── celery.py             # Celery app 定义（含队列与路由）
│   │   │   ├── asgi.py
│   │   │   ├── wsgi.py
│   │   │   └── urls.py               # 根路由：/api/v1/ 挂载
│   │   ├── tests/
│   │   │   ├── conftest.py           # pytest fixture（用户/工作空间/项目工厂）
│   │   │   ├── factories/            # factory-boy 工厂
│   │   │   ├── unit/
│   │   │   └── integration/
│   │   ├── bin/
│   │   │   ├── docker-entrypoint-api.sh      # migrate + collectstatic + gunicorn
│   │   │   ├── docker-entrypoint-worker.sh   # celery worker
│   │   │   ├── docker-entrypoint-beat.sh     # celery beat
│   │   │   └── docker-entrypoint-migrator.sh # 一次性迁移任务容器
│   │   ├── manage.py
│   │   ├── pyproject.toml            # 依赖声明（uv 管理），ruff / mypy / pytest 配置
│   │   ├── uv.lock                   # Python 锁文件（提交入库）
│   │   ├── .python-version           # 3.12
│   │   ├── Dockerfile
│   │   ├── .env.example
│   │   └── README.md
│   │
│   ├── live/                         # Node.js 实时协作服务
│   │   ├── src/
│   │   │   ├── server.ts             # Express + Hocuspocus 装配，WS upgrade 挂载
│   │   │   ├── config/               # 环境变量解析与校验（zod）
│   │   │   ├── hocuspocus/
│   │   │   │   ├── authenticate.ts   # onAuthenticate：校验 api 签发的短时效 JWT 票据
│   │   │   │   ├── extensions.ts     # Database / Redis / Logger 扩展装配
│   │   │   │   └── persistence.ts    # onStoreDocument：防抖后写入 PostgreSQL
│   │   │   ├── ydoc/                 # Yjs ↔ ProseMirror 转换、快照与版本
│   │   │   ├── rooms/                # 房间命名规则与权限映射（项目/实体维度隔离）
│   │   │   ├── routes/               # /health、/metrics、/internal/broadcast
│   │   │   └── lib/                  # logger(pino)、api 内部客户端
│   │   ├── tests/
│   │   ├── tsup.config.ts
│   │   ├── tsconfig.json
│   │   ├── Dockerfile
│   │   ├── .env.example
│   │   └── package.json
│   │
│   └── proxy/                        # Nginx 反向代理（★ 排除在 pnpm workspace 之外）
│       ├── nginx.conf.template       # envsubst 变量注入的主配置模板
│       ├── conf.d/
│       │   ├── upstreams.conf        # web / admin / space / api / live 上游定义
│       │   ├── websocket.conf        # Upgrade / Connection 头与超时
│       │   ├── security.conf         # 安全响应头、隐藏 server token
│       │   └── ratelimit.conf        # limit_req_zone 边缘限流
│       ├── docker-entrypoint.sh      # envsubst 渲染模板 → nginx -g 'daemon off;'
│       ├── Dockerfile
│       └── README.md
│
├── packages/
│   ├── ui/                           # @rp/ui —— 自研组件库 + Storybook
│   │   ├── src/
│   │   │   ├── components/           # Button/Input/Select/Dialog/Dropdown/Tooltip/Avatar/Badge/Table/Tabs/Toast...
│   │   │   ├── hooks/                # useOutsideClick / useKeyboard / useTheme
│   │   │   ├── utils/                # cn()（clsx + tailwind-merge）
│   │   │   └── index.ts              # 统一出口（named export，禁止 default）
│   │   ├── .storybook/
│   │   ├── stories/
│   │   ├── tsconfig.json
│   │   └── package.json
│   │
│   ├── editor/                       # @rp/editor —— TipTap 封装
│   │   ├── src/
│   │   │   ├── core/                 # 编辑器实例工厂、schema 定义
│   │   │   ├── extensions/           # slash 命令、@提及、图片上传、任务清单、代码块、表格
│   │   │   ├── collaboration/        # Yjs provider 接入、协作光标、在线成员
│   │   │   ├── editors/              # RichTextEditor（评论）/ DocumentEditor（协作文档）/ ReadOnlyEditor
│   │   │   ├── styles/               # 编辑器专属 Tailwind 层
│   │   │   └── index.ts
│   │   ├── tsconfig.json
│   │   └── package.json
│   │
│   ├── types/                        # @rp/types —— TypeScript 共享类型
│   │   ├── src/
│   │   │   ├── api/                  # 请求/响应包装类型、分页、错误码枚举
│   │   │   ├── entities/             # User/Workspace/Project/Issue/Cycle/Module/Page/View...
│   │   │   ├── enums/                # 优先级、状态组、角色、任务类型
│   │   │   ├── generated/            # ★ 由 OpenAPI schema 自动生成（禁止手改）
│   │   │   └── index.ts
│   │   ├── tsconfig.json
│   │   └── package.json
│   │
│   ├── shared-state/                 # @rp/shared-state —— MobX stores
│   │   ├── src/
│   │   │   ├── root.store.ts         # RootStore：聚合并注入全部子 store
│   │   │   ├── user/                 # 当前用户、权限、偏好
│   │   │   ├── workspace/            # 工作空间与成员
│   │   │   ├── project/              # 项目、成员、状态集、标签
│   │   │   ├── issue/                # 工作项：byId 规范化存储 + 分组/筛选派生计算
│   │   │   ├── view/                 # 视图配置（筛选/分组/排序/显示属性）
│   │   │   ├── realtime/             # WebSocket 增量 patch 入口
│   │   │   ├── lib/                  # 基类 BaseStore、乐观更新 + 回滚工具
│   │   │   └── index.ts
│   │   ├── tsconfig.json
│   │   └── package.json
│   │
│   └── tailwind-config/              # @rp/tailwind-config —— 共享 Tailwind 配置
│       ├── theme.css                 # Tailwind v4 @theme：设计 token（色板/字号/间距/圆角/阴影）
│       ├── preset.css                # 基础层（reset 补充、滚动条、focus-visible 规范）
│       ├── dark.css                  # 暗色主题变量覆盖
│       └── package.json
│
├── packages/config/                  # （可选，P1 引入）@rp/config —— 共享 tsconfig / oxlint 预设
│   ├── tsconfig.base.json
│   ├── tsconfig.react.json
│   ├── tsconfig.node.json
│   └── package.json
│
├── deploy/
│   ├── compose/
│   │   ├── docker-compose.yml        # 本地开发全套编排（11 服务）
│   │   ├── docker-compose.prod.yml   # 生产覆盖层（镜像 digest、副本、资源限制）
│   │   └── docker-compose.ci.yml     # CI 用最小依赖（db/redis/mq/minio）
│   ├── k8s/                          # Helm chart（P2 引入）
│   └── scripts/
│       ├── backup-db.sh              # 自动数据备份（P2）
│       └── restore-db.sh
│
├── scripts/
│   ├── check-yjs-version.mjs         # 校验 yjs 系列版本全仓库一致（见 tech-stack §4.1）
│   ├── gen-api-types.mjs             # 拉取 OpenAPI schema → packages/types/src/generated
│   └── setup.sh                      # 一次性初始化：复制 .env、安装依赖、起依赖服务
│
├── docs/
│   ├── 需求文档.md
│   └── architecture/
│       ├── tech-stack.md
│       ├── monorepo-structure.md
│       └── api-conventions.md
│
├── .github/
│   └── workflows/
│       ├── ci.yml                    # lint / typecheck / unit / build（Turbo 缓存）
│       ├── api-ci.yml               # 后端 ruff / mypy / pytest（仅 apps/api 变更触发）
│       └── e2e.yml                   # Playwright（compose 起依赖后执行）
│
├── .husky/
│   ├── pre-commit                    # lint-staged
│   ├── commit-msg                    # commitlint
│   └── pre-push                      # turbo run typecheck --filter=...[origin/main]
│
├── pnpm-workspace.yaml
├── turbo.json
├── package.json                      # 根包：仅 devDependencies + 编排脚本，private: true
├── docker-compose.yml                # → 软链或直接置于根，指向 deploy/compose
├── .env.example                      # 根级共享变量模板
├── .npmrc
├── .nvmrc
├── .gitignore
├── .dockerignore
├── .oxlintrc.json
├── commitlint.config.js
└── README.md
```

---

## 3. 各 app 职责说明

### apps/web —— 主工作台

面向已登录成员的核心生产力界面，承载需求文档九大功能模块的绝大部分：工作空间与项目导航、统一工作项（需求/缺陷/任务/测试/文档）的增删改查与详情面板、五种视图（列表 / 看板 / 甘特图 / 日历 / 表格）、迭代与模块管理、协作文档、通知中心、报表分析、以及个人/团队/项目三级设置。采用 React Router v7 Framework Mode 的 SPA 模式（`ssr: false`），构建为静态产物由 proxy 托管；所有数据经 `/api/v1/` 获取，Session 认证；实时增量经 live 服务的 WebSocket 推送到 MobX store。这是仓库中体量最大、迭代最频繁的应用，因此严格要求可复用组件上移 `packages/ui`、领域状态上移 `packages/shared-state`，防止其膨胀为无法维护的单体。

### apps/admin —— 实例管理后台（God Mode）

面向**系统管理员**的实例级管控台，与 web 的关键区别是：它管理的是**实例本身**而非业务数据，包括实例基础配置、SMTP 与发信测试、各认证方式（密码/魔法链接/OAuth/SSO）开关、对象存储策略与容量、AI 能力配置、全站用户增删改查与禁用、全站审计日志查询导出。独立成 app 而非 web 内一个路由的理由有三：①权限域完全不同，物理隔离可避免越权路由被前端误暴露；②可通过 proxy 层的 IP 白名单/独立域名单独收口；③体量小、迭代慢，独立构建不拖慢 web 的构建与部署节奏。部署路径 `/god-mode/`。

### apps/space —— 对外公开空间

面向**匿名或外部访客**的只读/受控写入界面，承载公开发布的工作项视图、公开视图分享链接、公开协作文档只读渲染、以及对外需求收集表单（intake）。它调用后端独立的 `space` API 分组（不复用内部 API），该分组在序列化层就剥离了所有敏感字段（成员邮箱、内部评论、审计信息）。这是唯一开启预渲染与 SEO meta 的应用，因为分享链接需要被搜索引擎与 IM 卡片抓取。安全上按「零信任」处理：任何未显式标记为 public 的资源一律 404（而非 403，避免资源存在性泄露）。部署路径 `/spaces/`。

### apps/api —— Django + DRF 后端

系统的**唯一数据权威**与业务规则中心。承载：数据模型与 migrations、面向 Web UI 的内部 API（`plane/app/`）、面向第三方的 Open API（`plane/api/`）、面向 space 的匿名只读 API（`plane/space/`）、自研认证（Session + Token + OAuth）、工作流与审批引擎、Celery 异步任务（worker + beat）、报表聚合、审计日志、许可校验。**它被排除在 pnpm workspace 之外**（见 §4.2），依赖由 `uv` + `pyproject.toml` + `uv.lock` 独立管理。它同时是 worker、beat、migrator 三个容器的镜像来源（同一 Dockerfile，不同 entrypoint），保证业务代码在 API 进程与任务进程之间零漂移。

### apps/live —— Node.js 实时协作服务

独立的 WebSocket 服务，唯一职责是**多人实时协同**，包含两条链路：①Hocuspocus + Yjs CRDT 驱动的富文本/文档协同编辑（含协作光标与在线成员感知）；②按项目/实体维度隔离的房间广播，用于看板拖拽、状态变更、评论新增等业务事件的精准推送。鉴权不自建体系：客户端先向 api 换取短时效 JWT 协同票据（含 room 与权限声明），live 在 `onAuthenticate` 中校验签名与声明。文档持久化走 `onStoreDocument` 防抖批量写库，避免每次击键都落盘。独立成服务的原因是长连接的生命周期、扩容策略、内存画像与无状态 HTTP 服务截然不同，混部会互相拖累。

### apps/proxy —— Nginx 反向代理

统一入口，把 5 个上游收敛到单一域名单一端口：`/` → web、`/god-mode/` → admin、`/spaces/` → space、`/api/` → api、`/live/` → live（WebSocket upgrade）。同时负责：TLS 终止、gzip/brotli 压缩、静态资源长缓存与 `index.html` 不缓存、`client_max_body_size` 上传上限、边缘粗粒度限流（与 DRF 应用层限流形成两层防护）、安全响应头（HSTS / X-Content-Type-Options / Referrer-Policy / CSP）。配置通过 `nginx.conf.template` + `envsubst` 在容器启动时注入上游地址与域名，实现「同一镜像跑本地与生产」。**同样排除在 pnpm workspace 之外**——它不含任何 JS 代码。

---

## 4. 各 package 职责说明

### packages/ui —— `@rp/ui`

自研基础组件库，对标 `@plane/ui`。以 Headless UI 提供可访问性行为、Tailwind 提供样式、`cn()` 处理变体合并，输出无业务语义的通用组件（Button、Input、Textarea、Select、Combobox、Dialog、Drawer、Dropdown、Tooltip、Popover、Avatar、AvatarGroup、Badge、Tag、Tabs、Toast、Spinner、EmptyState、Table 原语、DatePicker 等）。硬性约束：**不得 import `@rp/types` 的业务实体类型，不得发起任何网络请求，不得依赖 MobX store**——组件只接受 props。配套 Storybook 作为组件文档、评审载体与视觉回归基线。判断某组件是否该进入本包的标准：它是否在两个以上 app 中出现，且不含业务语义。

### packages/editor —— `@rp/editor`

TipTap/ProseMirror 的封装层，对标 `@plane/editor`。对外暴露三个成品编辑器：`RichTextEditor`（评论/描述，轻量工具集）、`DocumentEditor`（协作文档，接 Yjs provider，含目录、大纲、协作光标）、`ReadOnlyEditor`（space 只读渲染）。内部包含 schema 定义、扩展集（slash 命令、@提及、图片上传接 MinIO 预签名、任务清单、代码块高亮、表格、嵌入）以及 Yjs 协同接入。独立成包的必要性：①编辑器依赖体量大（TipTap + ProseMirror + Yjs 系列约 20 个包），独立成包便于统一约束版本并做 code splitting；②web 与 space 必须共用同一 schema，否则同一文档在两端渲染结果不一致；③服务端（live）需复用其 schema 做安全校验。

### packages/types —— `@rp/types`

前端全部共享 TypeScript 类型的单一来源，包含：API 请求/响应包装类型（`ApiSuccess<T>` / `ApiError` / `Paginated<T>`）、错误码枚举（与后端错误码表一一对应）、领域实体类型、业务枚举（优先级、状态组、角色、任务类型）。`src/generated/` 子目录由 `scripts/gen-api-types.mjs` 从后端 `drf-spectacular` 生成的 OpenAPI schema 自动生成，**禁止手工修改**，CI 校验生成结果与提交内容一致（防止前后端契约漂移）。手写类型仅用于生成器无法表达的部分（如动态自定义字段的判别联合类型）。本包**零运行时依赖**，只输出类型，编译产物仅 `.d.ts`。

### packages/shared-state —— `@rp/shared-state`

全部 MobX store 的集合，以 `RootStore` 为根聚合子 store 并通过 React Context 注入。职责边界严格遵循 `tech-stack.md` §2.1：store 只负责领域实体的规范化存储（`byId` map）、派生计算（分组、筛选、排序、统计）、乐观更新与回滚；**不负责**发起 HTTP 请求（由 app 层 service 注入函数）、不持有 UI 局部状态。三个前端应用共用同一套 store 定义，admin 与 space 仅实例化其中所需的子集。独立成包的价值在于：看板的分组逻辑、筛选器的求值逻辑这类高复杂度、高测试价值的代码可以脱离 UI 独立用 Vitest 测试。

### packages/tailwind-config —— `@rp/tailwind-config`

Tailwind v4 时代的共享样式配置。因 v4 采用 CSS-first 配置，本包不再导出 JS config，而是导出可被 `@import` 的 CSS 文件：`theme.css`（`@theme` 块定义设计 token：语义色板、字号阶梯、间距、圆角、阴影、动画时长）、`preset.css`（基础层规范：滚动条、`focus-visible`、选中态）、`dark.css`（暗色主题变量覆盖）。三个 app 与 Storybook 均 `@import "@rp/tailwind-config/theme.css"`，保证设计 token 全局唯一。新增颜色/字号必须改本包，禁止在业务代码中写任意值（`bg-[#3f76ff]` 形式在 CI lint 中告警）。

### packages/config —— `@rp/config`（P1 引入）

共享 `tsconfig` 基线与 oxlint 预设，避免每个包各写一份编译配置导致行为漂移。含 `tsconfig.base.json`（strict 全开、`moduleResolution: bundler`、路径别名约定）、`tsconfig.react.json`、`tsconfig.node.json`。P0 阶段可先在根目录放单一 `tsconfig.base.json`，包数量超过 6 个时再抽包。

---

## 5. pnpm-workspace.yaml 配置

```yaml
# pnpm-workspace.yaml
packages:
  # 前端与 Node 应用：纳入 workspace
  - "apps/web"
  - "apps/admin"
  - "apps/space"
  - "apps/live"

  # 共享包：整目录纳入
  - "packages/*"

  # ★ 显式排除：Python 项目与 Nginx 项目不含 package.json，
  #   若被 glob 匹配会导致 pnpm 解析失败与 Turborepo 任务图污染
  - "!apps/api"
  - "!apps/api/**"
  - "!apps/proxy"
  - "!apps/proxy/**"

  # 排除构建产物与 Storybook 静态产物中可能出现的嵌套 package.json
  - "!**/dist/**"
  - "!**/build/**"
  - "!**/storybook-static/**"
  - "!**/node_modules/**"

# 统一强制版本（协同链路红线，见 tech-stack.md §4.1）
overrides:
  yjs: "13.6.x"
  y-protocols: "1.0.x"

# 允许构建脚本的包白名单（pnpm 10+ 默认阻止 postinstall）
onlyBuiltDependencies:
  - esbuild
  - "@tailwindcss/oxide"
  - sharp
```

### 5.1 为什么必须显式列举 apps 而不用 `apps/*`

若写成 `apps/*`，pnpm 会尝试把 `apps/api`（Python）与 `apps/proxy`（Nginx 配置）识别为 workspace 包。实际后果：

1. `apps/api` 无 `package.json`，pnpm 会静默跳过，但 `turbo` 的包发现依赖 pnpm workspace 输出，导致后续「为 api 定义根任务」时行为不一致；
2. 若为了让 `turbo` 能跑 Python 命令而在 `apps/api` 放一个空 `package.json`，则 `pnpm -r install` / `pnpm -r update` / `pnpm audit` 会把它计入统计，且 IDE 会误在其中创建 `node_modules`，污染 Python 项目结构；
3. Renovate 等工具会为其生成无意义的升级 PR。

因此采用 **白名单式显式列举 + 黑名单二次排除** 的双保险写法。这与 Plane 的做法一致，是经过验证的合理设计。

### 5.2 Python 与 JS 两套体系的协同点

两套依赖体系不共享任何解析逻辑，仅在三个层面协同：

| 协同层面 | 实现方式 |
| --- | --- |
| 本地一键启动 | `docker compose up` 统一编排；或根 `package.json` 脚本 `dev:api` 调用 `uv run --project apps/api ...`（需本地装 uv） |
| CI 流水线 | 两条独立 workflow，通过 `paths` 过滤各自触发；E2E workflow 依赖两者产物 |
| 类型契约 | api 生成 OpenAPI schema → `scripts/gen-api-types.mjs` → `packages/types/src/generated` |

### 5.3 `.npmrc` 关键配置

```ini
# .npmrc
engine-strict=true              # Node/pnpm 版本不符直接失败
shamefully-hoist=false          # 严格布局，杜绝幽灵依赖
strict-peer-dependencies=false  # React 生态 peer 声明滞后，放宽但需人工复核
resolution-mode=highest
dedupe-peer-dependents=true
auto-install-peers=true
```

---

## 6. turbo.json 构建管道设计

```jsonc
// turbo.json
{
  "$schema": "https://turbo.build/schema.json",
  "ui": "tui",

  // 全局输入：变更即令所有任务缓存失效
  "globalDependencies": [
    "pnpm-lock.yaml",
    "tsconfig.base.json",
    ".oxlintrc.json"
  ],
  "globalEnv": ["NODE_ENV", "CI"],
  // 仅参与哈希、不注入进程（避免密钥进缓存 key 之外的泄露路径）
  "globalPassThroughEnv": ["VITE_API_BASE_URL", "VITE_LIVE_BASE_URL"],

  "tasks": {
    // ── 构建：先构建所有上游依赖包，再构建自身 ─────────────
    "build": {
      "dependsOn": ["^build"],
      "outputs": ["dist/**", "build/**", ".react-router/**"],
      "env": ["VITE_*"]
    },

    // ── 类型检查：依赖上游 build（需要上游 .d.ts 产物） ────
    "typecheck": {
      "dependsOn": ["^build"],
      "outputs": ["*.tsbuildinfo"]
    },

    // ── Lint：无跨包依赖，可完全并行 ─────────────────────
    "lint": {
      "dependsOn": [],
      "outputs": []
    },

    "format:check": {
      "dependsOn": [],
      "outputs": []
    },

    // ── 单元测试：依赖上游 build，产出覆盖率 ──────────────
    "test": {
      "dependsOn": ["^build"],
      "outputs": ["coverage/**"],
      "inputs": ["src/**", "tests/**", "vitest.config.*"]
    },

    // ── 开发：长驻任务，不缓存、不持久化输出 ─────────────
    "dev": {
      "dependsOn": ["^build"],   // 首次需要上游产物；之后由各包 watch 模式接管
      "cache": false,
      "persistent": true
    },

    // ── 包的 watch 构建（供 dev 期间上游包热更新） ────────
    "dev:watch": {
      "cache": false,
      "persistent": true
    },

    // ── Storybook ──────────────────────────────────────
    "storybook": { "cache": false, "persistent": true },
    "build-storybook": {
      "dependsOn": ["^build"],
      "outputs": ["storybook-static/**"]
    },

    // ── 清理 ───────────────────────────────────────────
    "clean": { "cache": false },

    // ── 根级任务：包裹 Python 后端（不属于任何 workspace 包） ──
    "//#api:lint":     { "cache": false },
    "//#api:typecheck":{ "cache": false },
    "//#api:test":     { "cache": false },
    "//#api:migrate":  { "cache": false }
  }
}
```

### 6.1 依赖拓扑说明

`dependsOn` 中的 `^` 前缀表示「**依赖包**（upstream dependencies）的同名任务」，无前缀表示「**本包**的其他任务」。据此形成的拓扑：

| 任务 | 拓扑 | 说明 |
| --- | --- | --- |
| `build` | `^build` → self | 严格自底向上：`tailwind-config` → `types` → `ui` / `editor` / `shared-state` → `web` / `admin` / `space`。Turbo 自动按依赖图分层并在每层内最大并行 |
| `typecheck` | `^build` → self | 类型检查需要上游包已产出 `.d.ts`；不依赖上游 `typecheck`（上游类型错误会在其自身 typecheck 任务中暴露，无需串行等待） |
| `lint` | 无依赖，全并行 | OxLint 基于源码而非产物，无需等待任何构建，全仓库通常 < 2s |
| `test` | `^build` → self | 单元测试 import 上游包的编译产物 |
| `dev` | `^build` → self（persistent） | 首次启动先把 packages 构建一遍产出 `.d.ts` 供 IDE 与 Vite 解析；之后各包以 `dev:watch` 增量重建 |
| `//#api:*` | 根任务，与 JS 任务无依赖关系 | 通过 `//#` 前缀定义在根包上，实现「Python 任务纳入统一编排入口，但不进入 JS 依赖图」 |

### 6.2 根 package.json 脚本编排

```jsonc
// package.json（根）
{
  "name": "rabbit-projects",
  "private": true,
  "packageManager": "pnpm@11.0.0",
  "engines": { "node": ">=22.14 <23", "pnpm": ">=11 <12" },
  "scripts": {
    "dev":            "turbo run dev --filter=@rp/web... --filter=@rp/live...",
    "dev:all":        "turbo run dev",
    "dev:admin":      "turbo run dev --filter=@rp/admin...",
    "dev:space":      "turbo run dev --filter=@rp/space...",
    "build":          "turbo run build",
    "lint":           "turbo run lint",
    "format":         "oxfmt .",
    "typecheck":      "turbo run typecheck",
    "test":           "turbo run test",
    "test:e2e":       "playwright test",

    // 仅构建/校验受本次改动影响的包（CI 主力命令）
    "ci:affected":    "turbo run lint typecheck test build --filter=...[origin/main]",

    // Python 侧（根任务，需本地安装 uv）
    "api:lint":       "uv run --project apps/api ruff check .",
    "api:typecheck":  "uv run --project apps/api mypy plane",
    "api:test":       "uv run --project apps/api pytest",
    "api:migrate":    "uv run --project apps/api python manage.py migrate",

    // 契约同步
    "gen:api-types":  "node scripts/gen-api-types.mjs",
    "check:yjs":      "node scripts/check-yjs-version.mjs",

    "compose:up":     "docker compose -f deploy/compose/docker-compose.yml up -d",
    "compose:down":   "docker compose -f deploy/compose/docker-compose.yml down",
    "compose:logs":   "docker compose -f deploy/compose/docker-compose.yml logs -f",

    "prepare":        "husky"
  }
}
```

### 6.3 缓存策略

- **本地缓存**：`.turbo/cache`，命中时任务耗时归零。
- **远程缓存**：CI 中启用（自托管 `turborepo-remote-cache` 或 Vercel Remote Cache），使 PR 之间可复用构建产物；私有化交付场景可仅用本地缓存。
- **缓存正确性红线**：任务若读取了未声明在 `inputs` / `env` / `globalDependencies` 中的文件或环境变量，会产生「缓存命中但结果错误」的隐蔽故障。约定：**任何构建时读取环境变量的行为必须在 `env` 中声明**（Turbo 对未声明的 `VITE_*` 会告警，CI 中将告警升级为错误）。

---

## 7. 包间依赖关系图

```mermaid
graph TD
    subgraph Apps["apps/ · 应用层"]
        WEB["apps/web<br/>主工作台"]
        ADMIN["apps/admin<br/>God Mode"]
        SPACE["apps/space<br/>公开空间"]
        LIVE["apps/live<br/>实时协作 (Node)"]
        API["apps/api<br/>Django + DRF<br/>★ 非 pnpm workspace"]
        PROXY["apps/proxy<br/>Nginx<br/>★ 非 pnpm workspace"]
    end

    subgraph Packages["packages/ · 共享层"]
        UI["@rp/ui<br/>组件库"]
        EDITOR["@rp/editor<br/>TipTap 封装"]
        STATE["@rp/shared-state<br/>MobX stores"]
        TYPES["@rp/types<br/>共享类型"]
        TWCFG["@rp/tailwind-config<br/>设计 token"]
    end

    %% 应用 → 共享包
    WEB --> UI
    WEB --> EDITOR
    WEB --> STATE
    WEB --> TYPES
    WEB --> TWCFG

    ADMIN --> UI
    ADMIN --> STATE
    ADMIN --> TYPES
    ADMIN --> TWCFG

    SPACE --> UI
    SPACE --> EDITOR
    SPACE --> TYPES
    SPACE --> TWCFG

    LIVE --> TYPES
    LIVE -.->|复用 ProseMirror schema 做安全校验| EDITOR

    %% 共享包之间（严格单向，自底向上）
    UI --> TWCFG
    EDITOR --> UI
    EDITOR --> TYPES
    EDITOR --> TWCFG
    STATE --> TYPES

    %% 运行时调用（非构建依赖）
    WEB -.->|HTTP /api/v1| API
    ADMIN -.->|HTTP /api/v1| API
    SPACE -.->|HTTP /api/v1 (space 分组)| API
    WEB -.->|WebSocket| LIVE
    SPACE -.->|WebSocket (只读)| LIVE
    LIVE -.->|内部 HTTP: 鉴权校验/文档持久化| API
    PROXY -.->|反向代理入口| WEB
    PROXY -.->|反向代理入口| ADMIN
    PROXY -.->|反向代理入口| SPACE
    PROXY -.->|反向代理入口| API
    PROXY -.->|反向代理入口| LIVE

    %% 契约生成
    API ==>|OpenAPI schema 代码生成| TYPES

    classDef excluded fill:#fff4e6,stroke:#e8890c,stroke-width:2px
    classDef pkg fill:#eef6ff,stroke:#2f6feb
    class API,PROXY excluded
    class UI,EDITOR,STATE,TYPES,TWCFG pkg
```

### 7.1 依赖方向硬规则

| 规则 | 说明 | 检查手段 |
| --- | --- | --- |
| `apps/*` 不得互相依赖 | web 需要 admin 的某组件 → 把组件上移 `@rp/ui` | CI 脚本扫描 `package.json` 依赖名 |
| `packages/*` 不得依赖 `apps/*` | 反向依赖会产生构建环 | 同上 |
| 依赖层级严格自底向上 | `tailwind-config` → `types` → `ui` → `editor` / `shared-state` → apps | Turbo 检测到环会直接报错 |
| `@rp/ui` 不得依赖 `@rp/shared-state` | 组件库必须与状态方案解耦，仅接受 props | Code Review + CI 依赖白名单 |
| `@rp/types` 零运行时依赖 | 只输出 `.d.ts`，任何 runtime 依赖都是设计错误 | CI 校验其 `dependencies` 为空 |
| 业务组件不得直连 axios | 网络请求统一在 app 的 `services/` 层 | oxlint 自定义 `no-restricted-imports` |

---

## 8. 与 Plane 仓库结构的对标分析

### 8.1 一致之处（直接沿用）

| 维度 | Plane 做法 | 本项目 | 沿用理由 |
| --- | --- | --- | --- |
| 顶层二分 | `apps/` + `packages/` | 完全一致 | 应用与共享库的边界最清晰的组织方式，Turbo 过滤器书写自然 |
| 三个前端应用拆分 | `web` / `admin` / `space` | 完全一致 | 权限域物理隔离，构建与部署可独立 |
| live 独立服务 | `apps/live`（Node + Hocuspocus） | 完全一致 | 长连接与无状态 HTTP 的运维特征差异大 |
| proxy 作为 app | `apps/proxy`（Caddy） | `apps/proxy`（Nginx） | 把入口配置纳入版本管理与镜像构建，避免「配置漂在服务器上」 |
| Python 排除在 workspace 外 | 是 | 是 | **本项目认定为合理设计并明确沿用**，理由见 §5.1 |
| 自研组件库成包 | `@plane/ui` | `@rp/ui` | 三端复用 + Storybook 评审 |
| 编辑器成包 | `@plane/editor` | `@rp/editor` | 依赖体量大 + schema 必须唯一 |
| 类型成包 | `@plane/types` | `@rp/types` | 前后端契约的前端落点 |
| Django 包内分层 | `plane/app` / `plane/api` / `plane/space` / `plane/db` / `plane/bgtasks` | 完全一致 | 内部 API、Open API、公开 API 三套认证与序列化策略必须物理分离 |
| 同一镜像多 entrypoint | api / worker / beat 共用镜像 | 完全一致 | 保证任务进程与 API 进程代码零漂移 |

### 8.2 改进点（有意偏离）

| 改进点 | Plane 现状 | 本项目改进 | 收益 |
| --- | --- | --- | --- |
| 部署编排集中管理 | `docker-compose.yml` 等散落在根与若干目录 | 统一收敛到 `deploy/compose/`，区分 `local` / `prod` / `ci` 三套 | 环境差异一目了然，避免生产误用开发配置 |
| MobX store 独立成包 | store 主要位于 `apps/web` 内 | 抽出 `@rp/shared-state`，三端共享 | admin/space 可复用用户与权限 store；高价值逻辑（看板分组、筛选求值）可脱离 UI 单测 |
| 类型自动生成 | 类型多为手写 | `packages/types/src/generated` 由 OpenAPI 自动生成并在 CI 校验一致性 | 消除前后端契约漂移这一类最高频、最难排查的 bug |
| Tailwind 配置形态 | JS preset（v3 时代） | Tailwind v4 CSS-first，`@rp/tailwind-config` 导出 CSS token | 设计 token 单一来源，Storybook 与三端零配置对齐 |
| 共享编译配置 | 各包自带 tsconfig | `@rp/config` 统一 base（P1） | 消除包间编译行为漂移 |
| 版本一致性守卫 | 依赖人工注意 | `scripts/check-yjs-version.mjs` + `pnpm.overrides` 强制 | 规避「协同静默失效」这类极难定位的故障 |
| 根任务包裹 Python | Python 命令基本在仓库外单独执行 | `turbo.json` 定义 `//#api:*` 根任务 + 根 `package.json` 脚本代理 | 开发者只需记住一套入口命令，同时不污染 JS 依赖图 |
| Storybook 定位 | 有但覆盖有限 | 作为 `@rp/ui` 的强制交付物，新组件无 story 不予合并 | 组件库可评审、可回归 |

---

## 9. 环境变量管理策略

### 9.1 文件层级与加载优先级

```
project-root/
├── .env.example                 # ★ 唯一模板，提交入库，含全部变量与注释说明（值为占位符）
├── .env                         # 本地实际值，★ .gitignore，供 docker compose 读取
├── apps/web/.env.example        # web 专属变量模板（仅 VITE_* 客户端变量）
├── apps/web/.env.local          # 本地覆盖，★ .gitignore（Vite 优先级最高）
├── apps/admin/.env.example
├── apps/space/.env.example
├── apps/live/.env.example       # live 专属（含 API_INTERNAL_URL、JWT 公钥）
└── apps/api/.env.example        # Django 专属（含 SECRET_KEY、DB、broker、S3）
```

加载优先级（后者覆盖前者）：

1. **前端（Vite）**：`.env` → `.env.[mode]` → `.env.local` → `.env.[mode].local` → 进程环境变量。仅 `VITE_` 前缀变量注入客户端 bundle。
2. **后端（Django）**：`django-environ` 读取 `apps/api/.env` → 被容器环境变量覆盖 → 生产以容器环境变量/Secret 为唯一来源，**不放 `.env` 文件**。
3. **live（Node）**：`dotenv` 读取 `apps/live/.env`，容器环境变量覆盖；启动时用 zod schema 校验，缺失必填项直接退出（fail fast，避免带着错配置半运行）。
4. **Docker Compose**：读取根 `.env` 做变量插值，通过 `environment` / `env_file` 下发到各服务。

### 9.2 变量命名与分类规范

| 前缀 / 分类 | 归属 | 是否暴露到浏览器 | 示例 |
| --- | --- | --- | --- |
| `VITE_*` | 三个前端 app | **是（明文进 bundle）** | `VITE_API_BASE_URL`、`VITE_LIVE_BASE_URL`、`VITE_SENTRY_DSN` |
| 无前缀（Django） | apps/api | 否 | `SECRET_KEY`、`DATABASE_URL`、`CELERY_BROKER_URL`、`REDIS_URL` |
| `AWS_*` / `MINIO_*` | apps/api | 否 | `AWS_S3_ENDPOINT_URL`、`AWS_ACCESS_KEY_ID`、`AWS_S3_BUCKET_NAME` |
| `LIVE_*` / `API_INTERNAL_*` | apps/live | 否 | `LIVE_PORT`、`API_INTERNAL_URL`、`LIVE_JWT_PUBLIC_KEY` |
| `NGINX_*` / `*_UPSTREAM` | apps/proxy | 否 | `NGINX_SERVER_NAME`、`API_UPSTREAM`、`WEB_UPSTREAM` |
| `POSTGRES_*` / `RABBITMQ_*` | 基础设施容器 | 否 | `POSTGRES_PASSWORD`、`RABBITMQ_DEFAULT_USER` |

**红线规则**：任何密钥、令牌、私钥**绝不允许**使用 `VITE_` 前缀。`VITE_*` 变量在构建时被静态替换进 JS 产物，等同于公开发布。CI 中加入扫描：`VITE_` 变量名若包含 `SECRET|KEY|TOKEN|PASSWORD`（`PUBLIC_KEY` 除外）直接阻断。

### 9.3 `.env.example` 结构约定

根 `.env.example` 按服务分区，每个变量三行式（说明 / 必填性与默认值 / 赋值）：

```bash
# ============================================================
# 通用
# ============================================================
# 部署环境：development | staging | production
# 必填，默认 development
NODE_ENV=development

# 对外访问的完整站点地址（proxy 入口），用于邮件链接与 CORS 白名单
# 必填
APP_BASE_URL=http://localhost

# ============================================================
# apps/api · Django
# ============================================================
# Django 密钥，生产必须为 50+ 位随机串，禁止复用示例值
# 必填
SECRET_KEY=change-me-in-production

# PostgreSQL 连接串
# 必填
DATABASE_URL=postgresql://rp:rp@db:5432/rabbit_projects

# Celery broker（RabbitMQ，AMQP 协议）
# 必填
CELERY_BROKER_URL=amqp://rp:rp@mq:5672//

# 缓存 / session / Celery result backend（Valkey）
# 必填
REDIS_URL=redis://redis:6379/0

# ...（其余分区略，实际文件需覆盖全部变量）
```

### 9.4 校验与治理

| 措施 | 实现 |
| --- | --- |
| 缺失变量早失败 | live 用 zod、api 用 `django-environ` 的必填声明，启动即校验；缺失则退出并打印缺失清单 |
| 模板与实现同步 | CI 脚本对比 `.env.example` 键集合与代码中实际读取的键集合，不一致则失败（防止「加了变量忘记更新模板」） |
| 生产密钥管理 | 不使用 `.env` 文件；由 Docker Secret / K8s Secret / 外部 Secret Manager 注入 |
| 密钥轮换 | `SECRET_KEY` 与 `LIVE_JWT_*` 支持新旧双密钥并存窗口（先加新、再切签发、后移旧），避免轮换导致全员掉线 |
| 泄露防护 | `.gitignore` 覆盖 `.env`、`.env.*.local`、`!*.example`；pre-commit 接入 secret 扫描 |

---

## 10. 关键工作流示例

### 10.1 新建一个共享组件

```
1. 在 packages/ui/src/components/ 新建组件（仅 props，无业务语义、无网络请求）
2. 在 packages/ui/src/index.ts 追加 named export
3. 在 packages/ui/stories/ 补 story（★ 无 story 不予合并）
4. pnpm --filter @rp/ui build     # 产出 .d.ts，供下游 IDE 与构建解析
5. 在 apps/web 中 import { Xxx } from "@rp/ui" 使用
6. pnpm ci:affected               # 只校验受影响的包
```

### 10.2 后端模型变更并同步前端类型

```
1. 修改 apps/api/plane/db/models/xxx.py
2. pnpm api:migrate 前先 uv run --project apps/api python manage.py makemigrations
3. 提交生成的 migration 文件（★ 禁止手改已合并的历史 migration）
4. 更新对应 serializer 与 OpenAPI 注解
5. pnpm gen:api-types             # 重新生成 packages/types/src/generated
6. pnpm typecheck                 # 前端因契约变化产生的类型错误会在此暴露
```

### 10.3 本地全栈启动

```
1. cp .env.example .env && 按需修改
2. pnpm i                                 # 仅安装 JS 依赖（api 不在 workspace 内）
3. pnpm compose:up                        # 起 db / redis / mq / minio / api / worker / beat / live / proxy
4. pnpm dev                               # 本机跑 web + live 的 HMR（覆盖容器内对应服务，可选）
5. 访问 http://localhost（proxy 入口）
```

---

## 11. 变更记录

| 日期 | 版本 | 变更内容 | 责任人 |
| --- | --- | --- | --- |
| 2026-08-31 | 1.0 | 初版：确立 apps/ + packages/ 结构、workspace 与 Turbo 管道、依赖方向规则、环境变量策略，完成与 Plane 仓库结构的对标 | 架构组 |
