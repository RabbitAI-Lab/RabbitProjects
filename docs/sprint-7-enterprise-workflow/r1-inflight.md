# Sprint-7 R1 引擎轮 inflight（进行中登记）

> 开工：2026-09-08（R0 收口后）｜分支 sprint-7｜上游：WF-001/WF-002 规格（评审 PASS 冻结版）

## R0 已完成（main 545ff69）

- 分支 sprint-7 from main(449dc47) + codegraph sync；runserver 带全量 env 重启（LIVE_JWT_PRIVATE_KEY + AWS_S3_* rpminio/rpminio123@localhost:9000）；celery worker 同款 env 常驻（-Q activity,celery）
- 基线：run-ci-checks 56/56、s0-flow 10/10、s1-flow 108/0、api-full-coverage 237/0、s2-flow 168/0、s3-flow 全过、s4-flow 213/0、e2e 162/0/1skip
- 基线修复（98c43bf）：8 处 INSERT 补 github_context、s3/s4-flow 清理序 FK 全表对齐（project_status_logs/workspace_login_daily[member_id!]/workspace_labels/project_templates/webhook_*/integration_*/backup_runs[created_by_id]/release_gates[created_by_id]/release_gate_events[**actor_id**!]）、AC-3 四态、s3-flow 补开局幂等清理
- 文档回改（45e9fd6）：known-debt #12 三件（api-conventions §13.3 / sprint-5 概览 §9#2#3+§3 权限码+§1 挂点边 / INTG-002 §1.2 收口）
- dg 口径同步（cf49ace）：sprint-7 概览 §3 图对齐 dg 2026-09-06 回改后口径（WF-001←TASK-005/TASK-010；WF-006 三直边；TASK-012←TASK-011）；dg 的 WF/AUTH/TASK 错位已由架构批次完成，R0 仅核验+反向收口概览
- **遗留登记**：sprint-5-flow 32/8 历史在案（Sprint-5 收口 39/47 同源；W6 段 mock 只 patch 同步 _post、重试经 apply_async 投 worker 无 mock+真退避 → 终态不可达；A1-01/P3-03/P3-04/I5-06/I5-09 待查）——R5 收口时入 sprint-7 known-debt，不当新回归

## R1 规格精读结论（关键决策速查）

### 模型（WF-001 §4.2 逐字实现）
- Workflow：project FK + issue_type FK(null=项目默认) + status(draft/published/archived) + version + based_on_version + published_at/by + source_template(SET_NULL, WF-005 预留)；两个部分唯一约束（published/draft per project+issue_type——仅类型专属生效，NULL 行靠 Service 行锁）
- WorkflowState：workflow FK + state FK(**PROTECT** BR-15) + is_initial + layout_x/y + field_locks JSONB(list)；uniq_state_per_workflow
- WorkflowTransition：workflow FK + from/to_state(WorkflowState FK) + name + guards/side_effects JSONB(list) + approval_flow FK(SET_NULL 预留) + sort_order；uniq(workflow,from,to,name)+CHECK 禁自环
- 审批四模型（WF-002 §4.2/4.3）：ApprovalFlow(project+name uniq+forbid_self_approve) / ApprovalNode(flow+level 1..10 uniq+pass_mode any|all+approver_type users|role|field+approver_config JSONB+timeout_hours) / ApprovalInstance(issue **PROTECT**+transition PROTECT+flow_snapshot JSONB+initiator+current_level+status+**is_terminal_passed**+terminal_reason+from_state+completed_at；部分唯一 uniq_pending_instance_per_edge) / ApprovalRecord(BigAuto+instance+level+approver PROTECT+action+comment+acted_at；无 updated_at；uniq(instance,level,approver))
- 迁移：PG 走 sqlmigrate 手工灌 + --fake（CLAUDE.md 坑 1）；索引 CONCURRENTLY 不适合事务迁移——按仓库既有 migration 惯例（先看既有 00XX 迁移怎么写的）

