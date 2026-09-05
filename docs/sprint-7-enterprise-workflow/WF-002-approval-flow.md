# WF-002 审批流（会签/或签/逐级/驳回/撤回）

| 元信息项 | 内容 |
| --- | --- |
| 文档编号 | WF-002 |
| 所属迭代 | Sprint 7 — 企业工作流核心（第 10 周主线） |
| 优先级 | P3（企业版核心级 · 付费核心场景） |
| 覆盖模块 | M11-WF 企业工作流与审批（模块码以 `docs/architecture/dependency-graph.md` §1.2 为唯一事实，同 `sprint-overview.md` §3 注一口径） |
| 工作量估算 | 8 人日（后端 4.5 + 前端 2.5 + QA 1） |
| 文档状态 | 待评审（Draft） |
| 最后更新日期 | 2026-09-04（R4 修复：issue FK 改 PROTECT+软删钩子终止、§2.3 覆盖编号、pending_approval_instance_id、runTransition 补 to_state_id、先例 TASK-008、清单 UT-01~18/IT-01~10、timeout_scan 补离职转交、UT-12 同口径 IT-09；R3：is_terminal_passed 三处/主键链/count_only 待补登/排期对齐/ApiError 统一/BR-09 级联/IT-09 拆分/UT-17~18/IT-10；R2/R1 见主记录） |
| 上游依赖 | `WF-001`（`WorkflowTransition.approval_flow` FK 挂点、单事务流转引擎、`side_effects` 协议） |
| 下游消费 | `WF-005`（审批流随模板下发）、`WF-006`（审批留痕与合规导出）、Sprint 9（审批时效报表） |

---

## 1. 概述

### 1.1 功能定位

WF-002 在 WF-001 状态机之上交付**审批子系统**：当流转边配置了 `approval_flow`，流转不再即时生效，而是先生成**审批实例**进入待审队列，经逐级节点审批通过后才由引擎补完状态迁移。五种审批形态全覆盖：

| 形态 | 语义 | 配置方式 |
| --- | --- | --- |
| 单人审批 | 单一审批人通过即过 | 节点 1 人 + `any` |
| 或签 | 节点内任一人通过即过 | 节点 N 人 + `any` |
| 会签 | 节点内全员通过才过 | 节点 N 人 + `all` |
| 逐级 | 多节点按 `level` 顺序依次推进 | 节点列表有序 |
| 驳回/撤回 | 驳回回发起前状态；撤回仅发起人且未有任何审批动作 | 动作接口 |

### 1.2 触发与执行总览

```mermaid
sequenceDiagram
    autonumber
    participant U as 发起人
    participant ENG as WorkflowService（WF-001）
    participant AP as ApprovalService
    participant PG as PostgreSQL
    participant A as 审批人

    U->>ENG: POST …/issues/{id}/transitions/（目标边挂 approval_flow）
    ENG->>ENG: GuardRegistry.run_all（守卫先过——守卫不过不立案，WF-001 §4.4）
    ENG->>AP: ApprovalService.start(edge, issue, actor)（WF-001 §4.4 挂接点）
    AP->>PG: INSERT approval_instances + 第 1 级节点的 approval_records
    AP-->>U: 202 Accepted + pending_approval（任务状态不变，标记审批中）
    AP->>A: on_commit → 通知（COLLAB-001 通道：收件箱 + 邮件）
    A->>AP: POST …/approval-instances/{id}/actions/ {approve}
    alt 当前节点通过且存在下一级
        AP->>PG: 推进 level+1，生成新 records → 通知下一级审批人
    else 最终节点通过
        AP->>ENG: transition()（WF-001 §4.4 单事务入口：**重跑守卫**→副作用→状态迁移→Activity）
        ENG-->>A: 200；任务进入目标状态
    else 任一人驳回
        AP->>PG: instance=rejected（任务保持发起前状态，BR-06）
        AP->>U: 通知发起人（附驳回意见）
    end
```

> **关键时序决策**：守卫在**发起时**与**终审通过时各跑一次**。两次之间任务字段可能被他人修改（如负责人被移除），终审重跑保证迁移瞬间仍满足全部守卫——这是「审批挂起期间世界在变」的正确性兜底。

### 1.3 范围边界

| 范围 | 本文档交付 | 明确不做 |
| --- | --- | --- |
| 审批定义 | `ApprovalFlow`/`ApprovalNode` CRUD、三种节点通过模式、逐级编排 | 条件分支审批（按字段值走不同分支，P4） |
| 审批执行 | 实例生命周期、五形态、超时提醒、与引擎单事务衔接 | 审批委派/加签（P4）；审批人休假代理（P4） |
| 审批人来源 | 指定成员 / 项目角色组 / 字段动态（报告人·负责人） | 组织架构部门主管（依赖 Sprint 8 `AUTH-007`，V1.1 衔接） |
| 通知 | 收件箱 + 邮件（复用 COLLAB-001 通道）、超时提醒 | 企业微信/钉钉推送（P4 `INTG-003`） |

### 1.4 前置依赖

| 依赖 | 内容 | 阻塞原因 |
| --- | --- | --- |
| `WF-001` §4.2/§4.4 | `approval_flow` FK（`SET_NULL` 摘挂，§4.2 现行口径）、`WorkflowService.transition()` 单事务入口 + §4.4 审批挂接分支（`ApprovalService().start(edge, issue=…, actor=…)`） | 引擎挂点 |
| `WF-001` §4.7 | `guards` 协议（发起/终审双跑） | 正确性 |
| `COLLAB-001` | 通知通道与偏好设置 | 审批触达 |
| `TASK-010` | Activity 管道（审批动作落任务动态） | 留痕一致性 |
| `INFRA-002` | Celery beat（超时扫描任务） | 超时提醒 |

### 1.5 竞品参考

| 竞品 | 参考点 | 处置 |
| --- | --- | --- |
| Ones | 审批流与状态流转绑定、会签/或签/逐级、审批中心 | 形态对齐；**审批票不可变（一次合法迁移，BR-09）+ 终审重跑守卫**补其弱项（Ones 社区反馈审批通过瞬间字段已变导致误迁移） |
| Jira (JSM) | Approvals 挂状态、任意人/全员通过、`approver` 字段驱动 | 节点模式对齐；不学其「审批人必须在 Insight 字段」的耦合 |
| 钉钉/飞书审批 | 审批中心三 Tab（待办/已办/我发起）、时间线详情 | 审批中心信息架构对齐 |
| Plane | 无审批能力 | 差异化卖点，无从参考 |

---

## 2. 业务逻辑

### 2.1 审批定义模型（业务视图）

- **ApprovalFlow（审批流定义）**：项目级，命名 + 说明；被 0..n 条流转边引用（`WorkflowTransition.approval_flow`，FK 口径以 WF-001 §4.2 现行版为准 = `SET_NULL`）。删除流时引用边**自动摘挂**（`approval_flow` 置空、边退化为普通流转边，画布侧提示）；治理口径建议**先停用再删除**——停用后新发起 409，存量实例凭 `flow_snapshot` 走完（BR-13）。
- **ApprovalNode（审批节点）**：属于一个流，`level` 从 1 递增（逐级推进顺序）；通过模式 `any`（或签）/ `all`（会签）；审批人来源三选一：
  - `users`：指定项目成员列表（1..20 人）；
  - `role`：项目角色组（如 `PROJ_ADMIN` 全体，运行时展开为成员集）；
  - `field`：任务字段动态解析——`reporter`（报告人）/ `assignees`（当前负责人，多人时按节点模式判定）。

> `field` 来源在**实例创建时刻快照**为具体成员集（写入 records），此后任务换负责人不改变本次审批人——防止「换人逃审」与「审批人悬空」两类争议。

### 2.2 实例生命周期

```mermaid
stateDiagram-v2
    [*] --> pending: 发起（守卫通过，立案）
    pending --> pending: 当前级通过→推进下一级
    pending --> approved: 最终级通过（引擎补完迁移）
    pending --> rejected: 任一级任一人驳回
    pending --> withdrawn: 发起人撤回（无任何审批动作前）
    pending --> terminated: PROJ_ADMIN 终止（comment 必填，审计）
    approved --> [*]
    rejected --> [*]
    withdrawn --> [*]
    terminated --> [*]
    note right of pending
        审批中任务：状态不变，
        transitions/available/ 返回锁定标记，
        重复发起同边 → 409
    end note
```

| 状态 | 语义 | 进入条件 |
| --- | --- | --- |
| `pending` | 审批中（含中间各级） | 立案 / 推进 |
| `approved` | 全员通过，状态迁移已完成 | 终审通过且引擎迁移成功 |
| `rejected` | 被驳回 | 任一审批人 reject（会签中一人驳回即整体驳回，BR-05） |
| `withdrawn` | 发起人撤回 | 无任何 `approve/reject` 动作（BR-07） |
| `terminated` | 管理员终止 | `PROJ_ADMIN` + comment（异常出口，BR-08） |

### 2.3 审批中任务的系统行为

| 行为 | 规则 |
| --- | --- |
| 状态显示 | 任务仍为发起前状态；详情页头部与看板卡片显示「审批中 · 第 N 级」徽标（非状态，实例查询派生） |
| 再次流转 | 同一条边重复发起 → `409 RESOURCE_ALREADY_EXISTS`；**其他边不受限**（可从待审状态走取消边，此时审批实例自动 `terminated`，原因 `state_changed`） |
| 字段编辑 | 不锁定（审批的是「流转」不是「任务」）；但终审重跑守卫可能因编辑而失败 → 实例置 `terminated`（原因 `guard_failed_at_complete`）并通知双方 |
| 任务删除/归档 | 软删（`deleted_at`）/归档（`archived_at`）写入钩子终止实例（原因 `issue_deleted`/`issue_archived`），记录保留；实例 FK 为 PROTECT——硬删任务须先经项目归档导出链（WF-006 BR-11） |

