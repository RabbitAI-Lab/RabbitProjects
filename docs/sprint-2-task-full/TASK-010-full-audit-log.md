# 全操作留痕审计日志

| 元信息项 | 内容 |
| --- | --- |
| 文档编号 | TASK-010 |
| 所属迭代 | Sprint 2 — 任务体系完善（第 4 周） |
| 优先级 | P2（标准版完整级 · **全部写操作的审计基础设施**） |
| 所属模块 | M4-TASK｜任务核心 |
| 文档状态 | 待评审（Draft） |
| 最后更新日期 | 2026-09-01 |
| 上游依赖 | `INFRA-003`（`issue_activities` 表与三索引 P0 已建）、`TASK-001`（创建/更新路径的前后快照范式）、`TASK-002~009`（各写操作的事件源）、`INFRA-004`（统一信封与 Celery 可靠性基线） |
| 下游消费 | **`COLLAB-003`（项目动态流 / 任务动态时间线 UI——直接消费本管道产出）**、`AUTH-010`（P3 全站审计日志扩展）、`WF-006`（P3 审批留痕）、`INTG-002`（Webhook 事件源之一）、`RPT-*`（状态变更事件供报表回算） |
| 上游依据 | `docs/需求文档.md` §3.4（任务操作日志：所有修改留痕、可追溯）、§8.2 任务核心 P2 列（基础操作日志→全量留痕） |
| 关联架构文档 | [`unified-issue-model.md`](../architecture/unified-issue-model.md)（**§2.10 IssueActivity 模型 / 逐字段 diff 生成逻辑 / epoch 分组 / 表体积控制**——本文档其工程化落地）、[`api-conventions.md`](../architecture/api-conventions.md)（§10.5 事务与 on_commit 纪律、§8 错误码）、[`monorepo-structure.md`](../architecture/monorepo-structure.md)（Celery worker/beat） |
| 对标基线 | Plane IssueActivity（event sourcing lite + 逐字段 diff + 异步投递） · Ones 全量审计（合规导出、敏感操作告警） |
| 工作量估算 | 后端 3 人日 / 前端 2 人日 / 联调与测试 1.5 人日，合计 **6.5 人日** |

---

## 1. 概述

### 1.1 功能定位

审计日志回答「**这个任务经历过什么**」。P0/P1 已有零散的 Activity 记录（创建/状态/属性变更）；本迭代把它升级为**全字段的统一留痕管道**，三条主线：

1. **全事件覆盖**——系统内一切对任务的写操作（含 Sprint 2 新增的子任务、依赖、工时、执行人、复制、归档、自定义字段）都产生结构化 Activity；
2. **可靠投递**——Activity 生成走 Celery 异步（不阻塞主请求），带重试、幂等与死信兜底，**业务成功而日志丢失**成为可监控、可补偿的异常而非静默事故；
3. **消费就绪**——任务详情「动态」Tab 按 epoch 聚合渲染人类可读时间线，`COLLAB-003` 的项目动态流直接复用本管道产出。

设计哲学与架构文档 §2.10 一致：**Event Sourcing lite**——不是用事件重建状态（那是完整 Event Sourcing 的重量），而是「状态表（`Issue`）+ 逐字段 diff 日志（`IssueActivity`）」双轨：读走状态表（快），审计与活动流走日志（全）。

### 1.2 事件覆盖矩阵（本迭代终点状态）

| 写操作 | verb | field | 事件源（Service） | 既有/新增 |
| --- | --- | --- | --- | --- |
| 创建任务 | `created` | `null` | `create_issue` | 既有，补全首版快照 |
| 更新标量字段（标题/描述/优先级/日期/估算） | `updated` | 字段名 | `IssueAttributeMixin` | 既有，补 `estimate_minutes` |
| 状态流转 | `updated` | `state` | 流转路径 | 既有（含 `TASK-005` 强制完成标记） |
| 类型 / 父项变更 | `updated` | `issue_type` / `parent` | `TASK-002/004` | Sprint 2 新增源接入 |
| 标签集合变更 | `updated` | `labels`（逐项 added/removed） | `TASK-002` | 既有，逐项化 |
| 执行人集合变更 | `updated` | `assignees`（逐人 added/removed） | `TASK-007` | Sprint 2 接入 |
| 自定义字段变更 | `updated` | `cf_<key>`（逐键） | `TASK-008` | Sprint 2 接入 |
| 依赖关系变更 | `updated` | `relations`（正反各一） | `TASK-005` | Sprint 2 接入 |
| 工时填报/修改/删除 | `updated` | `worklog` | `TASK-006` | Sprint 2 接入 |
| 复制产生 | `created` | `null`（comment 注明来源） | `TASK-009` | Sprint 2 接入 |
| 归档 / 恢复 | `updated` | `archived_at` | `TASK-009` | Sprint 2 接入 |
| 删除 | `deleted` | `null`（comment 含级联数） | `delete_subtree` | 既有，补级联数 |
| 评论（引用计数） | — | — | `COLLAB-001`（评论本体在 `IssueComment` 表，时间线合并渲染，不重复落 Activity） | 渲染层合并 |

