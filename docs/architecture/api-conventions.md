# REST API 统一规范

| 元信息项 | 内容 |
| --- | --- |
| 文档编号 | ARCH-003 |
| 所属层级 | 跨迭代架构决策（Cross-Iteration Architecture Decision） |
| 文档状态 | 已确认（Approved）· 破坏性变更需走 ADR + API 版本流程 |
| 最后更新日期 | 2026-08-31 |
| 适用范围 | `apps/api` 全部 HTTP 端点；`apps/web` / `admin` / `space` / `live` 的全部 API 调用 |
| 上游依据 | `docs/需求文档.md` §五（统一 RESTful API + 统一返回格式 + 全局异常捕获）、§8.2 部署运维行（统一接口返回格式、全局错误捕获、接口限流） |
| 对标基线 | Plane 开源版 API（180+ 端点）、Ones Open API |
| 关联文档 | `tech-stack.md`、`monorepo-structure.md` |

> **强制性说明**：本文档所列规范为**硬性约束**，不是建议。任何新增端点在 Code Review 时按 §14 的检查清单逐项核对，不符合者不予合并。

---

## 1. 总体设计原则

| 原则 | 含义 | 落地约束 |
| --- | --- | --- |
| 资源导向 | URL 表达资源，动作由 HTTP 方法表达 | 路径中禁止出现动词（`/create-issue/` ❌）；确实无法映射为 CRUD 的操作走「动作子资源」（§2.6） |
| 层级归属显式化 | 资源的租户与项目归属体现在 URL 路径中，而非查询参数 | `/workspaces/{slug}/projects/{project_id}/issues/`；权限校验直接基于路径参数，避免「忘记过滤租户」导致越权 |
| 一致性优于灵活性 | 宁可少一种表达方式，也不要两种做法并存 | 局部更新只用 `PATCH`；分页只用游标分页；响应只有一种包装格式 |
| 可预测的性能 | 不提供表达力无界的查询能力 | 无自定义查询语言；`expand` 深度限一层；分页上限 100 |
| 边界收窄 | 三套 API 分组认证与序列化策略物理隔离 | 内部 API（Session）、Open API（API Key/OAuth）、公开 API（匿名只读）见 §2.1 |
| 错误可编程 | 客户端依据机器码而非文案分支 | 全部错误必须带 `error.code`，文案可变、码不可变 |

---

## 2. URL 设计规范

### 2.1 三套 API 分组

| 分组 | 前缀 | 消费方 | 认证 | 序列化策略 |
| --- | --- | --- | --- | --- |
| 内部 API | `/api/v1/` | `apps/web`、`apps/admin` | Session（+ CSRF） | 完整字段，含成员邮箱、审计信息 |
| Open API | `/api/v1/external/` | 第三方应用、脚本、集成 | API Key（`X-API-Key`）或 OAuth 2.0 Bearer | 稳定契约，字段增删受版本约束，不含内部实现字段 |
| 公开 API | `/api/v1/public/` | `apps/space`（匿名访客） | 匿名（可选分享令牌） | 严格脱敏：剥离邮箱、内部评论、审计、成员列表 |

三者在 Django 项目内分别对应 `plane/app/`、`plane/api/`、`plane/space/` 三个包（见 `monorepo-structure.md` §2），**不共享 Serializer 与 Permission 类**。共享的只有 Model 与领域服务层（`plane/workflow/`、`plane/analytics/` 等）。

### 2.2 版本策略

- 版本号置于路径前缀：`/api/v1/`。**不使用** header 版本协商（不利于调试、缓存与日志分析）。
- `v1` 内只允许**向后兼容**变更：新增可选字段、新增端点、新增可选查询参数。
- 破坏性变更（删除字段、改变字段语义、改变默认行为）必须开 `v2`，`v1` 进入不少于 12 个月的维护期（仅修 bug 与安全问题）。
- 字段废弃流程：标记 `deprecated`（OpenAPI schema 中体现）→ 响应头 `Deprecation: true` + `Sunset: <RFC1123 日期>` → 至少两个迭代后移除。

### 2.3 路径命名规则

| 规则 | 正确 | 错误 |
| --- | --- | --- |
| 集合用**复数名词** | `/issues/` | `/issue/` |
| 全小写，多词用**连字符** | `/issue-properties/`、`/api-tokens/` | `/issueProperties/`、`/issue_properties/` |
| 集合与详情**均以斜杠结尾** | `/issues/`、`/issues/{id}/` | `/issues`、`/issues/{id}` |
| 路径参数用 `{}` 描述，实际为 UUID 或 slug | `/workspaces/{slug}/` | — |
| 禁止出现动词 | `/issues/{id}/archive/`（动作子资源） | `/archiveIssue/` |
| 禁止出现文件扩展名 | `/issues/` + `Accept` 头 | `/issues.json` |

**关于尾斜杠**：Django `APPEND_SLASH` 默认开启会把无尾斜杠请求 301 重定向，而 `POST` 重定向会丢失请求体。因此约定：**所有端点强制尾斜杠**，前端 axios 实例中加拦截器校验（开发环境对缺失尾斜杠的请求直接抛错），从源头消除该类事故。

### 2.4 层级嵌套约定

嵌套体现资源的**所有权**（ownership），不体现关联关系。规则：

- 最大嵌套深度 **3 层资源**（不含 `/api/v1/`）：`workspaces/{slug}/projects/{project_id}/issues/{issue_id}/`。
- 超过 3 层的从属资源，挂在第 3 层资源之下但**不再嵌套项目层以上**：`.../issues/{issue_id}/comments/{comment_id}/`（这是第 4 层，属于「叶子资源的直接子资源」，允许）。
- 若资源可脱离父资源独立存在（如 `users`、`instances`），则**不嵌套**：`/api/v1/users/me/`。
- 关联关系用**动作子资源**表达而非深层嵌套：`.../issues/{id}/links/`、`.../issues/{id}/relations/`。

### 2.5 端点清单（P0 - P2 核心，节选）

工作空间层级：

```
GET    /api/v1/workspaces/                                    工作空间列表
POST   /api/v1/workspaces/                                    创建工作空间
GET    /api/v1/workspaces/{slug}/                             详情
PATCH  /api/v1/workspaces/{slug}/                             更新
DELETE /api/v1/workspaces/{slug}/                             删除
GET    /api/v1/workspaces/slug-check/?slug=xxx                slug 可用性校验
GET    /api/v1/workspaces/{slug}/members/                     成员列表
POST   /api/v1/workspaces/{slug}/invitations/                 批量邀请
PATCH  /api/v1/workspaces/{slug}/members/{member_id}/         调整成员角色
DELETE /api/v1/workspaces/{slug}/members/{member_id}/         移除成员
GET    /api/v1/workspaces/{slug}/labels/                      工作空间级标签
GET    /api/v1/workspaces/{slug}/activities/                  工作空间动态
GET    /api/v1/workspaces/{slug}/issues/                      跨项目工作项聚合查询
```

项目层级：

```
GET    /api/v1/workspaces/{slug}/projects/                              项目列表
POST   /api/v1/workspaces/{slug}/projects/                              创建项目
GET    /api/v1/workspaces/{slug}/projects/{project_id}/                 详情
PATCH  /api/v1/workspaces/{slug}/projects/{project_id}/                 更新
DELETE /api/v1/workspaces/{slug}/projects/{project_id}/                 删除
POST   /api/v1/workspaces/{slug}/projects/{project_id}/archive/         归档（动作子资源）
DELETE /api/v1/workspaces/{slug}/projects/{project_id}/archive/         取消归档
POST   /api/v1/workspaces/{slug}/projects/{project_id}/favorite/        收藏
DELETE /api/v1/workspaces/{slug}/projects/{project_id}/favorite/        取消收藏
GET    /api/v1/workspaces/{slug}/projects/{project_id}/members/         项目成员
GET    /api/v1/workspaces/{slug}/projects/{project_id}/states/          状态集
GET    /api/v1/workspaces/{slug}/projects/{project_id}/labels/          标签
GET    /api/v1/workspaces/{slug}/projects/{project_id}/issue-types/     任务类型
GET    /api/v1/workspaces/{slug}/projects/{project_id}/issue-properties/自定义字段定义
```

工作项层级（系统核心）：

```
GET    .../projects/{project_id}/issues/                        列表（支持全部查询参数）
POST   .../projects/{project_id}/issues/                        创建
GET    .../projects/{project_id}/issues/{issue_id}/             详情
PATCH  .../projects/{project_id}/issues/{issue_id}/             部分更新
DELETE .../projects/{project_id}/issues/{issue_id}/             删除
POST   .../projects/{project_id}/issues/{issue_id}/duplicate/   复制
POST   .../projects/{project_id}/issues/{issue_id}/archive/     归档
POST   .../projects/{project_id}/issues/bulk/                   批量创建
PATCH  .../projects/{project_id}/issues/bulk/                   批量更新（拖拽多选场景）
DELETE .../projects/{project_id}/issues/bulk/                   批量删除
GET    .../issues/{issue_id}/sub-issues/                        子任务
POST   .../issues/{issue_id}/sub-issues/                        挂载子任务
GET    .../issues/{issue_id}/relations/                         依赖关系（blocks/blocked_by/relates_to/duplicate）
POST   .../issues/{issue_id}/relations/                         建立关系
GET    .../issues/{issue_id}/comments/                          评论
POST   .../issues/{issue_id}/comments/                          发表评论
GET    .../issues/{issue_id}/activities/                        操作日志（审计）
GET    .../issues/{issue_id}/attachments/                       附件
POST   .../issues/{issue_id}/attachments/presign/               申请预签名直传 URL
GET    .../issues/{issue_id}/worklogs/                          工时记录
POST   .../issues/{issue_id}/transitions/                       工作流状态流转（含校验与审批触发）
GET    .../issues/{issue_id}/transitions/available/             当前可用流转（供前端渲染按钮）
```

视图 / 迭代 / 模块 / 文档：

```
GET|POST      .../projects/{project_id}/views/                  自定义视图（Saved View）
GET|POST      .../projects/{project_id}/cycles/                 迭代
POST          .../cycles/{cycle_id}/issues/                     迭代关联工作项
GET|POST      .../projects/{project_id}/modules/                模块 / 项目集
GET|POST      .../projects/{project_id}/pages/                  协作文档
POST          .../pages/{page_id}/collab-token/                 换取 live 协同票据
```

认证与账户：

```
POST   /api/v1/auth/sign-up/                    注册
POST   /api/v1/auth/sign-in/                    登录（建立 Session）
POST   /api/v1/auth/sign-out/                   退出
POST   /api/v1/auth/forgot-password/            发起重置
POST   /api/v1/auth/reset-password/             提交重置
GET    /api/v1/auth/csrf-token/                 获取 CSRF token
GET    /api/v1/users/me/                        当前用户
PATCH  /api/v1/users/me/                        更新个人信息
GET    /api/v1/users/me/settings/               偏好设置
GET|POST|DELETE /api/v1/api-tokens/             API Key 管理
```

实例管理（admin，需系统管理员）：

```
GET|PATCH  /api/v1/instances/                       实例配置
GET|PATCH  /api/v1/instances/configurations/        SMTP / 认证方式 / AI / 存储
GET        /api/v1/instances/users/                 全站用户
PATCH      /api/v1/instances/users/{user_id}/       禁用/启用/改角色
GET        /api/v1/instances/audit-logs/            审计日志（P3）
```