> **终止触发落点**（§2.3 / §4.4 / §4.7 三处触发器实现挂点）：
> - `state_changed`：消费 WF-001 §7.3 `state_changed` 事件（`WorkflowService.transition` 第 6 步 `on_commit` 发布，事件登记面在 §7.3 下游消费表——**WF-001 §7.3 待同步登记本文消费方（上游待回改）**），handler 内 `ApprovalInstance.objects.filter(issue_id=…, status="pending").update(status=TERMINATED, terminated_reason="state_changed")`。
> - `issue_deleted`：订阅 `Issue.deleted_at` **写入钩子**（软删信号——TASK-009 §2.3 任务删除为软删进回收站，不触发 post_delete；与 issue_archived 的 archived_at 钩子同范式，注册于审批域 `apps.py.ready()`），handler 同上。硬删仅经 WF-006 BR-11 项目归档导出链（PROTECT 拦截直连硬删）。
> - `issue_archived`：订阅 `Issue` 归档字段更新 signal（`Issue.archived_at` 写入钩子——**本文新增**订阅，与 `issue_deleted` 同一注册点），handler 同上。
> - `admin` / `guard_failed_at_complete`：直接调 `_finalize(TERMINATED, reason=…)`。
> - 全部落库用单 UPDATE 减少 IO；用例覆盖：UT-13（state_changed）+ UT-17（issue_deleted）/ UT-18（issue_archived，§5.1）；`admin` 终止由 IT-04（集成级）、`guard_failed_at_complete` 由 UT-09 覆盖。

### 2.4 超时与提醒

| 机制 | 规则 |
| --- | --- |
| 节点超时 | 节点可配 `timeout_hours`（NULL = 不超时）；超时**不自动通过/拒绝**（企业审计不接受系统代决），仅触发提醒升级 |
| 提醒节奏 | beat 每 15 分钟扫描：到达超时点 → 提醒审批人；超时 24h → 加报发起人与 `PROJ_ADMIN` |
| 提醒通道 | 收件箱（必达）+ 邮件（按用户偏好）；去重窗口 24h（同实例同人同类型不重复轰炸） |

### 2.5 业务规则汇总

| 编号 | 规则 | 判定位置 | 违反后果 |
| --- | --- | --- | --- |
| BR-01 | 流转边挂审批流时：守卫先过→立案→202；守卫不过不立案 | WorkflowService | 409/400（守卫错误透传） |
| BR-02 | 审批人必须为本项目成员（含 role 展开后过滤非成员） | 立案快照 | 空审批人集 → `400 VALIDATION_ERROR` + `EMPTY_APPROVERS`（定义保存时也校验） |
| BR-03 | 会签任一人驳回 = 整体驳回；或签任一人通过 = 本级通过 | ApprovalService | — |
| BR-04 | 逐级推进：仅当前 `level` 的 records 可动作（`approval.act` + rbac §8.4 R8「当前级指定审批人集合」判定）；越级/非本级动作 403 | ApprovalService | `403 PERM_APPROVAL_NOT_ASSIGNEE`（api-conventions §8.3 预留码） |
| BR-05 | 终审通过瞬间**重跑全部守卫**，失败则实例 `terminated(guard_failed_at_complete)`，不迁移 | 引擎单事务 | 通知发起人+审批人 |
| BR-06 | 驳回/撤回/终止后任务保持发起前状态；驳回意见 `comment` 必填（reject），approve 选填 | Serializer | `400 VALIDATION_ERROR` + `REQUIRED` |
| BR-07 | 撤回仅发起人本人（`approval.withdraw`，rbac §8.2「仅本人提交」），且实例内无任何 approve/reject 记录 | ApprovalService | `403 PERM_DENIED`（非发起人）/ `409 RESOURCE_CONFLICT`（已有动作，子码 `HAS_ACTIONS`） |
| BR-08 | `PROJ_ADMIN` 可终止任意 pending 实例，comment 必填，落审计（WF-006） | ApprovalService | 缺 comment 400 |
| BR-09 | 审批票「一次合法迁移」：开票即 INSERT（`pending`），动作 = 唯一一次 UPDATE（`pending → approve/reject/skipped` 单向、仅 `action/comment/acted_at` 三列可写——触发器 `approval_records_guard` 列级白名单，WF-006 §2.2/§4.2）；其余 UPDATE（改 `level`/审批人/二次动作）拒绝；DELETE **直连一律拒绝，仅 `pg_trigger_depth()>0` 的项目 FK 级联上下文放行**（WF-006 BR-11 级联放行口）；审计事件流 `approval_audit_events` 纯 append（WF-006 交付，与票据双轨） | DB | 触发器异常 |
| BR-10 | 同任务同边仅允许一个 pending 实例；其他边发起成功则本实例自动终止 | 唯一约束（部分索引）+ 服务 | `409 RESOURCE_ALREADY_EXISTS` |
| BR-11 | `field` 来源审批人 = 立案时刻快照；快照后人员变动不影响本次 | 立案服务 | — |
| BR-12 | 禁止自审开关：流级 `forbid_self_approve`（默认开）——发起人是审批人时其票自动记 `skipped(self)`，会签中不计入通过集、或签中若仅剩自己则转交 `PROJ_ADMIN` | 动作服务 | 自动处置 + 记录 |
| BR-13 | 删除口径与 WF-001 §4.2 `approval_flow` FK（`SET_NULL`）统一：删除流 = 引用边自动摘挂（退化为普通流转边，响应返回受影响边清单）；**被引用中建议先停用**——停用后新发起 409，存量实例继续走完 | Service + DB | 停用后发起 `409 RESOURCE_CONFLICT`（子码 `DISABLED`） |
| BR-14 | 审批全程动作（立案/各级通过/驳回/撤回/终止/超时提醒）落 `IssueActivity(field='approval', verb='updated')` 与审计（WF-006）——经 TASK-010 §4.3.2 `issue_activity` 任务投递（载荷 `{issue_id, actor_id, verb, epoch, before, after, comment?}`，`epoch` 为毫秒时间戳，verb 不新增，见 §4.5 注） | on_commit | — |
| BR-15 | 审批实例与记录对项目成员可读（透明），动作仅当前级审批人（`approval.act` + R8） | Permission | `403 PERM_APPROVAL_NOT_ASSIGNEE` |

### 2.6 异常处理

| 场景 | HTTP | 错误码 | details 子码 | 前端表现 |
| --- | --- | --- | --- | --- |
| 发起时守卫不过 | 409/400 | `RESOURCE_TRANSITION_BLOCKED` / `VALIDATION_REQUIRED_FIELD_MISSING` / `VALIDATION_ESTIMATE_REQUIRED` | 按 WF-001 §4.8 ③ 矩阵透传 | 弹守卫补齐表单（WF-004 §3） |
| 同边重复发起 | 409 | `RESOURCE_ALREADY_EXISTS` | `UNIQUE` | Toast「已存在进行中的审批」+ 跳实例详情 |
| 审批流已停用仍被发起 | 409 | `RESOURCE_CONFLICT` | `DISABLED` | 「该审批流已停用，请联系管理员」 |
| 非当前级审批人动作 | 403 | `PERM_APPROVAL_NOT_ASSIGNEE` | — | 按钮本不渲染；直连触发 Toast（§8.3 动作建议：隐藏审批操作区） |
| 实例非 pending 时动作 | 409 | `RESOURCE_CONFLICT` | `INVALID_STATE` | 「审批已结束」，刷新实例 |
| 撤回但已有审批动作 | 409 | `RESOURCE_CONFLICT` | `HAS_ACTIONS` | 「已有审批动作，不可撤回」 |
| 非发起人撤回 | 403 | `PERM_DENIED` | — | — |
| reject/terminate 缺 comment | 400 | `VALIDATION_ERROR` | `REQUIRED` | 意见框标红 |
| 终审守卫重跑失败 | 200（动作成功） | — | 实例 `terminated` | 审批人见「终审校验失败」提示，发起人收通知 |
| 空审批人集（成员变动致 role 展开为空，BR-02） | 400 | `VALIDATION_ERROR` | `EMPTY_APPROVERS` | 立案被拒（与定义保存时同码同因），提示联系管理员修定义 |
| 删除被引用的审批流 | 200 | — | — | 引用边 `approval_flow` 置空（SET_NULL，BR-13）；`data.affected_transition_ids` 返回被摘挂边清单供画布刷新 |

> **子码登记**：上表 `details[].code` 子码中，`REQUIRED` / `UNIQUE` 已在 `api-conventions.md` §8.8 注册；`EMPTY_APPROVERS` / `DISABLED` / `INVALID_STATE` / `HAS_ACTIONS` / `LIMIT`（§2.7）为本文新增字段级子码，**待补登 §8.8**（随 Sprint 7 首个 PR 并入前后端错误码枚举的双源一致性校验），登记前客户端不得对其分支依赖。

### 2.7 边界条件

| 边界场景 | 限制值 | 超出处理 |
| --- | --- | --- |
| 单流节点数 | 10 级 | `409 RESOURCE_LIMIT_EXCEEDED`（裁决 F 上限类口径，与 TASK-002 层级深度/TASK-008 字段数（其 BR-10，50 字段同码同子码）同构；`LIMIT` 为其 details 子码） |
| 单节点审批人 | 20 人 | 同上 |
| 并发同级动作（会签两人同时提交） | 行锁串行 | 后到者按最新实例状态判定（可能因前者驳回而 409） |
| 审批人离职/移出项目 | 快照不变（BR-11） | 其待办转交：beat 每日扫描，pending 超 24h 且审批人已非成员 → 该票 `skipped(offboarded)` 并提醒 `PROJ_ADMIN` 补位（补位 = admin 动作，留痕） |
| 项目角色组展开规模 | ≤ 50 人 | 超出截断并告警（定义保存时校验） |
| 实例查询分页 | cursor `"100:1:0"` 规范 | 同全局 |