> 评论不落 `IssueActivity`（避免双写不一致），时间线渲染时 UNION 两表按时间归并——这是「日志唯一真相」与「展示完整性」的折中（BR-05）。

### 1.3 关键约定：可靠投递三原则

> ⚠️ Activity 是审计资产，「业务成功但日志丢了」不可接受。

1. **on_commit 后投递**：Celery 任务只在事务提交后入队（`transaction.on_commit`）——回滚的业务操作绝不产生日志（杜绝「幽灵日志」，api-conventions §10.5 原则）。
2. **任务幂等**：投递载荷带确定性 `event_key`（`{issue_id}:{epoch}:{verb}`），消费端按唯一键去重——RabbitMQ at-least-once 语义下的重复投递无副作用。
3. **死信可见**：重试 3 次仍失败的任务进入死信队列 + `SERVER_QUEUE_ERROR` 告警 + 管理端补偿入口（重放）——失败被监控而非被吞掉。

### 1.4 范围边界

| 能力 | 本文档（P2） | 归属 |
| --- | --- | --- |
| 全事件覆盖（§1.2 矩阵） | ✅ | — |
| 异步管道（重试/幂等/死信/补偿） | ✅ | — |
| 任务动态时间线 UI（详情「动态」Tab） | ✅ | — |
| 动态查询 API（游标 + 字段/操作人筛选） | ✅ | — |
| 项目级动态流 UI | ❌（管道就绪，聚合视图归） | `COLLAB-003`（Sprint 3） |
| 全站安全审计（登录/权限变更/导出行为） | ❌ | `AUTH-010`（P3） |
| 审计导出 / 合规留存 / 敏感操作告警 | ❌ | P4 `FILE-006` / `AUTH-012` |
| Activity 表按季度分区 | ❌（P2 起监控体积） | P4（架构 §2.10） |
| WebSocket 实时推送动态 | ❌ | `COLLAB-004`（Sprint 3） |

### 1.5 前置依赖

| 依赖 | 内容 | 阻塞原因 |
| --- | --- | --- |
| `INFRA-003` | `issue_activities` 表 + `idx_activity_issue_time` / `idx_activity_actor_time` / `idx_activity_field` | 零 DDL 消费 |
| `TASK-001` | `build_activities` 前后快照 diff 范式（架构文档 §2.10 原型） | 管道核心复用并扩展 |
| `TASK-002~009` | 各 Service 的 `on_commit` 钩子位 | 事件源接入点 |
| `INFRA-004` | Celery + RabbitMQ + 死信队列配置 | 可靠投递底座 |

### 1.6 竞品参考

| 竞品 | 参考点 | 处置 |
| --- | --- | --- |
| Plane | `IssueActivity` 逐字段 diff + View 层收集快照后 `.delay()` 投递 + `epoch` 分组 | **完全采纳**（含字段命名），补齐重试幂等与死信（Plane 的 activity 丢失无告警） |
| Plane | 批量操作共享 epoch 聚合展示 | 采纳（`BOARD-004` 批量场景复用） |
| Ones | 全量审计 + 合规导出 + 敏感告警（企业级） | P2 交付「可信记录」；「合规体系」归 P3/P4 |
| Jira | issue history 按字段逐行 + 人物时间 | 展示形态对齐（时间线分组渲染） |

---

## 2. 业务逻辑

### 2.1 留痕管道全景

```mermaid
flowchart TD
    subgraph 写路径["业务写路径（各 Service）"]
        A["事务内：取 before 快照"] --> B["执行写操作"]
        B --> C["构造 after 快照"]
        C --> D["transaction.on_commit<br/>→ issue_activity.delay(payload)"]
    end
    subgraph 异步["Celery Worker"]
        D --> E{"幂等检查：<br/>event_key 已存在？"}
        E -->|是| F["丢弃（at-least-once 去重）"]
        E -->|否| G["build_activities() 逐字段 diff<br/>（标量/FK/M2M/custom_fields）"]
        G --> H["bulk_create IssueActivity<br/>（同一 epoch）"]
    end
    H --> I["（Sprint 3 起）通知 / WebSocket /<br/>Webhook 事件扇出"]
    D -->|重试 3 次失败| J["死信队列 activity.dlq"]
    J --> K["SERVER_QUEUE_ERROR 告警<br/>+ 管理端补偿重放入口"]
```

### 2.2 单次更新的 diff 时序

```mermaid
sequenceDiagram
    autonumber
    participant U as 用户
    participant API as Django（PATCH 路径）
    participant PG as PostgreSQL
    participant MQ as RabbitMQ
    participant CW as Celery Worker

    U->>API: PATCH {"priority":"urgent","cf_severity":"critical"}
    API->>PG: BEGIN；SELECT … FOR UPDATE（before 快照）
    API->>PG: UPDATE issues SET …
    API->>PG: COMMIT
    API->>MQ: on_commit → issue_activity.delay({before, after, actor, epoch})
    API-->>U: 200（主请求不被日志阻塞）
    MQ->>CW: 投递（可能重复）
    CW->>CW: event_key 幂等检查（Redis SETNX + DB 兜底）
    CW->>PG: INSERT 2 条 IssueActivity：priority、cf_severity<br/>（共享 epoch = 本次 PATCH 的毫秒时间戳）
    Note over CW,PG: 失败 → 重试（1s/4s/16s）→ 死信 + 告警
```