### 2.6 动作子资源（Action Sub-resource）模式

对于无法自然映射到 CRUD 的操作，**不在路径中使用动词，而是把动作建模为子资源**，用 HTTP 方法表达方向：

| 语义 | 端点 | 方法 | 说明 |
| --- | --- | --- | --- |
| 归档 / 取消归档 | `.../issues/{id}/archive/` | `POST` / `DELETE` | 幂等：重复 POST 返回 200 而非报错 |
| 收藏 / 取消收藏 | `.../projects/{id}/favorite/` | `POST` / `DELETE` | 同上 |
| 状态流转 | `.../issues/{id}/transitions/` | `POST` | 请求体带 `to_state_id`、`comment`、必填字段补齐；服务端执行流转校验与审批触发 |
| 转交 | `.../issues/{id}/assignees/` | `PUT` | 全量替换执行人集合（这是唯一允许使用 PUT 的场景类型，见 §3.2） |
| 需求转任务 | `.../issues/{id}/decompose/` | `POST` | 请求体为子任务数组，事务内批量创建并建立父子关联 |
| 批量操作 | `.../issues/bulk/` | `POST` / `PATCH` / `DELETE` | 请求体携带 id 数组 |

---

## 3. HTTP 方法语义

### 3.1 方法约定表

| 方法 | 用途 | 请求体 | 成功状态码 | 幂等 | 安全 |
| --- | --- | --- | --- | --- | --- |
| `GET` | 获取列表或详情 | 无 | `200 OK` | ✅ | ✅ |
| `POST` | 创建资源 / 执行动作 | 有 | `201 Created`（创建，带 `Location` 头）/ `200 OK`（动作）/ `202 Accepted`（异步任务） | ❌（幂等键除外，见 §3.4） | ❌ |
| `PATCH` | **部分更新** | 有（仅含变更字段） | `200 OK` | ❌ | ❌ |
| `PUT` | 全量替换（**仅集合型子资源**） | 有（全量） | `200 OK` | ✅ | ❌ |
| `DELETE` | 删除资源 / 撤销动作 | 无 | `204 No Content`（无返回体）/ `200 OK`（返回受影响信息） | ✅ | ❌ |
| `HEAD` / `OPTIONS` | 由 DRF 自动处理 | — | `200 OK` | ✅ | ✅ |

### 3.2 为什么用 PATCH 而非 PUT 做更新（对标 Plane 的设计决策）

Plane 的全部资源更新端点使用 `PATCH`，本系统完全沿用。理由：

1. **协作场景的并发安全**：项目管理系统的典型场景是多人同时编辑同一工作项——A 改优先级、B 改执行人。`PUT` 语义要求客户端提交完整资源表示，服务端以之整体替换，会把客户端**加载时的旧快照**中未变更的字段一并写回，**静默覆盖** B 的修改（lost update）。`PATCH` 只提交变更字段，冲突面收窄到「同字段同时改」。
2. **契合前端交互形态**：看板拖拽只改 `state_id` + `sort_order`；详情页侧栏改单个属性即触发保存。`PATCH` 的请求体天然是「一个字段」，`PUT` 则要求前端持有并回传完整对象（含只读字段），既浪费带宽也易引入脏数据。
3. **契合 DRF 实现**：DRF `ModelViewSet` 的 `partial_update` 将 `serializer(instance, data=..., partial=True)`，未提供的字段跳过校验；而 `update`（PUT）会对全部必填字段做校验，导致「只改一个字段却因为另一个必填字段未传而 400」。
4. **字段级权限的可行性**：企业版要求字段权限（只读/隐藏/必填）。`PATCH` 可精确判定「本次请求试图修改哪些字段」并逐字段鉴权；`PUT` 下无法区分「用户主动改」与「回传原值」。
5. **审计日志质量**：`PATCH` 的请求体本身就是变更集，`Activity` 记录可直接落 `field / old_value / new_value` 三元组；`PUT` 需先 diff。

**因此：`PUT` 在本系统中被限制为仅用于「集合型子资源的全量替换」**（如 `PUT .../issues/{id}/assignees/` 替换执行人集合、`PUT .../issues/{id}/labels/` 替换标签集合）。这类场景语义上确实是「用新集合替换旧集合」，且集合本身就是完整表示。除此之外**禁止使用 PUT**，CI 中扫描路由表，出现非白名单的 PUT 端点直接失败。

### 3.3 乐观并发控制

`PATCH` 收窄了冲突面但不能消除。对高冲突资源（Issue、Page、Cycle）追加版本校验：

- 响应携带 `ETag`（值为资源 `updated_at` 的强哈希）。
- 客户端 `PATCH` 时可选携带 `If-Match: <etag>`。
- 服务端比对不一致返回 `409 Conflict`，错误码 `RESOURCE_CONFLICT`，`details` 中给出服务端当前值，由前端提示「他人已修改」并提供合并选项。
- 未携带 `If-Match` 时按 last-write-wins 处理（兼容简单客户端），但**字段级**写入——未在请求体中出现的字段绝不被覆盖。

### 3.4 幂等键（Idempotency-Key）

创建类 `POST` 支持可选请求头 `Idempotency-Key: <客户端生成 UUID>`：

- 服务端以 `(user_id, endpoint, key)` 为唯一键，在 Redis 中缓存首次响应 24 小时。
- 重复请求直接返回首次响应体与状态码，并附 `Idempotency-Replayed: true` 响应头。
- 适用场景：网络抖动重试、前端双击提交、Webhook 重投。
- **强制要求**：Open API 的所有创建端点、以及涉及金额/配额的操作必须支持；内部 API 建议在「批量导入」「创建工作项」上启用。

---

## 4. 统一响应格式

### 4.1 成功响应

所有 `2xx` 响应（除 `204`）必须为以下结构，**无例外**：

```json
{
  "status": "success",
  "data": { },
  "meta": { }
}
```

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `status` | `"success"` | ✅ | 固定字面量，便于客户端无需看状态码即可分支 |
| `data` | `object \| array \| null` | ✅ | 详情端点为对象；列表端点为数组；动作端点无返回内容时为 `null` |
| `meta` | `object` | ⭕ | 分页、统计、限流等旁路信息。列表端点必填；详情端点可省略 |

**详情响应示例**：

```json
{
  "status": "success",
  "data": {
    "id": "8a1f9c2e-6b3d-4a7e-9f11-2c4d5e6f7a8b",
    "sequence_id": 1042,
    "name": "支持看板卡片批量拖拽",
    "description_html": "<p>…</p>",
    "priority": "high",
    "state_id": "3f2c…",
    "type_id": "9d8e…",
    "assignee_ids": ["6c7d…", "2b3a…"],
    "label_ids": ["a1b2…"],
    "start_date": "2026-09-01",
    "target_date": "2026-09-15",
    "estimate_point": 5,
    "sort_order": 65536,
    "parent_id": null,
    "sub_issues_count": 3,
    "completed_sub_issues_count": 1,
    "attachment_count": 2,
    "link_count": 0,
    "created_at": "2026-08-20T03:12:45.120Z",
    "updated_at": "2026-08-28T11:02:03.884Z",
    "created_by": "6c7d…",
    "updated_by": "2b3a…"
  }
}
```

**列表响应示例**：

```json
{
  "status": "success",
  "data": [
    { "id": "…", "name": "…" },
    { "id": "…", "name": "…" }
  ],
  "meta": {
    "next_cursor": "100:1:0",
    "prev_cursor": "100:0:1",
    "next_page_results": true,
    "prev_page_results": false,
    "count": 100,
    "total_count": 1247,
    "total_pages": 13,
    "page": 1,
    "per_page": 100
  }
}
```

**分组列表响应**（看板/分组视图，`?group_by=state_id`）：

```json
{
  "status": "success",
  "data": {
    "3f2c-backlog": { "results": [ ], "total_results": 42 },
    "7a9d-in-progress": { "results": [ ], "total_results": 18 }
  },
  "meta": {
    "grouped_by": "state_id",
    "sub_grouped_by": null,
    "total_count": 60
  }
}
```

分组端点的每组独立分页（每组默认返回前 25 条 + `total_results`），前端按需对单组「加载更多」，避免看板首屏拉取全量。

### 4.2 错误响应

所有 `4xx` / `5xx` 响应必须为以下结构，**无例外**（包括 DRF 默认抛出的 404/403/405，由全局异常处理器统一改写）：

```json
{
  "status": "error",
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "请求参数校验失败",
    "details": [
      {
        "field": "name",
        "code": "REQUIRED",
        "message": "该字段为必填项"
      },
      {
        "field": "target_date",
        "code": "INVALID_DATE_RANGE",
        "message": "截止时间不能早于开始时间"
      }
    ],
    "request_id": "01JBX3K9Q7ZR4M8N2P5V6W7X8Y",
    "doc_url": "https://docs.example.com/api/errors#validation-error"
  }
}
```

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `status` | `"error"` | ✅ | 固定字面量 |
| `error.code` | `string` | ✅ | 机器可读错误码（§8 全表）。**客户端必须依据此字段分支，禁止匹配 message 文案** |
| `error.message` | `string` | ✅ | 面向用户的中文提示，可直接展示；文案可能随版本调整 |
| `error.details` | `array` | ⭕ | 字段级错误明细，校验错误必填；每项含 `field` / `code` / `message`；嵌套字段用点号路径 `properties.0.value` |
| `error.request_id` | `string` | ✅ | 请求追踪 ID（ULID），与服务端日志、Sentry 事件一一对应，用户报障时提供此 ID 即可定位 |
| `error.doc_url` | `string` | ⭕ | 错误码文档锚点 |

### 4.3 HTTP 状态码使用约定

| 状态码 | 使用场景 | 禁止误用 |
| --- | --- | --- |
| `200` | GET/PATCH/PUT 成功，POST 动作成功 | — |
| `201` | POST 创建成功，必须带 `Location` 响应头 | 不要用 200 代替 |
| `202` | 请求已受理，异步处理中（导入、导出、批量归档）；`data` 返回 `task_id` 与查询端点 | — |
| `204` | DELETE 成功且无返回体 | **`204` 响应体必须为空**，不得包装 `{status:"success"}` |
| `400` | 请求格式/参数校验失败 | 不要用于权限问题 |
| `401` | 未认证或凭证失效 | 不要用于「已登录但无权限」 |
| `403` | 已认证但无权限；CSRF 校验失败 | 不要用于「资源不存在」 |
| `404` | 资源不存在，或**因权限不可见而故意隐藏存在性** | — |
| `405` | 方法不允许（DRF 自动） | — |
| `409` | 唯一性冲突、状态冲突、乐观锁冲突、工作流非法流转 | 不要用 400 代替 |
| `413` | 请求体过大（Nginx 层拦截，需返回统一 JSON） | — |
| `422` | **本系统不使用**，统一用 `400` + `VALIDATION_ERROR` | 避免 400/422 语义争议 |
| `429` | 触发限流，必须带 `Retry-After` 与限流响应头 | — |
| `500` | 未预期的服务端异常，`message` 固定为通用文案不泄露堆栈 | 已知业务失败不得用 500 |
| `502` / `503` / `504` | 上游不可用 / 维护模式 / 超时（proxy 层需返回统一 JSON 而非 Nginx HTML 默认页） | — |

