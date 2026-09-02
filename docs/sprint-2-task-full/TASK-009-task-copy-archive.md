# 任务复制 / 归档 / 恢复

| 元信息项 | 内容 |
| --- | --- |
| 文档编号 | TASK-009 |
| 所属迭代 | Sprint 2 — 任务体系完善（第 4 周） |
| 优先级 | P2（标准版完整级） |
| 所属模块 | M4-TASK｜任务核心 |
| 文档状态 | 待评审（Draft） |
| 最后更新日期 | 2026-09-01 |
| 上游依赖 | `TASK-001`（Issue 创建服务与 advisory lock）、`TASK-002`（标签/类型/属性集合）、`TASK-003`（列表筛选白名单——`archived` 参数落点）、`TASK-004`（子树服务——深拷贝与整树归档消费）、`TASK-005`（IssueLink 表与 `link_service` 成对写入——副本 `duplicates` 关联锚点）、`TASK-006`（估算字段）、`TASK-008`（custom_fields 复制） |
| 下游消费 | `PROJ-003`（项目归档时任务归档语义对齐）、`TASK-010`（复制/归档 Activity）、`RPT-001/002`（归档任务退出统计口径）、`BOARD-002/BOARD-003`（看板归档折叠——入口 UI 归属待登记，见 §1.3） |
| 上游依据 | `docs/需求文档.md` §3.4（任务创建、编辑、删除、复制、归档）、§8.2 任务核心 P2 列（任务复制/归档） |
| 关联架构文档 | [`unified-issue-model.md`](../architecture/unified-issue-model.md)（§2.8 `archived_at` 与偏索引、§2.11 `IssueLink`、§3 序列号批量分配、§2.10 Activity）、[`api-conventions.md`](../architecture/api-conventions.md)（**§2.5 端点清单：`duplicate/` POST（创建型动作，`201 + Location`）与 `archive/` POST/DELETE；§2.6 动作子资源：`archive/` 幂等——重复 POST 返回 200**、§8 错误码）、[`rbac-permission-model.md`](../architecture/rbac-permission-model.md)（§8.2 权限矩阵：`issue.archive`） |
| 对标基线 | Plane `issues/{id}/duplicate/` 端点 + `archived_at` 软归档 · Ones 归档合规与数据留存 |
| 工作量估算 | 后端 2.5 人日 / 前端 1.5 人日 / 联调与测试 1 人日，合计 **5 人日** |

---

## 1. 概述

### 1.1 功能定位

两个收尾但高频的生命周期能力：

- **复制**：把一个任务（含可选的整棵子树）变成新任务——「上次那个需求再开一期」「这个缺陷在另一个模块也有」。核心工程问题是**深拷贝的事务一致性**：子树复制要在一次事务内完成编号分配（advisory lock 取一次锁分配连续号段）、字段选择拷贝、父子结构重建，任何一步失败整体回滚。
- **归档**：任务完成后退出活跃视图但**不删除**——`archived_at` 置位、整树级联、默认查询天然排除、归档视图可查、可恢复。与删除（进回收站）的分工：删除 = 「不该存在」，归档 = 「做完了/暂停了，留档」。

### 1.2 关键约定：复制什么、不复制什么

> ⚠️ 复制语义的核心是区分「**定义**」（可复制）与「**历史**」（不可复制）。

| 数据 | 复制？ | 理由 |
| --- | --- | --- |
| 标题（追加「(副本)」）/ 描述 JSON/HTML 双格式 / 优先级 / 类型 / 标签 / `custom_fields` 值 / `estimate_minutes` / 起止日期 | ✅ 默认（标签/字段/描述恒含，执行人/日期可选） | 任务的定义性内容 |
| 子任务整棵子树（含多层结构与相对父子关系） | ✅ 可选（`include_subtrees`） | 拆解结构属于定义 |
| 执行人集合 | ✅ 可选（默认否） | 人是「当时的安排」，复制常用于改期重做 |
| `sequence_id` | ❌ 新分配 | 编号是项目级审计资产，绝不复用/复制 |
| 状态 | ❌ 落项目默认状态（待办） | 新任务不该继承「已完成」；`completed_at` 恒 NULL |
| 评论 / 附件 / 依赖关系 / 工时记录 / Activity | ❌ | 历史——复制历史等于伪造审计链 |
| `description_binary`（Yjs） | ❌ | 协同状态含 client 时钟，复制等于伪造协作因果 |
| `sort_order` | ❌ 列尾追加 | 新任务进默认列尾 |

> 副本与源之间会在复制事务内经 `link_service.create_relation`（`TASK-005` BR-03 唯一写入口）**成对新建** `duplicates` 关联——正反各一行、`relation_type` 相同（成对存储，`IssueLink` 既有表，零 DDL）。正向行（issue=新根, related_issue=源）是 BR-01 后缀计数与溯源的锚点（见 §4.1），属于复制动作新产生的数据，不在上表「依赖关系不复制」的禁改范围内。

### 1.3 交付内容

| # | 能力 | 说明 |
| --- | --- | --- |
| 1 | 单任务复制 | `POST …/issues/{id}/duplicate/`，字段选择拷贝 |
| 2 | 深拷贝子树 | `include_subtrees` 选项，事务内重建多层结构 |
| 3 | 复制选项 | `include_subtrees` / `include_assignees` / `include_labels` / `include_custom_fields` / `include_dates` |
| 4 | 归档 / 恢复 | `POST/DELETE …/issues/{id}/archive/`，幂等；整树级联 |
| 5 | 归档视图 | 列表 `?archived=true`（参数白名单落点见 §1.5）；看板仅交付「归档任务退出列与计数扣减」语义，列尾「已归档」折叠入口 UI 不在本文交付物（上游待回改项——`BOARD-002`/`BOARD-003` 待登记已归档入口 UI 归属） |
| 6 | 归档写保护 | 归档任务只读，恢复后放行 |

### 1.4 范围边界