### 2.3 时间线聚合语义（epoch 分组）

同一次用户动作（一次 PATCH / 一次批量操作 / 一次深拷贝）产生多条 Activity **共享同一 `epoch`**（动作发生时刻的毫秒时间戳）。渲染层把同 epoch 的记录聚合为一组：

```
今天
  ├─ 14:32  张三 更新了 3 个字段            ← epoch 组：一条视觉记录
  │          优先级 高 → 紧急
  │          严重等级 major → critical
  │          截止时间 09-10 → 09-15
  ├─ 11:05  李四 ⏱ 填报了 2h 工时（联调收尾）
  └─ 09:41  系统 由 RBT-12 复制创建（共 4 个任务）
昨天
  ├─ 17:20  王五 将状态 待办 → 进行中
  └─ 16:58  张三 指派 李四、王五 执行（原：张三）
```

聚合规则：`epoch` 相同且 `actor` 相同且时间差 < 5s 的连续记录合组；组内逐条列出 `字段：旧值 → 新值`；跨天按日期分区。

### 2.4 业务规则汇总

| 编号 | 规则 | 判定位置 | 违反后果 |
| --- | --- | --- | --- |
| BR-01 | 每次成功的 create/update/delete 及 §1.2 矩阵事件必产生 ≥1 条 Activity；**回滚的操作不产生**（on_commit 纪律） | Service + 评审 | 评审拒绝 |
| BR-02 | diff 粒度：标量逐字段、FK 记 old/new 对象名+ID、M2M 拆 added/removed 逐项、custom_fields 逐键 | `build_activities` | — |
| BR-03 | `old_value`/`new_value` 存**人类可读文本**（状态名、人名、选项 label），`old_identifier`/`new_identifier` 存对象 ID——展示与追溯分离 | `build_activities` | — |
| BR-04 | `epoch` = 动作发生时刻毫秒时间戳，由 Service 统一生成并贯穿该动作全部日志 | Service | — |
| BR-05 | 评论不落 Activity（`IssueComment` 是其真相表）；时间线渲染 UNION 两表按 `created_at` 归并 | 渲染层 | — |
| BR-06 | 描述字段 diff 不存全文（体积失控）：old/new 存「已修改」标记，全文对比入口后续评估 | `build_activities` | — |
| BR-07 | 幂等键 `event_key = {issue_id}:{epoch}:{verb}`；Redis SETNX 前置去重 + DB 查询兜底 + `ignore_conflicts` 终局兜底 | Worker | — |
| BR-08 | 投递失败重试 3 次（1s/4s/16s 退避）后入死信 `activity.dlq`；触发 `SERVER_QUEUE_ERROR` 告警；管理端可重放 | Celery | — |
| BR-09 | Activity 不可修改不可删除（无 UPDATE/DELETE API；重放仅补插）；软删任务的 Activity 保留 | API 面 | — |
| BR-10 | 时间线查询游标分页（默认 30 组/页）；支持 `?field=` 与 `?actor_id=` 过滤 | ViewSet | — |
| BR-11 | 敏感值脱敏：字段名或值命中 `password/token/secret/webhook_url` 模式时值以 `***` 落库 | `build_activities` | — |
| BR-12 | 批量操作（`BOARD-004` 前瞻）：同批全部条目共享 epoch，聚合为「批量更新了 N 个任务」 | Service 约定 | — |
| BR-13 | 时间线权限 = 任务读权限（不可见任务动态亦不可见） | Permission | 403/404 |
| BR-14 | 通知与 Activity 独立：通知是触达（可已读），Activity 是记录（不可变）——两管道不共享表不互相补偿 | 架构约束 | — |

### 2.5 异常处理

| 场景 | 触发条件 | 前端表现 | 后端处理 | 错误码 |
| --- | --- | --- | --- | --- |
| Worker 宕机期间事件积压 | RabbitMQ 队列堆积 | 时间线暂缺新动态（SWR 刷新后补齐） | 恢复后按序消费，epoch 保序 | — |
| 死信 | 重试耗尽 | 时间线缺该条 + 管理端告警 | `SERVER_QUEUE_ERROR` + 补偿重放 | 告警侧 |
| 幂等重复投递 | MQ at-least-once | 无感 | event_key 去重 | — |
| 时间线查询越权 | 非项目成员 | 404 | Permission 收口 | `RESOURCE_NOT_FOUND` |
| 快照丢失（异常路径） | before 未捕获 | — | 该字段降级为「已修改」（无 old/new），ERROR 日志 | — |
| 动态 Tab 加载失败 | 网络/5xx | 行内重试 | — | — |

### 2.6 边界条件