**关于 404 vs 403 的一致性策略**：对于用户**无权知晓其存在**的资源（他人私密项目、涉密项目、未公开的 space 资源），统一返回 `404` + `RESOURCE_NOT_FOUND`，防止通过 403/404 差异探测资源存在性。对于用户**能看见但不能操作**的资源，返回 `403` + 具体 `PERM_*` 码。判定规则写入 Permission 基类，不由各 ViewSet 自行决定。

### 4.4 通用响应头

| 响应头 | 说明 |
| --- | --- |
| `X-Request-Id` | 请求追踪 ID，与 `error.request_id` 同值；成功响应也返回 |
| `X-RateLimit-Limit` / `-Remaining` / `-Reset` | 限流信息，见 §7 |
| `ETag` | 高冲突资源的版本标识 |
| `Deprecation` / `Sunset` | 端点或字段废弃提示 |
| `Idempotency-Replayed` | 幂等重放标识 |
| `Cache-Control` | 内部 API 统一 `no-store`；public API 的只读资源可 `public, max-age=60` |

### 4.5 数据类型与命名约定

| 项 | 约定 |
| --- | --- |
| 字段命名 | `snake_case`（与 Django/DRF 一致，避免序列化层做键名转换带来的调试成本与性能开销） |
| 主键 | UUID v4 字符串；同时对外暴露人类可读的 `sequence_id`（项目内自增，用于 `PROJ-1042` 展示） |
| 时间 | ISO 8601 + UTC + 毫秒 + `Z`：`2026-08-28T11:02:03.884Z`。**服务端一律存 UTC**，时区转换在前端完成 |
| 日期（无时间） | `YYYY-MM-DD`（`start_date` / `target_date` 为日期而非时间戳，避免跨时区偏移一天的经典 bug） |
| 枚举 | 小写下划线字符串，禁用数字魔法值：`"in_progress"`、`"urgent"` |
| 布尔 | 真正的 `true`/`false`，禁止 `0`/`1`/`"true"` |
| 空值 | 用 `null` 表示「无值」，用 `[]` / `{}` 表示「空集合」，二者语义不可混用 |
| 关联字段 | 默认返回 ID：单值 `state_id`、多值 `assignee_ids`；`expand` 时追加对象形式 `state`、`assignees`（§5.2） |
| 富文本 | 三字段并存：`description_html`（渲染）、`description_json`（ProseMirror 文档，编辑器权威来源）、`description_stripped`（纯文本，供搜索与列表摘要） |
| 数值排序键 | `sort_order` 使用浮点/大整数间隔分配（LexoRank 思路），拖拽仅更新单条记录，避免整列重排 |
| 金额/工时 | 整数最小单位（工时用分钟），禁止浮点 |

---

## 5. 查询能力规范

### 5.1 字段选择：`?fields=`

对标 Plane 的 `fields` 参数实现。

```http
GET /api/v1/workspaces/acme/projects/{pid}/issues/?fields=id,name,priority,state_id
```

- 逗号分隔字段名；不传则返回该端点的默认字段集。
- 支持点号路径裁剪 `expand` 后的嵌套对象：`?expand=state&fields=id,name,state.name,state.color`。
- 未知字段名**忽略而非报错**（前向兼容，防止客户端因服务端字段重命名而整体失败）。
- `id` 始终强制包含（客户端需要主键做规范化存储）。
- 实现要点：在 Serializer 层裁剪 `fields`，同时把字段集传给 QuerySet 层做 `only()` / 跳过不必要的 `prefetch_related`，让裁剪**真正减少数据库负载**而非仅减少响应体积。

**典型收益**：看板视图仅需 12 个字段，全字段响应约 4.2 KB/条，裁剪后约 380 B/条；1000 条卡片场景响应体从 4.2 MB 降至 380 KB。

### 5.2 关联展开：`?expand=`

对标 Plane 的 `expand` 参数实现。

```http
GET .../issues/?expand=state,assignees,labels,type
```

| 约束 | 说明 |
| --- | --- |
| 展开深度 | **最多一层**。`?expand=assignees.member.avatar` ❌ 直接返回 `400 VALIDATION_INVALID_PARAM` |
| 白名单 | 每个 ViewSet 声明 `expand_fields` 白名单，非白名单字段忽略 |
| N+1 防护 | 每个可展开字段必须在 `expand_map` 中声明对应的 `select_related` / `Prefetch`，由基类自动应用。**未声明映射的字段不允许进入白名单**（CI 通过单元测试断言 `assertNumQueries` 守护） |
| 组合行为 | `expand` 后原 ID 字段保留（`state_id` 与 `state` 并存），便于客户端规范化存储不必解构 |
| 上限 | 单请求 `expand` 字段数 ≤ 5；分页 `per_page` 与 `expand` 组合时，若 `per_page > 50` 且 `expand` 字段数 > 3，服务端降级 `per_page` 至 50 并在 `meta.degraded` 中告知 |

展开后响应片段：

```json
{
  "id": "8a1f…",
  "state_id": "3f2c…",
  "state": { "id": "3f2c…", "name": "进行中", "group": "started", "color": "#f59e0b" },
  "assignee_ids": ["6c7d…"],
  "assignees": [
    { "id": "6c7d…", "display_name": "张三", "avatar_url": "https://…" }
  ]
}
```

### 5.3 筛选

```http
GET .../issues/?state_id=3f2c…,7a9d…&priority=high,urgent&assignee_ids=6c7d…&target_date=2026-09-01;before
```

| 语法 | 语义 | 示例 |
| --- | --- | --- |
| `?field=value` | 等值 | `?priority=high` |
| `?field=v1,v2` | IN（逗号分隔即 OR） | `?priority=high,urgent` |
| `?field__isnull=true` | 空值判定 | `?assignee_ids__isnull=true`（未指派） |
| `?field=<date>;before` / `;after` | 日期比较（分号分隔修饰符，对标 Plane） | `?target_date=2026-09-30;before` |
| `?field=<a>,<b>;between` | 区间 | `?created_at=2026-08-01,2026-08-31;between` |
| `?field=<n>;gte` / `;lte` | 数值比较 | `?estimate_point=3;gte` |
| `?search=keyword` | 全文搜索（见 §5.5） | `?search=拖拽` |
| `?subscriber=me` / `?assignee=me` | 语义化快捷值 `me` 解析为当前用户 | — |
| 自定义字段 | `?property.<property_id>=<value>` | `?property.4f8a…=已评审` |

规则：

- **不同参数之间为 AND，同一参数内逗号为 OR**。这是唯一的组合语义，不支持任意布尔表达式。
- 需要复杂条件（嵌套 AND/OR/NOT）的场景，走 **Saved View**：`POST .../views/` 保存条件树，查询时 `?view_id=<id>`。条件树在服务端解析并受深度限制（≤ 3 层，≤ 20 个条件节点），杜绝客户端构造病态查询。
- 所有可筛选字段必须建立索引；**未建索引的字段不允许开放筛选**（CI 通过迁移检查脚本比对 `filterset_fields` 与索引清单）。
- 未知筛选参数：**忽略并在 `meta.ignored_params` 中回显**（前向兼容 + 便于发现前端拼写错误）。

### 5.4 排序：`?ordering=`

```http
GET .../issues/?ordering=-priority,target_date,-created_at
```

- 前缀 `-` 表示降序，无前缀为升序；逗号分隔实现多级排序。
- 每个 ViewSet 声明 `ordering_fields` 白名单，非白名单字段返回 `400 VALIDATION_INVALID_PARAM`（此处**不忽略**，因为静默忽略排序会让用户看到错误顺序却无提示）。
- 默认排序必须**确定且唯一**：所有列表端点的最终排序键追加 `-created_at, -id`，否则游标分页在排序值相同时会出现记录重复/丢失。
- 语义排序：`priority`、`state` 等枚举字段按业务权重排序而非字典序（数据库层用 `Case/When` 或预置权重列实现）。
- 手动排序：看板/列表的人工拖拽顺序用 `sort_order` 字段，`?ordering=sort_order`。

### 5.5 搜索：`?search=`

| 层级 | 实现 | 触发条件 |
| --- | --- | --- |
| 列表内搜索 | `?search=` 在 `name` / `sequence_id` / `description_stripped` 上做匹配 | 关键词长度 ≥ 1 |
| 前缀/模糊匹配 | PostgreSQL `pg_trgm` + GIN 索引，`ILIKE` 或相似度排序 | 关键词 < 3 字符时仅前缀匹配（避免全表扫描） |
| 全文检索 | `tsvector` 列（`name` + `description_stripped`，随写入由触发器更新）+ GIN 索引 | 关键词 ≥ 3 字符 |
| 全局搜索 | `GET /api/v1/workspaces/{slug}/search/?q=&types=issue,project,page,cycle` | ⌘K 命令面板；按类型分组返回，每类限 10 条 |

搜索结果必须先经权限过滤再排序，**禁止**「先搜后过滤」造成分页数量不稳定。

---

## 6. 分页设计

### 6.1 为什么用游标分页

对标 Plane 的实现。工作项列表是高频写入的数据集，使用 `?page=N&limit=M` 的 offset 分页存在两个硬伤：

1. **翻页漂移**：翻到第 3 页时若有人在前面插入/删除记录，会导致记录重复出现或被跳过；
2. **深翻性能塌陷**：`OFFSET 10000` 需数据库扫描并丢弃 10000 行，延迟随页码线性上升。

游标分页以「上一页最后一条记录的排序键」为锚点，翻页转化为 `WHERE (sort_key) < (last_value)` 的索引范围扫描，延迟恒定且无漂移。

### 6.2 游标格式（对标 Plane：`value:offset:is_prev`）

游标为字符串 `"{value}:{offset}:{is_prev}"`：

| 分段 | 含义 |
| --- | --- |
| `value` | 页大小（per_page）。首次请求可为空 |
| `offset` | 当前偏移量（页序号 × 页大小），用于定位与计算 `page` |
| `is_prev` | `0` = 向后翻页，`1` = 向前翻页 |

示例流程：

```
第一页：  GET .../issues/?per_page=100
          → meta.next_cursor = "100:1:0"   meta.prev_cursor = "100:0:1"

第二页：  GET .../issues/?cursor=100:1:0&per_page=100
          → meta.next_cursor = "100:2:0"   meta.prev_cursor = "100:0:0"
```

**保留此格式的理由**：与 Plane 生态兼容（便于对照排查、复用其前端分页组件思路），且该格式同时携带页大小与偏移，使响应可以给出 `page` / `total_pages`，兼顾「游标翻页的稳定性」与「用户可见页码」的产品需求。游标值经 URL 安全的 Base64 编码传输，避免客户端拼装或篡改语义（服务端解码失败返回 `400 VALIDATION_INVALID_CURSOR`）。

### 6.3 分页参数与元信息

| 参数 | 默认 | 上限 | 说明 |
| --- | --- | --- | --- |
| `per_page` | `100` | `100` | 超过上限**静默截断为 100** 并在 `meta.degraded` 中告知，不报错 |
| `cursor` | 空（首页） | — | 由服务端在上一次响应中给出，客户端原样回传 |