---

## 3. UI/UX 设计

### 3.1 审批中心（一级入口：侧边栏「审批」）

```
┌──────────────────────────────────────────────────────────────────────┐
│ 审批                                        [待办 3] 已办  我发起 2   │
├──────────────────────────────────────────────────────────────────────┤
│ ● PROJ-128 支付网关联调      提交评审 → 评审中   发起人: 陈曦           │
│   第 2 级 · 会签（2/3 已审）   剩余 19h 超时    [通过] [驳回]          │
│ ──────────────────────────────────────────────────────────────────── │
│ ● PROJ-131 首页改版视觉验收    验收 → 已上线     发起人: 王一           │
│   第 1 级 · 或签（0/2 已审）   无超时           [通过] [驳回]          │
│ ──────────────────────────────────────────────────────────────────── │
│ ○ PROJ-119 订单重构           开发中 → 提测     发起人: 我 · 已撤回     │
└──────────────────────────────────────────────────────────────────────┘
```

| 元素 | 行为 |
| --- | --- |
| 三 Tab | 待办（我是当前级审批人）/ 已办（我动作过的）/ 我发起——与 §4.6 审批中心三端点（`approvals/pending/`、`approvals/acted/`、`approvals/mine/`）一一对应；徽标数 = 待办数，5s 轮询 + WS 推送（COLLAB-004 个人房间 `user:{user_id}`，连接走 `WSS /live`；事件 `approval.updated` 按其 §2.3 信封协议随本迭代扩展登记） |
| 行内动作 | 通过/驳回在行内直接操作；驳回强制弹出意见框（BR-06） |
| 超时提示 | 剩余 < 4h 橙色、已超时红色 +「已超时 Nh」；无超时不显示 |
| 点击行 | 打开审批详情抽屉（§3.2） |

### 3.2 审批详情抽屉

```
┌─ 审批详情 · PROJ-128 支付网关联调 ────────────────────────────────┐
│ 流转：开发中 → 评审中（「提交评审」）          状态：审批中 · 第 2 级 │
│ 发起人：陈曦  09-06 10:22        审批流：研发上线审批 v3            │
├──────────────────────────────────────────────────────────────────┤
│ 时间线                                                            │
│  ✓ 立案            陈曦      09-06 10:22   守卫校验通过            │
│  ✓ 第1级·或签      王一      09-06 11:05   通过「联调环境已就绪」  │
│  ● 第2级·会签      2/3                          剩余 19h           │
│     ✓ 李雷（技术负责人）  09-06 13:40  通过                        │
│     ✓ 韩梅（测试负责人）  09-06 15:02  通过                        │
│     ○ 赵六（运维负责人）  待审          [通过] [驳回]              │
│  ○ 第3级·单人       产品负责人            未开始                   │
├──────────────────────────────────────────────────────────────────┤
│ 任务摘要：负责人 陈曦/李雷 · 截止 09-10 · 优先级 高     [查看任务]  │
└──────────────────────────────────────────────────────────────────┘
```

### 3.3 画布侧栏：审批节点配置（WF-001 画布内）

流转边侧栏新增「审批」分区（挂在 WF-001 §3 画布编辑器）：

```
┌─ 流转「提交评审」 ────────────────┐
│ 守卫（2）            [+ 添加]     │
│  必填字段：负责人、截止日期        │
│  前置依赖完成                      │
├──────────────────────────────────┤
│ 审批                [启用 ▾]      │
│  审批流：研发上线审批 v3  [更换]   │
│  ┌ 节点 1 · 或签 · 王一/产品组 ──┐ │
│  │ 超时 24h            [编辑]   │ │
│  └------------------------------┘ │
│  ┌ 节点 2 · 会签 · 指定成员(3) ──┐ │
│  │ 超时 48h            [编辑]   │ │
│  └------------------------------┘ │
│  [+ 添加节点]  ☑ 禁止自审          │
│                                  │
│ 保存后发布生效（WF-001 草稿机制）  │
└──────────────────────────────────┘
```

### 3.4 任务侧呈现

| 位置 | 呈现 |
| --- | --- |
| 详情页头部 | 状态旁「审批中 · 第 N 级」徽标，点击跳审批详情；发起人可见「撤回」按钮 |
| 看板卡片 | 右上角紫色沙漏徽标，tooltip 显示当前级与剩余超时 |
| 列表 | `approval_status` 可筛选（pending/approved/rejected…）——按可筛选字段登记进 FilterCompiler（归属 TASK-011 §4.3.2，TASK-008 子集扩全；WF-004 仅冻结守卫协议，不涉筛选） |
| 流转按钮 | 审批中同边按钮禁用（`available/` 项增 `pending_approval_instance_id` 键——**WF-001 §4.8① 待同步登记（上游待回改）**，`has_approval` 是边级静态位、此键为实例级锁定位） + tooltip「审批进行中」；其他边正常 |

### 3.5 空状态 / 加载 / 失败

| 场景 | 表现 |
| --- | --- |
| 待办为空 | 插画 +「暂无待审批事项」；已办 Tab 提示「审批记录将在这里保留 3 年」（WF-006 留存策略） |
| 实例加载失败 | 抽屉内错误块 + request_id + 重试 |
| 动作冲突（409） | Toast 具体原因 + 自动刷新实例时间线 |

---

## 4. 技术架构

### 4.1 实体关系

```mermaid
erDiagram
    WORKFLOW_TRANSITIONS ||--o| APPROVAL_FLOWS : "approval_flow (WF-001 FK)"
    APPROVAL_FLOWS ||--|{ APPROVAL_NODES : "level 1..10"
    ISSUES ||--o{ APPROVAL_INSTANCES : "发起"
    WORKFLOW_TRANSITIONS ||--o{ APPROVAL_INSTANCES : "经由此边"
    APPROVAL_INSTANCES ||--|{ APPROVAL_RECORDS : "各级审批票"
    APPROVAL_FLOWS {
        uuid id PK
        uuid project_id FK
        string name
        boolean is_active
        boolean forbid_self_approve
    }
    APPROVAL_NODES {
        uuid id PK
        uuid flow_id FK
        int level
        string pass_mode "any|all"
        string approver_type "users|role|field"
        jsonb approver_config
        int timeout_hours
    }
    APPROVAL_INSTANCES {
        uuid id PK
        uuid issue_id FK
        uuid transition_id FK
        jsonb flow_snapshot "立案时刻流+节点定义快照（§4.3）"
        int current_level
        string status
        boolean is_terminal_passed "终审回填瞬态位（§4.5 三文档时序闭环，默认 false）"
    }
    APPROVAL_RECORDS {
        bigint id PK
        uuid instance_id FK
        int level
        uuid approver_id FK
        string action "pending|approve|reject|skipped"
        text comment
    }
```

### 4.2 模型定义

```python
class ApprovalFlow(BaseModel):
    """审批流定义（项目级）——被流转边引用（SET_NULL 摘挂，WF-001 §4.2）；治理口径先停用再删除（BR-13）"""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="approval_flows")
    name = models.CharField(max_length=64)
    description = models.CharField(max_length=255, blank=True, default="")
    is_active = models.BooleanField(default=True)
    forbid_self_approve = models.BooleanField(default=True, verbose_name="禁止自审（BR-12）")

    class Meta(BaseModel.Meta):
        db_table = "approval_flows"
        constraints = [
            models.UniqueConstraint(fields=["project", "name"], name="uniq_approval_flow_name"),
        ]


class ApprovalNode(BaseModel):
    """审批节点：level 顺序推进；pass_mode 决定或签/会签"""

    class PassMode(models.TextChoices):
        ANY = "any", "或签"
        ALL = "all", "会签"

    class ApproverType(models.TextChoices):
        USERS = "users", "指定成员"
        ROLE = "role", "项目角色组"
        FIELD = "field", "任务字段"

    flow = models.ForeignKey(ApprovalFlow, on_delete=models.CASCADE, related_name="nodes")
    level = models.PositiveSmallIntegerField(verbose_name="1..10，流内连续递增")
    pass_mode = models.CharField(max_length=8, choices=PassMode.choices)
    approver_type = models.CharField(max_length=8, choices=ApproverType.choices)
    approver_config = models.JSONField(
        default=dict,
        help_text="users: {\"user_ids\":[…]}；role: {\"role\":\"PROJ_ADMIN\"}；field: {\"field\":\"reporter|assignees\"}",
    )
    timeout_hours = models.PositiveIntegerField(null=True, blank=True, verbose_name="NULL=不超时")

    class Meta(BaseModel.Meta):
        db_table = "approval_nodes"
        constraints = [
            models.UniqueConstraint(fields=["flow", "level"], name="uniq_node_level_per_flow"),
            models.CheckConstraint(check=models.Q(level__gte=1, level__lte=10), name="chk_node_level_range"),
        ]
```

### 4.3 实例与记录模型