| 边界场景 | 限制值 | 超出处理 |
| --- | --- | --- |
| 单动作产生日志条数 | 无硬限（一次 PATCH 改 20 字段=20 条） | epoch 聚合为一组展示 |
| 描述 diff | 不落全文（BR-06） | 标记化展示 |
| 时间线分页 | 30 组/页（组=epoch 聚合） | 游标加载 |
| `old/new_value` 单值长度 | 500 字符截断 + `…` | — |
| 表体积 | P2 监控（增速 ≈ 写 QPS × 平均字段数） | 季度分区 P4 |
| 死信堆积 | 告警阈值 100 条 | 升级处理 |

---

## 3. UI/UX 设计

### 3.1 任务详情「动态」Tab（时间线）

```
┌──────────────────────────────────────────────────────────────────┐
│ TZXM-13  后端导出 API            [概览] [动态] [评论]              │
├──────────────────────────────────────────────────────────────────┤
│ 今天 · 2026-09-01                              [全部 ▾] [⋯ ▾]    │
│                                                                   │
│  14:32  👤张三                                                    │
│  │      更新了 3 个字段                                            │
│  │      ├ 优先级        高 → 紧急                                  │
│  │      ├ 严重等级      major → critical                           │
│  │      └ 截止时间      09-10 → 09-15                              │
│  │                                                                 │
│  11:05  👤李四                                                    │
│  │      ⏱ 填报了 2h 工时 · 联调收尾                                │
│  │                                                                 │
│  09:41  ⚙系统                                                     │
│         由 RBT-12 复制创建（共 4 个任务）                           │
│ ──────────────────────────────────────────────────────────────── │
│ 昨天 · 2026-08-31                                                 │
│  17:20  👤王五   状态  待办 → 进行中                               │
│  16:58  👤张三   指派 李四、王五 执行（原：张三）                    │
│ ──────────────────────────────────────────────────────────────── │
│                    [加载更早的动态]                                 │
└──────────────────────────────────────────────────────────────────┘
  [全部 ▾] = 字段过滤（状态/优先级/工时/关联…）   [⋯ ▾] = 按操作人过滤
```

| 元素 | 规格 |
| --- | --- |
| 日期分区 | 「今天 / 昨天 / M月d日」sticky 分区头（`text-xs text-neutral-400`） |
| epoch 组 | 头行（时间+头像+操作者+动作摘要）+ 缩进字段行（`字段 旧值 → 新值`）；旧值删除线、新值加粗 |
| 系统事件 | `⚙系统` 头像（复制/归档/自动化来源）；Sprint 3 起自动化规则名展示 |
| FK 值渲染 | 状态/类型/人员显示名 + 色点；ID 不展示（BR-03） |
| M2M 渲染 | added/removed 合并为人名列表「（原：…）」式对比 |
| 过滤器 | 字段与操作人两个下拉（多选）；过滤态 URL 同源 |
| 空态 | 「暂无动态——第一次修改将出现在这里」 |
| 加载 | 8 行时间线骨架；「加载更早」按钮式分页（审计场景翻阅为主，不做无限滚动） |

### 3.2 管理端死信补偿（admin 应用）

| 元素 | 规格 |
| --- | --- |
| 死信列表 | 队列 `activity.dlq`：时间 / event_key / 错误摘要 / 重试次数 |
| 操作 | 单条重放 / 批量重放 / 丢弃（二次确认 + 留痕） |
| 告警 | 堆积 > 100 条触发 `SERVER_QUEUE_ERROR`（`INFRA-004` 监控通道） |

### 3.3 响应式与无障碍

| 断点 | 布局 |
| --- | --- |
| ≥ 1280px | 双栏（左时间轴竖线 + 右内容） |
| < 768px | 单栏；字段 diff 行换行渲染 |

无障碍：时间线为语义 `<ol>`；每组 `aria-label` 摘要（「张三在 14:32 更新了 3 个字段」）；新旧值用文本（`→` 分隔）不依赖颜色；过滤器键盘可达。

---

## 4. 技术架构

### 4.1 数据模型

**零新增表、零 DDL**。消费 `INFRA-003` 已建（与架构文档 §2.10 一致）：

```python
# apps/api/plane/db/models/activity.py —— 既有定义，本迭代全量点亮
class IssueActivity(BaseModel):
    """操作日志 —— Event Sourcing lite：状态表 + 逐字段 diff 日志"""

    class Verb(models.TextChoices):
        CREATED = "created", "创建"
        UPDATED = "updated", "更新"
        DELETED = "deleted", "删除"

    issue = models.ForeignKey(Issue, on_delete=models.CASCADE, null=True,
                              related_name="issue_activities")
    actor = models.ForeignKey("db.User", on_delete=models.SET_NULL, null=True,
                              related_name="issue_activities")
    verb = models.CharField(max_length=16, choices=Verb.choices, default=Verb.CREATED)
    field = models.CharField(max_length=64, null=True, blank=True)
    old_value = models.TextField(null=True, blank=True)      # 人类可读（BR-03）
    new_value = models.TextField(null=True, blank=True)
    old_identifier = models.UUIDField(null=True, blank=True) # 追溯 ID
    new_identifier = models.UUIDField(null=True, blank=True)
    comment = models.TextField(blank=True)
    epoch = models.FloatField(null=True)                      # 动作毫秒时间戳（BR-04）

    class Meta(BaseModel.Meta):
        db_table = "issue_activities"
        ordering = ("created_at",)
        indexes = [
            models.Index(fields=["issue", "created_at"], name="idx_activity_issue_time"),
            models.Index(fields=["actor", "created_at"], name="idx_activity_actor_time"),
            models.Index(fields=["field"], name="idx_activity_field"),
        ]
```