`meta` 必含字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `next_cursor` | `string` | 下一页游标 |
| `prev_cursor` | `string` | 上一页游标 |
| `next_page_results` | `boolean` | 是否存在下一页（客户端据此决定是否显示「加载更多」） |
| `prev_page_results` | `boolean` | 是否存在上一页 |
| `count` | `number` | 本页实际返回条数 |
| `total_count` | `number` | 符合筛选条件的总条数 |
| `total_pages` | `number` | 总页数 |
| `page` | `number` | 当前页序号（1-based） |
| `per_page` | `number` | 实际生效的页大小 |

### 6.4 `total_count` 的性能约定

`COUNT(*)` 在大表 + 复杂筛选下可能比主查询更慢。约定：

- `total_count` 默认返回精确值（工作项量级在单项目内通常 < 10 万，可接受）。
- 当筛选后的估算行数 > 50,000 时，改用 PostgreSQL 执行计划估算值，并在 `meta.total_count_estimated: true` 中标记。
- 客户端可显式传 `?count=false` 跳过计数（无限滚动场景不需要总数），此时 `total_count` 与 `total_pages` 为 `null`。

---

## 7. 限流规范

### 7.1 限流层级

三层防护，从外到内：

| 层级 | 位置 | 粒度 | 目的 |
| --- | --- | --- | --- |
| L1 边缘限流 | `apps/proxy`（Nginx `limit_req_zone`） | 按 IP，300 req/min（burst 60） | 抵御扫描与粗暴刷接口，保护应用层不被打满 |
| L2 应用限流 | DRF Throttle（Redis 计数） | 按用户 / API Key / 匿名 IP | 业务级公平性与配额 |
| L3 端点限流 | ViewSet 级 `throttle_classes` 覆盖 | 按端点 | 保护高成本端点 |

### 7.2 L2 配额表

| 主体 | 配额 | 说明 |
| --- | --- | --- |
| 已认证用户（内部 API） | **60 请求/分钟**（对标 Plane） | 按 `user_id` 计数；正常交互远低于此值 |
| API Key（Open API） | **60 请求/分钟** | 按 token 计数；企业版可按订阅提额 |
| OAuth 应用 | 60 请求/分钟/(用户 × 应用) | 防止单应用耗尽用户配额 |
| 匿名（public API） | 30 请求/分钟 | 按 IP |
| 登录/注册/重置密码 | 10 请求/分钟 + 失败 5 次锁定 15 分钟 | 按 IP + 账号双维度，抵御撞库 |
| 文件预签名申请 | 30 请求/分钟 | 防刷上传凭证 |
| 报表聚合端点 | 10 请求/分钟 | 高 CPU 成本 |
| 搜索端点 | 30 请求/分钟 | 高 IO 成本 |
| 批量端点 | 10 请求/分钟，单次 ≤ 100 条 | 双重限制 |
| Webhook 出站投递 | 不计入用户配额，独立队列与重试策略 | — |

### 7.3 限流响应头

**所有响应**（包括成功响应）都必须携带：

| 响应头 | 说明 | 示例 |
| --- | --- | --- |
| `X-RateLimit-Limit` | 当前窗口配额上限 | `60` |
| `X-RateLimit-Remaining` | 当前窗口剩余次数 | `47` |
| `X-RateLimit-Reset` | 窗口重置的 Unix 时间戳（秒） | `1788230400` |
| `Retry-After` | **仅 429 时返回**，需等待的秒数 | `23` |

`429` 响应体：

```json
{
  "status": "error",
  "error": {
    "code": "RATE_LIMIT_EXCEEDED",
    "message": "请求过于频繁，请在 23 秒后重试",
    "details": [{ "field": "retry_after", "code": "RETRY_AFTER", "message": "23" }],
    "request_id": "01JBX3K9Q7ZR4M8N2P5V6W7X8Y"
  }
}
```

### 7.4 客户端配合约定

前端 axios 拦截器统一处理 `429`：指数退避重试（初始 1s，因子 2，抖动 ±20%，最多 3 次），仍失败则提示用户；`Retry-After` 优先于退避算法计算值。**幂等方法（GET/PUT/DELETE）可自动重试；`POST` 仅在携带 `Idempotency-Key` 时允许自动重试**，否则只提示不重试。

---

## 8. 错误码体系

### 8.1 错误码设计规则

| 规则 | 说明 |
| --- | --- |
| 命名格式 | `<分类前缀>_<具体语义>`，全大写 + 下划线 |
| 稳定性 | 错误码一经发布**永不修改语义、永不复用**；废弃码保留在表中标记 `DEPRECATED` |
| 唯一性 | 全局唯一，不按端点区分（同一语义在任何端点使用同一码） |
| 定义位置 | 后端 `plane/utils/error_codes.py`（Python Enum）；前端 `packages/types/src/api/error-codes.ts`（联合类型）。**两者由脚本校验一致性**，不一致则 CI 失败 |
| 客户端契约 | 前端只允许对 `error.code` 做分支；出现 `message` 字符串匹配即视为 Bug |

### 8.2 认证错误（AUTH_*）→ 401

| 错误码 | HTTP | 触发场景 | 客户端建议动作 |
| --- | --- | --- | --- |
| `AUTH_REQUIRED` | 401 | 未携带任何有效凭证访问受保护资源 | 跳转登录页，保留 `next` 回跳地址 |
| `AUTH_SESSION_EXPIRED` | 401 | Session 过期 | 静默跳登录，提示「登录已过期」 |
| `AUTH_INVALID_CREDENTIALS` | 401 | 邮箱或密码错误 | 表单错误提示（**不区分邮箱不存在与密码错误**，防用户枚举） |
| `AUTH_INVALID_TOKEN` | 401 | API Key / Bearer token 无效或格式错误 | 提示检查凭证 |
| `AUTH_TOKEN_EXPIRED` | 401 | Token 已过期 | 触发刷新流程或提示重新签发 |
| `AUTH_TOKEN_REVOKED` | 401 | Token 已被吊销 | 提示凭证已失效 |
| `AUTH_ACCOUNT_DISABLED` | 401 | 账号被管理员禁用 | 提示联系管理员，不再重试 |
| `AUTH_EMAIL_NOT_VERIFIED` | 401 | 邮箱未验证且实例要求验证 | 引导重发验证邮件 |
| `AUTH_MFA_REQUIRED` | 401 | 需二次验证（P3） | 跳转 MFA 输入 |
| `AUTH_SSO_REQUIRED` | 401 | 实例强制 SSO，禁止密码登录（P3） | 跳转 IdP |
| `AUTH_PASSWORD_RESET_INVALID` | 400 | 重置令牌无效或已使用 | 引导重新发起重置 |
| `AUTH_PASSWORD_RESET_EXPIRED` | 400 | 重置令牌过期 | 同上 |
| `AUTH_TOO_MANY_ATTEMPTS` | 429 | 登录失败次数超限，账号临时锁定 | 展示剩余锁定时间 |
| `AUTH_OAUTH_INVALID_GRANT` | 400 | OAuth code / refresh_token 无效 | 重新授权 |
| `AUTH_OAUTH_INVALID_CLIENT` | 401 | client_id / client_secret 错误 | 检查应用配置 |
| `AUTH_OAUTH_INVALID_SCOPE` | 400 | 申请了未注册的 scope | 修正授权请求 |
| `AUTH_CSRF_FAILED` | 403 | CSRF token 缺失或不匹配 | 重新获取 CSRF token 后重试一次 |

### 8.3 权限错误（PERM_*）→ 403

| 错误码 | HTTP | 触发场景 | 客户端建议动作 |
| --- | --- | --- | --- |
| `PERM_DENIED` | 403 | 通用权限不足（兜底码） | 展示无权限空态 |
| `PERM_ROLE_INSUFFICIENT` | 403 | 角色等级不足（如普通成员执行管理员操作） | 隐藏入口 + 提示所需角色 |
| `PERM_NOT_WORKSPACE_MEMBER` | 403 | 非该工作空间成员 | 引导申请加入 |
| `PERM_NOT_PROJECT_MEMBER` | 403 | 非该项目成员 | 引导申请加入项目 |
| `PERM_PROJECT_ARCHIVED` | 403 | 项目已归档，禁止写操作 | 界面切只读态 |
| `PERM_WORKSPACE_ARCHIVED` | 403 | 工作空间已归档 | 同上 |
| `PERM_FIELD_READ_ONLY` | 403 | 字段级权限为只读（企业版字段权限） | 禁用对应表单控件，`details` 列出字段名 |
| `PERM_FIELD_HIDDEN` | 403 | 试图访问对当前角色隐藏的字段 | 界面不渲染该字段 |
| `PERM_TRANSITION_NOT_ALLOWED` | 403 | 当前角色无权执行该状态流转 | 该流转按钮置灰 |
| `PERM_APPROVAL_NOT_ASSIGNEE` | 403 | 非审批节点指定审批人 | 隐藏审批操作区 |
| `PERM_LICENSE_REQUIRED` | 403 | 企业版功能但当前许可不含该能力 | 展示升级引导 |
| `PERM_SEAT_LIMIT_EXCEEDED` | 403 | 席位数超出许可 | 展示席位管理入口 |
| `PERM_IP_NOT_ALLOWED` | 403 | 不在实例 IP 白名单内（P3） | 提示网络环境限制 |
| `PERM_TOKEN_SCOPE_INSUFFICIENT` | 403 | API Key 的 scope 不覆盖本次操作 | 提示重建具备所需 scope 的 Key |

### 8.4 校验错误（VALIDATION_*）→ 400

| 错误码 | HTTP | 触发场景 | 说明 |
| --- | --- | --- | --- |
| `VALIDATION_ERROR` | 400 | 字段级校验失败（**最常用**） | `details` 必须逐字段列出，字段级子码见 §8.8 |
| `VALIDATION_INVALID_JSON` | 400 | 请求体不是合法 JSON | — |
| `VALIDATION_INVALID_PARAM` | 400 | 查询参数非法（未知排序字段、非法 expand 深度） | `details.field` 指向参数名 |
| `VALIDATION_INVALID_CURSOR` | 400 | 游标解码失败或格式非法 | 客户端应回到首页 |
| `VALIDATION_UNSUPPORTED_MEDIA_TYPE` | 415 | `Content-Type` 非 `application/json`（文件上传除外） | — |
| `VALIDATION_PAYLOAD_TOO_LARGE` | 413 | 请求体超限 | Nginx 与 Django 两层都需返回此码 |
| `VALIDATION_BULK_LIMIT_EXCEEDED` | 400 | 批量操作条数超上限（100） | — |
| `VALIDATION_FILE_TYPE_NOT_ALLOWED` | 400 | 附件类型不在白名单 | `details` 列出允许的类型 |
| `VALIDATION_FILE_SIZE_EXCEEDED` | 400 | 附件体积超限 | `details` 给出上限 |
| `VALIDATION_INVALID_DATE_RANGE` | 400 | 截止时间早于开始时间 | — |
| `VALIDATION_CUSTOM_FIELD_INVALID` | 400 | 自定义字段值不符合其定义（类型/枚举/正则/必填） | `details.field` 为 `property.<property_id>` |
| `VALIDATION_REQUIRED_FIELD_MISSING` | 400 | 工作流流转要求的必填字段未提供 | `details` 列出缺失字段，前端弹出补齐表单 |
| `VALIDATION_ESTIMATE_REQUIRED` | 400 | 流转要求工时必填但未填 | — |