| 能力 | 本文档（P2） | 归属 |
| --- | --- | --- |
| 复制（含子树）/ 归档 / 恢复 | ✅ | — |
| 跨项目复制 | ❌ 同项目限定 | P4（跨项目引用归 `PROJ-004` 评估） |
| 批量复制 / 批量归档 | ❌ | `BOARD-004`（Sprint 3 批量操作） |
| 归档留存策略 / 自动归档规则 | ❌ 手动归档 | P3 `WF-003` / P4 `FILE-006` |
| 回收站 UI（已删除浏览恢复） | ❌（管理端 `all_objects` 可操作） | P4 评估 |
| 复制为模板 / 任务模板库 | ❌ | P3+ |
| 全字段筛选器 / 视图保存 | ❌ 仅新增 `archived` 布尔查询参数 | `TASK-011`（Sprint 3；README §4 文档索引中筛选器文档为 `TASK-011`，本文为「复制 / 归档 / 恢复」，不承接「筛选器」标注） |

### 1.5 前置依赖

| 依赖 | 内容 | 阻塞原因 |
| --- | --- | --- |
| `TASK-001` | `create_issue` 服务、advisory lock、批量号段分配范式 | 深拷贝 = 批量创建 + 结构重建 |
| `TASK-003` | `IssueFilterSet` 参数白名单；默认排除 `archived_at IS NOT NULL`（其 BR-03，P1 已防御式落地） | `archived` 布尔参数加入其白名单（沿 `parent_id` 先例，TASK-004 §4.2）；全字段筛选器本体归 `TASK-011`，本文不交付筛选器 |
| `TASK-004` | 递归 CTE 目标集范式（`fetch_subtree`）、`CTE_GUARD_DEPTH` 保险丝；BR-09 归档级联语义 | 归档整树复用 CTE 目标集范式；深拷贝**不复用其结果集**——`subtree/` 在外层 `SELECT … LIMIT 501` 取回后判定 `truncated` 并裁剪至 500（PostgreSQL 不支持递归项内 LIMIT，其 BR-11/§4.3.3），且排除已归档后代，作为复制输入会静默丢节点，故深拷贝使用专用计数 CTE（§4.3.1） |
| `TASK-005` | `IssueLink` 表、`duplicates` 关联类型与 `link_service.create_relation` 唯一写入口（模型见 unified-issue-model §2.11） | 副本↔源 `duplicates` 关联是 BR-01 计数锚点；复制事务内调用 `create_relation` 成对写入（不绕过其 BR-03「任何路径不得直写 `IssueLink`」禁令；其内部同样先取项目 advisory lock，同事务重入安全，复制临界区不受影响） |
| `TASK-008` | `custom_fields` 值结构 | 复制原样拷贝 JSONB（同项目同定义，无需重校验） |
| `PROJ-002` | 项目状态（归档项目写保护前置） | 归档/复制在归档项目中被拦 |

### 1.6 竞品参考

| 竞品 | 参考点 | 处置 |
| --- | --- | --- |
| Plane | `POST issues/{id}/duplicate/`；子任务**不随复制** | 补齐 `include_subtrees` 深拷贝（社区长期诉求） |
| Plane | `archived_at` 软归档 | 采纳（datetime 精度，架构文档 §7.1 已注明） |
| Ones | 归档合规（留存策略）企业级 | P2 手动归档 + 可恢复；策略化归 P4 |
| Jira | Clone issue 全字段含描述，子任务不复制，assignee 保留 | 对齐「定义复制、历史不复制」；assignee 默认不复制是差异化选择 |

---

## 2. 业务逻辑

### 2.1 复制主流程（深拷贝）

```mermaid
flowchart TD
    A["详情 ⋯ 菜单 →「复制任务」"] --> B["选项弹层：含子任务 ✓ / 含执行人 ✗ 等"]
    B --> C["POST …/issues/{id}/duplicate/"]
    C --> D{"权限 PROJ_CONTRIBUTOR+<br/>且项目 active？"}
    D -->|否| D1["403"]
    D -->|是| E["事务开启 + pg_advisory_xact_lock 一次取锁"]
    E --> F["深拷贝专用 CTE 全量取子树<br/>（不截断·仅排除软删·含已归档后代）"]
    F --> F1{"节点数超过 500？（BR-05）"}
    F1 -->|是| F2["409 RESOURCE_LIMIT_EXCEEDED"]
    F1 -->|否| G["next_sequence_id 连续分配 N 个号码（号段）"]
    G --> H["逐节点构造副本：name+'(副本)'·默认状态·<br/>按选项拷贝字段·排除历史"]
    H --> I["bulk_create + 重建 parent 映射<br/>（旧 id → 新 id 字典）"]
    I --> J["按选项 bulk 同步 labels / assignees"]
    J --> K["事务提交 → 锁释放"]
    K --> L["on_commit → Activity(verb=created,<br/>comment='由 RBT-12 复制')"]
    L --> M["201 + Location(新根任务)"]
```

**子树重建的关键**：编号按「广度优先」分配（同层连续）；`旧id→新id` 映射在内存构建后两次 `bulk_*` 落库——子节点 `parent_id` 引用同批新父，无需逐层往返数据库。N 上限 500（BR-05）由复制服务对目标集**全量计数**判定——不复用 `subtree/` 端点的展示侧截断结果集（TASK-004 BR-11 在外层 `SELECT … LIMIT 501` 取回后判定 `truncated` 并裁剪至 500——PostgreSQL 不支持递归项内 LIMIT；截断集作为复制输入会静默丢节点、使 409 永不可达）。目标集仅排除软删、不排除已归档后代（BR-15）。

### 2.2 归档 / 恢复