```python
class ApprovalInstance(BaseModel):
    """审批实例：一次发起一条；flow_snapshot 冻结定义（定义后续修改不影响在途实例）"""

    class Status(models.TextChoices):
        PENDING = "pending", "审批中"
        APPROVED = "approved", "已通过"
        REJECTED = "rejected", "已驳回"
        WITHDRAWN = "withdrawn", "已撤回"
        TERMINATED = "terminated", "已终止"

    issue = models.ForeignKey(Issue, on_delete=models.PROTECT, related_name="approval_instances",
        help_text="PROTECT：硬删任务前须先终止/归档其实例——与 transition/initiator 同档；软删（deleted_at）不涉级联，实例由 §2.3 钩子终止")
    transition = models.ForeignKey("WorkflowTransition", on_delete=models.PROTECT,
                                   related_name="instances")
    flow_snapshot = models.JSONField(verbose_name="立案时刻的流+节点定义快照")
    initiator = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    current_level = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    is_terminal_passed = models.BooleanField(
        default=False,
        help_text="终审回填瞬态位（§4.5 _complete_via_engine 三文档时序闭环）：终级票据全通过后、"
                  "引擎 transition() 回调前置 True——WF-001 §4.4 守门据此放行「is_terminal_passed=True "
                  "AND status=pending」形态；迁移成功 finalize APPROVED / 守卫失败复位 False 并 finalize "
                  "TERMINATED(guard_failed_at_complete)。回调窗口外恒 False；成功路径 finalize APPROVED 后随终态保留 True（终态行只读，不再参与判定）")
    terminal_reason = models.CharField(max_length=32, blank=True, default="",
        help_text="terminated 时：state_changed|guard_failed_at_complete|issue_deleted|issue_archived|admin（与 §2.3 终止原因一致）")
    from_state = models.ForeignKey(State, on_delete=models.PROTECT, related_name="+",
        verbose_name="发起前状态（驳回回退语义锚点）")
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        db_table = "approval_instances"
        constraints = [
            models.UniqueConstraint(
                fields=["issue", "transition"], condition=models.Q(status="pending"),
                name="uniq_pending_instance_per_edge",           # BR-10 部分唯一索引
            ),
        ]
        indexes = [
            models.Index(fields=["issue", "status"], name="idx_instance_issue"),
            models.Index(fields=["status", "updated_at"], name="idx_instance_scan"),
        ]


class ApprovalRecord(models.Model):
    """审批票：开票 INSERT + 一次合法迁移 pending→终态（仅 action/comment/acted_at 可写；BR-09，WF-006 §4.2 触发器强制；BigAutoField 主键保时序）"""

    class Action(models.TextChoices):
        PENDING = "pending", "待审"
        APPROVE = "approve", "通过"
        REJECT = "reject", "驳回"
        SKIPPED = "skipped", "跳过（自审/离职）"

    id = models.BigAutoField(primary_key=True)
    instance = models.ForeignKey(ApprovalInstance, related_name="records", on_delete=models.CASCADE)
    level = models.PositiveSmallIntegerField()
    approver = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    action = models.CharField(max_length=8, choices=Action.choices, default=Action.PENDING)
    comment = models.TextField(blank=True, default="")
    acted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "approval_records"
        constraints = [
            models.UniqueConstraint(fields=["instance", "level", "approver"],
                                    name="uniq_record_per_approver"),
        ]
        indexes = [
            models.Index(fields=["approver", "action", "created_at"], name="idx_record_todo"),
            models.Index(fields=["instance", "level"], name="idx_record_instance"),
        ]
```

### 4.4 迁移要点

1. 四表迁移全部 `CONCURRENTLY` 建索引；`uniq_pending_instance_per_edge` 为部分唯一索引（PG 原生支持，`CREATE UNIQUE INDEX … WHERE status='pending'`）。`approval_instances.is_terminal_passed` 布尔列随本表迁移落列（§4.3 字段定义；默认 false，存量行无需回填）。
2. `approval_records` 无 `updated_at`（动作一次成型、无二次更新语义——BR-09 落到表结构）。
3. 触发器 `approval_records_guard`（DDL 见 WF-006 §4.2）：**直连 DELETE 拒绝；仅 `pg_trigger_depth()>0` 的项目 FK 级联上下文放行 DELETE（WF-006 BR-11）**；UPDATE 仅放行 BR-09 的一次合法迁移（列级白名单 `action/comment/acted_at` + `pending` 单向出边），其余一律 EXCEPTION。
4. 历史数据零迁移：无工作流配置的项目不触碰（WF-001 默认工作流兜底不变）。

### 4.5 核心服务（`ApprovalService`）

```python
class ApprovalService:
    """审批执行唯一入口。所有写路径必须经此（BR-14 留痕完整性依赖唯一入口）。"""

    @transaction.atomic
    def start(self, transition, *, issue, actor) -> ApprovalInstance:
        """发起立案——WF-001 §4.4 审批挂接分支的唯一被调方
        （引擎侧调用：`ApprovalService().start(edge, issue=issue, actor=actor)`，签名逐字对齐）。"""
        flow = transition.approval_flow
        if not flow.is_active:
            raise ConflictError("RESOURCE_CONFLICT", sub="DISABLED")
        epoch = time.time() * 1000                                 # TASK-010 BR-04：动作入口毫秒时间戳
        snapshot = self._snapshot_flow(flow)                       # BR-11 定义冻结
        instance = ApprovalInstance.objects.create(
            issue=issue, transition=transition, initiator=actor,
            flow_snapshot=snapshot, from_state=issue.state,
        )
        self._open_level(instance, level=1)                        # 生成第 1 级审批票（INSERT pending）
        transaction.on_commit(lambda: notify_approvers.delay(str(instance.id), level=1))
        transaction.on_commit(lambda: issue_activity.delay({       # TASK-010 §4.3.2 载荷契约（BR-14）
            "issue_id": str(issue.id), "actor_id": str(actor.id),
            "verb": "updated", "epoch": epoch,
            "before": {"approval": None},
            "after": {"approval": f"{flow.name} · 第 1 级"},
            "comment": f"发起流转审批「{transition.name}」",
        }))
        return instance

    @transaction.atomic
    def act(self, *, instance_id, actor, action: str, comment: str = "") -> ApprovalInstance:
        instance = (ApprovalInstance.objects
                    .select_for_update()                           # 并发同级动作串行（§2.7）
                    .get(id=instance_id))
        if instance.status != Status.PENDING:
            raise ConflictError("RESOURCE_CONFLICT", sub="INVALID_STATE")
        if action in ("withdraw", "terminate"):
            return self._lifecycle(instance, actor, action, comment)   # BR-07/BR-08（非票动作：撤回/终止）
        record = self._current_record(instance, actor)             # 非当前级审批人 → 403 PERM_APPROVAL_NOT_ASSIGNEE
        if action == "reject" and not comment.strip():
            raise ValidationError("REQUIRED", field="comment")

        if action in ("approve", "reject"):
            record.action, record.comment, record.acted_at = action, comment, timezone.now()
            record.save(update_fields=["action", "comment", "acted_at"])
            # ↑ 唯一一次合法迁移：pending→终态、仅三列白名单（BR-09；触发器 approval_records_guard 兜底）
        self._emit_activity(instance, actor, action, comment)      # BR-14：动作落 Activity（载荷契约同 start）
        if action == "reject" or self._level_rejected(instance):
            return self._finalize(instance, Status.REJECTED, actor)
        if self._level_passed(instance):
            nxt = instance.current_level + 1
            if nxt <= len(instance.flow_snapshot["nodes"]):
                instance.current_level = nxt
                instance.save(update_fields=["current_level", "updated_at"])
                self._open_level(instance, nxt)
                transaction.on_commit(lambda: notify_approvers.delay(str(instance.id), level=nxt))
            else:
                return self._complete_via_engine(instance, actor)  # 终审：引擎补完迁移
        return instance

    @transaction.atomic
    def _complete_via_engine(self, instance, actor):
        """终审回填（WF-001 §4.4 / WF-006 §4.2 三文档时序闭环）：
        ① 先置 `is_terminal_passed=True`（实例仍 `pending`，守卫在 WF-001 §4.4 守门接受「`is_terminal_passed=True AND status=pending`」形态）；
        ② 单事务内调 `WorkflowService().transition(... approval_instance_id=…)` 重跑守卫——守卫失败经 **WF-004 现行 `guard_error(list[GuardFailure]) → ApiError`** 抛出（WF-001 §4.4 引擎对非守卫失败抛 `TransitionError(ApiError 子类)`，两类统一按 `ApiError` 捕获）；
        ③ 成功 → finalize `APPROVED`；失败 → 守门置 `is_terminal_passed=False`（回滚守卫放行位）+ finalize `TERMINATED(reason=guard_failed_at_complete)`。
        WF-006 §4.2 实例守卫仅锁终态（approved/rejected/withdrawn/terminated 后任何 UPDATE 拒绝），
        pending 四条出边（approved/rejected/withdrawn/terminated）全放行——本闭环所需两条出边自然放行。"""
        from plane.workflow.services import WorkflowService        # 防循环导入（领域服务层，api-conventions §2.1）
        instance.is_terminal_passed = True
        instance.save(update_fields=["is_terminal_passed", "updated_at"])
        try:
            WorkflowService().transition(                          # WF-001 §4.4 单事务入口（重跑守卫，BR-05）
                issue_id=str(instance.issue_id),
                # **WF-001 §4.4 引擎 to_state 形参为 State 主键**（`get_object_or_404(State, pk=…)` 解析）。
                # ApprovalInstance 无 to_state 直连 FK——经 transition 边取 WorkflowTransition.to_state
                # （WorkflowState），再取其 .state_id（State 主键）——sprint-7 R3 修订
                to_state_id=str(instance.transition.to_state.state_id),
                actor=actor,
                transition_id=str(instance.transition_id),
                approval_instance_id=str(instance.id))             # 终审回填标识：携带则走「跳过二次挂起」分支
        except ApiError as e:                                      # 守卫失败经 WF-004 guard_error → ApiError；引擎非守卫失败 TransitionError 为其子类，统一按 ApiError 捕获
            instance.is_terminal_passed = False
            instance.save(update_fields=["is_terminal_passed", "updated_at"])
            return self._finalize(instance, Status.TERMINATED, actor,
                                  reason="guard_failed_at_complete", detail=str(e))
        return self._finalize(instance, Status.APPROVED, actor)

**act() 边界条件表**（与 §4.5 引擎契约对齐「发起 / 终审回填两路径」分支判据一一对应，调用方按入口形态与实例状态决定走哪一支）：

| 进入分支 | 入口形态 | 前置条件 | 后续动作 | 失败/异常响应 |
| --- | --- | --- | --- | --- |
| L1 非本级动作 | `action` ∈ {`approve`,`reject`} 且审批人非当前级 | — | `_current_record()` 查无对应票 | `403 PERM_APPROVAL_NOT_ASSIGNEE`（rbac §8.4 R8，§2.6） |
| L2 本级 reject 缺意见 | `action=reject` 且 `comment` 为空 | 已通过 L1 | — | `400 VALIDATION_ERROR` + `details[].code=REQUIRED,field=comment`（BR-06） |
| L3 本级 reject 成功 | `action=reject` 校验通过 | 已通过 L1、L2 | 票一次性迁移 → 任一人 reject 立即 `instance=rejected`（BR-03） | — |
| L4 本级 approve 失败（级未过） | `action=approve` 校验通过 | 已通过 L1；会签未满 / 或签同票已存在 | 票一次性迁移，停留当前级 | — |
| L5 本级 approve 通过且存在下一级 | `action=approve` 校验通过；`_level_passed()` 为真 | 已通过 L1；当前级全员满足 `pass_mode` | `current_level += 1`、开新级审批票、`on_commit` 通知下一级审批人 | — |
| L6 本级 approve 终级通过 | `action=approve` 校验通过；`_level_passed()` 为真且无下一级 | 已通过 L1；当前级为终级且全员满足 `pass_mode` | 调 `_complete_via_engine()`：携带 `approval_instance_id` 走「终审回填直放行」路径——引擎侧守卫重跑通过则迁移 + Activity；失败抛 `ApiError`（WF-004 `guard_error`）→ 实例 `terminated(guard_failed_at_complete)`，任务**未迁移**（BR-05） | 终审守卫重跑失败：实例 `terminated(guard_failed_at_complete)`、任务保持发起前状态，双方通知（§2.5） |
| L7 撤回 | `action=withdraw` | 调用人 == `initiator`（rbac §8.2「仅本人提交」）且实例内无任何 `approve/reject` 记录 | 调 `_lifecycle(instance, actor, "withdraw", comment)` | 非发起人 → `403 PERM_DENIED`；已有动作 → `409 RESOURCE_CONFLICT` + `HAS_ACTIONS`（BR-07） |
| L8 终止 | `action=terminate` | 调用人具备 `PROJ_ADMIN`（rbac §8.2）且 `comment` 非空 | 调 `_lifecycle(instance, actor, "terminate", comment)`，comment 落审计（WF-006） | 缺 comment → `400 VALIDATION_ERROR` + `REQUIRED`；非管理员 → `403 PERM_DENIED`（BR-08） |
| L9 实例非 pending | 任意 `action` | `instance.status != "pending"` | — | `409 RESOURCE_CONFLICT` + `INVALID_STATE`（§2.6） |

> L6「终级通过」为 act() 唯一对外触发引擎 `transition()` 的分支，与 §4.5 引擎契约对齐「② 终审回填」路径同源——引擎侧依 `approval_instance_id` 跳过二次挂起、直放行迁移。本表 L1~L9 边界按动作类型 + 调用人身份 + 实例状态三元组正交判定，不引入「WF-001 引擎锁图」等额外状态；图状态由 WF-001 自身维护，act() 仅消费引擎结果（`ApiError`（守卫/引擎失败，WF-004 `guard_error` 与 WF-001 `TransitionError` 同基类）/ 正常返回）。
```