```mermaid
erDiagram
    Issue ||--o{ IssueActivity : "issue_activities（不可变追加流）"
    User ||--o{ IssueActivity : "actor（SET_NULL 存活）"
    IssueComment ||..o{ Timeline : "渲染层 UNION（不落 Activity）"
    IssueActivity {
        uuid issue_id FK "CASCADE 仅硬删生效（软删不触发）"
        uuid actor_id FK "nullable(系统操作)"
        string verb "created|updated|deleted"
        string field "64, cf_* 键亦入此列"
        text old_value "可读文本 ≤500"
        text new_value "可读文本 ≤500"
        uuid old_identifier
        uuid new_identifier
        text comment "人类可读动作描述"
        float epoch "动作毫秒时间戳（分组键）"
    }
```

#### 4.1.1 索引设计说明

| 索引 | 服务的查询 | 使用频率 |
| --- | --- | --- |
| `idx_activity_issue_time` | 任务时间线 `WHERE issue_id=? ORDER BY created_at` | 极高（详情打开） |
| `idx_activity_actor_time` | 「某人的操作史」（P3 审计直查预置） | P3 起高 |
| `idx_activity_field` | 字段维度审计（如全部状态变更 → 报表回算） | 中 |

### 4.2 API 定义

| # | 方法 | 路径 | 描述 | 权限 | 成功码 |
| --- | --- | --- | --- | --- | --- |
| 1 | `GET` | `…/issues/{issue_id}/activities/` | 时间线（游标，epoch 预聚合） | `issue.read` | `200` |
| 2 | `GET` | `…/issues/{issue_id}/activities/?field=state&actor_id=<uuid>` | 字段/操作人过滤 | `issue.read` | `200` |

> 无 POST/PATCH/DELETE——Activity 只能由管道产生（BR-09）。

#### 4.2.1 `GET …/activities/` — 时间线

**请求**

```http
GET /api/v1/workspaces/acme/projects/7b3e9c1a-…/issues/b2c3d4e5-…/activities/?per_page=30 HTTP/1.1
```

**成功响应 `200`**

```json
{
  "status": "success",
  "data": [
    {
      "id": "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d",
      "epoch": 1756727520000,
      "actor": { "id": "6c7d1a2b-3e4f-4a5b-9c8d-7e6f5a4b3c2d",
                 "display_name": "张三", "avatar_url": null },
      "verb": "updated", "comment": "更新了 3 个字段",
      "created_at": "2026-09-01T06:32:00.000Z",
      "items": [
        { "field": "priority", "field_label": "优先级",
          "old_value": "高", "new_value": "紧急",
          "old_identifier": null, "new_identifier": null },
        { "field": "cf_severity", "field_label": "严重等级",
          "old_value": "major", "new_value": "critical",
          "old_identifier": null, "new_identifier": null },
        { "field": "target_date", "field_label": "截止时间",
          "old_value": "2026-09-10", "new_value": "2026-09-15",
          "old_identifier": null, "new_identifier": null }
      ]
    },
    {
      "id": "9f8e7d6c-5b4a-4938-8271-6a5b4c3d2e1f",
      "epoch": 1756713900000,
      "actor": { "id": "2b3a4c5d-6e7f-4a8b-9c0d-1e2f3a4b5c6d",
                 "display_name": "李四", "avatar_url": null },
      "verb": "updated", "comment": "⏱ 填报了 2h 工时 · 联调收尾",
      "created_at": "2026-09-01T02:45:00.000Z",
      "items": [{ "field": "worklog", "field_label": "工时",
                  "old_value": null, "new_value": "120 分钟（2026-09-01）",
                  "old_identifier": null,
                  "new_identifier": "7c8d9e0f-1a2b-4c3d-8e9f-0a1b2c3d4e5f" }]
    }
  ],
  "meta": { "next_cursor": "30:1:0", "next_page_results": true, "count": 2,
            "total_count": 47, "grouped_by": "epoch" }
}
```

**契约要点**：

1. **服务端预聚合**：同 epoch 的多条 Activity 已合并为一条「组记录」（`items[]` 展开字段）——前端零聚合逻辑；
2. `field_label` 由 `FIELD_LABELS` 常量 + Schema API（`cf_*` 用字段显示名）解析；
3. 游标锚定 epoch（组粒度），翻页不割裂同组；
4. `COLLAB-003` 的项目动态流复用同一响应结构（加 project 维度聚合端点，Sprint 3 增量）。

**失败响应 `404`**（任务不可见，与详情一致）：

```json
{
  "status": "error",
  "error": {
    "code": "RESOURCE_NOT_FOUND",
    "message": "任务不存在或你没有访问权限",
    "request_id": "01JCBA0Z8XE3V6W2C0D4F5G8H1"
  }
}
```

### 4.3 核心逻辑