```mermaid
sequenceDiagram
    autonumber
    participant U as 用户
    participant FE as 前端
    participant API as Django API
    participant PG as PostgreSQL

    U->>FE: 详情 ⋯ →「归档任务」（含 N 个子任务提示）
    FE->>API: POST …/issues/{id}/archive/
    API->>PG: BEGIN
    API->>PG: CTE 下行取整树目标集
    API->>PG: UPDATE issues SET archived_at=now()
    Note over PG: 幂等（BR-09）：仅更新 archived_at IS NULL 的行<br/>保留各节点首次归档时间（BR-10）
    API->>PG: COMMIT
    API-->>FE: 200 { "archived_count": N, "archived_at": "…" }
    Note over FE: 列表/看板即时消失（默认查询排除）
    U->>FE: （后悔）归档视图 ?archived=true 找到 →「恢复」
    FE->>API: DELETE …/issues/{id}/archive/
    API->>PG: UPDATE issues SET archived_at=NULL WHERE id IN (target)
    API-->>FE: 200 { "restored_count": N }
```

### 2.3 任务生命周期状态机（删除/归档全景）

```mermaid
stateDiagram-v2
    [*] --> active: 创建
    active --> active: 编辑/流转/评论…
    active --> archived: POST archive/（整树）
    archived --> active: DELETE archive/（整树恢复）
    archived --> archived: 只读（写操作 409）
    active --> deleted: DELETE（软删，进回收站）
    archived --> deleted: 归档态也可删除
    deleted --> active: 管理端恢复（P2 无 UI）
    note right of archived
        archived ≠ deleted：
        归档可恢复且在归档视图可见；
        删除在默认/归档视图均不可见
    end note
```

### 2.4 业务规则汇总

| 编号 | 规则 | 判定位置 | 违反后果 |
| --- | --- | --- | --- |
| BR-01 | 副本标题 = 原标题 + `" (副本)"`；后缀序号 = 同事务内源的**现存**（未软删）`duplicates` 关联数 + 1（对同一源第 2 次复制 → `" (副本 2)"`）。计数锚点为复制事务内经 `link_service.create_relation`（TASK-005 BR-03 唯一写入口）成对写入的正向行 `IssueLink(relation_type='duplicates', issue=新根, related_issue=源)`（镜像行 issue=源、related=新根、同 `relation_type`，使源任务详情按 `issue_id=me` 单向查询即可见副本关联；§4.1；Activity 为 on_commit 异步落库，不可作计数依据）；计数按 `related_issue=源` 方向取正向行、成对镜像不重复计数；计数与建号同处项目 advisory lock 临界区，同源并发复制无竞态 | Service | — |
| BR-02 | 副本状态 = 项目 `is_default` 状态；`completed_at` 恒 NULL；`sort_order` = 默认列尾 +65535 步进 | Service | — |
| BR-03 | 复制仅限同项目（URL 推导，无跨项目参数） | Service | 防御性 400 |
| BR-04 | 复制内容按 §1.2 表：定义拷贝、历史不拷贝；`include_*` 五选项控制边界 | Service | — |
| BR-05 | 深拷贝节点数 ≤ 500（数值与 `subtree/` 上限一致，**语义不同**：`subtree/` 为展示侧截断（TASK-004 BR-11），深拷贝必须全量计数、超限**拒绝而非截断**）；超限提示先拆分或关闭子任务选项 | Service | `409 RESOURCE_LIMIT_EXCEEDED` |
| BR-06 | 深拷贝一次事务 + 一次取锁 + 连续号段；失败整体回滚（无半树） | Service | — |
| BR-07 | 归档整树级联：父归档 → 全部后代 `archived_at` 同事务置位；恢复对称 | Service CTE | — |
| BR-08 | 归档任务只读：任何写（PATCH/评论/工时/挂子任务/关联/复制源）返回 `409 RESOURCE_STATE_INVALID`；仅允许恢复与删除 | Permission + Service | 409 |
| BR-09 | `archive/` 幂等：重复 POST 返回 200（`archived_count=0`）；重复 DELETE 同理（api-conventions §2.6） | Service | — |
| BR-10 | 首次归档时间保留：级联 UPDATE 仅触及 `archived_at IS NULL` 的行 | Service | — |
| BR-11 | 归档任务退出统计：`RPT-001/002`、看板计数、我的待办全部以 `archived_at IS NULL` 过滤（该**排除方向**命中既有偏索引 `idx_issue_active_by_project`；归档视图 `?archived=true` 为反方向条件、不命中该偏索引，索引分工见 §4.1） | ORM 全局 | — |
| BR-12 | 复制/归档/恢复各产生 Activity（复制=created+来源注释；归档=field=archived_at） | on_commit | — |
| BR-13 | 归档项目内禁止复制与归档操作 | Permission | `403 PERM_PROJECT_ARCHIVED` |
| BR-14 | 权限：复制 / 归档 / 恢复统一为 `issue.archive`（rbac §8.2 将 archive / duplicate 归并为该 Key，最低 `PROJ_CONTRIBUTOR`）；VIEWER/COMMENTER 均不可 | Permission | `403 PERM_ROLE_INSUFFICIENT` |
| BR-15 | 深拷贝目标集仅排除软删，**不排除已归档后代**：已归档子树一并复制、落活跃默认状态（归档属「状态/历史」，不属「定义」；结构完整性优先，杜绝静默跳过）；归档态不随副本继承 | Service | — |

> **跨文档口径同步**：归档任务的写保护本文定为 `409 RESOURCE_STATE_INVALID`——任务资源**状态类**冲突按 `api-conventions.md` §8.5 归 409；`PERM_PROJECT_ARCHIVED`（§8.3）保留给「项目已归档」场景（本文 BR-13）。已同步：`TASK-004` BR-13 现为任务级 `409 RESOURCE_STATE_INVALID`（与本文 BR-08 完全同口径），项目级归档 `403 PERM_PROJECT_ARCHIVED` 独立分工。

### 2.5 异常处理