**`_open_level` 审批人解析**（快照语义 BR-11 + 禁自审 BR-12）：

```python
def _open_level(self, instance, level: int):
    node = instance.flow_snapshot["nodes"][level - 1]
    approvers = resolve_approvers(node, issue=instance.issue)      # users/role/field 三源
    approvers = [u for u in approvers if is_project_member(u, instance.issue.project)]
    if not approvers:
        raise ValidationError("EMPTY_APPROVERS", field="approvers")   # BR-02：空审批人集不可立案（400，与 §2.6/BR-02 统一）
    records = []
    for u in approvers:
        skipped = (instance.flow_snapshot["forbid_self_approve"]
                   and u.id == instance.initiator_id)
        records.append(ApprovalRecord(
            instance=instance, level=level, approver=u,
            action=Action.SKIPPED if skipped else Action.PENDING,
            comment="self" if skipped else "",
        ))
    ApprovalRecord.objects.bulk_create(records)
    if all(r.action == Action.SKIPPED for r in records):           # 全员被跳（只剩发起人）
        self._escalate_to_proj_admin(instance, level)              # BR-12 转交管理员
```

**引擎契约对齐（WF-001 §4.4 现行版）**：

1. 两个回调签名逐字取自 WF-001 §4.4：`ApprovalService().start(edge, issue=…, actor=…)`（审批挂接分支的唯一被调方）与 `WorkflowService().transition(issue_id=…, to_state_id=…, actor=…, transition_id=…, approval_instance_id=None)`（构造器无参、关键字传参，`approval_instance_id` 为可选项，仅终审回填路径携带）——本文**不另设** `complete_transition` 之类的第二引擎入口。
2. 终审回填复用 `transition()` 单事务：守卫在引擎事务内重跑（WF-001 BR-12 顺序「守卫 → 审批触发 → 副作用 → 状态更新 → Activity」），失败抛 `ApiError`（WF-004 `guard_error`）→ 实例 `terminated(guard_failed_at_complete)`、不迁移（BR-05）。引擎审批分支（`approval_flow` 非空 → 挂起）须严格区分两条进入路径，与 WF-001 §4.4「审批发起与终审回填」同源同义——**协议权威为 WF-001 §4.4 引擎定义，本表为其 WF-002 侧调用形态镜像**（任一侧变更须同步，见 §8 跨文档契约同步点）：

   | 进入路径 | `approval_instance_id` | 触发条件 | 引擎行为 | 视图层响应 |
   | --- | --- | --- | --- | --- |
   | ① 发起 | `None`（缺省） | 用户常规流转（点击按钮 / 看板拖拽） | 守卫先过 → `ApprovalService().start()` 创建实例并挂起，状态不变 | `202 Accepted` + `pending_approval`（§4.6 发起响应） |
   | ② 终审回填 | 携带 `approval_instance_id` | WF-002 终审通过经 `_complete_via_engine()` 再入 | 引擎校验实例归属本边且 `status=approved` **或**（`is_terminal_passed=True` 且 `status=pending`，§4.5 时序闭环）→ 守卫按 BR-12 顺序重跑 → 跳过二次挂起，直接执行副作用与状态更新 | `200 OK` + `issue_state`（§4.6 终审通过响应） |
   | 守门失败 | 携带 `approval_instance_id` 但实例不属于本边 / 未到 `approved` 终态（如 `pending`/`rejected`/`withdrawn`/`terminated`） | WF-002 误调或竞态 | 拒绝再入，不迁移 | `409 RESOURCE_STATE_INVALID`（`details[].field=approval_instance_id`、`code=INVALID`，WF-001 §4.4 同码同 `details[]` 形态） |

   `rejected`/`withdrawn`/`terminated` 终态由 WF-002 业务规则保证不发起回填（驳回 / 撤回 / 终止链路无 `_complete_via_engine` 调用），守门为**深度防御**——后端不可信边界。

**Activity 载荷（TASK-010 §4.3.2 worker 契约）**：任务名为 `issue_activity`（不自造任务名），载荷 dict `{issue_id, actor_id, verb, epoch, before, after, comment?}`，幂等键 `sha256(verb + issue_id + actor_id + epoch)`。verb 固定 `updated`（TASK-010 §1.2 verb ∈ {created, updated, deleted}，本文不新增 verb）；`field="approval"` 为对 §1.2 事件矩阵的**字段扩展**（与 TASK-004 `parent` 单字段特例同范式：固定 verb+field、不入 `TRACKED_*` 常量、行内容由载荷 `before/after` 显式携带）；`epoch` 一律**毫秒时间戳**（float，BR-04，服务入口生成）——禁止传 `datetime` 对象。

### 4.6 API 定义