### 8.5 资源错误（RESOURCE_*）→ 404 / 409 / 410

| 错误码 | HTTP | 触发场景 | 客户端建议动作 |
| --- | --- | --- | --- |
| `RESOURCE_NOT_FOUND` | 404 | 资源不存在，或因权限不可见而隐藏存在性 | 展示 404 空态 |
| `RESOURCE_GONE` | 410 | 资源曾存在但已永久删除 | 展示「已被删除」并清理本地缓存 |
| `RESOURCE_ALREADY_EXISTS` | 409 | 唯一性冲突（工作空间 slug、项目标识、状态名重复） | 表单字段级提示 |
| `RESOURCE_CONFLICT` | 409 | 乐观锁冲突（`If-Match` 不匹配） | 提示「他人已修改」，提供刷新/覆盖选项，`details` 含服务端当前值 |
| `RESOURCE_STATE_INVALID` | 409 | 资源当前状态不允许该操作（如归档项目下创建工作项） | 提示当前状态限制 |
| `RESOURCE_TRANSITION_INVALID` | 409 | 工作流不存在该状态流转路径 | 刷新可用流转列表 |
| `RESOURCE_TRANSITION_BLOCKED` | 409 | 流转被约束拦截（前置任务未完成等） | `details` 列出阻塞原因与阻塞项 ID |
| `RESOURCE_CIRCULAR_DEPENDENCY` | 409 | 任务依赖/父子关系构成环 | `details` 给出环路径 |
| `RESOURCE_IN_USE` | 409 | 被引用的资源不可删除（状态下仍有工作项、标签仍被使用） | 提示先迁移引用，`details` 给出引用数量 |
| `RESOURCE_LIMIT_EXCEEDED` | 409 | 触达数量上限（单项目自定义字段数、子任务层级深度） | 提示上限值 |
| `RESOURCE_LOCKED` | 409 | 资源被锁定（文档锁定编辑、任务基线锁定字段） | 界面切只读并显示锁定人 |

### 8.6 服务端错误（SERVER_*）→ 5xx

| 错误码 | HTTP | 触发场景 | 客户端建议动作 |
| --- | --- | --- | --- |
| `SERVER_ERROR` | 500 | 未预期异常（兜底码） | 展示通用错误 + `request_id`，引导反馈 |
| `SERVER_DATABASE_ERROR` | 500 | 数据库异常（连接失败、死锁重试耗尽） | 建议稍后重试 |
| `SERVER_STORAGE_ERROR` | 500 | 对象存储不可用（MinIO/S3） | 上传失败提示，允许重试 |
| `SERVER_QUEUE_ERROR` | 500 | 消息队列投递失败（RabbitMQ 不可用） | 提示操作可能延迟生效 |
| `SERVER_EMAIL_ERROR` | 500 | 邮件发送失败 | 主流程不阻塞，提示邮件可能延迟 |
| `SERVER_EXTERNAL_SERVICE_ERROR` | 502 | 第三方服务调用失败（GitHub/Slack/Zoom/AI 提供方） | 提示集成侧异常 |
| `SERVER_LIVE_SERVICE_UNAVAILABLE` | 503 | live 协作服务不可达 | 编辑器降级为非协同模式并提示 |
| `SERVER_MAINTENANCE` | 503 | 维护模式（带 `Retry-After`） | 展示维护页 |
| `SERVER_TIMEOUT` | 504 | 上游处理超时 | 建议重试或改用异步端点 |
| `SERVER_NOT_IMPLEMENTED` | 501 | 端点已定义但功能未上线（迭代占位） | 隐藏入口 |

### 8.7 限流与配额（RATE_*、QUOTA_*）

| 错误码 | HTTP | 触发场景 |
| --- | --- | --- |
| `RATE_LIMIT_EXCEEDED` | 429 | 超出 §7 配额 |
| `QUOTA_STORAGE_EXCEEDED` | 409 | 工作空间存储配额耗尽 |
| `QUOTA_MEMBER_EXCEEDED` | 409 | 成员数超出许可 |
| `QUOTA_PROJECT_EXCEEDED` | 409 | 项目数超出套餐限制 |

### 8.8 字段级子码（用于 `details[].code`）

| 子码 | 含义 |
| --- | --- |
| `REQUIRED` | 必填项缺失 |
| `INVALID` | 格式非法（通用） |
| `INVALID_EMAIL` / `INVALID_URL` / `INVALID_UUID` / `INVALID_DATE` / `INVALID_COLOR` | 特定格式非法 |
| `TOO_SHORT` / `TOO_LONG` | 长度越界（`message` 中给出边界值） |
| `TOO_SMALL` / `TOO_LARGE` | 数值越界 |
| `NOT_A_CHOICE` | 不在允许的枚举值内 |
| `UNIQUE` | 唯一性冲突 |
| `DOES_NOT_EXIST` | 引用的关联对象不存在或不可见 |
| `READ_ONLY` | 试图写入只读字段 |
| `INVALID_DATE_RANGE` | 日期区间逻辑错误 |
| `RETRY_AFTER` | 限流场景下承载等待秒数 |

### 8.9 前端消费范式

```ts
// packages/types/src/api/error-codes.ts（节选）
export const ErrorCode = {
  AUTH_REQUIRED: "AUTH_REQUIRED",
  AUTH_SESSION_EXPIRED: "AUTH_SESSION_EXPIRED",
  PERM_DENIED: "PERM_DENIED",
  VALIDATION_ERROR: "VALIDATION_ERROR",
  RESOURCE_CONFLICT: "RESOURCE_CONFLICT",
  RATE_LIMIT_EXCEEDED: "RATE_LIMIT_EXCEEDED",
  SERVER_ERROR: "SERVER_ERROR",
  // …与后端 Enum 一一对应，由 scripts 校验一致性
} as const;
export type ErrorCode = (typeof ErrorCode)[keyof typeof ErrorCode];

export type ApiFieldError = { field: string; code: string; message: string };
export type ApiError = {
  status: "error";
  error: {
    code: ErrorCode;
    message: string;
    details?: ApiFieldError[];
    request_id: string;
    doc_url?: string;
  };
};
```

axios 拦截器的统一分派策略：

| 错误码类别 | 统一处理 |
| --- | --- |
| `AUTH_*`（401） | 清理本地用户态 → 跳转登录（保留回跳） |
| `AUTH_CSRF_FAILED` | 重新拉取 CSRF token 后自动重试一次 |
| `PERM_*` | 不弹全局 toast，交由调用方渲染局部空态（避免权限探测产生大量弹窗） |
| `VALIDATION_ERROR` | 将 `details` 映射为 react-hook-form 的 `setError`，落到具体字段 |
| `RESOURCE_CONFLICT` | 弹出冲突对话框（刷新 / 覆盖） |
| `RATE_LIMIT_EXCEEDED` | 指数退避重试（见 §7.4） |
| `SERVER_*` | 全局 toast + 上报 Sentry（附 `request_id`） |

---

## 9. 认证方式

### 9.1 三种认证方式对照

| 方式 | 适用消费方 | 凭证载体 | 有效期 | CSRF 防护 |
| --- | --- | --- | --- | --- |
| Session 认证 | `apps/web`、`apps/admin`、`apps/space`（登录态） | `HttpOnly` + `Secure` + `SameSite=Lax` 的 session cookie | 默认 14 天滑动过期；「记住我」30 天 | 必需（双提交 cookie） |
| Token 认证（API Key） | 脚本、CI、自建集成 | `X-API-Key: rp_live_xxxx` 请求头 | 用户自定义（默认 1 年，可设永不过期） | 不需要（非 cookie，无 CSRF 面） |
| OAuth 2.0 | 第三方应用、集成市场 | `Authorization: Bearer <access_token>` | access_token 1 小时，refresh_token 30 天 | 不需要 |

### 9.2 Session 认证（Web UI）

- 后端：Django `contrib.sessions`，backend = `django.contrib.sessions.backends.cache`，缓存指向 Valkey（DB 1）；session 数据仅存 `user_id` 与必要标记，业务数据不入 session。
- Cookie 属性：`HttpOnly`（防 XSS 窃取）、`Secure`（生产强制）、`SameSite=Lax`（允许顶层导航携带，阻断跨站 POST）、`Path=/`、`Domain` 由 `APP_BASE_URL` 推导。
- CSRF：采用 Django 的双提交模式。前端启动时 `GET /api/v1/auth/csrf-token/` 获取 token（同时写入 `csrftoken` cookie），axios 拦截器为所有非安全方法自动附加 `X-CSRFToken` 头。校验失败返回 `403 AUTH_CSRF_FAILED`。
- 登录流程：`POST /api/v1/auth/sign-in/` → 校验凭证（Argon2id）→ `login()` 建立 session → 返回当前用户对象。**响应体中不返回任何 token**。
- 会话管理：`GET /api/v1/users/me/sessions/` 列出活跃会话（设备/IP/最近活跃），`DELETE .../sessions/{id}/` 支持远端下线；改密码时吊销全部其他会话。
- 并发登录：默认允许多设备；实例配置可开启「单会话模式」（企业合规场景）。

### 9.3 Token 认证（API Key）

- 格式：`rp_<env>_<24 位随机>`（如 `rp_live_9fK3…`）。前缀可识别便于密钥扫描工具检测泄露。
- 存储：**仅存 SHA-256 哈希 + 前 8 位明文前缀**（供 UI 展示 `rp_live_9fK3…****`）。创建时返回完整明文**且仅此一次**。
- 属性：`name`（用途备注）、`scopes`（作用域数组）、`expires_at`、`last_used_at`（异步更新，避免每请求写库）、`created_by`。
- 作用域（scope）设计：`<resource>:<action>` 形式，如 `issues:read`、`issues:write`、`projects:read`、`webhooks:manage`。校验在 Permission 层完成，不足则 `403 PERM_TOKEN_SCOPE_INSUFFICIENT`。
- 传递方式：**仅** `X-API-Key` 请求头。禁止通过查询参数传递（会进入访问日志与 Referer）。
- 权限继承：API Key 的权限不超过其创建者的权限交集；创建者被降权或禁用时，Key 即时失效。
- 吊销：`DELETE /api/v1/api-tokens/{id}/` 立即失效（Redis 维护吊销位图，避免每请求查库）。

### 9.4 OAuth 2.0（第三方应用）

- 支持授权类型：**Authorization Code + PKCE**（唯一推荐）、`refresh_token`。**不支持** implicit 与 password grant（已被 OAuth 2.1 弃用）。
- 端点：

```
GET  /api/v1/oauth/authorize/     授权页（用户确认 scope）
POST /api/v1/oauth/token/         换取 / 刷新 token
POST /api/v1/oauth/revoke/        吊销 token
GET  /api/v1/oauth/introspect/    token 自省（受信客户端）
GET  /api/v1/oauth/userinfo/      当前授权用户基本信息
```