### 引擎（WF-001 §4.4-4.6）
- WorkflowService.transition(*, issue_id, to_state_id, actor, transition_id=None, guard_payload=None, approval_instance_id=None)：@atomic；行锁 select_for_update；resolve_workflow 缓存（wf:resolved:{project}:{type} 哨兵 NONE + wf:graph:{id}；TTL 3600 + 信号失效）
- 无工作流 → V1.0 兜底：assert_completable + 自由流转；有 → _match_edge（无→409 RESOURCE_TRANSITION_INVALID；多边未指定→400）→ guards（R2 接 WF-004，R1 先跑隐式 blocker_completed=assert_completable）→ 审批挂接（发起 202 / 终审回填守门「is_terminal_passed=True AND status=pending」or APPROVED）→ 副作用（R3 接 WF-003，R1 空注册表）→ 状态更新 + Activity
- **Activity 投递用 enqueue_activity()（bgtasks/issue_activity.py:67 统一入口，带降级）不是裸 delay**；verb=updated、before/after 带 {state, transition}（BR-14 field="transition" 单字段特例）
- assert_completable 现行签名 = (*, issue, to_state, force, is_admin)（db/services/issue_transition_guard.py:30）——**规格引用的「actor= 冻结签名」与实现不符**：R1 以实现为准 force=False, is_admin=False 调用（行为等价），登记 ADR（R1 提交内）
- resolve_initial_state(project, issue_type)：类型专属 is_default → 项目级 is_default 两级回落（BR-16）；IssueService.create 接线（R1 顺带）
- validate_for_publish：五项（NON_SINGLE_INITIAL/UNREACHABLE(BFS)/NO_COMPLETED/MISSING_REF/INITIAL_MISMATCH）+ STATE_IN_USE(409 优先)；发布=两行模型（旧 published 翻 archived、草稿翻 published version+1；响应带 archived_previous_id/version）
- PUT graph/：整图替换+单事务+updated_at bump（ETag 自洽）+If-Match；draft-only；PUT 白名单待补登 api-conventions §3.2（随 R1 PR）

### 审批（WF-002 §4.5）
- ApprovalService.start(transition, *, issue, actor)：flow.is_active 检查(409 DISABLED)→快照→立案(含 from_state=issue.state)→_open_level(1)（三源审批人解析+成员过滤+空集 400 EMPTY_APPROVERS+禁自审 skipped(self)+全员跳→转交 PROJ_ADMIN）→ on_commit notify+activity
- act(*, instance_id, actor, action, comment)：行锁；L1-L9 边界表（见 WF-002 §4.5）；reject comment 必填；会签任一 reject=整体驳回；_level_passed 按 pass_mode（all=全员 approve、any=任一 approve）；终级→_complete_via_engine
- _complete_via_engine：先置 is_terminal_passed=True(pending) → WorkflowService.transition(approval_instance_id=…，to_state_id=**str(instance.transition.to_state.state_id)** 经 WorkflowState 换算 State 主键) → 成功 finalize APPROVED / ApiError → 复位 False + finalize TERMINATED(guard_failed_at_complete)
- 终止触发五挂点：state_changed/issue_deleted/issue_archived（信号钩子 apps.py.ready）/admin/guard_failed_at_complete
- 端点：approval-flows CRUD（workflow.manage；PATCH nodes 整体替换；DELETE→SET_NULL 摘挂返回 affected_transition_ids）+ approvals/pending|acted|mine（WS 级三 Tab，per_page≤100，pending 支持 ?count_only=1）+ approval-instances/{aid}/ 详情+actions/ + issues/{id}/approvals/
- Celery：notify_approvers(queue=notifications)/approval_timeout_scan(queue=**workflow 新队列**——tech-stack §9 待补登) beat 15min
- 错误码：RESOURCE_TRANSITION_INVALID/PERM_TRANSITION_NOT_ALLOWED/PERM_APPROVAL_NOT_ASSIGNEE 已在 error_codes.py；子码 EMPTY_APPROVERS/DISABLED/INVALID_STATE/HAS_ACTIONS/LIMIT 待补登 §8.8

### R1 实现顺序
1. models：plane/db/models/workflow.py（三表）+ approval.py（四表）+ __init__ 导出
2. migration（sqlmigrate 手工灌 + --fake；参照既有惯例）
3. services：plane/workflow/services.py（WorkflowService+publish 校验+缓存信号）+ plane/workflow/approval.py（ApprovalService）——注意 WF-002 §4.5 导入路径写的是 plane.workflow.services（领域服务层）
4. 注册表骨架：GuardRegistry/SideEffectRegistry（R1 空实现+blocker_completed 隐式守卫）
5. 权限码注册（workflow.manage/approval.act/approval.withdraw——rbac 权限点表位置待查：TC-AUTH5-006 权限点↔中文标签集合）
6. views+serializers+urls：workflows.py（7 端点）+ approvals.py（8 端点）+ issues transitions（available/+POST——挂 issues.py 或独立）
7. bgtasks：notify_approvers/approval_timeout_scan + workflow 队列声明 + beat
8. pytest：test_workflow_engine.py（状态机事务/终审回填三态/回滚/零行为变化/发布校验/BR 全覆盖）
9. 门禁：run-ci-checks + 既有 flow 复跑（零行为变化）+ 新单测突变自检

### 已知坑速记
- release_gate_events.user FK 列 = actor_id（不是 created_by_id）；workspace_login_daily = member_id
- commitlint：中文 subject 开头、header≤100、无 merge type（用 chore）
- 迁移后要重启 worker（坑 20 多代 worker）；新表入 s3/s4-flow 清理序（本批新表：workflows/workflow_states/workflow_transitions/approval_flows/approval_nodes/approval_instances/approval_records）