| 场景 | HTTP | 错误码 | details 子码 | 前端表现 |
| --- | --- | --- | --- | --- |
| 子树超 500 节点 | 409 | `RESOURCE_LIMIT_EXCEEDED` | `LIMIT` | 「任务树过大（N 节点），请先拆分或关闭子任务选项」 |
| 归档任务上写操作 | 409 | `RESOURCE_STATE_INVALID` | `STATE` | 「任务已归档，恢复后才能编辑」+ 恢复入口 |
| 复制归档任务（源只读） | 409 | `RESOURCE_STATE_INVALID` | `STATE` | 同上 |
| 归档项目内操作 | 403 | `PERM_PROJECT_ARCHIVED` | — | 只读态 |
| 源不存在/不可见 | 404 | `RESOURCE_NOT_FOUND` | — | 通用 404 |
| 权限不足 | 403 | `PERM_ROLE_INSUFFICIENT` | — | 菜单项隐藏 |
| 深拷贝中途失败 | 500 | `SERVER_ERROR` | — | 整体回滚；Toast + request_id |
| 重复归档/恢复 | 200 | —（幂等） | — | count=0，UI 无感 |

### 2.6 边界条件

| 边界场景 | 限制值 | 超出处理 |
| --- | --- | --- |
| 深拷贝节点数 | 500 | 409 |
| 标题长度 | 512 − 后缀 | 截断原标题保留后缀 |
| 同源并发复制 | 两请求同时 | 各自成组（「副本」「副本 2」），号段不冲突 |
| 并发归档与编辑 | 竞态 | 行锁串行；后到的写 409 |
| 归档树含混合状态 | — | 整树无差别归档 |
| 源树含已归档后代 | — | 一并复制为活跃副本（BR-15）；弹层提示「N 个已归档子任务将复制为活跃副本」 |
| 恢复后状态 | — | 各节点保持归档前状态与字段 |

---

## 3. UI/UX 设计

### 3.1 复制选项弹层

```
┌────────────────────────────────────────────────┐
│  复制任务 · RBT-12 导出功能                      │
│                                                  │
│  ☑ 包含子任务（3 个，将一并复制结构）              │
│  ☐ 包含执行人（张三、李四）                       │
│  ☑ 包含标签（前端 / P0）                          │
│  ☑ 包含自定义字段值（严重等级 等 5 项）            │
│  ☐ 包含起止日期（2026-09-01 ~ 09-15）            │
│                                                  │
│  ⓘ 新任务将进入「待办」状态，编号重新分配；         │
│    评论、附件、依赖、工时不会被复制。               │
│                                                  │
│  副本预览：导出功能 (副本) + 3 个子任务            │
│                        [取消]  [创建副本]         │
└────────────────────────────────────────────────┘
```

| 元素 | 规格 |
| --- | --- |
| 选项区 | 五 checkbox；默认 子任务✓ 执行人✗ 标签✓ 字段✓ 日期✗ |
| 信息条 | 固定说明不可复制项（管理预期，降低「评论怎么没了」客诉）；源树含已归档子任务时追加「N 个已归档子任务将复制为活跃副本」（BR-15） |
| 副本预览 | 标题 + 子任务计数实时更新 |
| 提交 | `201` 后 Toast「已创建 RBT-31 导出功能 (副本)」+「查看」跳转 |
| 入口 | 详情 ⋯ 菜单 + 列表行菜单 |

### 3.2 归档交互

| 交互 | 规格 |
| --- | --- |
| 入口 | 详情 ⋯ →「归档任务」；行菜单同款 |
| 确认 | 含子任务时提示「将同时归档 N 个子任务」 |
| 成功 | 详情关闭 + 列表移除 + Toast 带「撤销」按钮（10s 内一键恢复） |
| 归档视图 | 列表工具条「显示已归档」→ `?archived=true`；归档行 `opacity-60` + `archive` 图标 + 菜单仅「恢复/删除」 |
| 看板 | 归档任务不占列、列计数即时扣减（默认板查询排除 `archived_at IS NOT NULL`，BR-11）；列尾「已归档 (N)」折叠入口 UI 不在本文交付物——上游待回改项：`BOARD-002`/`BOARD-003` 待登记该入口 UI 归属，本文仅提供归档退出列与计数扣减的联动契约（见 §7.1） |
| 归档详情 | 只读横幅「已归档于 2026-09-01 · [恢复]」；编辑控件全部禁用 |

### 3.3 空状态 / 加载 / 失败

| 场景 | 处置 |
| --- | --- |
| 归档视图为空 | 「没有已归档的任务」插画 |
| 深拷贝大事务 | 按钮 loading +「正在复制 N 个任务…」（>1s 进度文案） |
| 复制失败 | Toast + request_id；无半成品残留（事务保证） |

### 3.4 响应式与无障碍

- 弹层 < 768px 全屏底部抽屉；checkbox 键盘可达。
- 归档行 `archive` 图标 + 文本 tooltip；只读横幅 `role="status"`；撤销按钮 `aria-label` + 10s 倒计时可见文本。

---

## 4. 技术架构

### 4.1 数据模型

**零新增表、零 DDL**。消费 `Issue.archived_at`（P0 已建）与 `IssueLink`（unified-issue-model §2.11 既有模型，Sprint 2 内随 `TASK-005` 落表）及既有字段。

**索引分工**（BR-11 / IT-06 的依据）：默认视图「排除归档」（`archived_at IS NULL`）命中偏索引 `idx_issue_active_by_project`（unified-issue-model §2.8，条件列含 `archived_at IS NULL`）；归档视图 `?archived=true` 是**反方向**条件（`archived_at IS NOT NULL`），不命中该偏索引，走项目复合索引（`idx_issue_proj_state_sort` 首列 `project`）扫描 + 过滤——单项目量级 < 10 万（api-conventions §6.4），半数归档的过滤成本可接受；若 P3 归档存量显著增长，再评估非破坏性补建 `archived_at IS NOT NULL` 偏索引。

