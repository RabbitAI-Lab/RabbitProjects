# RabbitProjects 设计文档体系

> 企业级项目管理系统 · 全量设计文档导航索引

---

## 一、项目简介

RabbitProjects 是一套复刻 [Ones](https://ones.cn/) 与 [Plane](https://github.com/makeplane/plane) 的企业级项目管理与团队协作系统，围绕「统一工作项（Issue）」模型承载需求、缺陷、任务、测试、文档五类工作，覆盖账号权限、团队管理、项目管理、任务核心、看板视图、甘特图进度、文件资源、实时协作通知、第三方集成九大模块，并按**标准版（个人/中小团队）**与**企业版（中大型企业）**双层能力体系分级交付；产品节奏以「2 周跑通 POC、8 周标准版生产可用、12 周企业版核心能力交付」为主线，严格按 P0→P4 优先级推进，上游能力未稳定不开发下游功能。

---

## 二、技术栈概要

| 层次 | 选型 | 说明 |
|---|---|---|
| 工程结构 | pnpm workspace + Turborepo Monorepo | `apps/` 承载应用，`packages/` 承载共享包，对齐 Plane 仓库结构 |
| 前端框架 | React Router v7（Framework Mode）+ Vite + TypeScript | 三应用：`web` 主工作台、`admin` 实例管理后台、`space` 对外公开空间 |
| 后端框架 | Django + Django REST Framework（Python） | RESTful API 服务，统一返回格式 + 全局异常捕获 |
| 数据库 | PostgreSQL 14+（生产 15.7） | Django ORM + migrations 自动迁移，JSONB + GIN 承载动态字段 |
| 异步任务 | Celery（worker + beat）+ RabbitMQ | 耗时操作异步可靠执行 |
| 缓存 | Redis（Valkey 7.x 兼容） | 会话、缓存、限流 |
| 实时协作 | Node.js live 服务（Express + WebSocket）+ Hocuspocus + Yjs CRDT | 富文本/文档多人协同、看板实时同步 |
| UI 体系 | Tailwind CSS + Headless UI + 自研组件库 + lucide-react | 参照 `@plane/ui` 模式 |
| 状态与数据 | MobX（mobx + mobx-react）+ SWR + Axios | 响应式 Store + 乐观更新 |
| 富文本 | Tiptap（ProseMirror）自研编辑器包 | 参照 `@plane/editor` 模式 |
| 交互组件 | @atlaskit/pragmatic-drag-and-drop、TanStack Table、recharts | 拖拽 / 表格 / 图表 |
| 认证 | Django 自研认证（Session + Token + OAuth） | 不使用 NextAuth |
| 文件存储 | MinIO / S3 兼容对象存储 | 预签名直传，不经服务端中转 |
| 反向代理 | Nginx（`apps/proxy`） | 统一路由 web / admin / space / api / live |
| 编排与质量 | Docker Compose + OxLint + oxfmt + Husky | 一键启动全套服务；TS 强类型 + Python 类型注解 |

环境要求：Node.js 20+、Python 3.8+、PostgreSQL 14+、Redis 6.2+。

---

## 三、文档体系说明

文档采用**三层结构**，自上而下逐层细化，任何功能文档都必须可回溯到架构决策：

```
第一层：架构文档（architecture/）
        技术栈、仓库结构、API 规范、核心数据模型、权限模型、依赖关系
        —— 全局约束，一次确定、长期遵循，所有迭代共同依赖
                        ↓
第二层：迭代文档（sprint-*/）
        按 Sprint 分组的功能规格集合，一个 Sprint 一个文件夹
        —— 回答「这一周交付什么、验收标准是什么」
                        ↓
第三层：功能规格（sprint-*/MODULE-NNN-*.md）
        单个功能点的完整规格：背景、范围边界、数据模型、API 契约、
        前端交互、验收标准、依赖关系、工作量估算
        —— 回答「这个功能怎么做、做到什么程度算完成」
```

阅读建议：

- **新成员入门**：`glossary.md` → `architecture/tech-stack.md` → `architecture/unified-issue-model.md` → 当前 Sprint 文件夹
- **开始开发某功能**：先读该功能文档「依赖关系」章节列出的上游文档，再读本体
- **评审排期**：`architecture/dependency-graph.md` + 本页「迭代排期里程碑总览表」

---

## 四、完整文档目录索引（82 份）

优先级说明：**P0** POC 阻塞级 ｜ **P1** MVP 必备级 ｜ **P2** 标准版完整级 ｜ **P3** 企业版核心级 ｜ **P4** 远期增强级 ｜ **—** 架构/质量类无优先级分层。

### 4.1 架构文档（7 份 · 全局约束）

| # | 文档 | 标题 | 所属迭代 | 优先级 | 模块 |
|---|---|---|---|---|---|
| 1 | [architecture/tech-stack.md](architecture/tech-stack.md) | 技术栈确认与版本锁定 | 前置 | — | ARCH |
| 2 | [architecture/monorepo-structure.md](architecture/monorepo-structure.md) | Monorepo 仓库结构规范 | 前置 | — | ARCH |
| 3 | [architecture/api-conventions.md](architecture/api-conventions.md) | REST API 统一规范 | 前置 | — | ARCH |
| 4 | [architecture/unified-issue-model.md](architecture/unified-issue-model.md) | 统一工作项模型设计 | 前置 | — | ARCH |
| 5 | [architecture/dynamic-fields-design.md](architecture/dynamic-fields-design.md) | 动态自定义字段技术方案 | 前置 | — | ARCH |
| 6 | [architecture/rbac-permission-model.md](architecture/rbac-permission-model.md) | 三重权限模型设计 | 前置 | — | ARCH |
| 7 | [architecture/dependency-graph.md](architecture/dependency-graph.md) | 模块依赖关系图 | 前置 | — | ARCH |

### 4.2 Sprint 0 — POC 技术验证（10 份 · P0 · 第 1-2 周）

目标：跑通「登录 → 建团队 → 建项目 → 建任务 → 拖拽看板」最小闭环，清零全栈技术风险。

| # | 文档 | 标题 | 所属迭代 | 优先级 | 模块 |
|---|---|---|---|---|---|
| 8 | [sprint-0-poc/INFRA-001-monorepo-scaffold.md](sprint-0-poc/INFRA-001-monorepo-scaffold.md) | Monorepo 工程骨架搭建 | Sprint 0 | P0 | INFRA |
| 9 | [sprint-0-poc/INFRA-002-docker-compose.md](sprint-0-poc/INFRA-002-docker-compose.md) | Docker Compose 全套服务编排 | Sprint 0 | P0 | INFRA |
| 10 | [sprint-0-poc/INFRA-003-django-models-init.md](sprint-0-poc/INFRA-003-django-models-init.md) | Django ORM 初始数据模型 | Sprint 0 | P0 | INFRA |
| 11 | [sprint-0-poc/AUTH-001-registration-login.md](sprint-0-poc/AUTH-001-registration-login.md) | 邮箱注册 / 登录 / 退出 | Sprint 0 | P0 | AUTH |
| 12 | [sprint-0-poc/AUTH-002-route-guard.md](sprint-0-poc/AUTH-002-route-guard.md) | 前端路由拦截 + 后端鉴权 | Sprint 0 | P0 | AUTH |
| 13 | [sprint-0-poc/AUTH-003-basic-isolation.md](sprint-0-poc/AUTH-003-basic-isolation.md) | 最小权限隔离 | Sprint 0 | P0 | AUTH |
| 14 | [sprint-0-poc/TEAM-001-team-crud.md](sprint-0-poc/TEAM-001-team-crud.md) | 团队创建 / 查询 / 默认初始化 | Sprint 0 | P0 | TEAM |
| 15 | [sprint-0-poc/PROJ-001-project-crud.md](sprint-0-poc/PROJ-001-project-crud.md) | 项目 CRUD | Sprint 0 | P0 | PROJ |
| 16 | [sprint-0-poc/TASK-001-task-crud.md](sprint-0-poc/TASK-001-task-crud.md) | 任务 CRUD（5 固定字段） | Sprint 0 | P0 | TASK |
| 17 | [sprint-0-poc/BOARD-001-fixed-kanban.md](sprint-0-poc/BOARD-001-fixed-kanban.md) | 固定三列看板 + 拖拽 | Sprint 0 | P0 | BOARD |

### 4.3 Sprint 1 — MVP 能力补齐（11 份 · P1 · 第 3 周）

目标：补齐 10 人以内小团队日常真实协作必备能力。

| # | 文档 | 标题 | 所属迭代 | 优先级 | 模块 |
|---|---|---|---|---|---|
| 18 | [sprint-1-mvp/AUTH-004-profile-password.md](sprint-1-mvp/AUTH-004-profile-password.md) | 个人信息修改与密码重置 | Sprint 1 | P1 | AUTH |
| 19 | [sprint-1-mvp/AUTH-005-button-permission.md](sprint-1-mvp/AUTH-005-button-permission.md) | 按钮级权限 + 接口二次鉴权 | Sprint 1 | P1 | AUTH |
| 20 | [sprint-1-mvp/TEAM-002-member-management.md](sprint-1-mvp/TEAM-002-member-management.md) | 团队成员邀请 / 移除 / 角色分配 | Sprint 1 | P1 | TEAM |
| 21 | [sprint-1-mvp/PROJ-002-member-search.md](sprint-1-mvp/PROJ-002-member-search.md) | 项目成员管理与搜索收藏 | Sprint 1 | P1 | PROJ |
| 22 | [sprint-1-mvp/TASK-002-task-attributes.md](sprint-1-mvp/TASK-002-task-attributes.md) | 任务扩展属性与一级子任务 | Sprint 1 | P1 | TASK |
| 23 | [sprint-1-mvp/TASK-003-list-filter-sort.md](sprint-1-mvp/TASK-003-list-filter-sort.md) | 任务列表筛选 / 搜索 / 排序 | Sprint 1 | P1 | TASK |
| 24 | [sprint-1-mvp/BOARD-002-kanban-filter.md](sprint-1-mvp/BOARD-002-kanban-filter.md) | 看板筛选与卡片悬浮预览 | Sprint 1 | P1 | BOARD |
| 25 | [sprint-1-mvp/FILE-001-task-attachment.md](sprint-1-mvp/FILE-001-task-attachment.md) | 任务级附件上传下载 | Sprint 1 | P1 | FILE |
| 26 | [sprint-1-mvp/COLLAB-001-comment-notify.md](sprint-1-mvp/COLLAB-001-comment-notify.md) | 任务评论 / @提醒 / 通知中心 | Sprint 1 | P1 | COLLAB |
| 27 | [sprint-1-mvp/RPT-001-personal-stats.md](sprint-1-mvp/RPT-001-personal-stats.md) | 个人待办与已完成统计 | Sprint 1 | P1 | RPT |
| 28 | [sprint-1-mvp/INFRA-004-api-error-config.md](sprint-1-mvp/INFRA-004-api-error-config.md) | 统一返回格式 / 全局错误 / 环境配置 | Sprint 1 | P1 | INFRA |

### 4.4 Sprint 2 — 任务体系完善（7 份 · P2 · 第 4 周）

目标：补齐标准版完整任务核心能力，含动态自定义字段与全字段筛选器。

| # | 文档 | 标题 | 所属迭代 | 优先级 | 模块 |
|---|---|---|---|---|---|
| 29 | [sprint-2-task-full/TASK-004-multilevel-subtask.md](sprint-2-task-full/TASK-004-multilevel-subtask.md) | 多层级子任务与进度联动 | Sprint 2 | P2 | TASK |
| 30 | [sprint-2-task-full/TASK-005-task-dependency.md](sprint-2-task-full/TASK-005-task-dependency.md) | 任务前置 / 后置依赖关系 | Sprint 2 | P2 | TASK |
| 31 | [sprint-2-task-full/TASK-006-worklog-estimate.md](sprint-2-task-full/TASK-006-worklog-estimate.md) | 工时估算与工时填报 | Sprint 2 | P2 | TASK |
| 32 | [sprint-2-task-full/TASK-007-multi-assignee.md](sprint-2-task-full/TASK-007-multi-assignee.md) | 多执行人 / 任务转交 / 认领 | Sprint 2 | P2 | TASK |
| 33 | [sprint-2-task-full/TASK-008-custom-fields.md](sprint-2-task-full/TASK-008-custom-fields.md) | 基础自定义字段动态增删 | Sprint 2 | P2 | TASK |
| 34 | [sprint-2-task-full/TASK-009-advanced-filter-view.md](sprint-2-task-full/TASK-009-advanced-filter-view.md) | 全字段组合筛选器与视图保存 | Sprint 2 | P2 | TASK |
| 35 | [sprint-2-task-full/TASK-010-copy-archive-log.md](sprint-2-task-full/TASK-010-copy-archive-log.md) | 任务复制 / 归档 / 全量操作日志 | Sprint 2 | P2 | TASK |

### 4.5 Sprint 3 — 高级视图 + 实时协作（6 份 · P2 · 第 5 周）

目标：补齐看板高级能力、评论协作与 WebSocket 多人实时同步。

| # | 文档 | 标题 | 所属迭代 | 优先级 | 模块 |
|---|---|---|---|---|---|
| 36 | [sprint-3-views-collab/BOARD-003-multi-kanban.md](sprint-3-views-collab/BOARD-003-multi-kanban.md) | 多个独立看板与视图配置 | Sprint 3 | P2 | BOARD |
| 37 | [sprint-3-views-collab/BOARD-004-batch-operations.md](sprint-3-views-collab/BOARD-004-batch-operations.md) | 任务批量操作 | Sprint 3 | P2 | BOARD |
| 38 | [sprint-3-views-collab/COLLAB-002-thread-reply.md](sprint-3-views-collab/COLLAB-002-thread-reply.md) | 楼中楼回复 / 表情 / 图片评论 | Sprint 3 | P2 | COLLAB |
| 39 | [sprint-3-views-collab/COLLAB-003-activity-stream.md](sprint-3-views-collab/COLLAB-003-activity-stream.md) | 项目动态流 / 任务动态时间线 | Sprint 3 | P2 | COLLAB |
| 40 | [sprint-3-views-collab/COLLAB-004-websocket-sync.md](sprint-3-views-collab/COLLAB-004-websocket-sync.md) | WebSocket 实时推送 / 多人数据同步 | Sprint 3 | P2 | COLLAB |
| 41 | [sprint-3-views-collab/TASK-011-advanced-filter.md](sprint-3-views-collab/TASK-011-advanced-filter.md) | 全字段 AND/OR 组合筛选器与视图保存 | Sprint 3 | P2 | TASK |

### 4.6 Sprint 4 — 甘特图 + 文件管理（5 份 · P2 · 第 6 周）

目标：补齐进度可视化与项目级文件资源能力。

| # | 文档 | 标题 | 所属迭代 | 优先级 | 模块 |
|---|---|---|---|---|---|
| 42 | [sprint-4-gantt-file/GANTT-001-gantt-base.md](sprint-4-gantt-file/GANTT-001-gantt-base.md) | 甘特图渲染与日 / 周 / 月粒度 | Sprint 4 | P2 | GANTT |
| 43 | [sprint-4-gantt-file/GANTT-002-gantt-drag-dependency.md](sprint-4-gantt-file/GANTT-002-gantt-drag-dependency.md) | 任务条拖拽 / 依赖连线 / 导出 | Sprint 4 | P2 | GANTT |
| 44 | [sprint-4-gantt-file/FILE-002-project-file-library.md](sprint-4-gantt-file/FILE-002-project-file-library.md) | 项目文件库与多层级目录 | Sprint 4 | P2 | FILE |
| 45 | [sprint-4-gantt-file/FILE-003-chunk-upload-preview.md](sprint-4-gantt-file/FILE-003-chunk-upload-preview.md) | 大文件分片续传与在线预览 | Sprint 4 | P2 | FILE |
| 46 | [sprint-4-gantt-file/FILE-004-version-share-permission.md](sprint-4-gantt-file/FILE-004-version-share-permission.md) | 文件多版本 / 分享链接 / 权限 | Sprint 4 | P2 | FILE |

### 4.7 Sprint 5 — 集成 + 标准版收尾（6 份 · P2 · 第 7 周）

目标：补齐 GitHub 集成、基础统计与生产配置，标准版功能全量冻结。

| # | 文档 | 标题 | 所属迭代 | 优先级 | 模块 |
|---|---|---|---|---|---|
| 47 | [sprint-5-integration-standard/INTG-001-github-integration.md](sprint-5-integration-standard/INTG-001-github-integration.md) | GitHub 基础集成 | Sprint 5 | P2 | INTG |
| 48 | [sprint-5-integration-standard/INTG-002-webhook-basic.md](sprint-5-integration-standard/INTG-002-webhook-basic.md) | 基础 Webhook 出站通知 | Sprint 5 | P2 | INTG |
| 49 | [sprint-5-integration-standard/RPT-002-project-progress-stats.md](sprint-5-integration-standard/RPT-002-project-progress-stats.md) | 项目进度与成员任务量统计 | Sprint 5 | P2 | RPT |
| 50 | [sprint-5-integration-standard/PROJ-003-lifecycle-timeline.md](sprint-5-integration-standard/PROJ-003-lifecycle-timeline.md) | 项目生命周期与动态时间线 | Sprint 5 | P2 | PROJ |
| 51 | [sprint-5-integration-standard/AUTH-006-row-level-isolation.md](sprint-5-integration-standard/AUTH-006-row-level-isolation.md) | 数据库行级隔离与成员权限分配 | Sprint 5 | P2 | AUTH |
| 52 | [sprint-5-integration-standard/TEAM-003-team-archive-config.md](sprint-5-integration-standard/TEAM-003-team-archive-config.md) | 团队归档与全局模板配置 | Sprint 5 | P2 | TEAM |

### 4.8 Sprint 6 — 稳定性缓冲（2 份 · 第 8 周）

目标：无新增功能，标准版 V1.0 正式发布。

| # | 文档 | 标题 | 所属迭代 | 优先级 | 模块 |
|---|---|---|---|---|---|
| 53 | [sprint-6-stabilize/INFRA-005-rate-limit-backup-deploy.md](sprint-6-stabilize/INFRA-005-rate-limit-backup-deploy.md) | 接口限流 / 数据备份 / 生产部署 | Sprint 6 | P2 | INFRA |
| 54 | [sprint-6-stabilize/QA-001-stabilize-hardening.md](sprint-6-stabilize/QA-001-stabilize-hardening.md) | 缺陷修复 / 性能优化 / 权限加固 | Sprint 6 | — | QA |

### 4.9 Sprint 7 — 企业工作流核心（8 份 · P3 · 第 9-10 周）

目标：交付企业版最核心的自定义工作流与审批能力（企业版最大技术难点）。

| # | 文档 | 标题 | 所属迭代 | 优先级 | 模块 |
|---|---|---|---|---|---|
| 55 | [sprint-7-enterprise-workflow/WF-001-workflow-engine-model.md](sprint-7-enterprise-workflow/WF-001-workflow-engine-model.md) | 工作流引擎数据模型与状态机 | Sprint 7 | P3 | WF |
| 56 | [sprint-7-enterprise-workflow/WF-002-workflow-canvas.md](sprint-7-enterprise-workflow/WF-002-workflow-canvas.md) | 可视化工作流画布编辑器 | Sprint 7 | P3 | WF |
| 57 | [sprint-7-enterprise-workflow/WF-003-transition-rules.md](sprint-7-enterprise-workflow/WF-003-transition-rules.md) | 流转条件校验与字段锁定 | Sprint 7 | P3 | WF |
| 58 | [sprint-7-enterprise-workflow/WF-004-approval-flow.md](sprint-7-enterprise-workflow/WF-004-approval-flow.md) | 审批流（会签 / 或签 / 逐级 / 驳回） | Sprint 7 | P3 | WF |
| 59 | [sprint-7-enterprise-workflow/WF-005-automation-rules.md](sprint-7-enterprise-workflow/WF-005-automation-rules.md) | 自动化规则引擎 | Sprint 7 | P3 | WF |
| 60 | [sprint-7-enterprise-workflow/WF-006-workflow-template-library.md](sprint-7-enterprise-workflow/WF-006-workflow-template-library.md) | 工作流模板库与全局下发 | Sprint 7 | P3 | WF |
| 61 | [sprint-7-enterprise-workflow/TASK-012-advanced-custom-fields.md](sprint-7-enterprise-workflow/TASK-012-advanced-custom-fields.md) | 高级自定义字段与字段权限 | Sprint 7 | P3 | TASK |
| 62 | [sprint-7-enterprise-workflow/TASK-013-worklog-team-stats.md](sprint-7-enterprise-workflow/TASK-013-worklog-team-stats.md) | 团队工时统计与工时管控 | Sprint 7 | P3 | TASK |

### 4.10 Sprint 8 — 企业组织权限（5 份 · P3 · 第 11 周）

目标：企业级组织架构、高级权限与安全审计。

| # | 文档 | 标题 | 所属迭代 | 优先级 | 模块 |
|---|---|---|---|---|---|
| 63 | [sprint-8-enterprise-org/AUTH-007-org-department.md](sprint-8-enterprise-org/AUTH-007-org-department.md) | 部门层级组织架构 | Sprint 8 | P3 | AUTH |
| 64 | [sprint-8-enterprise-org/AUTH-008-custom-role-group.md](sprint-8-enterprise-org/AUTH-008-custom-role-group.md) | 自定义角色组与细粒度资源权限 | Sprint 8 | P3 | AUTH |
| 65 | [sprint-8-enterprise-org/AUTH-009-audit-log.md](sprint-8-enterprise-org/AUTH-009-audit-log.md) | 全量操作审计日志 | Sprint 8 | P3 | AUTH |
| 66 | [sprint-8-enterprise-org/AUTH-010-sso.md](sprint-8-enterprise-org/AUTH-010-sso.md) | SSO 单点登录 | Sprint 8 | P3 | AUTH |
| 67 | [sprint-8-enterprise-org/BOARD-005-view-share-lock.md](sprint-8-enterprise-org/BOARD-005-view-share-lock.md) | 视图共享 / 锁定 / 多维度分组 | Sprint 8 | P3 | BOARD |

### 4.11 Sprint 9 — 企业项目 / 报表 / Wiki（5 份 · P3 · 第 12 周）

目标：项目集管理、敏捷报表与团队知识库，企业版 V1.0 正式交付。

| # | 文档 | 标题 | 所属迭代 | 优先级 | 模块 |
|---|---|---|---|---|---|
| 68 | [sprint-9-enterprise-portfolio/PROJ-004-portfolio.md](sprint-9-enterprise-portfolio/PROJ-004-portfolio.md) | 项目集 / 项目组合与跨项目依赖 | Sprint 9 | P3 | PROJ |
| 69 | [sprint-9-enterprise-portfolio/RPT-003-agile-charts.md](sprint-9-enterprise-portfolio/RPT-003-agile-charts.md) | 燃尽图 / 迭代速率 / 累积流图 | Sprint 9 | P3 | RPT |
| 70 | [sprint-9-enterprise-portfolio/RPT-004-team-load-health.md](sprint-9-enterprise-portfolio/RPT-004-team-load-health.md) | 团队负载 / 项目健康度 / 报表导出 | Sprint 9 | P3 | RPT |
| 71 | [sprint-9-enterprise-portfolio/FILE-005-wiki-knowledge-base.md](sprint-9-enterprise-portfolio/FILE-005-wiki-knowledge-base.md) | 项目 Wiki 与全局知识检索 | Sprint 9 | P3 | FILE |
| 72 | [sprint-9-enterprise-portfolio/GANTT-003-critical-path.md](sprint-9-enterprise-portfolio/GANTT-003-critical-path.md) | 关键路径计算与延期预警 | Sprint 9 | P3 | GANTT |

### 4.12 P4 远期增强（10 份 · P4 · 第 13 周起）

目标：按商业化节奏排期，标准版 V1.0 正式发布前一律不占用排期。

| # | 文档 | 标题 | 所属迭代 | 优先级 | 模块 |
|---|---|---|---|---|---|
| 73 | [sprint-future-p4/AUTH-011-ldap-scim.md](sprint-future-p4/AUTH-011-ldap-scim.md) | LDAP / SCIM 账号同步 | 远期 | P4 | AUTH |
| 74 | [sprint-future-p4/AUTH-012-multi-tenant-risk.md](sprint-future-p4/AUTH-012-multi-tenant-risk.md) | 多租户隔离与风控告警溯源 | 远期 | P4 | AUTH |
| 75 | [sprint-future-p4/TASK-014-formula-cascade-fields.md](sprint-future-p4/TASK-014-formula-cascade-fields.md) | 公式 / 级联 / 跨项目关联字段 | 远期 | P4 | TASK |
| 76 | [sprint-future-p4/TASK-015-baseline-version.md](sprint-future-p4/TASK-015-baseline-version.md) | 任务基线与版本对比 | 远期 | P4 | TASK |
| 77 | [sprint-future-p4/INTG-003-open-api-platform.md](sprint-future-p4/INTG-003-open-api-platform.md) | 完整 OpenAPI 与应用接入市场 | 远期 | P4 | INTG |
| 78 | [sprint-future-p4/INTG-004-slack-zoom.md](sprint-future-p4/INTG-004-slack-zoom.md) | Slack / Zoom 全量集成 | 远期 | P4 | INTG |
| 79 | [sprint-future-p4/FILE-006-file-compliance.md](sprint-future-p4/FILE-006-file-compliance.md) | 文件水印 / 脱敏 / 合规留存 | 远期 | P4 | FILE |
| 80 | [sprint-future-p4/AI-001-ai-assistant.md](sprint-future-p4/AI-001-ai-assistant.md) | AI 辅助能力（摘要 / 预警 / 生成） | 远期 | P4 | AI |
| 81 | [sprint-future-p4/RPT-005-dashboard-custom-report.md](sprint-future-p4/RPT-005-dashboard-custom-report.md) | 企业数据大屏与自定义报表 | 远期 | P4 | RPT |
| 82 | [sprint-future-p4/INFRA-006-ha-private-deploy.md](sprint-future-p4/INFRA-006-ha-private-deploy.md) | 高可用集群与私有化部署 | 远期 | P4 | INFRA |

---

## 五、命名规范

### 5.1 迭代文件夹命名

格式：`sprint-{序号}-{英文短标识}`，全小写，单词间用 `-` 连接。

| 文件夹 | 对应迭代 | 周期 |
|---|---|---|
| `architecture/` | 架构前置文档（不属于任何 Sprint） | 前置 |
| `sprint-0-poc/` | Sprint 0：POC 技术验证 | 第 1-2 周 |
| `sprint-1-mvp/` | Sprint 1：MVP 能力补齐 | 第 3 周 |
| `sprint-2-task-full/` | Sprint 2：任务体系完善 | 第 4 周 |
| `sprint-3-views-collab/` | Sprint 3：高级视图 + 实时协作 | 第 5 周 |
| `sprint-4-gantt-file/` | Sprint 4：甘特图 + 文件管理 | 第 6 周 |
| `sprint-5-integration-standard/` | Sprint 5：集成 + 标准版收尾 | 第 7 周 |
| `sprint-6-stabilize/` | Sprint 6：稳定性缓冲 | 第 8 周 |
| `sprint-7-enterprise-workflow/` | Sprint 7：企业工作流核心 | 第 9-10 周 |
| `sprint-8-enterprise-org/` | Sprint 8：企业组织权限 | 第 11 周 |
| `sprint-9-enterprise-portfolio/` | Sprint 9：企业项目 / 报表 / Wiki | 第 12 周 |
| `sprint-future-p4/` | P4 远期增强（未锁定 Sprint 编号） | 第 13 周起 |

### 5.2 功能文档命名

格式：`{模块缩写}-{三位序号}-{英文短标识}.md`

```
TASK-008-custom-fields.md
 │     │        └── 英文短标识：全小写，kebab-case，2-4 个单词概括功能
 │     └── 三位序号：模块内全局递增，跨 Sprint 连续，一经分配永不复用
 └── 模块缩写：大写字母，见 5.3 映射表
```

约定：

- 序号在模块内**跨 Sprint 连续递增**（如 TASK-001 在 Sprint 0，TASK-011 在 Sprint 3），便于按编号唯一定位文档
- 文档一旦创建，**编号与文件名不再变更**；功能取消则在文档头部标注 `状态：已废弃`，不删除文件、不复用编号
- 架构文档不带编号，直接使用语义化文件名（如 `api-conventions.md`）
- 文档内的一级标题必须与索引表中的「标题」列保持一致

### 5.3 模块缩写映射表

| 缩写 | 模块中文名 | 对应需求章节 | 文档数 | 编号范围 |
|---|---|---|---|---|
| `ARCH` | 架构设计 | 1.2 / 五 | 7 | 无编号 |
| `INFRA` | 基础设施与部署运维 | 1.2 / 8.2 部署运维 | 6 | INFRA-001 ~ 006 |
| `AUTH` | 账号与权限 | 3.1 / 四 | 12 | AUTH-001 ~ 012 |
| `TEAM` | 团队管理 | 3.2 | 3 | TEAM-001 ~ 003 |
| `PROJ` | 项目管理 | 3.3 | 4 | PROJ-001 ~ 004 |
| `TASK` | 任务核心（统一工作项） | 3.4 | 15 | TASK-001 ~ 015 |
| `WF` | 工作流与审批 | 3.4 企业级工作流 | 6 | WF-001 ~ 006 |
| `BOARD` | 看板视图 | 3.5 | 5 | BOARD-001 ~ 005 |
| `GANTT` | 甘特图进度 | 3.6 | 3 | GANTT-001 ~ 003 |
| `FILE` | 文件资源与知识库 | 3.7 | 6 | FILE-001 ~ 006 |
| `COLLAB` | 实时协作与通知 | 3.8 | 4 | COLLAB-001 ~ 004 |
| `INTG` | 第三方工具集成 | 3.9 | 4 | INTG-001 ~ 004 |
| `RPT` | 数据报表 | 8.2 数据报表 | 5 | RPT-001 ~ 005 |
| `AI` | AI 辅助能力 | 3.9 / 8.2 AI 能力 | 1 | AI-001 |
| `QA` | 质量保障与加固 | 8.2 / Sprint 6 | 1 | QA-001 |
| | **合计** | | **82** | |

---

## 六、迭代排期里程碑总览

总节奏：**2 周交付可演示 POC，之后每 1 周一个 Sprint，8 周标准版生产可用，12 周交付企业版核心能力**。上一迭代验收不通过不进入下一迭代。

| 迭代 | 周期 | 核心目标 | 覆盖优先级 | 文档数 | 交付里程碑 |
|---|---|---|---|---|---|
| 前置 | 第 0 周 | 架构决策与规范定稿 | — | 7 | 技术栈锁定、数据模型与 API 规范评审通过 |
| Sprint 0：POC 技术验证 | 第 1-2 周 | 跑通最小核心闭环，清零技术风险 | P0 全量 | 10 | `docker compose up` 一键启动，可演示「登录→建团队→建项目→建任务→拖拽看板」 |
| Sprint 1：MVP 能力补齐 | 第 3 周 | 10 人小团队日常协作可用 | P1 全量 | 11 | 支持成员邀请、任务分配、基础筛选、评论通知 |
| Sprint 2：任务体系完善 | 第 4 周 | 标准版完整任务核心 | P2 任务模块 | 7 | 多层子任务、依赖、工时、多负责人、动态字段、全操作留痕 |
| Sprint 3：高级视图 + 实时协作 | 第 5 周 | 看板高级能力与多人实时同步 | P2 看板 / 协作 | 6 | 多看板、楼中楼评论、@通知、WebSocket 实时同步 |
| Sprint 4：甘特图 + 文件管理 | 第 6 周 | 进度可视化与文件能力 | P2 甘特 / 文件 | 5 | 完整甘特图、项目文件库、在线预览与分享 |
| Sprint 5：集成 + 标准版收尾 | 第 7 周 | GitHub 集成、统计与生产配置 | P2 剩余全部 | 6 | 标准版功能全量冻结，对标 Plane 免费版 |
| Sprint 6：稳定性缓冲 | 第 8 周 | 缺陷修复、性能、权限加固 | 无新增功能 | 2 | **标准版 V1.0 正式发布**，通过压测与安全校验 |
| Sprint 7：企业工作流核心 | 第 9-10 周 | 自定义工作流与审批 | P3 工作流 / 审批 | 8 | 流程画布、流转规则、多级审批、自动化规则、审批留痕 |
| Sprint 8：企业组织权限 | 第 11 周 | 组织架构、高级权限与审计 | P3 组织 / 权限 | 5 | 部门层级、自定义角色、细粒度权限、审计日志、SSO |
| Sprint 9：企业项目 / 报表 / Wiki | 第 12 周 | 项目集、敏捷报表、知识库 | P3 项目 / 报表 / Wiki | 5 | **企业版 V1.0 正式交付** |
| 后续商业化迭代 | 第 13 周起 | 远期增强能力 | P4 全量 | 10 | LDAP/SCIM、AI、开放平台、文件合规、私有化部署、数据大屏 |

人力投入参考：2 人全栈按上表节奏（2 周 POC / 12 周企业版 V1.0）；单人全栈 POC 约 3 周、每 Sprint 约 1.5 周（标准版约 11 周、企业版约 17 周）。每个 Sprint 固定预留 20% 缓冲时间。

---

## 七、核心依赖链简图

### 7.1 模块强依赖链（不可颠倒）

```
账号权限(AUTH) → 团队(TEAM) → 项目(PROJ) → 任务核心(TASK) → 看板(BOARD)
                                                  ↓
                                        甘特(GANTT) / 文件(FILE)
                                                  ↓
                                            协作通知(COLLAB)
                                                  ↓
                                            第三方集成(INTG)
                                                  ↓
                                        企业工作流(WF) / 报表(RPT)
```

禁止上游核心能力未稳定就提前开发下游功能。

### 7.2 关键文档依赖简图

```
                        ┌──────────────────────────────┐
                        │ architecture/tech-stack.md    │
                        │ monorepo-structure.md         │
                        │ api-conventions.md            │
                        └──────────────┬───────────────┘
                                       │ 全局约束
        ┌──────────────────────────────┼──────────────────────────────┐
        ↓                              ↓                              ↓
unified-issue-model.md      rbac-permission-model.md      dynamic-fields-design.md
        │                              │                              │
        │                    AUTH-001 → AUTH-002 → AUTH-003           │
        │                              ↓                              │
        │                    TEAM-001 → PROJ-001                      │
        │                              ↓                              │
        └────────────────────→ TASK-001（5 固定字段）                  │
                                       ↓                              │
                              BOARD-001（固定三列看板）                 │
                                       ↓                              │
                    TASK-002/003 → TASK-004~007 → TASK-008 ←──────────┘
                                                     ↓
                                    TASK-009（全字段筛选器）
                                          ↓            ↓
                              BOARD-003/004      TASK-011（类型模板 / 需求池）
                                          ↓            ↓
                              GANTT-001/002        WF-001 → WF-002~006
                                          ↓            ↓
                              COLLAB-004      TASK-012（高级字段）
                              （WebSocket）          ↓
                                                TASK-014（公式 / 级联）
```

### 7.3 三条最关键的依赖约束

1. **INFRA-003（Django 初始数据模型）是全系统总闸**：`unified-issue-model.md` 与 `dynamic-fields-design.md` 未定稿前，不得开始任何业务表建模，避免后期返工改表。
2. **TASK-008（动态自定义字段）是筛选器与视图体系的前置**：TASK-009 全字段筛选器、BOARD-003/004 视图保存、TASK-011 类型字段模板均依赖其字段配置表与 JSONB + GIN 方案。
3. **WF-001（工作流引擎模型）是企业版最大技术难点**：POC 阶段仅在任务状态字段预留扩展位，不提前开发；Sprint 7 前 WF-002~006 一律不启动。

---

## 八、相关文档

- [术语表 glossary.md](glossary.md) — 中英对照术语定义与版本归属说明
- [需求文档 需求文档.md](需求文档.md) — 原始全量需求清单与优先级分级