#### 4.3.1 逐字段 diff 生成（架构文档 §2.10 全量落地）

```python
# apps/api/plane/db/services/activity_builder.py
import json, re, time

TRACKED_SCALAR_FIELDS = ("name", "priority", "start_date", "target_date",
                         "estimate_minutes")
TRACKED_FK_FIELDS = ("state", "issue_type", "parent")
TRACKED_M2M_FIELDS = ("assignees", "labels")
DESCRIPTION_MARKER = "__modified__"                              # BR-06 不落全文
SENSITIVE_PATTERNS = re.compile(r"password|token|secret|webhook_url", re.I)
VALUE_MAX_LEN = 500


def build_activities(*, issue: Issue, before: dict, after: dict,
                     actor_id: uuid.UUID, epoch: float | None = None) -> list:
    """比对前后快照逐字段生成日志（BR-02/03/06/11）。

    before/after 由 Service 在事务内收集：标量/FK 为对象或值，
    M2M 为 ID 集合，custom_fields 为 dict。
    """
    epoch = epoch or time.time() * 1000                          # BR-04
    activities = []

    for field in TRACKED_SCALAR_FIELDS:
        if before.get(field) != after.get(field):
            activities.append(_scalar_row(issue, actor_id, epoch, field,
                                          before.get(field), after.get(field)))

    if before.get("description_html") != after.get("description_html"):
        activities.append(IssueActivity(                          # BR-06 描述标记化
            issue_id=issue.id, actor_id=actor_id, verb="updated",
            field="description",
            old_value=DESCRIPTION_MARKER, new_value=DESCRIPTION_MARKER,
            comment="更新了描述", epoch=epoch))

    for field in TRACKED_FK_FIELDS:
        old_o, new_o = before.get(field), after.get(field)
        if (getattr(old_o, "id", None) != getattr(new_o, "id", None)):
            activities.append(IssueActivity(
                issue_id=issue.id, actor_id=actor_id, verb="updated", field=field,
                old_value=getattr(old_o, "name", None),
                new_value=getattr(new_o, "name", None),
                old_identifier=getattr(old_o, "id", None),
                new_identifier=getattr(new_o, "id", None),
                comment=f"将 {FIELD_LABELS[field]} 从 "
                        f"{getattr(old_o, 'name', '空')} 改为 {getattr(new_o, 'name', '空')}",
                epoch=epoch))

    for field in TRACKED_M2M_FIELDS:                             # 拆 added/removed 逐项
        old_ids, new_ids = set(before.get(field) or ()), set(after.get(field) or ())
        for uid in sorted(new_ids - old_ids):
            activities.append(_m2m_row(issue, actor_id, epoch, field, uid, "added"))
        for uid in sorted(old_ids - new_ids):
            activities.append(_m2m_row(issue, actor_id, epoch, field, uid, "removed"))

    for key in set(before.get("custom_fields") or {}) | set(after.get("custom_fields") or {}):
        ov = (before.get("custom_fields") or {}).get(key)
        nv = (after.get("custom_fields") or {}).get(key)
        if ov != nv:
            activities.append(IssueActivity(
                issue_id=issue.id, actor_id=actor_id, verb="updated", field=key,
                old_value=_clip(_display(ov)), new_value=_clip(_display(nv)),
                comment=f"更新了 {key}", epoch=epoch))
    return activities


def _clip(text: str | None) -> str | None:
    """BR-11 敏感脱敏 + 500 字符截断"""
    if text is None:
        return None
    if SENSITIVE_PATTERNS.search(text):
        return "***"
    return text[:VALUE_MAX_LEN] + ("…" if len(text) > VALUE_MAX_LEN else "")
```

#### 4.3.2 幂等消费 Worker（重试 + 死信）

```python
# apps/api/plane/bgtasks/issue_activity.py
@shared_task(bind=True, max_retries=3, retry_backoff=[1, 4, 16],
             autoretry_for=(OperationalError,), acks_late=True)
def issue_activity(self, payload: dict) -> None:
    """Activity 落库 Worker —— at-least-once 下的幂等消费（BR-07/08）。

    payload = {issue_id, actor_id, verb, epoch, before, after, comment?}
    event_key 幂等：Redis SETNX（TTL 24h）前置 + DB 同键查询兜底。
    """
    event_key = f"{payload['issue_id']}:{int(payload['epoch'])}:{payload['verb']}"
    if cache.add(f"activity-dedup:{event_key}", 1, timeout=86400) is False:
        return                                                  # 重复投递，安全丢弃
    if IssueActivity.objects.filter(
            issue_id=payload["issue_id"], epoch=payload["epoch"],
            verb=payload["verb"]).exists():
        return                                                  # Redis 失效后的 DB 兜底

    issue = Issue.objects.get(id=payload["issue_id"])            # 只传 ID 重查（快照可能过期）
    rows = build_activities(issue=issue, actor_id=payload["actor_id"],
                            before=payload["before"], after=payload["after"],
                            epoch=payload["epoch"])
    if payload.get("comment") and rows:
        rows[0].comment = payload["comment"]
    IssueActivity.objects.bulk_create(rows, batch_size=100,
                                      ignore_conflicts=True)    # 并发兜底：冲突即重复
    # Sprint 3 挂点：dispatch_events.delay(...) 通知/WebSocket/Webhook 扇出


# 死信路由（celery 配置）
task_routes = {"plane.bgtasks.issue_activity": {"queue": "activity"}}
dead_letter = {"activity": "activity.dlq"}                      # 重试耗尽 → dlq + 告警
```