- 应用注册：在 admin 中登记 `client_id`、`client_secret`（仅存哈希）、`redirect_uris`（**精确匹配**，不允许通配）、`scopes`、应用图标与说明。
- Token 形态：access_token 为 JWT（RS256 签名，含 `sub`/`aud`/`scope`/`exp`/`jti`），资源服务器本地验签避免每请求查库；refresh_token 为不透明随机串，存库并支持轮换（每次刷新签发新 refresh_token 并作废旧值，检测到旧值复用即吊销整个授权链——refresh token rotation 防重放）。
- 授权撤销：用户可在「已授权应用」中撤销，撤销后 JWT 通过 `jti` 黑名单（Redis，TTL = 剩余有效期）即时失效。

### 9.5 live 服务的协同票据

live 服务不重复实现认证，采用短时效票据模式：

```
1. 前端 POST /api/v1/workspaces/{slug}/projects/{pid}/pages/{page_id}/collab-token/
2. api 校验 Session + 文档权限 → 签发 JWT（RS256，私钥仅 api 持有）
   payload: { sub: user_id, room: "page:<page_id>", perm: "write", exp: now+120s, jti }
3. 前端以该 JWT 作为 Hocuspocus provider 的 token 建立 WebSocket
4. live 在 onAuthenticate 中用公钥验签，校验 room 与请求房间一致、perm 满足操作要求
5. 长连接期间每 30 分钟由前端静默续签，续签失败则降级只读并提示
```

设计要点：票据有效期极短（120 秒，仅覆盖握手窗口），泄露风险可控；live 持有的是**公钥**，即使 live 被攻破也无法伪造票据；权限声明内嵌于票据，live 无需理解业务权限模型。

### 9.6 认证失败的统一行为

| 场景 | 状态码 | 错误码 | 附加行为 |
| --- | --- | --- | --- |
| 无凭证 | 401 | `AUTH_REQUIRED` | `WWW-Authenticate` 头声明支持的方案 |
| 凭证过期 | 401 | `AUTH_SESSION_EXPIRED` / `AUTH_TOKEN_EXPIRED` | 清理 cookie |
| 凭证无效 | 401 | `AUTH_INVALID_TOKEN` | 计入失败计数 |
| 账号禁用 | 401 | `AUTH_ACCOUNT_DISABLED` | 同时吊销其全部 session 与 API Key |
| 登录失败 | 401 | `AUTH_INVALID_CREDENTIALS` | **响应时间常量化**（无论邮箱是否存在都执行一次哈希运算），防时序攻击枚举用户 |

---

## 10. DRF 实现约定

### 10.1 ViewSet 基类设计

```python
# plane/app/views/base.py
from typing import Any, ClassVar

from django.db.models import QuerySet
from rest_framework import status
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from plane.utils.expand import ExpandMixin
from plane.utils.field_selection import FieldSelectionMixin
from plane.utils.paginator import CursorPagination
from plane.utils.response import success_response


class BaseAPIView(FieldSelectionMixin, ExpandMixin, ModelViewSet):
    """
    全部内部 API ViewSet 的唯一基类。

    统一提供：
      1. 游标分页（CursorPagination）
      2. ?fields= 字段选择（FieldSelectionMixin）
      3. ?expand= 关联展开（ExpandMixin，含 select_related / Prefetch 自动装配）
      4. 统一成功响应包装（success_response）
      5. 工作空间 / 项目作用域强制过滤（get_queryset 收口）
      6. 审计上下文注入（created_by / updated_by）

    子类必须声明：
      - queryset / serializer_class
      - permission_classes
      - expand_map（可展开字段 → ORM 优化映射）
      - filterset_class（如需筛选）
      - ordering_fields（如需排序）
    """

    pagination_class = CursorPagination

    # 可展开字段白名单及其 ORM 优化策略（未声明的字段不允许 expand）
    expand_map: ClassVar[dict[str, Any]] = {}
    # 可排序字段白名单
    ordering_fields: ClassVar[tuple[str, ...]] = ("created_at", "updated_at")
    # 默认排序：必须以唯一键结尾，保证游标分页稳定
    ordering: ClassVar[tuple[str, ...]] = ("-created_at", "-id")

    # ── 作用域 ────────────────────────────────────────────────
    @property
    def workspace_slug(self) -> str | None:
        return self.kwargs.get("slug")

    @property
    def project_id(self) -> str | None:
        return self.kwargs.get("project_id")

    def get_queryset(self) -> QuerySet:
        """
        ★ 强制作用域过滤。任何子类覆盖 get_queryset 都必须调用 super()，
        否则将绕过租户隔离 —— CI 通过 AST 静态检查守护此约束。
        """
        qs = super().get_queryset()
        if self.workspace_slug:
            qs = qs.filter(workspace__slug=self.workspace_slug)
        if self.project_id:
            qs = qs.filter(project_id=self.project_id)
        return self.apply_expand(qs)     # 自动 select_related / prefetch_related

    # ── 序列化上下文 ──────────────────────────────────────────
    def get_serializer_context(self) -> dict[str, Any]:
        ctx = super().get_serializer_context()
        ctx.update(
            fields=self.requested_fields,      # ?fields= 解析结果
            expand=self.requested_expand,      # ?expand= 解析结果
            workspace_slug=self.workspace_slug,
            project_id=self.project_id,
        )
        return ctx

    # ── 统一响应包装 ──────────────────────────────────────────
    def list(self, request, *args, **kwargs) -> Response:
        qs = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(qs)
        serializer = self.get_serializer(page, many=True)
        return self.get_paginated_response(serializer.data)   # 内含 meta 装配

    def retrieve(self, request, *args, **kwargs) -> Response:
        serializer = self.get_serializer(self.get_object())
        return success_response(serializer.data)

    def create(self, request, *args, **kwargs) -> Response:
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(created_by=request.user, updated_by=request.user)
        return success_response(
            serializer.data,
            status_code=status.HTTP_201_CREATED,
            headers={"Location": self.build_location(serializer.data["id"])},
        )

    def update(self, request, *args, **kwargs) -> Response:
        # ★ PUT 在本系统被禁用（见 §3.2），仅集合型子资源的专用 View 可覆盖
        raise MethodNotAllowedError(method="PUT")

    def partial_update(self, request, *args, **kwargs) -> Response:
        instance = self.get_object()
        self.check_etag(request, instance)          # If-Match 乐观锁校验
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save(updated_by=request.user)
        return success_response(serializer.data)

    def destroy(self, request, *args, **kwargs) -> Response:
        self.perform_destroy(self.get_object())
        return Response(status=status.HTTP_204_NO_CONTENT)
```

派生基类：

| 基类 | 用途 | 差异 |
| --- | --- | --- |
| `BaseAPIView` | 内部 API（`plane/app/`） | 完整字段，Session 认证 |
| `WorkspaceScopedAPIView` | 需工作空间作用域 | 自动注入 `WorkspaceMemberPermission` |
| `ProjectScopedAPIView` | 需项目作用域 | 自动注入 `ProjectMemberPermission`，校验项目归属工作空间 |
| `OpenAPIBaseView` | Open API（`plane/api/`） | API Key/OAuth 认证 + scope 校验 + 独立 throttle + 稳定字段集 |
| `PublicAPIBaseView` | 公开 API（`plane/space/`） | `AllowAny` + 强制 `is_public` 过滤 + 脱敏序列化器 + 只读（`http_method_names = ["get", "head", "options"]`） |

### 10.2 Serializer 规范

```python
# plane/app/serializers/base.py
class BaseSerializer(serializers.ModelSerializer):
    """
    统一约定：
      1. 强制 read_only 字段集（id/created_at/updated_at/created_by/updated_by）
      2. 支持 context 中的 fields / expand 动态裁剪与展开
      3. 校验错误统一转换为 details[] 结构（由全局异常处理器完成）
    """

    id = serializers.UUIDField(read_only=True)

    class Meta:
        read_only_fields = (
            "id", "created_at", "updated_at",
            "created_by", "updated_by", "workspace", "project",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_field_selection()   # 依据 context["fields"] 裁剪 self.fields
        self._apply_expand()            # 依据 context["expand"] 追加嵌套 serializer
```

硬性规则：

| 规则 | 说明 |
| --- | --- |
| 一个资源三个 serializer | `XxxSerializer`（读，完整）、`XxxWriteSerializer`（写，仅可写字段）、`XxxLiteSerializer`（嵌套展开用，仅 4-6 个必要字段）。避免「一个 serializer 承担读写」导致的字段权限混乱 |
| 禁止 `fields = "__all__"` | 必须显式枚举字段，防止新增模型字段被意外暴露 |
| 关联字段默认返回 ID | 用 `PrimaryKeyRelatedField` 或 `source` 映射为 `<name>_id`；对象形态仅在 `expand` 时出现 |
| 计数字段用 annotate | `sub_issues_count` 等在 QuerySet 层 `annotate`，**禁止**在 `SerializerMethodField` 中查询（N+1 元凶） |
| 跨字段校验放 `validate()` | 单字段校验放 `validate_<field>()`；业务规则校验（工作流约束）放领域服务层，不放 serializer |
| 写操作的作用域校验 | 关联对象必须校验其归属当前 workspace/project（`queryset` 过滤而非全表），否则可通过传入他人资源 ID 造成跨租户写入 |
| 自定义字段 | 动态构造校验规则：读取 `IssueProperty` 定义 → 生成校验器 → 校验 `properties` 字典；错误的 `details.field` 使用 `property.<property_id>` |

### 10.3 Permission 类层级

四层递进，每层只做一件事，通过组合而非继承扩展：

```python
# plane/app/permissions/
class IsAuthenticatedAndActive(BasePermission):
    """L0：已认证 + 账号未禁用。所有端点必备。"""

class WorkspaceBasePermission(IsAuthenticatedAndActive):
    """L1：工作空间成员校验 + 角色等级判定（ADMIN=20 / MEMBER=15 / GUEST=5）。"""
    allowed_roles: tuple[int, ...] = (20, 15, 5)

class ProjectBasePermission(WorkspaceBasePermission):
    """L2：项目成员校验 + 项目角色等级 + 归档态写保护。"""

class ProjectEntityPermission(ProjectBasePermission):
    """L3：实体级校验 —— 对象归属、私密项目可见性、创建者特权（GUEST 可改自己创建的）。"""

class FieldLevelPermission(BasePermission):
    """L4（企业版）：字段级读写校验，作用于 PATCH 请求体的键集合。"""
```

约定：

| 约定 | 说明 |
| --- | --- |
| 权限声明式化 | ViewSet 中 `permission_classes = [ProjectEntityPermission]` + `allowed_roles = (20, 15)`，禁止在 `get_queryset` / view 方法里写 `if user.role != ...` 的散落判断 |
| 三重防护对齐需求 | UI 层（前端按钮级）→ 接口层（Permission 类）→ 数据层（`get_queryset` 强制作用域过滤 + 私密项目过滤）。**三层必须独立生效**，任一层缺失即视为安全缺陷 |
| 404 vs 403 判定收口 | 在 `ProjectEntityPermission` 中统一决策（不可知晓存在性 → 404，可见不可操作 → 403），不由各 ViewSet 自行决定 |
| 对象级权限 | 必须实现 `has_object_permission`，且 `get_object()` 必须走 `self.get_queryset()`（已含作用域过滤），双重保险 |
| 测试要求 | 每个端点必须有「无权限用户访问返回正确状态码与错误码」的测试用例，覆盖 GUEST / MEMBER / ADMIN / 非成员四种主体 |

