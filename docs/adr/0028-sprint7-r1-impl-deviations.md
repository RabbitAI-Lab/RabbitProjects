# ADR-0028：Sprint-7 R1 实现偏差登记（工作流引擎与审批）

> 登记日期：2026-09-08　|　迭代：Sprint 7 R1（WF-001 引擎 + WF-002 审批后端）　|　状态：已按裁决实现

## 背景与偏差清单

R1 按 WF-001/WF-002 评审冻结版实现引擎与审批后端过程中，与规格文本存在以下实现偏差，逐项登记（sprint-overview §10.3 纪律：架构偏离先登记再收口）。

### 1. `assert_completable` 签名引用偏差（BLOCKER 级文档偏差，实现零影响）

- **规格口径**：WF-001 §4.4 两处引用「TASK-005 §4.3.3 冻结签名 `assert_completable(*, issue, to_state, force=False, actor=actor)`（keyword-only）」，并声称 `actor` 必填用于审计溯源（rbac §5.4）。
- **实现事实**：TASK-005 现行代码（`plane/db/services/issue_transition_guard.py:30`）签名为 `assert_completable(*, issue, to_state, force, is_admin)`——第 4 参为 `is_admin: bool`，无 `actor`。规格所称「冻结签名」在 TASK-005 文档与代码中均不存在（评审漏网：两文档互相引用了一个未冻结的签名形态）。
- **裁决**：以实现为准。引擎调用 `assert_completable(issue=…, to_state=…, force=False, is_admin=False)`——兜底路径 `force=False` 时 `is_admin` 不参与任何分支判定（仅 force=True 的管理员豁免分支消费），行为与规格期望逐字等价（V1.0 兜底禁用豁免）。审计溯源由 Activity 管道的 `actor_id` 承载（引擎第 6 步），不依赖守卫函数签名。
- **后续**：WF-001 §4.4 两处签名引用待回改为 `(*, issue, to_state, force, is_admin)`（随 R2 PR 顺带，不在本批展开）。

### 2. 守卫失败异常统一口径（WF-002 §4.5 注的对齐）

- **规格口径**：「守卫失败经 WF-004 现行 `guard_error(list[GuardFailure]) → ApiError` 抛出……两类统一按 `ApiError` 捕获」——该 `ApiError` 基类在本仓库不存在（现有异常体系为裸 Exception 子类 + 视图层转信封，见 `issue_link.py` 先例）。
- **裁决**：`_complete_via_engine` 捕获集 = `(TransitionError, ApprovalError, TransitionBlockedError)`——R1 阶段守卫失败的唯一真实形态是隐式 blocker 守卫（TASK-005 `TransitionBlockedError`）；WF-004（R2）注册四类执行器后新增的失败形态统一抛 `TransitionError`（本文件定义，视图层转信封），天然在捕获集内。语义与规格「两类统一捕获→TERMINATED(guard_failed_at_complete)」一致。

### 3. `guard_error` / `ApiError` / `TransitionResult.pending_approval` 类型采用 dataclass + Any

规格代码块中的 `TransitionResult.pending_approval` 标注为 ApprovalInstance；实现为 `Any | None`（防 `plane.workflow.services ↔ approval` 循环导入，注释指明 WF-002 域）。行为无差。

### 4. 端点信封细节（视图层裁决）

- **304 变体**：`GET …/workflows/{id}/` 的 If-None-Match 命中返回裸 304（无 body）——`success_response` 会装 `{status, data}` body，与 304 语义冲突，改用 `Response(status=304, headers={ETag})`。
- **202 变体载荷**：`pending_approval` 摘要按 WF-001 §4.8② 最小形态（instance_id/status/current_level/flow_name）返回；WF-002 §4.6 全量展开形态（records 数组等）由实例详情端点承载——两文档以「WF-002 §4.6 为审批详情权威、WF-001 为摘要」的裁决在摘要侧落地。

### 5. PATCH `issues/{id}/` 的 `state_id` 直改不拦截（决策留待 R2）

- **现状**：R1 未对既有 PATCH `issues/` 改 `state_id` 做工作流拦截（规格未定义该路径的处置；V1.0 兼容约束「无工作流时行为不变」优先）。
- **风险**：受控流转可被 PATCH 绕过（前端改造后看板走 transitions/，但 API 层旁路存在）。
- **决策**：R2（WF-004 守卫矩阵轮）统一裁决——若拦截则 `resolve_workflow` 非 None 时 PATCH state_id 返回 409 引导走 transitions/；裁决后回改本 ADR 或新增条目。

### 6. `sprint-5-flow` 32/8 基线状态（非 R1 引入，登记在案）

Sprint-5-flow 8 项失败为历史在案（Sprint-5 收口 39/47 同源；W6 段 mock 只 patch 同步 `_post`、重试经 `apply_async` 投 worker 后无 mock + 真退避，终态不可达；A1-01/P3-03/P3-04/I5-06/I5-09 待查）。R0 基线如实记录，R5 收口入 sprint-7 known-debt。

## 已兑现的架构登记项（随本 ADR 同批完成）

- `workflow` Celery 新队列：`plane/celery.py` task_queues/task_routes 已声明 + beat `approval-timeout-scan`（15min）——tech-stack §9 队列表待补登（随本 PR）。
- 权限点四枚注册：`workflow.manage` / `automation.manage`（PROJ_ADMIN）+ `approval.act`（COMMENTER 门槛，真正判定=当前级票据）/ `approval.withdraw`（CONTRIBUTOR 门槛，判定=仅发起人）——`constants/permissions.py` + 中文标签（TC-AUTH5-006 集合相等守卫覆盖）。
- 错误码零新增：`RESOURCE_TRANSITION_INVALID` / `PERM_TRANSITION_NOT_ALLOWED` / `PERM_APPROVAL_NOT_ASSIGNEE` / `RESOURCE_STATE_INVALID` / `RESOURCE_ALREADY_EXISTS` 均为 P3 预留已注册；字段级子码（EMPTY_APPROVERS / DISABLED / INVALID_STATE / HAS_ACTIONS / LIMIT）走 `details[].code`，api-conventions §8.8 待补登（WF-002 §2.6 已注）。