#### 4.3.3 时间线服务端预聚合

```python
# apps/api/plane/app/views/activity.py（节选）
def list(self, request, *args, **kwargs):
    qs = (IssueActivity.objects
          .filter(issue_id=self.issue_id)
          .select_related("actor")
          .order_by("-epoch", "-created_at"))
    if field := request.query_params.get("field"):
        qs = qs.filter(field=field)
    if actor_id := request.query_params.get("actor_id"):
        qs = qs.filter(actor_id=actor_id)

    page = self.paginate_queryset(qs)
    groups, current = [], None
    for row in page:                                            # epoch 相邻即同组（页内聚合）
        if current and current["epoch"] == row.epoch:
            current["items"].append(serialize_item(row))
        else:
            current = serialize_group(row)
            groups.append(current)
    return success_response(groups, meta=self.build_meta(grouped_by="epoch"))
```

> **页内聚合的边界**：同组记录因 `ORDER BY -epoch` 物理相邻，单页 30 组不会割裂；跨页由游标锚定 epoch 值避免（下一页 `WHERE epoch < cursor`）。

#### 4.3.4 死信补偿（管理端）

```python
# apps/api/plane/bgtasks/activity_dlq.py
@shared_task
def replay_dead_letters(limit: int = 100) -> int:
    """管理端触发的死信重放：从 activity.dlq 取消息 → 重新投递 issue_activity。
    幂等键保证重放不产生重复日志（BR-07 使重放天然安全）。"""
    ...
```

### 4.4 前端实现

- `ActivityStore`（`packages/shared-state`）：`byIssue: Map<issueId, ActivityGroup[]>`（SWR key `issue:{id}:activities:{cursor}`）；过滤态换 key。
- `ActivityTimeline` 组件：日期分区（date-fns `isToday/isYesterday`）+ epoch 组渲染（§3.1 结构）；`field_label` 直接用服务端值（`cf_*` 显示名来自 Schema，不前端猜）。
- 评论合并渲染：`TimelineComposer` 在客户端 merge `activities` 与 `comments`（两 SWR 结果按 `created_at` 归并排序）——BR-05 的展示半边。
- 新动态刷新：P2 靠 SWR focus revalidate；`COLLAB-004` 上线后改为 WebSocket 触发 `mutate`。

---

## 5. 测试用例

### 5.1 单元测试

| 用例 ID | 测试目标 | 输入 | 预期输出 | 覆盖类型 |
| --- | --- | --- | --- | --- |
| UT-01 | 标量 diff | 改 priority | 1 条，old/new 文本 | 正常 |
| UT-02 | 多字段一次 PATCH | 改 3 字段 | 3 条同 epoch | 正常 |
| UT-03 | FK diff | 状态流转 | old/new 名+ID 四字段齐 | 正常 |
| UT-04 | M2M 拆分 | 加 2 删 1 执行人 | 3 条逐项 | 正常 |
| UT-05 | custom_fields 逐键 | 改 2 键 | 2 条 field=cf_* | 正常 |
| UT-06 | 描述标记化 | 改描述 | `__modified__` 不落全文 | 边界 |
| UT-07 | 敏感脱敏 | 值含 token 字样 | 值为 `***` | 安全 |
| UT-08 | 值截断 | 600 字新值 | 500+`…` | 边界 |
| UT-09 | 回滚不留痕 | 事务内抛异常 | 0 条 Activity | 正常 |
| UT-10 | 幂等（Redis） | 同 payload 投递两次 | 1 组落库 | 并发 |
| UT-11 | 幂等（DB 兜底） | Redis 清空后重放 | 不重复 | 并发 |
| UT-12 | epoch 贯穿 | 深拷贝 4 节点 | 4 条 created 同 epoch | 正常 |
| UT-13 | 死信路由 | Worker 持续失败 | 3 次重试后入 dlq + 告警 | 异常 |
| UT-14 | 时间线过滤 | field=state | 仅状态记录 | 正常 |
| UT-15 | 越权 | 非成员查时间线 | 404 | 安全 |
| UT-16 | 软删保留 | 删除任务 | 其 Activity 管理端可查 | 正常 |

### 5.2 集成测试