```mermaid
erDiagram
    Issue ||--o{ IssueLink : "duplicates（新副本根 ↔ 源，成对两行；正向行 BR-01 计数/溯源锚点）"
    Issue {
        datetime archived_at "P0 已建；级联置位（BR-07/10）"
        int sequence_id "副本新分配（连续号段）"
        uuid parent_id "深拷贝重建（新映射）"
    }
```

> 复制源不建 `duplicated_from` 外键，代之以复制事务内经 `link_service.create_relation` 成对写入正反两行 `duplicates` 关联（正向 issue=新根/related_issue=源，镜像 issue=源/related=新根，`relation_type` 相同——TASK-005 成对存储）：① BR-01 后缀计数有了事务内可查询的锚点，按 `related_issue=源` 方向取正向行（`IssueActivity` 经 on_commit 异步落库，不可作计数依据）；② 溯源与 P3「副本族谱」直接消费该关联，无需加列，镜像行使源任务详情按 `issue_id=me` 单向查询即可见副本。`IssueActivity.comment`（「由 RBT-12 复制」）保留为人类可读审计。关联写入走 TASK-005 BR-03 唯一入口（任何路径不得绕过 `link_service` 直写 `IssueLink`）；其内部同样先取项目 advisory lock，与复制事务同锁重入、安全复用，计数与建号仍处同一临界区。

### 4.2 API 定义

| # | 方法 | 路径 | 描述 | 权限 | 成功码 |
| --- | --- | --- | --- | --- | --- |
| 1 | `POST` | `…/issues/{issue_id}/duplicate/` | 复制（含选项） | `issue.archive`（CONTRIBUTOR+，rbac §8.2） | `201` |
| 2 | `POST` | `…/issues/{issue_id}/archive/` | 归档（整树，幂等） | `issue.archive` | `200` |
| 3 | `DELETE` | `…/issues/{issue_id}/archive/` | 恢复（整树，幂等） | `issue.archive` | `200` |
| 4 | `GET` | `…/issues/?archived=true` | 归档视图（`archived` 参数加入 `TASK-003` `IssueFilterSet` 白名单；筛选器本体归 `TASK-011`） | `issue.read`（VIEWER+） | `200` |

#### 4.2.1 `POST …/duplicate/` — 复制

**请求**

```json
{
  "include_subtrees": true,
  "include_assignees": false,
  "include_labels": true,
  "include_custom_fields": true,
  "include_dates": false
}
```

**成功响应 `201 Created`**

```json
{
  "status": "success",
  "data": {
    "root": {
      "id": "f0a1b2c3-4d5e-4f6a-8b9c-0d1e2f3a4b5c",
      "issue_key": "RBT-31",
      "name": "导出功能 (副本)",
      "state_id": "e3f4a5b6-7c8d-4e9f-8a1b-2c3d4e5f6a7b",
      "parent_id": null,
      "source_issue_id": "8a1f9c2e-6b3d-4a7e-9f11-2c4d5e6f7a8b"
    },
    "copies": [
      { "source_id": "b2c3…", "id": "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d",
        "issue_key": "RBT-32", "parent_id": "f0a1…", "name": "后端导出 API" },
      { "source_id": "d4e5…", "id": "b2c3d4e5-f6a7-4b8c-9d0e-1f2a3b4c5d6e",
        "issue_key": "RBT-33", "parent_id": "a1b2…", "name": "分页游标改造" },
      { "source_id": "c3d4…", "id": "c3d4e5f6-a7b8-4c9d-0e1f-2a3b4c5d6e7f",
        "issue_key": "RBT-34", "parent_id": "f0a1…", "name": "前端导出按钮" }
    ],
    "total_created": 4
  }
}
```

**失败响应 `409`（子树超限）**

```json
{
  "status": "error",
  "error": {
    "code": "RESOURCE_LIMIT_EXCEEDED",
    "message": "任务树过大，无法整体复制",
    "details": [{ "field": "include_subtrees", "code": "LIMIT",
                  "message": "子树共 612 个节点，上限 500；可关闭子任务选项或先拆分" }],
    "request_id": "01JCB9Y4S7DV2U5V1B9C2E3F6A"
  }
}
```

> `duplicate/` 是**创建型**动作子资源（api-conventions §2.5 端点清单 / §3.1 方法语义）：成功 `201 + Location`，**不承诺动作幂等**——网络重试防重的幂等性由可选 `Idempotency-Key`（api-conventions §3.4）承载。`archive/` 才是 §2.6 定义的动作幂等子资源（重复 POST 返回 200，BR-09）。

#### 4.2.2 `POST/DELETE …/archive/` — 归档 / 恢复

**归档成功响应 `200`**

```json
{
  "status": "success",
  "data": { "archived_count": 4, "archived_at": "2026-09-01T10:22:31.114Z" }
}
```

**恢复成功响应 `200`**

```json
{ "status": "success", "data": { "restored_count": 4 } }
```

**失败响应 `409`（对已归档任务写操作——以 PATCH 为例）**

```json
{
  "status": "error",
  "error": {
    "code": "RESOURCE_STATE_INVALID",
    "message": "任务已归档，恢复后才能编辑",
    "details": [{ "field": "state_id", "code": "STATE",
                  "message": "RBT-12 已于 2026-09-01 归档" }],
    "request_id": "01JCB9Y4S7DV2U5V1B9C2E3F6B"
  }
}
```

### 4.3 核心逻辑

#### 4.3.1 深拷贝服务（一次锁、连续号段、映射重建）

