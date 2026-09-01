# 技术栈确认与版本锁定

| 元信息项 | 内容 |
| --- | --- |
| 文档编号 | ARCH-001 |
| 所属层级 | 跨迭代架构决策（Cross-Iteration Architecture Decision） |
| 文档状态 | 已确认（Approved）· 变更需走 ADR 流程 |
| 最后更新日期 | 2026-08-31 |
| 适用范围 | 全仓库（apps/* 与 packages/* 全部子项目），P0 ~ P4 全部迭代 |
| 上游依据 | `docs/需求文档.md` §1.2 技术栈固定、§五 核心技术能力与约束、§8.3 POC 范围 |
| 对标基线 | Plane 开源版 master 分支（https://github.com/makeplane/plane） |
| 关联文档 | `monorepo-structure.md`（仓库结构）、`api-conventions.md`（API 规范） |

---

## 1. 版本锁定原则

本文档是全仓库依赖版本的**唯一权威来源**。任何新增依赖或版本变更，必须先修改本文档，再修改 `package.json` / `pyproject.toml` / `docker-compose.yml`。

### 1.1 锁定粒度约定

| 生态 | 声明方式 | 锁文件（唯一事实来源） | 说明 |
| --- | --- | --- | --- |
| npm（前端 / live） | `^` 允许 minor 升级，框架级依赖（react、react-router、vite）用 `~` 仅允许 patch | `pnpm-lock.yaml`（提交入库） | 本文档记录已验证的基线 minor 版本；实际安装的 patch 号以 lockfile 为准 |
| Python（api） | `pyproject.toml` 内 `>=x.y,<x.(y+1)` 区间约束 | `uv.lock`（提交入库） | 禁止裸 `install package` 直接改环境，必须走 `uv add` |
| 容器镜像 | 生产 `image:tag@sha256:digest`，本地开发 `image:tag` | `docker-compose.yml` / `docker-compose.prod.yml` | 禁止使用 `latest` |
| Node / Python 运行时 | `.nvmrc`、`package.json#engines`、`.python-version` | — | CI 与本地必须一致，否则 CI 直接 fail |

### 1.2 硬性约束

1. **单一版本原则**：同一依赖在整个 monorepo 中只允许存在一个 major.minor 版本。`pnpm dedupe --check` 纳入 CI 门禁，出现重复版本即失败。
2. **禁止幽灵依赖**：所有 import 的包必须在该子包自身 `package.json` 中显式声明。pnpm 默认严格 node_modules 布局，禁止开启 `shamefully-hoist`。
3. **零隐式 any**：TypeScript 全仓库 `strict: true`，禁止 `// @ts-ignore`（如必须使用，改为 `@ts-expect-error` 并附原因注释）。
4. **后端全量类型注解**：所有函数签名带类型注解，`mypy` 以 `--strict` 之外的渐进模式接入（先 `disallow_untyped_defs`）。
5. **升级窗口**：依赖升级只允许在每个迭代的第 1 周合并（迭代冻结期禁止升级），安全补丁（CVE ≥ High）不受此限制，可随时热修。

---

## 2. 前端技术栈

三个前端应用（`apps/web`、`apps/admin`、`apps/space`）共享同一套技术栈与版本，通过 `packages/*` 复用。

| 技术 | 版本 | 用途 | 选型理由 |
| --- | --- | --- | --- |
| React | `19.1.x` | UI 框架，所有前端应用的渲染基础 | 与 Plane 保持一致；React 19 提供稳定的 `useOptimistic`（看板拖拽乐观更新直接受益）、`use` hook、Actions、以及自动批处理；生态成熟度最高，团队上手成本最低 |
| React Router | `7.x`（Framework Mode） | 客户端路由 + 数据加载 + 嵌套布局，Vite 构建 | 需求文档明确要求 v7；Framework Mode 提供 loader/action 数据契约、类型安全路由（`typegen`）、嵌套路由与布局，能力对齐 Next.js App Router 但**不绑定 Vercel 运行时**，可纯 SPA 部署到 Nginx 静态目录，契合私有化部署要求 |
| Vite | `6.3.x` | 前端构建工具与 dev server | HMR 毫秒级，冷启动基于 esbuild 预打包；Rollup 生产构建产物可控；React Router v7 官方一等公民集成；Monorepo 下对 workspace 源码链接支持良好（无需先 build packages 即可 dev） |
| TypeScript | `5.8.x` | 全量静态类型 | 需求文档要求「零隐式 any」；5.x 的 `satisfies`、const 类型参数、`isolatedDeclarations` 提升 monorepo 包构建速度 |
| MobX | `6.13.x` | 领域状态管理（observable store） | 与 Plane 一致；面向对象的 store 树天然匹配「工作空间 → 项目 → 任务」的领域模型；细粒度依赖追踪使看板万级卡片局部更新无需手写 memo；相较 Redux 样板代码极少 |
| mobx-react-lite | `4.1.x` | React 绑定（`observer`） | 函数组件场景下比 `mobx-react` 更轻（不含 class 组件支持）；本项目全函数组件 |
| mobx-react | `9.2.x` | React 绑定（兼容 class 组件 / `Provider`） | 仅在需要 class ErrorBoundary + observer 的少数场景引入；与 Plane 依赖保持一致 |
| SWR | `2.3.x` | 远程数据获取与缓存 | 与 Plane 一致；`stale-while-revalidate` 策略让切换视图秒开；内置去重、焦点重验证、`mutate` 乐观更新，与 MobX 分工明确（见 §2.1） |
| Axios | `1.8.x` | HTTP 客户端（SWR fetcher 底座） | 需求文档 §1.2 明确「SWR + Axios」；拦截器统一注入 CSRF token / `X-API-Key`、统一解包 `{status,data,meta}` 响应体、统一映射错误码（见 `api-conventions.md`） |
| TailwindCSS | `4.1.x` | 原子化 CSS | 与 Plane 一致；v4 的 Oxide 引擎（Rust）构建提速数量级，CSS-first 配置（`@theme`）替代 JS config，天然适配设计 token；无运行时开销，产物体积随实际使用裁剪 |
| @tailwindcss/vite | `4.1.x` | Tailwind v4 的 Vite 插件 | v4 推荐接入方式，替代 PostCSS 链路，减少一层构建开销 |
| Headless UI | `2.2.x` | 无样式可访问组件（Dialog/Menu/Combobox/Popover） | 与 Plane 一致；提供完整键盘导航与 ARIA 语义，作为自研 `@rp/ui` 的可访问性底座，样式 100% 由 Tailwind 掌控，不与设计系统冲突 |
| lucide-react | `0.5xx.x` | 图标库 | 与 Plane 一致；Tree-shakable，线性风格统一，1500+ 图标覆盖项目管理全场景；按需引入不影响首屏体积 |
| TipTap | `2.14.x`（`@tiptap/core`、`starter-kit`、各 extension） | 富文本编辑器内核（基于 ProseMirror） | 与 Plane 一致；ProseMirror 的文档模型是结构化 schema（非 HTML 字符串），是接入 Yjs CRDT 协同的**前提条件**；扩展体系支撑 slash 命令、@提及、任务清单、代码块、表格、图片上传 |
| ProseMirror（`prosemirror-*`） | 随 TipTap 传递依赖 | 编辑器底层视图/状态/事务模型 | 不直接声明，由 TipTap 统一约束版本；仅在自定义 NodeView / 插件时直接 import |
| y-prosemirror | `1.3.x` | ProseMirror ↔ Yjs 桥接 | 将 ProseMirror 事务映射为 Yjs 更新，实现多人并发编辑无冲突 + 光标/选区共享（`ySyncPlugin`、`yCursorPlugin`、`yUndoPlugin`） |
| yjs | `13.6.x` | 前端侧 CRDT 文档 | 与 live 服务共用同一版本，**必须严格一致**，否则更新协议不兼容 |
| @hocuspocus/provider | `2.15.x` | 前端 WebSocket 协同 provider | 与 live 服务的 Hocuspocus server 配对，负责鉴权握手、断线重连、离线队列 |
| @atlaskit/pragmatic-drag-and-drop | `1.7.x` | 看板 / 列表 / 甘特图拖拽 | 与 Plane 一致；Atlassian 出品（Jira 看板同源方案），基于原生 HTML5 DnD，性能与可访问性最佳；相较 react-beautiful-dnd（已停止维护）支持虚拟滚动容器内拖拽 |
| @atlaskit/pragmatic-drag-and-drop-hitbox | `1.0.x` | 拖拽落点边缘检测 | 计算「插入到卡片前/后」的边缘位置，实现精准排序 |
| @atlaskit/pragmatic-drag-and-drop-auto-scroll | `2.1.x` | 拖拽时容器自动滚动 | 长看板列拖拽必备 |
| @tanstack/react-table | `8.21.x` | 数据表格（表格视图、成员管理、工时台账） | 与 Plane 一致；Headless 架构，仅提供状态与逻辑（排序/分组/列固定/列宽），渲染完全自主，可与虚拟滚动组合 |
| @tanstack/react-virtual | `3.13.x` | 虚拟滚动 | 任务列表/表格万级行渲染保持 60fps；与 react-table 同作者，API 心智一致 |
| recharts | `2.15.x` | 图表可视化（燃尽图、迭代速率、累积流图、团队负载） | 与 Plane 一致；声明式 React 组件模型，SVG 渲染便于主题化与导出；报表模块所需图表类型全覆盖 |
| Framer Motion（`motion`） | `12.x` | 动画与微交互 | 抽屉/弹窗进出场、看板卡片 layout 动画（`layoutId` 实现拖拽落位平滑过渡）、列表 stagger；声明式 API 优于手写 CSS keyframes 的组合场景 |
| cmdk | `1.1.x` | 命令面板（⌘K 全局搜索与快捷操作） | 与 Plane 一致；内置模糊匹配与键盘导航，可访问性完善；作为全局导航与「快速创建任务」的统一入口 |
| Zod | `3.25.x` | 运行时数据校验 | 表单校验 + API 响应边界校验双用途；`z.infer` 让类型与校验规则单一来源，避免类型与运行时校验漂移；与 react-hook-form 通过 resolver 集成 |
| react-hook-form | `7.5x.x` | 表单状态管理 | 非受控模式减少重渲染，天然适配大表单（自定义字段可达数十项）；与 Zod resolver 组合 |
| i18next | `25.x` | 国际化运行时 | 与 Plane 一致（Plane 自研 `@plane/i18n` 亦基于同类方案）；命名空间按模块拆分实现按需加载，支持复数、插值、日期本地化 |
| react-i18next | `15.x` | i18next 的 React 绑定 | `useTranslation` / `Trans` 组件，支持组件内插值 |
| swr + mobx 协同层 | 自研（`packages/shared-state`） | 数据流编排 | 见 §2.1 |
| date-fns | `4.1.x` | 日期计算与格式化 | 甘特图/迭代/工时大量日期运算；Tree-shakable，不可变 API，体积远小于 moment |
| clsx + tailwind-merge | `2.1.x` / `3.x` | className 合并 | 组件库变体样式合并的事实标准（`cn()` 工具函数） |
| Storybook | `8.x` | `@rp/ui` 组件文档与视觉回归 | 组件库独立开发与评审载体，Vite builder 与主应用共享构建配置 |

### 2.1 SWR 与 MobX 的职责边界（架构约束）

两套状态方案并存必须有明确边界，否则会产生双份真源。约定如下：

| 关注点 | 归属 | 说明 |
| --- | --- | --- |
| 服务端资源的获取、缓存、重验证、去重 | **SWR** | 所有 `GET` 请求经 SWR，key 为标准化 URL |
| 领域实体的规范化存储（byId map）与派生计算 | **MobX store** | SWR 拿到数据后写入 store，组件只读 store |
| 乐观更新与回滚 | **MobX store 发起，SWR `mutate` 兜底重验证** | store 先改本地，API 失败则回滚并 toast |
| UI 局部状态（弹窗开合、筛选草稿） | React `useState` | 不进 store，避免 store 膨胀 |
| 跨页面共享的视图配置（筛选/分组/排序/布局） | **MobX store + 服务端持久化** | 用户级视图偏好落库 |
| 实时推送（WebSocket）到达的增量 | **MobX store 直接 patch** | 不经 SWR，避免整列表重拉 |

---

## 3. 后端技术栈

| 技术 | 版本 | 用途 | 选型理由 |
| --- | --- | --- | --- |
| Python | `3.12+`（锁定 `3.12.x`，CI 矩阵含 3.13 前瞻） | 后端运行时 | 需求文档要求 3.8+，实际锁定 3.12：性能较 3.8 提升约 25%（专用自适应解释器）、错误信息定位精确、`typing` 支持 PEP 695 泛型语法，利于「全量类型注解」目标 |
| Django | `5.1.x` | Web 框架（ORM、migrations、middleware、admin） | 与 Plane 一致；ORM + migrations 是「数据库表自动迁移」需求的直接支撑；成熟的权限与 session 体系为三级 RBAC 打底；`transaction.atomic` 保证拖拽/状态流转的一致性 |
| Django REST Framework | `3.15.x` | RESTful API 层（Serializer / ViewSet / Permission / Throttle） | 与 Plane 一致；ViewSet + Router 生成层级嵌套路由，Serializer 承载校验与序列化，内置 throttling 直接满足限流要求（见 `api-conventions.md`） |
| django-cors-headers | `4.7.x` | CORS 策略 | 三个前端应用同源部署于 proxy 之后，但本地开发跨端口，需精确白名单 |
| django-filter | `25.x` | 查询参数筛选 | 与 DRF `DjangoFilterBackend` 集成，声明式实现 `?status=&priority=` 等值筛选与范围筛选 |
| Celery | `5.4.x` | 异步任务队列（worker + beat 双进程） | 与 Plane 一致；worker 处理通知推送、文件后处理、Webhook 投递、导入导出；beat 处理截止提醒扫描、审批超时、报表预聚合、自动备份 |
| django-celery-beat | `2.7.x` | 数据库驱动的周期任务调度 | 周期任务可通过 admin 动态增删改，无需重启 beat；支持按团队维度配置提醒策略 |
| kombu | 随 Celery 传递依赖 | AMQP 消息传输层 | 由 Celery 统一约束版本 |
| RabbitMQ | `3.13.x`（镜像 `rabbitmq:3.13-management-alpine`） | 消息队列（Celery broker） | 需求文档明确要求；相较 Redis 作为 broker：支持消息持久化 + publisher confirm + 手动 ack + 死信队列（DLX），任务不会因 broker 重启丢失；管理插件提供队列堆积可视化，便于容量规划 |
| PostgreSQL | `15.7`（镜像 `postgres:15.7-alpine`） | 关系数据库 | 与 Plane 生产镜像一致；`JSONB` + GIN 索引是「动态自定义字段免改表 + 全字段筛选」方案的技术基础；CTE 递归查询支撑子任务多层级树与任务依赖图；`pg_trgm` 支撑模糊搜索；`tsvector` 支撑全文检索 |
| psycopg（binary） | `3.2.x` | PostgreSQL 驱动 | Django 5 首选 psycopg 3；支持服务端游标与 pipeline 模式，性能优于 psycopg2 |
| Redis / Valkey | `7.2.x`（镜像 `valkey/valkey:7.2-alpine`） | 缓存、session 存储、限流计数器、分布式锁 | 需求文档要求 Redis 6.2+ / Valkey 7.x 兼容；Valkey 为 Redis 的 BSD 许可分叉，规避 RSAL 许可风险，协议 100% 兼容；承担 DRF throttle 计数、热点数据缓存、Django session backend、Celery result backend |
| django-redis | `5.4.x` | Django 缓存后端 | 提供 `CACHES` 后端与原子操作封装 |
| MinIO | 镜像 `RELEASE.2025-xx-xx`（生产按 digest 锁定） | S3 兼容对象存储 | 需求文档要求；预签名 URL 实现浏览器直传，文件不经 Django 中转，节省带宽与内存；生产可无缝切换 AWS S3 / 阿里云 OSS（S3 兼容协议） |
| boto3 | `1.3x.x` | S3 客户端（生成预签名 URL、生命周期策略） | 官方 SDK，MinIO 与 S3 通用 |
| django-storages | `1.14.x` | Django 存储后端抽象 | 统一本地/MinIO/S3 三种后端，切换仅改环境变量 |
| Pillow | `11.x` | 图片处理（头像裁剪、缩略图、水印） | 企业版文件水印能力依赖 |
| PyJWT | `2.10.x` | JWT 签发与校验（live 服务鉴权票据、OAuth id_token） | live 服务需校验 Django 签发的短时效协同票据 |
| argon2-cffi | `23.x` | 密码哈希 | Django 密码哈希器优先使用 Argon2id（优于默认 PBKDF2），满足「密码加密存储」与企业合规要求 |
| gunicorn | `23.x` | WSGI 生产服务器 | 与 Plane 一致；多 worker 进程模型，配合 `gthread` 应对 IO 密集场景 |
| uvicorn + ASGI（预留） | `0.3x.x` | 异步端点承载（SSE 通知流） | P2 阶段按需启用，仅用于长连接端点 |
| whitenoise | `6.8.x` | Django 静态文件服务（admin 静态资源） | 避免为 Django admin 静态资源单独配置 Nginx 规则 |
| python-dotenv / django-environ | `1.x` / `0.12.x` | 环境变量解析 | 统一 `.env` 读取与类型转换 |
| sentry-sdk | `2.x` | 错误监控（可选，私有化部署可指向自托管 Sentry） | 全局异常捕获落地到可观测平台 |
| structlog | `24.x` | 结构化日志（JSON 输出） | 日志持久化与合规审计要求，便于 ELK/Loki 采集 |
| drf-spectacular | `0.28.x` | OpenAPI 3 文档自动生成 | Open API 能力（企业版）与前端类型生成的上游 |

### 3.1 认证方案说明

需求文档明确「Django 自研认证（Session + Token + OAuth），不使用 NextAuth」。因此**不引入** `django-allauth`、`djangorestframework-simplejwt` 等成套方案，而是自研：

- Session 认证：复用 Django `contrib.sessions`（backend = Redis），供 web/admin/space 三个前端使用，配合 CSRF 双提交 cookie。
- Token 认证：自研 `APIToken` 模型（前缀可识别 + 仅存哈希 + 作用域 + 过期时间），通过 `X-API-Key` 头传递。
- OAuth 2.0：自研 Authorization Code + PKCE 授权服务端，供第三方应用与集成市场使用。
- SSO / LDAP / SCIM（P3-P4）：以独立 app 形式接入，不影响核心认证链路。

---

## 4. 实时协作服务（apps/live）

| 技术 | 版本 | 用途 | 选型理由 |
| --- | --- | --- | --- |
| Node.js | `22.x LTS`（锁定 `22.14.x`，`.nvmrc` 统一） | live 服务运行时 | 需求文档要求 20+，锁定 22 LTS：原生 WebSocket 客户端、`node:test`、性能与内存表现更佳，维护周期至 2027 |
| Express | `4.21.x` | HTTP 服务框架（健康检查、鉴权回调、WS upgrade 挂载点） | 与 Plane 一致；生态与中间件最成熟；本服务 HTTP 面极薄，无需 Nest/Fastify 的额外抽象。**暂不升 5.x**，等待 Hocuspocus 生态验证 |
| Hocuspocus（`@hocuspocus/server`） | `2.15.x` | Yjs CRDT 协作后端 | 与 Plane 一致；提供房间管理、`onAuthenticate` 鉴权钩子、`onStoreDocument` 持久化钩子（防抖批量落库）、`onLoadDocument` 加载钩子；扩展式架构便于接入 Redis 扩展做多实例水平扩容 |
| @hocuspocus/extension-database | `2.15.x` | 文档持久化扩展 | 将 Yjs 二进制状态写入 PostgreSQL（经 api 内部接口或直连只写表） |
| @hocuspocus/extension-redis | `2.15.x` | 多实例房间广播 | 水平扩容时保证同一文档的不同连接跨实例同步（P2 启用） |
| Yjs | `13.6.x`（与前端**严格同版本**） | CRDT 实时协同引擎 | 与 Plane 一致；无中心冲突解决，离线编辑后可自动合并；二进制更新体积小；`Y.Doc` 子文档能力支撑「一个任务描述 + 多个评论」的复合协同 |
| y-prosemirror | `1.3.x` | ProseMirror ↔ Yjs 桥接 | 服务端用于文档快照转换与 schema 校验（防恶意客户端注入非法节点） |
| y-protocols | `1.0.x` | 同步与感知（awareness）协议 | 在线成员列表、协作光标 |
| ws | `8.18.x` | WebSocket 实现 | Hocuspocus 底层依赖，显式声明以统一版本 |
| TypeScript + tsup | `5.8.x` / `8.x` | 构建为单文件 ESM 产物 | 容器镜像仅需 Node 运行时 + 产物，镜像体积可控 |
| pino | `9.x` | 结构化日志 | 与后端 structlog 输出格式对齐，统一采集 |

### 4.1 版本一致性红线

`yjs`、`y-prosemirror`、`y-protocols` 三者在 `apps/web`、`packages/editor`、`apps/live` 中**必须完全同版本**。实现手段：在根 `package.json` 中声明 `pnpm.overrides`，并由 CI 脚本 `scripts/check-yjs-version.mjs` 校验。多版本 Yjs 共存会导致 `Y.Doc` 实例互不识别，表现为「协同静默失效」，排查成本极高。

---

## 5. 工程工具链

| 技术 | 版本 | 用途 | 选型理由 |
| --- | --- | --- | --- |
| pnpm | `11.x`（`packageManager` 字段锁定 `pnpm@11.x.y`） | 包管理器 + workspace | 与 Plane 一致；硬链接 + 内容寻址存储节省磁盘与安装时间；严格 node_modules 布局杜绝幽灵依赖；`workspace:*` 协议实现包间源码级链接 |
| Turborepo | `2.5.x` | Monorepo 任务编排 | 与 Plane 一致；基于任务依赖拓扑的并行调度 + 本地/远程增量缓存，未变更包直接命中缓存；`--filter` 支持按受影响包精确构建，CI 时间随改动量而非仓库规模增长 |
| Docker | `27.x+` | 容器化 | 全套服务本地一键起停，POC 交付硬性要求 |
| Docker Compose | `v2.3x+`（Compose Spec） | 多服务编排 | 编排 web/admin/space/api/worker/beat/live/db/redis/mq/minio/proxy 共 11 个服务；`profiles` 区分本地/生产；`healthcheck` + `depends_on: condition` 保证启动顺序 |
| Nginx | `1.27.x`（镜像 `nginx:1.27-alpine`） | 反向代理（apps/proxy） | **需求文档明确要求**，与 Plane 的 Caddy 不同（差异说明见 §6.2）；统一路由 web / admin / space / api / live，处理 WebSocket upgrade、gzip/brotli、静态资源缓存、上传体积限制 |
| OxLint | `1.x` | 前端 Lint | 与 Plane 一致；Rust 实现，较 ESLint 快 50-100 倍，全仓库 lint 秒级完成；内置 500+ 规则覆盖 ESLint / typescript-eslint / react-hooks 核心集 |
| oxfmt | `0.x`（随 oxc 发布节奏） | 前端格式化 | 与 Plane 一致；替代 Prettier，与 OxLint 同源无规则冲突，格式化速度数量级提升 |
| Husky | `9.1.x` | Git hooks | `pre-commit` 跑 lint-staged，`commit-msg` 校验 Conventional Commits，`pre-push` 跑受影响包的类型检查 |
| lint-staged | `15.x` | 增量校验 | 仅对 staged 文件执行 oxlint/oxfmt，提交无感 |
| commitlint | `19.x` | 提交信息规范 | Conventional Commits，为自动生成 CHANGELOG 与语义化版本铺路 |
| ruff | `0.9.x` | 后端 Lint + 格式化 | Rust 实现，一次性替代 flake8 + isort + black + pyupgrade；与前端 oxc 工具链哲学一致（快、单一工具） |
| mypy | `1.15.x` | 后端静态类型检查 | 支撑「后端 Python 全量类型注解」约束 |
| pytest | `8.3.x` | 后端测试框架 | 参数化、fixture、插件生态完善 |
| pytest-django | `4.9.x` | Django 集成（DB fixture、`django_db` 标记） | 复用 Django TestCase 的事务回滚机制，测试互相隔离 |
| Django TestCase | 随 Django | 集成测试基类 | 需求文档指定；用于涉及 migrations、信号、事务的场景 |
| pytest-cov | `6.x` | 覆盖率 | 核心模块（权限、工作流、任务）行覆盖门禁 ≥ 80% |
| factory-boy | `3.3.x` | 测试数据工厂 | 替代 fixture JSON，构造复杂关联对象（工作空间→项目→任务→评论） |
| Playwright | `1.5x.x` | E2E 测试 | 与 Plane 一致；跨浏览器、自动等待、trace viewer 便于排查；覆盖「建项目→建任务→拖看板」核心闭环与多人协同双开页面场景 |
| Vitest | `3.x` | 前端单元测试 | 与 Vite 共享配置与转换链路，无需二次配置；用于工具函数、MobX store、hooks |
| @testing-library/react | `16.x` | 组件测试 | 面向用户行为的查询 API，避免测试实现细节 |
| GitHub Actions | — | CI/CD | 与 Turborepo 远程缓存集成；矩阵作业分别跑 前端 lint/test/build、后端 pytest、E2E |
| Renovate | — | 依赖升级机器人 | 按 §1.2 升级窗口配置调度，自动开 PR 并触发全量 CI |

---

## 6. 对标 Plane 开源版选型对比表

### 6.1 逐项对比

| 维度 | Plane 开源版（master） | 本系统 | 一致 / 差异 | 理由 |
| --- | --- | --- | --- | --- |
| Monorepo 工具 | pnpm workspace + Turborepo | pnpm 11 + Turborepo 2.5 | ✅ 一致 | 直接沿用被验证的组合 |
| 前端应用拆分 | `web` / `admin` / `space` / `live` | 完全相同 | ✅ 一致 | 三端职责边界清晰，权限域天然隔离 |
| 路由框架 | React Router **8.3**（最新版） | React Router **v7** | ⚠️ 差异 | **需求文档明确要求 v7**。v7 是 Framework Mode 的首个稳定大版本，社区文档、模板、第三方适配最完整；v8 引入的 breaking change（RSC 相关）对本项目无收益，且生态适配尚在追赶。锁 v7 降低 POC 阶段风险，v8 迁移列入 P4 技术债 |
| 构建工具 | Vite | Vite 6 | ✅ 一致 | — |
| 语言 | TypeScript | TypeScript 5.8 | ✅ 一致 | — |
| UI 框架 | React 19 | React 19.1 | ✅ 一致 | — |
| 状态管理 | MobX + mobx-react | 同 + mobx-react-lite | ✅ 基本一致 | 增加 lite 版减少函数组件场景的包体积 |
| 数据请求 | SWR + Axios | 同 | ✅ 一致 | — |
| 样式 | Tailwind CSS + Headless UI | Tailwind **4** + Headless UI 2 | ✅ 一致（版本更新） | Tailwind 4 的 Oxide 引擎显著提速；采用 CSS-first `@theme` 配置 |
| 组件库 | 自研 `@plane/ui` | 自研 `@rp/ui`（同模式） | ✅ 一致 | 复刻其「packages 内自研组件库 + Storybook」的做法 |
| 图标 | lucide-react | 同 | ✅ 一致 | — |
| 编辑器 | 自研 `@plane/editor`（TipTap/ProseMirror） | 自研 `@rp/editor`（同模式） | ✅ 一致 | 编辑器必须独立成包，供 web / space 复用且与 Yjs 强耦合 |
| 协同 | Hocuspocus + Yjs + y-prosemirror | 同 | ✅ 一致 | — |
| 拖拽 | @atlaskit/pragmatic-drag-and-drop | 同 | ✅ 一致 | — |
| 表格 | TanStack Table | 同 + TanStack Virtual | ✅ 一致（增强） | 显式引入虚拟滚动应对万级任务 |
| 图表 | recharts | 同 | ✅ 一致 | — |
| 命令面板 | cmdk | 同 | ✅ 一致 | — |
| 国际化 | 自研 `@plane/i18n` | i18next + react-i18next + 自研 `@rp/i18n` 薄封装 | ⚠️ 轻微差异 | 直接采用 i18next 生态，减少自研成本；仅在其上做命名空间约定与类型生成 |
| 动画 | 少量 CSS / framer-motion | Framer Motion 12（明确纳入） | ⚠️ 差异（增强） | 看板拖拽落位、抽屉转场需要 layout 动画，统一由 Motion 承载 |
| 运行时校验 | Zod（部分场景） | Zod 3（全 API 边界 + 全表单） | ⚠️ 差异（增强） | 自定义字段动态 schema 需要运行时构造校验规则，Zod 是刚需 |
| 后端框架 | Django + DRF | 同（Django 5.1 + DRF 3.15） | ✅ 一致 | — |
| 异步任务 | Celery + Redis（默认） / RabbitMQ | Celery + **RabbitMQ**（唯一 broker） | ⚠️ 差异 | **需求文档明确要求**。RabbitMQ 提供持久化、publisher confirm、手动 ack、死信队列，审批超时/通知投递等任务不可丢；Redis 作为 broker 在实例重启时有丢消息窗口。Redis 仅保留为 cache + result backend |
| 数据库 | PostgreSQL 15.7（生产镜像） | 同 | ✅ 一致 | — |
| 缓存 | Redis / Valkey | Valkey 7.2 | ✅ 一致 | 明确采用 BSD 许可的 Valkey，规避许可风险 |
| 对象存储 | MinIO（S3 兼容） | 同 | ✅ 一致 | — |
| 反向代理 | **Caddy** | **Nginx** | ⚠️ 差异 | **需求文档明确要求 Nginx**。理由：①企业私有化交付场景运维团队对 Nginx 熟悉度与既有配置资产远高于 Caddy；②WebSocket、大文件上传、限流、灰度分流的配置范式成熟且案例充足；③证书管理在企业内网多由既有网关/ACME 流程统一处理，Caddy 的自动 HTTPS 优势无法发挥。代价：需手写证书续期与配置模板，由 `apps/proxy` 内的模板 + entrypoint 变量替换解决 |
| Python 项目与 workspace 关系 | **排除在 pnpm workspace 之外** | 同（明确排除 `apps/api`） | ✅ 一致 | **这是合理设计**：Python 项目不含 `package.json`，纳入 workspace 会导致 pnpm 试图解析其依赖、Turborepo 任务图混入无意义节点、`pnpm -r` 误伤。正确做法是 workspace 只管 JS/TS 包，Python 由 `uv` 独立管理，二者在 Docker Compose 与 Turborepo 的 `//#` 根任务层面协同 |
| Lint / 格式化 | OxLint + oxfmt | 同 | ✅ 一致 | — |
| Git hooks | Husky | 同 + lint-staged + commitlint | ✅ 一致（增强） | — |
| 后端测试 | Django TestCase | pytest + pytest-django + Django TestCase | ⚠️ 差异（增强） | 需求文档指定 pytest + Django TestCase 双栈；pytest 的参数化与 fixture 更适合工作流规则矩阵测试 |
| E2E | Playwright | 同 | ✅ 一致 | — |
| API 文档 | 部分手写 | drf-spectacular 自动生成 OpenAPI 3 | ⚠️ 差异（增强） | 开放平台（P3/P4）与前端类型生成均依赖机器可读 schema |
| 容器编排 | Docker Compose（多套 profile） | 同 | ✅ 一致 | — |

### 6.2 三处关键差异的决策记录

**差异 1：Nginx 替代 Caddy**

- 决策：`apps/proxy` 使用 Nginx 1.27-alpine。
- 影响面：需自行维护 `nginx.conf.template`（envsubst 注入上游地址与域名）、WebSocket upgrade 段、`client_max_body_size`（默认 100M，与 MinIO 直传上限对齐）、`limit_req_zone`（边缘粗粒度限流，与 DRF 应用层限流形成两层防护）。
- 迁移成本：低。proxy 层无业务逻辑，配置文件一次性成型。

**差异 2：React Router v7 而非 8.3**

- 决策：锁定 `react-router@7.x`，Framework Mode，SPA 模式（`ssr: false`）构建为静态产物交由 Nginx 托管。
- 风险对冲：路由定义集中在 `app/routes.ts`，数据加载统一走 loader 抽象层，升级 v8 时改动面收敛在 3 个文件内。
- 复核时点：P2 结束时评估 v8 生态成熟度。

**差异 3：RabbitMQ 作为唯一 Celery broker**

- 决策：`CELERY_BROKER_URL` 指向 RabbitMQ；`CELERY_RESULT_BACKEND` 指向 Redis/Valkey（result 可丢，追求读写性能）。
- 配套约束：所有 Celery 任务必须幂等（`acks_late=True` + 手动 ack 场景下可能重复投递）；每个队列配置 DLX 死信队列，失败任务进入 `*.dlq` 便于人工介入；队列按优先级拆分（`notifications` / `webhooks` / `reports` / `imports`），避免长任务阻塞实时通知。

---

## 7. 与 Ones 的技术对比

### 7.1 事实边界声明

Ones（ones.com / ones.ai）是**闭源商业产品**，其内部技术栈未完整公开。本节结论仅基于其**公开的 Open Platform 开发者文档**所披露的信息推断，不代表其真实内部实现全貌：

- Ones Open Platform 文档中，**应用（App）开发模型**明确基于 **NestJS（服务端插件）+ Vite + React（前端插件）**；
- 提供覆盖 Project / Issue / TestCase / Wiki / Account 的完整 REST Open API；
- 提供 **ONESQL** 自定义查询语言，允许开发者以类 SQL 语法跨实体查询；
- 提供 Webhook、插件市场与前端扩展槽位（slot）机制。

### 7.2 技术栈对比

| 维度 | Ones（据公开文档推断） | 本系统 | 差异说明 |
| --- | --- | --- | --- |
| 前端框架 | React + Vite | React 19 + Vite 6 + React Router v7 | 基本同源；本系统明确使用 React Router Framework Mode 承载路由与数据加载 |
| 服务端框架 | NestJS（Node.js / TypeScript） | Django + DRF（Python） | **根本差异**。见 §7.3 |
| 扩展模型 | 插件化：前端 slot 注入 + 服务端插件（NestJS 模块） | 内建模块 + Open API + Webhook + OAuth 应用 | Ones 走「宿主 + 插件运行时」路线；本系统走「稳定 API + 外部应用」路线，不在宿主进程内运行第三方代码 |
| 查询能力 | ONESQL 自定义查询语言 | REST + `fields` / `expand` / `filter` / `ordering` 参数组合 | 见 §7.4 |
| 富文本 / 协同 | 未公开（Wiki 支持协同编辑） | TipTap + Yjs CRDT + Hocuspocus（完全自主可控） | 本系统协同链路全开源，可私有化部署 |
| 部署形态 | SaaS + 私有化 | Docker Compose / K8s 私有化优先 | 本系统以私有化为第一等公民 |
| 数据模型 | 工作项统一模型（Issue 承载需求/任务/缺陷） | 同（需求文档 §3.4.1 明确统一工作项模型） | ✅ 设计理念一致 |

### 7.3 技术哲学差异：Django/Python vs NestJS/TypeScript

| 对比项 | NestJS 路线（Ones） | Django 路线（本系统） |
| --- | --- | --- |
| 语言统一性 | 前后端同为 TypeScript，类型可跨端共享，人员可全栈流动 | 前 TS / 后 Python，类型契约需通过 OpenAPI + 代码生成对齐（本系统用 drf-spectacular 弥补） |
| 框架哲学 | **依赖注入 + 装饰器 + 显式装配**，「框架提供骨架，能力自行组合」 | **约定优于配置 + 电池齐全（batteries-included）**，ORM/migrations/admin/auth/session 开箱即用 |
| 数据层 | TypeORM / Prisma，migration 需较多手工介入 | Django ORM + migrations 自动生成，`makemigrations` 推导 schema 变更——这是需求文档「migrations 自动迁移」的直接支撑点 |
| 后台管理 | 需自行搭建 | Django admin 开箱可用，God Mode（apps/admin）可先用 admin 兜底再逐步替换 |
| 数据密集型能力 | 需要更多自研 | Django ORM 的 `Prefetch`、`annotate`、`Subquery` 对报表/聚合场景表达力强；Python 生态（pandas/numpy）为后期报表与 AI 能力预留空间 |
| 异步任务 | BullMQ 等 Node 方案 | Celery + RabbitMQ，工作流引擎/审批超时/定时扫描的成熟度与可观测性更好 |
| 团队适配 | 适合纯 Node 团队 | 适合数据模型复杂、需要快速搭建管理后台与迁移体系的团队 |

**本系统的选择理由**：项目的复杂度集中在**领域模型与权限体系**（多租户、三级 RBAC、行级隔离、动态自定义字段、可视化工作流与审批），而非 IO 编排。Django ORM + migrations + admin 的组合让模型演进成本最低；DRF 的 Serializer / Permission / Throttle 三层抽象与需求文档「接口层、数据层、UI 层三重权限校验」天然对应。付出的代价是前后端语言不统一，通过 OpenAPI 契约 + 自动类型生成消解。

### 7.4 查询能力对比：ONESQL vs REST 参数化

Ones 的 ONESQL 提供了极强的跨实体查询表达力，代价是：①学习成本高；②查询复杂度不可控，容易产生慢查询与数据越权风险；③服务端需实现查询解析器、优化器与权限重写。

本系统**不引入自定义查询语言**，改为：

1. **标准化参数组合**：`?fields=` 字段裁剪、`?expand=` 关联展开、`?<field>=` 等值/范围筛选、`?ordering=` 排序、`?search=` 全文检索（详见 `api-conventions.md`）；
2. **服务端预置视图（Saved View）**：把复杂筛选条件持久化为具名视图对象，前端引用视图 ID 而非传递复杂表达式；
3. **报表专用聚合端点**：燃尽图、迭代速率、累积流图等由后端提供固定语义端点 + Celery 预聚合，避免通用查询语言承担分析负载。

设计取向：**牺牲部分表达力，换取一致性、可预测的性能与更小的权限攻击面**。若后期确有强分析需求（P4），走「只读分析副本 + 受限 SQL 沙箱」而非扩展在线 API 的查询语言。

---

## 8. 运行时环境要求矩阵

| 环境 | Node.js | pnpm | Python | PostgreSQL | Redis/Valkey | RabbitMQ | Docker |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 需求文档最低要求 | 20+ | — | 3.8+ | 14+ | 6.2+ | 3.x | — |
| 本项目锁定（开发） | 22.14.x | 11.x | 3.12.x | 15.7 | 7.2 | 3.13 | 27+ |
| 本项目锁定（生产镜像） | `node:22-alpine` | 随镜像内置 | `python:3.12-slim` | `postgres:15.7-alpine` | `valkey/valkey:7.2-alpine` | `rabbitmq:3.13-management-alpine` | — |
| CI 矩阵 | 22.x（主）/ 24.x（前瞻，允许失败） | 11.x | 3.12（主）/ 3.13（前瞻，允许失败） | 15.7 | 7.2 | 3.13 | — |

约束落地：

- 根目录 `.nvmrc` 写入 `22.14.0`，`package.json#engines` 声明 `{"node": ">=22.14 <23", "pnpm": ">=11 <12"}`，并开启 `engine-strict=true`（`.npmrc`）。
- `apps/api/.python-version` 写入 `3.12`，`pyproject.toml` 声明 `requires-python = ">=3.12,<3.13"`。
- `package.json#packageManager` 固定 pnpm 精确版本，Corepack 自动对齐，杜绝「本地 pnpm 版本不同导致 lockfile 抖动」。

---

## 9. 依赖治理与升级流程

### 9.1 新增依赖准入清单

引入任何新依赖前必须逐项确认，不满足则不予引入：

1. 是否可用现有依赖或少量自研代码替代（尤其 < 100 行的工具类）；
2. 许可证是否为 MIT / Apache-2.0 / BSD / ISC（**禁止** GPL / AGPL / RSAL / SSPL 及自定义商业限制协议）；
3. 近 6 个月是否有维护活动，是否有已知未修复的 High/Critical CVE；
4. 安装体积与运行时体积（前端依赖需评估对首屏 bundle 的增量，> 30KB gzip 需说明必要性）；
5. 是否有 TypeScript 类型定义（无类型定义的包原则上不引入）；
6. 是否与既有依赖功能重叠（不允许同时存在两个日期库、两个表单库）。

### 9.2 升级流程

```
Renovate 开 PR（按 §1.2 窗口调度）
   → CI 全量校验（lint / typecheck / unit / build / E2E 冒烟）
   → patch 且 CI 全绿：允许自动合并
   → minor：需 1 名 reviewer 确认 CHANGELOG 无 breaking
   → major：必须新建 ADR 记录动因/影响面/回滚方案，并同步更新本文档
   → 合并后更新本文档版本表与「最后更新日期」
```

### 9.3 安全基线

- CI 中执行 `pnpm audit --audit-level=high` 与 `uv pip audit`（或 `pip-audit`），High 及以上直接阻断合并。
- 容器镜像纳入 Trivy 扫描，Critical 漏洞阻断发布。
- 生产环境镜像按 digest 锁定，禁止浮动 tag，确保可重现部署与可精确回滚。

---

## 10. 变更记录

| 日期 | 版本 | 变更内容 | 责任人 |
| --- | --- | --- | --- |
| 2026-08-31 | 1.0 | 初版：确认全栈技术选型与版本锁定，完成 Plane / Ones 对标分析 | 架构组 |