| 方法 | 路径 | 说明 | 权限 |
| --- | --- | --- | --- |
| GET | `/api/v1/workspaces/{slug}/projects/{id}/approval-flows/` | 审批流列表（含节点） | `workflow.manage`（完整配置）；项目成员经 `project.read` 可见名称 |
| POST | 同上 | 创建审批流（nodes 数组**全量**创建；与 WF-001 §4.8④ 集合替换同形） | `workflow.manage` |
| GET/PATCH/DELETE | `…/approval-flows/{fid}/` | 详情/更新/删除（PATCH 对 nodes 字段为**整体替换**——与 WF-001 `PUT graph/` 集合型同形，逐项 PATCH 不在本文档语义内；删除 = SET_NULL 摘挂引用边，返回 200 + `affected_transition_ids`，BR-13） | `workflow.manage` |
| GET | `/api/v1/workspaces/{slug}/approvals/pending/` | 我的待办（跨项目）；支持 `?count_only=1` 精简信封（徽标轮询，见下注） | 工作空间成员（行级仅返回本人当前级票） |
| GET | `/api/v1/workspaces/{slug}/approvals/acted/` | 我的已办（跨项目）——当前用户有过审批动作的实例（行级口径见下注；§3.1 已办 Tab 数据源） | 同上（行级仅返回本人动作过的实例） |
| GET | `/api/v1/workspaces/{slug}/approvals/mine/` | 我发起的 | 同上（行级仅本人发起） |
| GET | `/api/v1/workspaces/{slug}/projects/{id}/approval-instances/{aid}/` | 实例详情（时间线） | 项目成员（BR-15） |
| POST | `…/approval-instances/{aid}/actions/` | `approve/reject/withdraw/terminate` | approve/reject：`approval.act`（rbac §8.2 + §8.4 R8 当前级指定审批人）；withdraw：`approval.withdraw`（仅发起人）；terminate：`PROJ_ADMIN` |
| GET | `/api/v1/workspaces/{slug}/projects/{id}/issues/{issue_id}/approvals/` | 任务的实例列表 | 项目成员 |

> **路径参数记号**：`{aid}` = 审批实例 UUID（`ApprovalInstance.id`）；`{issue_id}` = 任务 UUID——两者不同名，避免同符异义。

> **WF-001 `transitions/` 端点入参契约**（发起 / 终审回填两条路径的入参差异，与 §4.5 引擎契约对齐表同源；端点本体定义在 WF-001 §4.8②，本处仅声明 WF-002 视角下的入参形态）：`POST /api/v1/workspaces/{slug}/projects/{id}/issues/{issue_id}/transitions/` 请求体为 WF-001 §4.8② 正典形态 `{to_state_id, transition_id?, guard_payload?}` **外加可选 `approval_instance_id`**——`approval_instance_id` 缺省 = 发起路径（§4.5 表 ①，前端必填 `to_state_id`）；携带 = 终审回填路径（§4.5 表 ②，`to_state_id` 由后端从边取、客户端无须传），仅 WF-002 终审通过后 `_complete_via_engine()` 内部调用、**不暴露给前端**（前端发起流转永远不带该字段，由后端守卫入参与守门逻辑共同保证路径唯一；**WF-001 §4.8② 待同步登记 `approval_instance_id` 可选键与 202 变体（上游待回改）**）。

**审批中心三端点公共约定**（`pending` / `acted` / `mine` 与 §3.1 三 Tab 一一对应，游标分页按 api-conventions §6.2/§6.3）：

- **行级口径**：`pending` = 当前用户为某 pending 实例**当前级** `pending` 票的审批人；`acted`（已办）= 当前用户在该实例存在 `approve/reject` 动作票（任一级含历史级；`skipped` 票不计入已办）；`mine` = 当前用户为 `initiator`。
- **游标分页**：`per_page` 默认 100、上限 100（超出静默截断并经 `meta.degraded` 告知）；`meta` 必含九字段——`next_cursor`、`prev_cursor`、`next_page_results`、`prev_page_results`、`count`、`total_count`、`total_pages`、`page`、`per_page`（`?count=false` 时 `total_count`/`total_pages` 为 `null`，§6.4）。三端点分页 `meta` 形态（外层 `{"status": "success", "data": [...]}` 信封略）：

```json
{
  "next_cursor": "100:1:0",
  "prev_cursor": "100:0:1",
  "next_page_results": true,
  "prev_page_results": false,
  "count": 100,
  "total_count": 236,
  "total_pages": 3,
  "page": 1,
  "per_page": 100
}
```

- **排序固定**、不开放 `?ordering=`（无白名单，不适用 api-conventions §5.4 参数）：`pending`/`mine` 按实例 `created_at` 倒序、`acted` 按动作票 `acted_at` 倒序；末级一律追加 `-created_at, -id` 保游标稳定（§5.4 默认排序规则）。
- **`?count_only=1`**（仅 `pending` 支持）：跳过列表构造，返回**完整信封** `{"status":"success","data":{"count": <int>}}`（与 §4.6 全量列表信封对称，仅 `data` 退化为单数字段；`count` = 行级口径下的待办票总数）——§4.8 `refreshCount` 徽标轮询与 WS `approval.updated` 增量刷新共用该端点。**登记**：该参数为本文档新增查询参数，api-conventions §5 现文未含——**架构文档待回改**（随 Sprint 7 首个 PR 补登入 §5 查询参数白名单；同款「待补登」范式见 WF-001 §4.8④ PUT 白名单注）。

**发起响应**（WF-001 transitions/ 端点在挂审批边时的 202 变体。WF-001 §4.8② 为该响应的**最小摘要**（`{instance_id, flow, current_node}` 三键）；下表为**全量展开形态**——**语义映射**（键名不一一对应）：摘要 `flow` = 全量 `flow_name`（流程展示名）；摘要 `current_node` = 全量 `current_level`（当前级号）的**渲染层简化**——前端展示用「当前审批人姓名」直接代替「级号 N + 人员表」。两文档以本表为审批详情权威，WF-001 侧摘要语义映射到本表全量形态的解释口径以本节为准。

```json
{
  "status": "success",
  "data": {
    "pending_approval": {
      "instance_id": "2e6a9c4f-8b1d-4e3a-a7c5-9f2b6d8e0c08",
      "status": "pending",
      "current_level": 1,
      "flow_name": "研发上线审批 v3",
      "from_state": {"id": "3f2c8e6a-…", "name": "开发中"},
      "to_state": {"id": "8c5d2f7a-…", "name": "评审中"},
      "records": [
        {"level": 1, "approver": {"id": "4b8d1f6c-…", "name": "王一"}, "action": "pending"},
        {"level": 1, "approver": {"id": "6c7d1a2b-…", "name": "陈曦"}, "action": "skipped", "comment": "self"}
      ]
    }
  }
}
```

> 成功响应不携带 `meta.request_id`——信封约定（api-conventions §4.1/§4.2）：`request_id` 仅出现在 `error` 对象内；全部响应（含成功）经 `X-Request-Id` 响应头回传。实体主键一律 UUID v4 字符串（api-conventions §4.5），`request_id` 为 ULID。

**动作请求/响应**：

```http
POST /api/v1/workspaces/acme/projects/6f9c1e3a-…/approval-instances/2e6a9c4f-…/actions/
Content-Type: application/json

{"action": "approve", "comment": "联调环境已就绪"}
```

```json
{
  "status": "success",
  "data": {
    "instance_id": "2e6a9c4f-8b1d-4e3a-a7c5-9f2b6d8e0c08",
    "status": "pending",
    "current_level": 2,
    "level_passed": 1
  }
}
```

**终审通过响应**（引擎已补完迁移）：

```json
{
  "status": "success",
  "data": {
    "instance_id": "2e6a9c4f-8b1d-4e3a-a7c5-9f2b6d8e0c08",
    "status": "approved",
    "issue_state": {"id": "8c5d2f7a-…", "name": "评审中"},
    "completed_at": "2026-09-07T09:15:22.418Z"
  }
}
```

**错误响应**（越级动作，`details` 缺省——PERM 类错误按 api-conventions §8.9 交由调用方渲染局部空态，不弹全局 toast）：

```json
{
  "status": "error",
  "error": {
    "code": "PERM_APPROVAL_NOT_ASSIGNEE",
    "message": "当前审批级别为第 2 级，您不是本级指定审批人",
    "request_id": "01J9AM7C3D5F8H2K4N6Q9S1VXA"
  }
}
```

### 4.7 Celery 任务

```python
@app.task(queue="notifications", ignore_result=True)   # 队列取自既有清单（INFRA-002 §4.1 / tech-stack §6.2：notifications/webhooks/reports/imports）
def notify_approvers(instance_id: str, level: int):
    """当前级 pending 审批票 → 收件箱 + 邮件（COLLAB-001 通道与偏好）；去重键 instance+level"""
    instance = ApprovalInstance.objects.get(id=instance_id)
    if instance.status != "pending" or instance.current_level != level:
        return                                        # 状态已变，通知作废（延迟窗口防护）
    for rec in instance.records.filter(level=level, action="pending"):
        notify(rec.approver, kind="approval_todo", payload=instance_payload(instance))

@app.task(queue="workflow")    # 队列名登记：tech-stack.md §9 准入同步（INFRA-002 §4.1 既有四队列 notifications/webhooks/reports/imports 不含 workflow；Sprint 7 由 WF-002/003/005/006 共用，按 §9 流程随首个 PR 并入 worker 编排表）。**架构文档待回改**
def approval_timeout_scan():
    """beat 每 15min：超时提醒；离职审批人转交按每日聚合窗口执行（§2.4 / §2.7——offboarded 判定
    以「非成员且 pending 超 24h」为条件，15min 扫描内以任务内 24h 窗口自然限频，与 §2.7
    「每日扫描」语义等价：每任务每审批人至多每日转交一次）"""
    now = timezone.now()
    qs = ApprovalInstance.objects.filter(status="pending").select_related("issue")
    for inst in qs.iterator(chunk_size=200):
        node = inst.flow_snapshot["nodes"][inst.current_level - 1]
        hours = node.get("timeout_hours")
        if not hours:
            continue
        entered = inst.records.filter(level=inst.current_level).aggregate(
            t=models.Min("created_at"))["t"]
        overdue_h = (now - entered).total_seconds() / 3600
        if overdue_h >= hours:
            remind_once(inst, kind="timeout", window="24h")       # 去重窗口 24h
        if overdue_h >= hours + 24:
            remind_once(inst, kind="timeout_escalate",
                        extra_targets=[inst.initiator, *proj_admins(inst.issue.project)])
    # 离职转交（§2.7）：当前级审批人已非项目成员且 pending 超 24h → 该票 skipped(offboarded)
    for inst in qs:                                            # 同一扫描内二次遍历，条件独立
        if pending_over_24h(inst) and not is_active_member(inst.current_approvers, inst.issue.project):
            skip_offboarded_and_notify(inst)                   # 置 skipped(offboarded) + 提醒 PROJ_ADMIN 补位（admin 动作留痕）
```