```python
# apps/api/plane/db/services/issue_duplicate.py
from plane.db.services.issue_link import create_relation   # TASK-005 BR-03 唯一写入口

COPY_EXCLUDED_FIELDS = {"id", "sequence_id", "sort_order", "state", "completed_at",
                        "created_at", "updated_at", "created_by", "updated_by",
                        "deleted_at", "archived_at", "parent", "description_binary"}

# 深拷贝目标集（BR-05/BR-15）：专用 CTE——① 不做展示侧截断（subtree/ 端点在外层 SELECT
# LIMIT 501 取回后判定 truncated 并裁剪至 500——PostgreSQL 不支持递归项内 LIMIT，
# TASK-004 BR-11/§4.3.3），超限由服务层全量计数后 409 拒绝；② 仅排除软删、不排除已归档后代
# （根活跃校验由下方 Service 层显式 409 承担，CTE 不重复设 archived 条件）；
# ③ 按 (depth, sequence_id) 返回即广度优先序（同层编号连续）
COPY_TARGET_SQL = """
    WITH RECURSIVE target(id, sequence_id, depth) AS (
        SELECT id, sequence_id, 0 FROM issues
         WHERE id = %(root)s AND deleted_at IS NULL
        UNION ALL
        SELECT i.id, i.sequence_id, t.depth + 1
          FROM issues i JOIN target t ON i.parent_id = t.id
         WHERE i.deleted_at IS NULL              -- BR-15：不排除已归档后代，完整复制
           AND t.depth < %(guard)s               -- 深度保险丝，同 TASK-004 CTE_GUARD_DEPTH
    )
    SELECT id FROM target ORDER BY depth, sequence_id
"""


def _copy_target_nodes(source_id: uuid.UUID) -> list[Issue]:
    """全量取回深拷贝目标集（不截断），保持广度优先序。"""
    with connection.cursor() as cursor:
        cursor.execute(COPY_TARGET_SQL,
                       {"root": source_id, "guard": settings.CTE_GUARD_DEPTH})
        ids = [row[0] for row in cursor.fetchall()]
    by_id = {i.id: i for i in Issue.objects.filter(id__in=ids)}
    return [by_id[i] for i in ids]


@transaction.atomic
def duplicate_issue(*, source_id: uuid.UUID, actor_id: uuid.UUID,
                    options: DuplicateOptions) -> dict:
    """深拷贝：BR-01~06 / BR-15 全景。"""
    source = Issue.objects.select_related("project").get(
        id=source_id, deleted_at__isnull=True)               # 软删/不存在 → DoesNotExist → 404
    if source.archived_at:                                    # BR-08/§2.5：归档源显式 409，
        raise BusinessError(code="RESOURCE_STATE_INVALID", http_status=409,
                            message="任务已归档，恢复后才能复制")
                                                               # 与 §4.3.3 Permission 层同口径双保险，
                                                               # 契约不依赖拦截顺序
    project = source.project
    default_state = State.objects.get(project=project, is_default=True,
                                      issue_type__isnull=True)

    nodes = [source] if not options.include_subtrees else _copy_target_nodes(source_id)
    if len(nodes) > 500:                                                   # BR-05：全量计数（非截断集）
        raise LimitExceeded(got=len(nodes), limit=500)

    acquire_project_lock(project.id)                                       # 一次取锁
    start_seq = next_sequence_id(project.id)                               # 连续号段
    suffix = _copy_suffix(source)   # BR-01：现存（未软删）duplicates 关联数 + 1（锁内计数，无竞态）

    id_map: dict[uuid.UUID, Issue] = {}
    rows = []
    for offset, node in enumerate(nodes):                                  # 广度优先序
        new = Issue(
            project=project,
            sequence_id=start_seq + offset,
            name=_apply_suffix(node.name, suffix) if node.id == source.id else node.name,
            state=default_state,                                           # BR-02
            sort_order=_column_tail(project.id, default_state) + 65535.0 * (offset + 1),
            created_by_id=actor_id,
            **{f: getattr(node, f) for f in _copy_field_set(options)
               if f not in COPY_EXCLUDED_FIELDS},
        )
        id_map[node.id] = new
        rows.append((new, node))
    Issue.objects.bulk_create([r[0] for r in rows], batch_size=100)

    updates = [new for new, old in rows if old.parent_id]                  # 重建父子映射
    for new, old in rows:
        new.parent = id_map[old.parent_id] if old.parent_id else None
    Issue.objects.bulk_update(updates, ["parent"], batch_size=100)

    if options.include_labels:
        _bulk_copy_labels(rows)                                            # bulk_create IssueLabel
    if options.include_assignees:
        _bulk_copy_assignees(rows, actor_id)                               # assigned_by=操作者

    create_relation(                                                       # BR-01 计数/溯源锚点（零 DDL）
        issue_id=id_map[source_id].id, related_issue_id=source.id,
        relation_type=IssueLink.RelationType.DUPLICATES, actor_id=actor_id)
    # TASK-005 BR-03 唯一写入口：成对写入正反两行（duplicates 镜像行同 relation_type），
    # 源任务详情按 issue_id=me 单向查询即可见副本关联；其内部先取同一项目 advisory lock
    # （同事务重入安全，仍处本复制临界区）；_copy_suffix 按 related_issue=源 方向计数不受镜像行影响

    transaction.on_commit(lambda: record_duplicate.delay(
        str(id_map[source_id].id), str(source_id), str(actor_id), len(rows)))
    return {"root": id_map[source_id], "copies": [r[0] for r in rows[1:]],
            "total": len(rows)}
```

> `description_binary` 在 `COPY_EXCLUDED_FIELDS`：Yjs 协同状态含 client 时钟与操作历史，复制等于伪造协作因果——副本描述以 `description_json/html` 重建（P2 恒 NULL；P3 协同上线后由 live 惰性迁移补）。

#### 4.3.2 归档 / 恢复服务（复用 CTE 目标集）