### 10.4 全局异常处理

```python
# plane/utils/exception_handler.py
def custom_exception_handler(exc: Exception, context: dict) -> Response | None:
    """
    注册为 REST_FRAMEWORK["EXCEPTION_HANDLER"]。
    职责：把一切异常收敛为 §4.2 的统一错误结构。

    处理顺序：
      1. BusinessError（自研基类，携带 code / http_status / details）→ 直接映射
      2. DRF ValidationError → VALIDATION_ERROR + details[] 平铺（含嵌套字段点号路径）
      3. DRF NotAuthenticated / AuthenticationFailed → AUTH_*
      4. DRF PermissionDenied → PERM_DENIED（或由 Permission 类附带的具体码）
      5. Http404 / ObjectDoesNotExist → RESOURCE_NOT_FOUND
      6. MethodNotAllowed / UnsupportedMediaType → 对应 VALIDATION_* 码
      7. Throttled → RATE_LIMIT_EXCEEDED + Retry-After
      8. IntegrityError → 解析约束名 → RESOURCE_ALREADY_EXISTS / VALIDATION_ERROR
      9. DatabaseError / OperationalError → SERVER_DATABASE_ERROR
     10. 其余未捕获异常 → SERVER_ERROR（★ 记录完整堆栈到日志与 Sentry，
         但响应体绝不包含堆栈、SQL、文件路径等内部信息）

    所有分支统一注入 request_id（来自 RequestIDMiddleware 的 contextvar）。
    """
```

配套中间件（顺序敏感，自外向内）：

| 顺序 | 中间件 | 职责 |
| --- | --- | --- |
| 1 | `RequestIDMiddleware` | 生成/透传 `X-Request-Id`（ULID），写入 contextvar，供日志与错误响应使用 |
| 2 | `StructuredLoggingMiddleware` | 结构化访问日志：request_id / user_id / method / path / status / 耗时 / 查询数 |
| 3 | `RateLimitHeaderMiddleware` | 为所有响应注入 `X-RateLimit-*` |
| 4 | `AuditContextMiddleware` | 将 user / IP / UA 写入 contextvar，供模型 `save()` 自动填充审计字段与 Activity 记录 |
| 5 | `ResponseEnvelopeMiddleware` | 兜底包装：捕获未经 `success_response` 的 2xx 响应并补齐 envelope（防止漏包装），开发环境对此类情况直接抛错以尽早暴露 |
| 6 | `MaintenanceModeMiddleware` | 维护模式下除白名单端点外统一返回 `503 SERVER_MAINTENANCE` |

### 10.5 事务与一致性约定

| 场景 | 约定 |
| --- | --- |
| 单资源写操作 | 依赖 `ATOMIC_REQUESTS = True`（整个请求包裹在事务中），简单可靠 |
| 涉及多资源的业务动作 | 显式 `with transaction.atomic():`，且**副作用（通知、Webhook、搜索索引）必须放 `transaction.on_commit()`**，否则事务回滚后仍会发出通知，产生「幽灵通知」 |
| 看板拖拽 | `select_for_update()` 锁定目标状态下相邻记录，重算 `sort_order` 后单条更新；冲突重试 3 次后返回 `409 RESOURCE_CONFLICT` |
| 工作流流转 | 领域服务 `WorkflowService.transition()` 内：校验路径 → 校验约束 → 校验字段 → 更新状态 → 写 Activity → `on_commit` 触发通知与自动化规则 |
| Celery 任务入参 | **只传 ID 不传对象**（避免序列化过期快照）；任务内重新查询；所有任务必须幂等（RabbitMQ 可能重复投递） |
| 批量端点 | 全成功或全失败（单事务）；部分失败场景返回 `400` 并在 `details` 中指出失败项索引与原因 |

### 10.6 OpenAPI 文档约定

- `drf-spectacular` 自动生成，端点必须补齐 `@extend_schema`：`summary`、`description`、`responses`（含错误响应示例）、`parameters`（自定义查询参数需显式声明，否则不会出现在 schema 中）。
- `GET /api/v1/schema/`（YAML）与 `/api/v1/docs/`（Swagger UI，仅非生产或管理员可见）。
- CI 校验：schema 生成无警告；`scripts/gen-api-types.mjs` 生成的前端类型与提交内容一致。

---

## 11. 与 Plane API 设计的对标分析

### 11.1 Plane 的具体实现与本系统的处理

| 维度 | Plane 的实现 | 本系统 | 关系 |
| --- | --- | --- | --- |
| URL 层级嵌套 | 严格嵌套：`/api/v1/workspaces/{slug}/projects/{project_id}/issues/{issue_id}/`，180+ 端点全部遵循 | 完全一致，并明确「最大 3 层资源 + 叶子子资源」的量化规则 | ✅ 沿用 + 规则显式化 |
| 局部更新 | 全量使用 `PATCH`，不提供 `PUT` | 一致，并把 `PUT` 明确限定为「集合型子资源全量替换」的唯一例外，且用 CI 扫描守护 | ✅ 沿用 + 收紧 |
| 游标分页 | 自研 `CursorPagination`，游标格式 `value:offset:is_prev`，默认/最大 100 | 完全沿用该格式与上限，并追加 Base64 编码与 `total_count` 估算降级策略 | ✅ 沿用 + 增强 |
| 字段选择 | `?fields=id,name,...`，在 serializer 层裁剪 | 沿用，并要求把字段集下推到 QuerySet 的 `only()`，让裁剪真正降低 DB 负载 | ✅ 沿用 + 增强 |
| 关联展开 | `?expand=state,assignees,...`，一层深度 | 沿用，并强制「可展开字段必须声明 ORM 优化映射」+ `assertNumQueries` 测试守护 N+1 | ✅ 沿用 + 增强 |
| 分组返回 | 看板视图按 `group_by` 返回分组结构，每组独立计数 | 一致，并明确每组独立分页（默认 25 条 + `total_results`） | ✅ 沿用 + 明确化 |
| 限流 | 60 请求/分钟 | 一致，并补齐三层限流（Nginx 边缘 / DRF 全局 / 端点级）与全响应携带 `X-RateLimit-*` | ✅ 沿用 + 增强 |
| 认证 | Session（Web）+ API Key（`X-API-Key`）+ OAuth | 一致，并补齐 PKCE 强制、refresh token 轮换、scope 体系、live 短时效票据 | ✅ 沿用 + 增强 |
| API 分组 | `plane/app`（内部）/ `plane/api`（Open）/ `plane/space`（公开）三套 | 完全一致 | ✅ 沿用 |
| 响应包装 | **不统一**：多数端点直接返回资源对象或裸数组，分页端点返回带分页键的对象，错误多为 DRF 默认 `{"detail": "..."}` 或 `{"error": "..."}` | **统一为 `{status, data, meta}` / `{status, error}` 两种结构，无例外** | ⚠️ 改进（见 §11.2） |
| 错误码 | 无系统化机器码；错误主要靠 HTTP 状态码 + 中文/英文文案，部分端点有零散的 `error_code` 数字码 | **完整的 `<分类>_<语义>` 字符串错误码体系（§8），前后端双向校验一致性** | ⚠️ 改进（见 §11.2） |
| 请求追踪 | 无统一 request_id 回传 | 全响应携带 `X-Request-Id`，错误体内含 `request_id` | ⚠️ 改进 |
| 乐观并发 | 无版本校验，last-write-wins | 高冲突资源支持 `ETag` + `If-Match` → `409 RESOURCE_CONFLICT` | ⚠️ 改进 |
| 幂等性 | 无幂等键机制 | 创建类 POST 支持 `Idempotency-Key` | ⚠️ 改进 |
| 复杂筛选 | 支持较多筛选参数，语义分散 | 收敛为「参数间 AND / 参数内逗号 OR」单一语义 + Saved View 承载复杂条件树 | ⚠️ 改进 |
| API 文档 | 部分手写文档 | `drf-spectacular` 自动生成 OpenAPI 3 + 前端类型代码生成 | ⚠️ 改进 |

### 11.2 两处关键改进的动因

**改进 1：统一响应包装**

Plane 的响应结构不统一，导致前端每个 service 函数都要写一遍「判断是数组还是对象、判断分页键在哪里、判断错误在 `detail` 还是 `error`」的适配逻辑。本系统固定两种结构后：

- axios 响应拦截器可以**一次性**解包 `data` 与 `meta`，业务代码直接拿到实体；
- 错误处理完全集中在一个拦截器内，业务代码不再有 try/catch 样板；
- 前端可以为 `ApiSuccess<T>` / `ApiError` 编写通用泛型类型，配合代码生成实现端到端类型安全。

代价是响应体多一层嵌套（约 30 字节/响应）与 `204` 的特例说明，相对收益可忽略。

**改进 2：系统化错误码**

无机器码的直接后果是前端只能用 HTTP 状态码 + 文案匹配来分支。文案一旦调整或做国际化，前端逻辑静默失效——这是极难发现的一类缺陷。引入错误码后：

- 前端分支基于稳定契约，文案与国际化自由演进；
- 同一状态码下的不同业务语义可区分（`409` 可能是 slug 重复、乐观锁冲突、工作流阻塞，处理方式完全不同）；
- 支持精细化用户引导（`PERM_LICENSE_REQUIRED` → 升级页；`RESOURCE_TRANSITION_BLOCKED` → 展示阻塞任务列表）；
- 错误码可作为监控维度，按码聚合告警（如 `SERVER_STORAGE_ERROR` 突增即刻定位到对象存储故障）。

---

## 12. 与 Ones Open API 的对比

### 12.1 事实边界

Ones 为闭源商业产品，以下基于其**公开的 Open Platform / Open API 文档**：提供覆盖 Project（项目）、Issue（工作项）、TestCase（测试用例）、Wiki（知识库）、Account（账户与组织）等领域的完整 REST Open API；提供 **ONESQL** 自定义查询语言；提供 Webhook 与插件（App）扩展机制。

### 12.2 对比表

| 维度 | Ones Open API | 本系统 | 评述 |
| --- | --- | --- | --- |
| 领域覆盖 | 广：Project / Issue / TestCase / Wiki / Account / Sprint / Manhour 等 | P0-P2 覆盖 Workspace / Project / Issue / Cycle / Module / Page / View / Member；TestCase 与 Wiki 通过「统一工作项 + 任务类型」和「协作文档」承载，不单独建模 | Ones 为不同领域建独立模型与 API；本系统遵循需求文档 §3.4.1 的统一工作项设计，API 面更小，一致性更高 |
| 查询能力 | **ONESQL**：类 SQL 语法，可跨实体做条件组合与字段投影，表达力极强 | 参数化查询（`fields` / `expand` / 筛选 / `ordering` / `search`）+ Saved View 条件树 + 报表专用聚合端点 | 见 §12.3 |
| URL 风格 | 组织维度前缀（`/team/{team_uuid}/...`），资源导向 | 工作空间 slug 维度前缀（`/workspaces/{slug}/...`），资源导向 | 理念一致；本系统用可读 slug 而非 UUID 作为工作空间标识，利于分享链接可读性 |
| 更新语义 | 以 `POST`/`PUT` 为主（部分端点用 POST 承载更新动作） | 严格 `PATCH` 局部更新 | 本系统语义更贴近 REST，且天然规避 lost update |
| 响应格式 | 各端点结构不完全统一，部分直接返回业务对象 | 强制统一 envelope | 本系统一致性更强 |
| 分页 | 以 offset/limit 分页为主 | 游标分页 | 本系统在高频写入数据集上无翻页漂移、深翻性能恒定 |
| 认证 | Token / 插件凭证（App 维度授权） | Session + API Key（scope）+ OAuth 2.0（PKCE） | 本系统的 OAuth 面向第三方应用生态更标准 |
| 扩展模型 | 插件运行在宿主内（前端 slot 注入 + 服务端 NestJS 插件） | 稳定 API + Webhook + OAuth 应用，第三方代码**不进入宿主进程** | 本系统牺牲深度定制能力，换取安全边界清晰与升级不破坏第三方 |
| 错误码 | 有错误码约定，粒度与公开程度有限 | 完整分类错误码表 + 前后端一致性校验 | 本系统对客户端更友好 |
| 限流 | 有配额限制 | 60/min + 三层限流 + 全响应携带限流头 | 本系统的限流状态对客户端完全可观测 |