### 4.8 前端实现

```tsx
// apps/web/features/approval/approvalStore.ts（MobX）
export class ApprovalStore {
  @observable pending: ApprovalInstance[] = [];
  @observable pendingCount = 0;

  constructor(private root: RootStore) {
    // COLLAB-004 个人房间 user:{user_id}（连接 WSS /live）→ approval.updated 增量刷新徽标
    root.ws.subscribe(`user:${root.user.id}`, (evt) => {
      if (evt.event === "approval.updated") this.refreshCount();
    });
  }

  @action async refreshCount() {
    const { data } = await api.get("/workspaces/:slug/approvals/pending/", { params: { count_only: 1 } });
    runInAction(() => (this.pendingCount = data.count));
  }

  async act(instanceId: string, action: "approve" | "reject", comment?: string) {
    const { data } = await api.post(
      `/workspaces/:slug/projects/:pid/approval-instances/${instanceId}/actions/`, { action, comment });
    if (data.status === "approved") {
      this.root.issueStore.applyStateChange(data.issue_state);   // 终审迁移已生效
      toast.success("审批通过，任务已流转");
    }
    return data;
  }
}
```

流转按钮侧（WF-001 transitions/available/ 消费的 202 分支处理）：

```tsx
async function runTransition(edge: TransitionEdge) {
  const res = await api.post(issueUrl + "transitions/",
      { to_state_id: edge.to_state.id, transition_id: edge.id });   // §4.6 正典形态：发起路径必填 to_state_id（WF-001 §4.8②）
  if (res.status === 202 || res.data.pending_approval) {
    toast.info("已提交审批，通过后自动完成流转");      // 不乐观迁移卡片
    approvalStore.refreshCount();
    return;
  }
  kanbanStore.applyMove(res.data.issue);               // 无审批边：即时生效
}
```

### 4.9 性能与索引

| 查询 | 索引 | 预算 |
| --- | --- | --- |
| 我的待办/已办（`records: approver+action` JOIN instances；pending 限定 `status=pending`，acted 限定本人动作票） | `idx_record_todo` + `idx_instance_scan` | P95 < 150ms |
| 实例详情时间线 | `idx_record_instance` | P95 < 100ms |
| 任务审批徽标（列表批量） | `idx_instance_issue` + `prefetch_related`（单查批量实例，防 N+1） | 含在列表总预算内 |
| 超时扫描 | `idx_instance_scan`（status+updated_at） | 单轮 < 2s（10k 实例） |

---

## 5. 测试用例

### 5.1 单元测试

| 编号 | 用例 | 断言 |
| --- | --- | --- |
| UT-01 | 或签节点：1/3 通过即本级过 | 推进 level+1，新票生成 |
| UT-02 | 会签节点：2/3 通过不推进 | 停留当前级 |
| UT-03 | 会签 1 人驳回 | 实例 rejected，任务状态未变 |
| UT-04 | `field=reporter` 快照 | 立案后换报告人，审批人集不变 |
| UT-05 | 禁自审：发起人=审批人 | 票记 `skipped(self)`；仅剩发起人时转交 PROJ_ADMIN |
| UT-06 | 撤回：无动作可撤回 / 有动作 409 | BR-07 两分支 |
| UT-07 | reject 缺 comment → 400 | `VALIDATION_ERROR/REQUIRED` |
| UT-08 | 越级动作 → 403 | 非当前级审批人 |
| UT-09 | 终审守卫重跑失败 | 实例 `terminated(guard_failed_at_complete)`，任务未迁移，双方通知 |
| UT-10 | 同边并发发起 | 部分唯一索引兜底，后到 409 |
| UT-11 | 停用流发起 → 409 `DISABLED`；存量实例可继续 | BR-13 |
| UT-12 | 审批人被移出项目后仍持快照票 | 动作时校验项目成员身份：快照审批人身份失效（被移出项目）→ 404 `RESOURCE_NOT_FOUND`（行级过滤先行，不可见即不达动作校验——与 §4.6/IT-09 一致）；被移出项目后直连项目级端点 → 404 `RESOURCE_NOT_FOUND`（行级过滤不可见，rbac §6，与 IT-09 同口径）；其待办由 beat 扫描置 `skipped(offboarded)` 并转交（§2.7） |
| UT-13 | 其他边流转成功 → 本实例自动 `terminated(state_changed)` | §2.3 |
| UT-14 | 超时扫描：到点提醒、24h 升级、去重窗口 | 通知次数精确断言 |
| UT-15 | BR-02 空审批人集两入口同码：role 组展开后全员非项目成员 | 立案（发起流转）→ 400 `VALIDATION_ERROR` + 子码 `EMPTY_APPROVERS`；审批流定义保存（POST/PATCH nodes）→ 同码同子码 |
| UT-16 | §2.7 三截断边界：节点 11 级 / 单节点 21 人 / role 组展开 > 50 人 | 前两者 → 409 `RESOURCE_LIMIT_EXCEEDED`（details 子码 `LIMIT`，§2.7 裁决 F 口径）；role 组展开 > 50 人 → 200 + `meta.warnings[]` 截断告警（定义保存时校验，TASK-013 BR-08 软上限同范式） |
| UT-17 | §2.3 终止触发：任务删除 → pending 实例自动 `terminated(issue_deleted)` | 实例状态/原因断言；审批票保留（审计） |
| UT-18 | §2.3 终止触发：任务归档（`archived_at` 写入）→ pending 实例自动 `terminated(issue_archived)` | 同上 |

### 5.2 集成测试

| 编号 | 用例 | 断言 |
| --- | --- | --- |
| IT-01 | 全链路：发起（202）→ L1 或签过 → L2 会签全员过 → 终审迁移 | 任务到目标态；Activity 含 approval 全事件；时间线完整 |
| IT-02 | 驳回链路 | 状态回发起前（从未离开）；发起人收到意见通知 |
| IT-03 | 撤回链路 | 实例 withdrawn；流转按钮恢复可用 |
| IT-04 | 管理员终止 | comment 落审计；实例 terminated(admin) |
| IT-05 | 并发同级会签动作 | 行锁串行，结果确定（先到生效，后到按新态判定） |
| IT-06 | 审批中任务字段编辑 → 终审守卫重跑 | 编辑导致必填缺失时终审失败路径 |
| IT-07 | 通知管道 | on_commit 后 Celery 收到任务；偏好关闭邮件时仅收件箱 |
| IT-08 | 审批流删除：被引用时 DELETE 200 + 摘挂清单 | `data.affected_transition_ids` 含全部引用边；各边 `approval_flow` 变 NULL（SET_NULL，BR-13）；停用流再发起 → 409（UT-11 关联） |
| IT-09 | 审批端点权限矩阵（rbac §8.2 `approval.act`/`approval.withdraw` + §8.4 R8；四主体，TASK-005 IT-09 范式）：项目内 `PROJ_VIEWER`/`PROJ_COMMENTER`/非本级审批人的 `PROJ_CONTRIBUTOR` 三名成员 + 一名**同工作空间**他项目成员，存在 1 条 pending 实例（当前级审批人 = `PROJ_ADMIN`） | VIEWER/COMMENTER：GET 实例详情/待办 200（`project.read`，行级过滤），POST actions 403 `PERM_ROLE_INSUFFICIENT`（无 `approval.act`）；CONTRIBUTOR（非本级）：approve 403 `PERM_APPROVAL_NOT_ASSIGNEE`、withdraw 403 `PERM_DENIED`（非发起人）；他项目成员：**项目级端点**（实例详情/动作/任务实例列表）404 `RESOURCE_NOT_FOUND`（行级过滤不可见，rbac §6）、**工作空间级三端点**（`pending/acted/mine`）200 空列表（工作空间成员即有权限，行级过滤为空）；PROJ_ADMIN（本级审批人）approve 200 对照 |
| IT-10 | 审批流定义配置端点负向矩阵（`workflow.manage`，rbac §8.2；WF-001 IT-08 四主体范式）：`PROJ_VIEWER`/`PROJ_COMMENTER`/`PROJ_CONTRIBUTOR` 三名项目成员分别调 `approval-flows/` 四端点（GET 列表 / POST 创建 / GET|PATCH|DELETE 详情） | GET 列表 200（名称可见，`project.read` 面）；POST/PATCH/DELETE 全部 403 `PERM_ROLE_INSUFFICIENT`（无 `workflow.manage`）；PROJ_ADMIN 对照组全通过 |

### 5.3 E2E 测试

| 编号 | 场景 | 断言 |
| --- | --- | --- |
| E2E-01 | 审批中心三 Tab：待办动作 → 已办可见 → 我发起进度 | 徽标数实时变化（WS） |
| E2E-02 | 会签逐级全链路（3 浏览器登录 3 审批人） | 每级通知到达；最终任务流转 |
| E2E-03 | 驳回：意见框必填校验 → 提交 → 发起人收件箱见意见 | 全流程 |
| E2E-04 | 撤回：发起人在无动作时撤回 | 待办从审批人列表消失 |
| E2E-05 | 画布配置：流转边启用审批 + 节点编辑 → 发布生效 | 发起时走 202 分支 |
| E2E-06 | 任务详情「审批中」徽标 → 跳审批详情抽屉 | 时间线与后端一致 |