```python
ARCHIVE_TARGET_CTE = """
    WITH RECURSIVE target AS (
        SELECT id FROM issues
         WHERE id = %(root)s AND deleted_at IS NULL
        UNION ALL
        SELECT i.id FROM issues i JOIN target t ON i.parent_id = t.id
         WHERE i.deleted_at IS NULL
    )
"""


@transaction.atomic
def archive_subtree(*, issue_id: uuid.UUID, actor_id: uuid.UUID) -> dict:
    """整树归档（BR-07/09/10）：仅触及未归档行以保留各节点首次归档时间。"""
    now = timezone.now()
    with connection.cursor() as cursor:
        cursor.execute(
            ARCHIVE_TARGET_CTE +
            "UPDATE issues SET archived_at = %(now)s, updated_by_id = %(actor)s "
            " WHERE id IN (SELECT id FROM target) AND archived_at IS NULL",
            {"root": issue_id, "now": now, "actor": actor_id})
        count = cursor.rowcount
    transaction.on_commit(lambda: record_archive.delay(str(issue_id), str(actor_id), now))
    return {"archived_count": count, "archived_at": now}


@transaction.atomic
def restore_subtree(*, issue_id: uuid.UUID, actor_id: uuid.UUID) -> dict:
    """整树恢复：对称清空（恢复以根为准整树放行，含此前分别归档的后代）。"""
    with connection.cursor() as cursor:
        cursor.execute(
            ARCHIVE_TARGET_CTE +
            "UPDATE issues SET archived_at = NULL, updated_by_id = %(actor)s "
            " WHERE id IN (SELECT id FROM target) AND archived_at IS NOT NULL",
            {"root": issue_id, "actor": actor_id})
        count = cursor.rowcount
    transaction.on_commit(lambda: record_restore.delay(str(issue_id), str(actor_id)))
    return {"restored_count": count}
```

#### 4.3.3 归档写保护（Permission 层收口）

```python
# plane/app/permissions/issue.py —— ProjectEntityPermission 扩展
ARCHIVE_SAFE_ACTIONS = {"archive", "restore"}          # GET 天然安全，另加恢复

def has_object_permission(self, request, view, obj) -> bool:
    ...
    if request.method in ("PATCH", "PUT", "POST") and view.action not in ARCHIVE_SAFE_ACTIONS:
        if getattr(obj, "archived_at", None):
            raise BusinessError(                                            # BR-08
                code="RESOURCE_STATE_INVALID", http_status=409,
                message="任务已归档，恢复后才能编辑")
    return True
```

> 归档任务允许的动作仅：`GET`、`DELETE archive/`（恢复）、`DELETE`（删除）。`duplicate` 对已归档源在 Service 层显式再拦 409（§4.3.1/§2.5），与本层拦截同口径双保险——契约不依赖拦截顺序。

#### 4.3.4 Celery 任务

```python
@shared_task(bind=True, max_retries=3, retry_backoff=True)
def record_duplicate(self, new_root_id: str, source_id: str,
                     actor_id: str, total: int) -> None:
    """复制 Activity：新根 verb=created，comment='由 RBT-12 复制（共 4 个任务）'——幂等"""
    ...

@shared_task(bind=True, max_retries=3)
def record_archive(self, issue_id: str, actor_id: str, at) -> None:
    """归档 Activity：field='archived_at'，new_value=ISO 时间——幂等"""
    ...
```

### 4.4 前端实现

- `IssueStore` 扩展：`archiveSubtree/restoreSubtree`（乐观移除 + Toast 撤销）；`duplicate` 成功后 `mutate` 列表并滚动定位新根。
- 归档视图：`FilterStore` 增 `archived` 布尔参数（URL 同源 `?archived=true`）；行渲染 `opacity-60 + archive` 图标变体。
- 撤销交互：Toast 内 10s 倒计时按钮（`setTimeout` 清理）。
- 复制弹层 `DuplicateDialog`：选项受控；loading 文案随预估 `total_created` 更新。

---

## 5. 测试用例

### 5.1 单元测试

| 用例 ID | 测试目标 | 输入 | 预期输出 | 覆盖类型 |
| --- | --- | --- | --- | --- |
| UT-01 | 副本标题后缀 | 首次/二次复制 | `(副本)` / `(副本 2)`（由现存 `duplicates` 关联计数驱动，BR-01） | 正常 |
| UT-02 | 标题超长截断 | 510 字标题 | 截断保留后缀 | 边界 |
| UT-03 | 状态落默认 | 源为已完成 | 副本=默认待办，completed_at NULL | 正常 |
| UT-04 | 号段连续 | 复制 4 节点 | 4 个连续 sequence_id | 正常 |
| UT-05 | 结构重建 | 3 层子树 | 副本同构（parent 映射正确） | 正常 |
| UT-06 | 历史不复制 | 源有评论/工时/关联 | 副本全无 | 正常 |
| UT-07 | 选项边界 | 全部选项关 | 纯净副本（无标签/执行人/字段/日期） | 边界 |
| UT-08 | 子树超限 | 612 节点（目标集全量计数） | 409 LIMIT（拒绝而非截断复制，BR-05） | 边界 |
| UT-09 | 归档级联 | 3 层树归档根 | 整树 archived_at 置位 | 正常 |
| UT-10 | 首次时间保留 | 先归档子、再归档父 | 子保持更早时间戳 | 边界 |
| UT-11 | 归档幂等 | 连续两次 POST archive | 均 200，第二次 count=0 | 边界 |
| UT-12 | 恢复对称 | 归档→恢复 | 整树回归，状态字段不变 | 正常 |
| UT-13 | 归档写保护 | 归档后 PATCH/评论/挂子任务/复制 | 全部 409 | 异常 |
| UT-14 | 统计退出 | 归档后查 RPT-001 | 不计数（BR-11） | 正常 |
| UT-15 | 权限矩阵（参照 TASK-005 IT-09 三角色范式） | VIEWER / COMMENTER / 非成员分别调 复制+归档+恢复 | VIEWER、COMMENTER：全部 403 `PERM_ROLE_INSUFFICIENT`（BR-14）；非成员：404 `RESOURCE_NOT_FOUND`（存在性隐藏） | 安全 |
| UT-16 | 并发复制同源 | 两请求同时 | 各自成组互不干扰 | 并发 |
| UT-17 | 已归档后代复制 | 源树含已归档子任务 | 一并复制为活跃副本（BR-15），副本树结构同构 | 边界 |