| 用例 ID | 场景 | 前置条件 | 操作步骤 | 预期结果 |
| --- | --- | --- | --- | --- |
| IT-01 | 全矩阵覆盖 | — | 逐一执行 §1.2 全部操作 | 每操作 ≥1 条对应 Activity |
| IT-02 | 主请求零阻塞 | 慢 Worker | 计时 PATCH | 业务响应不含日志耗时 |
| IT-03 | Worker 宕机恢复 | 停 Worker 执行写 | 重启 | 积压按序消费，时间线完整 |
| IT-04 | 死信重放 | 制造死信 → 重放 | 查时间线 | 补齐且无重复 |
| IT-05 | 预聚合正确 | 同 epoch 3 条 | GET activities | 1 组 items=3 |
| IT-06 | 评论合并 | 2 评论 + 1 更新 | 前端时间线 | 三条按时间归并（BR-05） |
| IT-07 | 性能门禁 | 单任务 5000 条动态 | GET 首页 | P95 < 100ms（索引排序） |
| IT-08 | 分页不割裂 | 同 epoch 跨页边界 | 翻页 | 组完整（游标 epoch 锚定） |

### 5.3 E2E 测试

| 用例 ID | 用户场景 | 操作路径 | 验收标准 |
| --- | --- | --- | --- |
| E2E-01 | 时间线完整性 | 改 3 字段 + 流转 + 指派 | 分组渲染正确、值对比清晰、刷新保持 |
| E2E-02 | 工时与复制事件 | 填工时、复制任务 | 「⏱ 填报…」「由 RBT-12 复制…」正确呈现 |
| E2E-03 | 过滤 | 只看状态变更 | 仅状态组；URL 分享还原 |
| E2E-04 | 异步收敛 | Worker 延迟 3s | 200 立即返回；时间线稍后出现（无报错） |
| E2E-05 | 权限 | 移出成员后访问旧链接 | 404 |

---

## 6. 竞品深度对标

### 6.1 Plane 实现分析

- `apps/api/plane/db/models/issue.py` 的 `IssueActivity` 与本系统逐字段对齐（verb/field/old_value/new_value/identifier/comment/epoch）；其写入路径在 View 层收集前后快照后 `.delay()`——**无重试幂等与死信**，worker 异常时 activity 静默丢失（社区有「活动缺失」类反馈）。
- Plane 的 bulk 操作同样共享 epoch，前端 activity 流按 epoch 折叠——本系统 BR-04/BR-12 原样制度化。
- 本系统增强点：① `event_key` 幂等 + 死信 + 补偿重放（把「尽力而为」升级为「可运维承诺」）；② 服务端预聚合（Plane 把分组留给前端，跨端表现易漂移）；③ 敏感值脱敏与值长度纪律。

### 6.2 Ones 实现分析

- 全量审计体系（操作日志/合规导出/敏感告警/权限变更溯源）是企业版核心卖点，记录范围覆盖登录与导出行为。
- 本系统 P2 把「任务域审计」做扎实（结构化、幂等、可重放），`AUTH-010`（P3）再把记录面扩展到全站实体并接合规导出——**管道复用，记录面分阶段扩大**是成本最优路径。

### 6.3 本系统设计决策

1. **双轨而非 Event Sourcing**：状态表服务读性能，diff 日志服务审计——不背「事件重建状态」的实现重量（架构文档 §2.10 原判断）。
2. **幂等是管道的地基**：at-least-once 语义下「重复无害」让重试、重放、补偿全部安全——死信敢做一键重放正因如此。
3. **服务端预聚合**：epoch 分组在 API 层完成，前端只渲染——三端（web/admin/space）时间线表现一致，`COLLAB-003` 直接复用。
4. **记录与触达分离**（BR-14）：Activity（不可变事实）与 Notification（可已读触达）两套管道——混用一张表是协作系统最常见的建模错误之一。
5. **体积纪律前置**：描述标记化 + 值截断 + 敏感脱敏在 P2 立规矩——`issue_activities` 是全库增长最快的表（架构 §2.10），纪律晚立一年，治理成本翻十倍。

---

## 7. 里程碑与验收

### 7.1 交付物清单

| 类型 | 交付物 |
| --- | --- |
| Model / Migration | 零 DDL |
| 后端 | `activity_builder.py`（全字段 diff）、`issue_activity` Worker（幂等/重试/死信路由）、时间线预聚合 ViewSet（过滤/游标）、`replay_dead_letters` 补偿、`TASK-002~009` 全事件源接入（on_commit 钩子） |
| 前端 | 详情「动态」Tab（日期分区/epoch 组/值对比/过滤）、`ActivityStore`、评论合并渲染、admin 死信补偿页 |
| 测试 | UT-01~16、IT-01~08、E2E-01~05 |

### 7.2 可操作演示的验收标准

1. 对任务执行一组操作（改 3 字段、流转、指派 2 人、填工时、建依赖、复制、归档恢复）：动态 Tab 按时间倒序、epoch 分组完整呈现每一步，字段值对比与操作者无误。
2. 停掉 Worker 后执行写操作：业务请求正常成功；重启 Worker 后时间线自动补齐（积压按序消费）。
3. 人为制造 Worker 失败：3 次重试后进死信并触发告警；管理端一键重放后时间线补齐且无重复记录。
4. 同一 PATCH 重复投递（模拟 MQ 重发）：时间线仅一组；Redis 清空后再重放仍不重复。
5. 只看「状态」过滤：仅状态流转记录；按操作人过滤正确；过滤态 URL 可分享还原。
6. 5000 条动态的任务首屏 P95 < 100ms；修改含 `token` 字样的自定义字段，日志值为 `***`。