---

## 6. 竞品深度对标

### 6.1 Ones 审批实现分析

| 观察点 | Ones 做法 | 本系统决策 |
| --- | --- | --- |
| 审批与流转耦合 | 审批配置在状态流转上，通过后状态自动迁移 | 一致（`approval_flow` 挂边） |
| 通过模式 | 会签/或签/逐级齐全 | 对齐为三原语（pass_mode × level） |
| 弱点（社区反馈） | 审批周期长时任务字段已变，通过瞬间按旧认知迁移 | **终审重跑守卫**（BR-05）——Ones 无此机制，是本系统的正确性差异化 |
| 审批人动态来源 | 支持「字段成员」（如负责人） | 对齐 + **立案快照**（BR-11），消除「换人逃审」窗口 |

### 6.2 Jira (JSM Approvals) 分析

| 观察点 | Jira 做法 | 代码路径 / 证据 | 本系统决策 |
| --- | --- | --- | --- |
| 审批定义 | 挂工作流状态（进入状态即待审），审批人是 issue 字段 | JSM `approval` workflow function | 本系统挂**流转边**而非状态——语义更准（审批的是「这次流转」），且不污染字段体系 |
| 通过规则 | `ANY/ALL` 两模式 | 对齐 `pass_mode` | — |
| 驳回去向 | 预配置目标状态 | 本系统「回发起前状态」（from_state 锚点），免配置且无歧义 | — |
| 不学之处 | 审批人依赖 Insight/用户-picker 字段，配置链长 | — | 三源审批人（users/role/field）自足 |

### 6.3 钉钉/飞书审批中心分析

| 观察点 | 做法 | 本系统采纳 |
| --- | --- | --- |
| 信息架构 | 待办/已办/我发起三 Tab + 全局徽标 | §3.1 全对齐 |
| 详情时间线 | 节点纵向时间线 + 意见留痕 | §3.2 全对齐 |
| 差异 | 通用 OA 审批与业务对象弱关联 | 本系统实例强绑定任务与流转边（任务摘要内嵌、徽标联动），不做独立审批单 |

### 6.4 本系统设计决策汇总

1. **守卫双跑**（发起 + 终审）：长审批周期下的正确性兜底，竞品均无。
2. **定义快照 + 审批人快照**（`flow_snapshot` + records 冻结）：在途实例不受定义修改影响，审计口径稳定。
3. **超时不自动通过/拒绝**：企业审计不接受系统代决，只做提醒升级——与钉钉「超时自动通过」刻意分歧。
4. **驳回 = 回发起前状态**：任务在审批期间从未离开原状态，无「回退迁移」概念，Activity 链干净。

---

## 7. 里程碑与验收

### 7.1 交付物清单

| 类别 | 产物 |
| --- | --- |
| Model / Migration | `approval_flows` / `approval_nodes` / `approval_instances`（部分唯一索引）/ `approval_records`（一次合法迁移，触发器强制）四表 |
| 后端 | `ApprovalService`（立案/动作/推进/终审/撤回/终止）、审批人三源解析、超时扫描与转交 beat、与 `WorkflowService.transition()` 单事务衔接（终审回填，§4.5） |
| API | 审批流 CRUD 四端点 + 审批中心三端点（`pending`/`acted`/`mine`，§4.6 与 §3.1 三 Tab 一一对应）+ 实例详情/动作/任务实例列表三端点 |
| 前端 | 审批中心三 Tab 页、审批详情抽屉（时间线）、画布审批分区配置、任务/看板审批徽标、202 分支流转交互 |
| 测试 | UT-01~18、IT-01~10、E2E-01~06 |

### 7.2 可操作演示的验收标准

1. 配置「研发上线审批」（3 节点：或签 2 人 → 会签 3 人 → 单人产品负责人，分别设 24h/48h/无超时），挂到「提交评审」边并发布。
2. 发起流转：守卫不过时不立案；守卫过后 202 + 第 1 级审批人收到收件箱与邮件；任务停留原状态并显示「审批中 · 第 1 级」。
3. 五场景演示：或签 1 人过 → 会签 2 人过后第 3 人驳回（整体驳回，意见送达发起人）→ 重新发起 → 发起人撤回（无动作时）→ 管理员终止（comment 留痕）→ 终审全员通过后任务自动迁移且 Activity 含完整审批事件链。
4. 正确性演示：审批挂起期间修改任务使必填字段缺失 → 终审通过时实例 `terminated(guard_failed_at_complete)`，任务未迁移，双方收通知。
5. 禁自审演示：发起人即审批人时其票 `skipped(self)`；仅剩发起人时转交 PROJ_ADMIN。
6. 超时演示：测试时钟拨快触发超时提醒与 24h 升级加报；去重窗口内不重复。
7. 篡改演示：改 `level`/审批人/二次动作的 UPDATE 与 DELETE 被触发器 `approval_records_guard` 拒绝；唯一合法迁移（`pending → approve`，仅 `action/comment/acted_at` 三列）成功；时间线与数据库记录一致。
8. 性能：待办列表 1 万实例规模 P95 < 150ms；超时扫描 10k 实例单轮 < 2s。

---

## 8. 排期（与 WF-001 §1.2 目标 5、sprint-overview §8 对齐）

WF-002 排期严格对齐 `WF-001` §1.2 目标 5「画布编辑器在第 10 周 D3-4 窗交付」的同窗联调窗口：WF-002 引擎（含端点、`approval_flow` FK、终审回填契约、act() 边界条件）须在第 9 周 D1-2 窗之前可联调，以便 WF-001 画布侧栏审批分区在第 10 周 D3-4 窗同窗挂接。画布编辑器本体（节点 / 边 / 守卫 / 副作用 UI）归属 WF-001，**WF-002 仅交付画布内「审批」分区侧栏**（§3.3，挂在 WF-001 流转边侧栏内）。

| 工作项 | 人日 | 归属 | 排期窗 | 与 WF-001 关系 |
| --- | --- | --- | --- | --- |
| 引擎与模型：`ApprovalService`（立案 / act() / 终审回填 / 撤回 / 终止 / 超时扫描）+ 四表 + 触发器 `approval_records_guard`（WF-006 §4.2） | 后端 3.0 | WF-002 | 第 9 周 D1-2 窗 | 必须在 WF-001 引擎交付同期可联调，触发器由 WF-006 提供 DDL |
| API 端点：审批流 CRUD + 审批中心三端点（`pending`/`acted`/`mine`，§4.6）+ 实例详情 / 动作 / 任务实例列表 | 后端 1.0 | WF-002 | 第 9 周 D1-2 窗（与引擎同窗交付） | 与 WF-001 `transitions/` 端点对齐 `approval_instance_id` 入参契约（§4.5、§4.6 注） |
| 端到端接口测试：JMeter `sprint-7-flow.py` + 静态检查 `ci-checks-sprint-7.sh`（新增 WF-002 集合断言：守卫双跑 / 终审回填 200 / 实例未终态回填 409 / 触发器白名单 / BR-02 空审批人集） | 后端 0.5 | WF-002 | 第 9 周 D1-2 窗 | 与 WF-001 引擎 IT-03 同窗收口（WF-001 §6.1 IT-03 已覆盖「发起挂起 / 终审回填 / 守门 409」三态） |
| 前端审批中心页：三 Tab（待办 / 已办 / 我发起，§3.1）+ 详情抽屉（§3.2）+ 任务侧徽标 + 202 分支流转交互 | 前端 2.0 | WF-002 | 第 10 周 D1-2 起步、D3-4 联调收口 | 与 WF-001 流转按钮侧（§3.4、§4.8 `runTransition`）联调，徽标走 COLLAB-004 个人房间 `approval.updated` |
| 前端画布审批分区侧栏（§3.3，挂在 WF-001 流转边侧栏内） | 前端 0.5 | WF-002 | 第 10 周 D3-4 窗 | 与 WF-001 画布编辑器（§1.2 目标 5）同窗交付——WF-001 画布先稳定侧栏挂点，本分区挂接后联调；画布本体不属 WF-002 |
| 测试：UT-01~18、IT-01~10、E2E-01~06 + Playwright `parity.spec.ts` 新增审批中心 / 画布侧栏 / 任务徽标字段断言（带出处注释，UI parity 五步纪律②，ADR-0010） | QA 1.0 | WF-002 | 第 10 周 D3-4 窗收口 | — |

> **排期对齐锚点**：上表「第 9 周 D1-2 窗（后端三项）」与 `sprint-overview.md` §8 第 9 周 D1-2 主线 A「`WF-001` 模型与执行引擎 + `WF-002` 审批引擎与端点（同窗联调）」一致——TASK-013 第 9 周 D5 工时审批依赖本文审批语义对齐（轻量单级、不复用 ApprovalFlow 引擎，其 §0 meta 口径），同窗交付不倒挂；「前端审批中心页 D1-2 起步、D3-4 联调收口」与概览 §8 第 10 周 D1-2 主线 A 一致；「前端画布审批分区侧栏 D3-4」与概览 §8 第 10 周 D3-4 槽位（WF-001 画布编辑器同窗联调）一致，画布编辑器本体仍归属 WF-001（不重复计入 WF-002 工作量）。
> **跨文档契约同步点**：WF-001 §4.4「审批发起与终审回填」与本节 §4.5「引擎契约对齐」为同一协议的两端文档化形态；WF-001 侧定义引擎守门与 `details[]` 形态，WF-002 侧定义 `act()` 边界条件表与 `_complete_via_engine()` 调用形态，任一侧变更须同步更新另一侧（CI 静态检查：`tests/jmeter/_contract.py` 错误码枚举 + 路径口径为唯一事实源）。