### 5.2 集成测试

| 用例 ID | 场景 | 前置条件 | 操作步骤 | 预期结果 |
| --- | --- | --- | --- | --- |
| IT-01 | 深拷贝事务性 | 3 层子树 | 注入第 2 节点构造失败 | 整体回滚，零残留（BR-06） |
| IT-02 | 复制后独立性 | 复制后改副本标签/字段 | 源零变化 | — |
| IT-03 | 归档→看板联动 | 归档根任务 | 看板/列表即时消失；归档视图可见 | — |
| IT-04 | 撤销恢复 | Toast 撤销 | DELETE archive | 整树回归原位 |
| IT-05 | Activity 留痕 | 复制+归档+恢复 | 查日志 | 3 类 Activity 齐全（BR-12） |
| IT-06 | 归档视图查询 | 1 万任务半数归档 | `?archived=true` | 项目复合索引扫描 + 归档过滤（不命中 active 偏索引，§4.1 索引分工），P95 < 200ms |
| IT-07 | 归档项目前置 | 项目归档 | duplicate/archive | 403 PERM_PROJECT_ARCHIVED |

### 5.3 E2E 测试

| 用例 ID | 用户场景 | 操作路径 | 验收标准 |
| --- | --- | --- | --- |
| E2E-01 | 整树复制 | 详情复制（默认选项） | 新树 4 任务同构、待办态、连续编号；Toast 可跳转 |
| E2E-02 | 无子任务复制 | 关闭子任务选项 | 仅根副本 1 个 |
| E2E-03 | 归档与撤销 | 归档含子树任务 → 点撤销 | 移除后 10s 内一键回归 |
| E2E-04 | 归档视图浏览 | 切「显示已归档」 | 半透明行、只恢复/删除菜单；恢复后可再编辑 |
| E2E-05 | 只读保护 | 归档任务尝试拖看板/评论 | 409 提示与恢复入口 |

---

## 6. 竞品深度对标

### 6.1 Plane 实现分析

- `duplicate/` 端点存在于其端点清单（api-conventions §2.5 对标基线），实现为 View 内构造新 Issue 逐字段拷贝——**子任务不随复制**（社区 issue 长期诉求），无选项。本系统以 `include_subtrees` + 五选项补齐，且用「一次锁 + 连续号段 + 映射重建」保证深拷贝原子性与编号纪律。
- `archived_at` 软归档语义一致；Plane 归档任务在其「已归档」过滤器下可见，本系统对齐为 `?archived=true`。

### 6.2 Ones 实现分析

- 归档在其合规体系内（留存策略、归档规范、项目归档强制）——P4 `FILE-006`「合规留存周期」承接；P2 先交付可信的手动归档/恢复闭环。
- 复制在 Ones 常与「模板」体系相邻（复制为模板/从模板创建）——本系统模板归 P3+，P2 复制保持朴素语义。

### 6.3 本系统设计决策

1. **定义/历史二分法**：一句话说清复制边界（§1.2 表），并写进 UI 信息条——复制引发的「数据去哪了」客诉在竞品社区反复出现，根因是边界未被显式表达。
2. **深拷贝原子性靠号段而非逐个创建**：500 节点逐个 `create_issue` 将取锁 500 次；一次锁 + 内存映射 + 三次 `bulk_*` 把事务压到毫秒级（IT-01 回滚测试锚定）。
3. **归档与删除正交**：归档可恢复且可见，删除进回收站不可见——两个独立布尔维度（`archived_at`/`deleted_at`）+ 偏索引组合，语义与查询成本都最优。
4. **首次归档时间不可变**（BR-10）：级联 UPDATE 只碰 NULL 行——「什么时候归档的」是审计事实，恢复-再归档不覆写。
5. **写保护收口在 Permission 层**：归档只读判定不散落各 ViewSet，由 `ProjectEntityPermission` 统一拦截（三重防护的接口层一环）。

---

## 7. 里程碑与验收

### 7.1 交付物清单

| 类型 | 交付物 |
| --- | --- |
| Model / Migration | 零 DDL |
| 后端 | `issue_duplicate.py`（深拷贝目标集 CTE/选项/号段/映射/`duplicates` 关联——经 `link_service.create_relation` 成对写入）、`archive_subtree`/`restore_subtree`（CTE 幂等）、归档写保护 Permission 扩展、`duplicate/` `archive/` 端点、`?archived=true` 参数（白名单落点 `TASK-003`） |
| Celery | `record_duplicate` / `record_archive` / `record_restore`（幂等） |
| 前端 | 复制选项弹层（预览/信息条）、归档确认与 Toast 撤销、归档视图（半透明行/受限菜单）、归档详情只读横幅（看板「已归档」折叠入口不在本文交付物——上游待回改项：`BOARD-002`/`BOARD-003` 待登记归属） |
| 测试 | UT-01~17、IT-01~07、E2E-01~05 |

### 7.2 可操作演示的验收标准

1. 复制一个 3 层 4 节点任务树（默认选项）：得到同构新树，全部落「待办」、编号连续、标题带 `(副本)`；评论区/工时/依赖为空；修改副本任何字段源零变化。
2. 关闭「包含子任务」复制：仅产生根副本；开启「包含执行人」复制：执行人集合完整迁移；源树含已归档子任务时复制，副本树完整同构且全部落活跃默认状态（BR-15）。
3. 归档含子树任务：列表与看板即时消失、Toast 10 秒内可撤销；归档视图半透明可见且仅提供恢复/删除。
4. 对已归档任务拖看板、评论、挂子任务：全部收到 409 与恢复入口；恢复后一切写操作放行且状态字段与归档前一致。
5. 归档后个人统计（`RPT-001`）与看板计数即时扣减；612 节点的超大树复制被 409 拦截并给出拆分建议。
6. 复制中途人为制造失败：库中零半成品残留（事务回滚验证）。