### 12.3 核心分歧：ONESQL vs 参数化查询

Ones 的 ONESQL 是其开放能力的显著优势——一次请求即可完成跨实体、多条件、投影裁剪的复杂查询，极大减少集成方的往返次数。但引入自定义查询语言的代价是系统性的：

| 成本项 | 说明 |
| --- | --- |
| 实现复杂度 | 需自建词法/语法解析器、AST 校验器、到 ORM/SQL 的翻译层，以及**权限重写器**（把租户与行级权限强制注入用户查询，任何遗漏即为越权漏洞） |
| 性能不可控 | 用户可构造任意 JOIN 与条件组合，服务端难以预判执行计划，慢查询与全表扫描无法从 API 层面阻断，只能靠事后超时与熔断 |
| 攻击面 | 查询语言本身是攻击面（解析器崩溃、资源耗尽型查询、通过错误信息推断数据结构） |
| 演进耦合 | 查询语言暴露了内部数据模型，模型重构会破坏所有集成方的查询语句，实质上把数据库 schema 变成了公开契约 |
| 客户端成本 | 集成方需学习专有语言，无法复用通用 REST/OpenAPI 工具链 |

**本系统的取向**：不引入查询语言，代之以三层能力，覆盖绝大多数真实场景：

1. **参数化查询**（`fields` / `expand` / 筛选 / `ordering` / `search`）——覆盖单实体的常规查询；每个可筛选字段有索引、每个可展开字段有 ORM 优化映射，**性能可预测、可压测、可容量规划**。
2. **Saved View**——复杂布尔条件树在服务端解析并受深度与节点数限制（≤ 3 层，≤ 20 节点），条件以对象形式持久化，可被前端 UI 可视化编辑，也可被 API 引用（`?view_id=`）。既满足了表达力，又保留了服务端对查询形态的完全控制。
3. **语义化聚合端点**——燃尽图、迭代速率、累积流图、团队负载等分析需求由专用端点提供，背后是 Celery 预聚合结果，响应恒定在毫秒级，不与在线交易查询争抢资源。

若未来确有强分析需求（P4），采取「**只读分析副本 + 受限 SQL 沙箱 + 行级安全策略**」的独立通道，而不是扩展在线 API 的查询表达力——把分析负载与分析风险都隔离在主链路之外。

---

## 13. 附加规范

### 13.1 异步操作（202 模式）

耗时操作（数据导入导出、批量归档、报表生成、工作空间迁移）不阻塞 HTTP：

```
POST /api/v1/workspaces/{slug}/exports/          → 202 Accepted
{ "status": "success",
  "data": { "task_id": "01JBX…", "state": "queued",
            "status_url": "/api/v1/tasks/01JBX…/" } }

GET  /api/v1/tasks/{task_id}/                    → 200
{ "status": "success",
  "data": { "task_id": "01JBX…", "state": "processing",
            "progress": 62, "result": null, "error": null } }
```

- `state` 枚举：`queued` / `processing` / `succeeded` / `failed` / `cancelled`。
- 完成后 `result` 携带产物（导出文件的预签名下载 URL，有效期 1 小时）。
- 失败时 `error` 使用与 §4.2 相同的错误结构。
- 前端轮询间隔 2s 起指数放大至 10s；亦可通过 live 服务的通知通道接收完成事件，避免轮询。

### 13.2 文件上传（预签名直传）

```
1. POST .../issues/{id}/attachments/presign/
   body: { file_name, file_size, content_type }
   → 服务端校验类型白名单、体积上限、工作空间存储配额
   → 201 { upload_url, fields, asset_id, expires_at }
2. 前端直接 POST/PUT 到 MinIO/S3（不经 Django，节省带宽与内存）
3. POST .../attachments/{asset_id}/complete/
   → 服务端 HEAD 校验对象确实存在且大小匹配，标记附件可用
```

未在 30 分钟内 `complete` 的预签名记录由 Celery beat 定时清理（同时删除可能已上传的孤儿对象）。

### 13.3 Webhook 出站规范

| 项 | 约定 |
| --- | --- |
| 事件命名 | `<resource>.<action>`：`issue.created`、`issue.updated`、`issue.deleted`、`comment.created`、`project.archived` |
| 载荷结构 | `{ event, event_id, occurred_at, workspace_id, project_id, data, previous }`（`previous` 仅 updated 事件提供变更前值） |
| 签名 | `X-RP-Signature: sha256=<hmac>`，HMAC-SHA256(secret, timestamp + "." + body)；同时发送 `X-RP-Timestamp`，接收方需校验时间偏差 ≤ 5 分钟以防重放 |
| 投递保证 | at-least-once；接收方必须以 `event_id` 去重 |
| 重试策略 | 指数退避 6 次（1s / 10s / 1m / 10m / 1h / 6h），期间非 2xx 均重试；全部失败进入死信队列并在 UI 中提示 |
| 超时 | 单次投递 10 秒超时 |
| 自动禁用 | 连续 50 次失败自动停用该 Webhook 并通知创建者 |

### 13.4 CORS 与安全响应头

| 项 | 约定 |
| --- | --- |
| CORS Origin | 精确白名单（由 `APP_BASE_URL` 与实例配置推导），**禁止** `*`；`credentials: true`（Session 认证需要） |
| 允许方法 | `GET, POST, PATCH, PUT, DELETE, OPTIONS`（`PUT` 仅为集合子资源保留） |
| 允许请求头 | `Content-Type, X-CSRFToken, X-API-Key, Authorization, If-Match, Idempotency-Key` |
| 暴露响应头 | `X-Request-Id, X-RateLimit-Limit, X-RateLimit-Remaining, X-RateLimit-Reset, ETag, Location, Retry-After` |
| 安全响应头（proxy 层统一注入） | `Strict-Transport-Security`、`X-Content-Type-Options: nosniff`、`X-Frame-Options: DENY`（space 的嵌入页例外）、`Referrer-Policy: strict-origin-when-cross-origin`、`Content-Security-Policy`、`Permissions-Policy` |
| 敏感数据 | 响应中禁止出现密码哈希、token 明文（创建时的一次性返回除外）、内部 ID 之外的数据库细节 |

### 13.5 日志与可观测性

| 项 | 约定 |
| --- | --- |
| 访问日志 | 结构化 JSON：`request_id`、`user_id`、`workspace_id`、`method`、`path`（路由模板而非实际 URL，避免 ID 爆炸）、`status`、`error_code`、`duration_ms`、`db_query_count` |
| 慢请求 | > 1000ms 记 WARN 并附 SQL 摘要；> 3000ms 记 ERROR 并上报 |
| 查询数告警 | 单请求 DB 查询 > 30 次记 WARN（N+1 早期预警） |
| 敏感信息脱敏 | 日志中的 `password`、`token`、`secret`、`X-API-Key` 一律脱敏为 `***` |
| 审计日志 | 与访问日志分离，独立表 + 独立留存策略（企业版可配置留存年限），记录操作主体、对象、字段级前后值、IP、UA |

---

## 14. 端点交付检查清单（Code Review 必查）

新增或修改端点时逐项核对，任一项不符则不予合并：

**URL 与方法**

- [ ] 路径为复数名词、全小写连字符、**以斜杠结尾**、无动词、无扩展名
- [ ] 嵌套层级 ≤ 3 层资源（叶子子资源除外），归属关系正确
- [ ] 方法语义正确；未使用 `PUT`（除集合型子资源全量替换）
- [ ] 挂载在正确的 API 分组下（`app` / `api` / `space`）

**请求与响应**

- [ ] 成功响应为 `{status, data, meta}`（`204` 除外，其响应体必须为空）
- [ ] `201` 携带 `Location` 头；异步操作返回 `202` + `task_id`
- [ ] 字段命名 `snake_case`；时间为 UTC ISO 8601 带毫秒；日期字段为 `YYYY-MM-DD`
- [ ] 关联字段默认返回 ID，对象形态仅在 `expand` 时出现

**查询能力**

- [ ] 列表端点接入游标分页，`meta` 字段完整
- [ ] 声明了 `ordering_fields` 白名单，默认排序以唯一键结尾
- [ ] 声明了 `filterset_class`，且所有可筛选字段**均有索引**
- [ ] 声明了 `expand_map`，每个可展开字段有对应的 `select_related` / `Prefetch`
- [ ] 有 `assertNumQueries` 测试守护，确认列表端点查询数不随记录数增长

**权限与安全**

- [ ] `permission_classes` 与 `allowed_roles` 显式声明，未在 view 方法内散写角色判断
- [ ] `get_queryset()` 调用了 `super()`，作用域过滤未被绕过
- [ ] 实现了 `has_object_permission`；`get_object()` 走 `self.get_queryset()`
- [ ] 写操作校验了关联对象归属当前 workspace/project
- [ ] 404/403 的选择符合「存在性隐藏」策略
- [ ] 覆盖 GUEST / MEMBER / ADMIN / 非成员四种主体的权限测试

**错误处理**

- [ ] 所有失败路径返回了 §8 中已定义的错误码（未定义则先补充错误码表 + 前后端枚举）
- [ ] 校验错误提供了字段级 `details`
- [ ] 未向客户端泄露堆栈、SQL、内部路径

**一致性与副作用**

- [ ] 多资源写操作包裹在 `transaction.atomic()` 中
- [ ] 通知 / Webhook / 索引更新等副作用置于 `transaction.on_commit()`
- [ ] Celery 任务仅传 ID 且实现幂等
- [ ] 高冲突资源支持 `ETag` / `If-Match`

**限流与文档**

- [ ] 高成本端点（搜索、报表、批量、预签名）设置了专用 throttle
- [ ] 补齐 `@extend_schema`（summary / description / responses / parameters）
- [ ] 运行 `pnpm gen:api-types` 并提交生成的前端类型
- [ ] 若为 Open API，声明了所需 scope 并更新对外文档

---

## 15. 变更记录

| 日期 | 版本 | 变更内容 | 责任人 |
| --- | --- | --- | --- |
| 2026-08-31 | 1.0 | 初版：确立 URL / 方法 / 响应格式 / 分页 / 查询 / 限流 / 错误码 / 认证 / DRF 实现全套规范，完成与 Plane API 及 Ones Open API 的对标分析 | 架构组 |
