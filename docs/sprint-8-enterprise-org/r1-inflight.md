# Sprint-8 R0+R1 inflight（进行中登记）

> 开工：2026-09-09｜分支 sprint-8（自 main 72df6a7）｜上游：5 份规格评审冻结版
> 计划：R0 基线 → R1 AUTH-007 → R2 AUTH-008 → R3 AUTH-009 → R4 AUTH-010 → R5 BOARD-005 → R6 前端+收口（7 轮，用户已确认）

## R0 基线轮完成（b209d4d / caae826 / 4208327 / b8c14a9）

- 基线门禁全绿：run-ci-checks 56/56、s0 10/10、s1 108/0、契约 237/0、s2 169/0、
  s3 215/0、s4 213/0、s7-flow 35/35、e2e 全量复验（7 个失败全为环境问题根治）
- 四真问题修复：celery include 漏登 workflow 两模块（worker 五任务全 unregistered、
  notifications 积压 18 条全损）；TC-COVER-004 functions 24.13%<25%（补
  session-probe/permissions-revalidator 单测至 25.47%，突变自检过）；s2-flow
  T8-09 断言漂移（TASK-012 放开 P3 类型后未复跑的漏网）；sprint-3 概览 39 行
  P3→P4 残留
- 演示数据重灌（30 条）+ C.35 golden-path 5/5；S7 known-debt 补登记 #8
  （sprint-5-flow 32/8 历史在案）；dg §1.3 错位核验已回改 ✓
- dev 栈全量起法（踩坑实录）：live 需 LIVE_PORT=3000 API_INTERNAL_URL=
  http://localhost:8000（缺则 env_validation_failed、web 出 PresenceBar 黄条、
  e2e REG-3/6/7 strict violation 连锁红）；worker -Q activity,celery,workflow,
  notifications + AWS_*；s7-flow 必须 venv python + 全量 env

## R1 AUTH-007 部门组织架构轮（本轮）

### 交付
- 模型：Department（path 物化路径/PROTECT parent/双唯一约束含根名部分索引）+
  DepartmentGrantBatch（SET_NULL + department_id_snapshot 自含溯源）；
  WorkspaceMember 增 department 可空列（岗位复用 company_role）；迁移 0025
  （sqlmigrate 手工灌 + fake；顺带收编 ApprovalAuditEvent.created_at 属性漂移）
- 权限：department.manage 落地 PERMISSION_MATRIX（rbac §8.1 既有码非新增）；
  写端点 403 PERM_WORKSPACE_ADMIN_REQUIRED（本地 _department_manage 装饰器——
  require_permission 会收敛 PERM_ROLE_INSUFFICIENT 与 PM-02 期望不符）；读端点
  借 workspace.member.read 口径（GUEST 403），**仅守 safe method**（写方法交
  写门槛判定，防 guest 被读门槛改码）
- 服务层 plane/db/services/department.py：create/rename/reorder（间距 <1e-6
  整级等差重排）/move（环校验 + 整子树 path 单 UPDATE 重写）/delete（BR-04
  空部门 + 批次保留）/bulk_move（成员行 id 语义）/expand_grant（快照展开 +
  dry_run 预览 + ≤20 分批复用 PROJ-002 + BR-08/09/GUEST 逐人跳过 + 幂等重同步
  + 计数恒等式）/department_stats（直属/含子级/任务量 JOIN）
- 端点 11 个：routes/departments.py（list-create/detail patch+delete/move/
  bulk-move/grants preview+exec+batch/stats）+ members 域扩展（PATCH 白名单
  department_id/company_role + R2 层级保护 OWNER 豁免 + 列表 ?department=
  &with_descendants 过滤 + 序列化器带 department_id/company_role）
- audit：bgtasks/audit.py 占位（record_audit 结构化日志，R4 兑现真管道）+
  celery include 注册；调用点 .delay 形态照规格
- 测试：test_departments.py 35 项全绿（UT-01~20 + IT 恒等式/快照语义 +
  PM-01~04 四主体矩阵）；突变自检 2/2 被咬住（_depth_of 偏移 → 4 红；
  幂等检查移除 → 1 红）

### 实现偏差（对规格）
1. §4.3 伪码 `record_audit` 为真管道投递——R1 以结构化日志占位（AUTH-010 未
   交付），R4 替换函数体，调用点不动
2. PM-02 错误码：require_permission 装饰器（仓库既有）会把 403 收敛为
   PERM_ROLE_INSUFFICIENT，规格期望 PERM_WORKSPACE_ADMIN_REQUIRED——改用
   本地装饰器显式抛码（与 workspace_governance 惯例一致）
3. 删除端点合并进 DepartmentDetailView（PATCH/DELETE 同路径），无独立
   /delete/ 子路径

### 踩坑实录（R1）
- values() 返回 UUID：to_add/existing 键类型错配（UUID vs str）→ 幂等失效
  撞唯一键——统一 str 化
- AppException 的 str() 是 message 不含 code——测试断言用 .error_code 属性
- Service 层 name 未 strip（serializer trim 只救了 API 路径，直调服务绕过）
- Department 硬删后实例 pk 置 None——断言前留存 id
- expand_grant 第二段「已加过的人进 unchanged 桶」——目标集口径 =
  added ∪ unchanged（不是只 added）

## 并行会话实录（重要）

- 另一会话在同一 sprint-8 分支活跃开发，已提交 3 笔（86f4fdf 工作流四页 +
  02ece3f 画布守卫/字段权限矩阵 + e641cd6/04755f0 补口 spec）——内容为
  S7 known-debt A#6 补口（R6 计划的一部分已由对方完成）
- 对方 02ece3f 含 4 个 api 文件改动（issue/custom_fields/workflows 视图 +
  serializers/issue）；R1 收口全量 pytest 已含对方代码
- 曾发生对方半成品致 dev server 500（automation-rules.tsx 缺失）——已由对方
  自行补齐；后续轮开工前先 git status 核对对方状态

## R2 自定义角色轮完成（5099244）

- 42 码冻结目录（constants/custom_role_catalog.py：rbac §8.2 可自定义子集
  推导定稿——排除 *.manage/系统级/P4/integration.config，CATALOG_THRESHOLDS
  供 GUEST 天花板与目录端点）；CustomRole + ProjectRoleAssignment（0026）+
  批次表 target_type 判别列（0027）
- 判定层：require_permission 并集提升分支（threshold 先行 + custom_codes
  只加不减——零差异的结构保证）；effective_perms.py（模块级 Redis 单例
  + 主动失效 + TTL 300s + 回源降级；注意 permissions.py 是模块不能建同名包，
  故平铺 effective_perms.py）
- 9 端点 + BR-11（成员移出级联清挂接，挂 remove_member）+ BR-16（挂接拒绝
  + WS 降级级联卸除——GUEST 降级真入口是 member_admin.bulk_role_workspace
  而非 change_role）+ BR-17 名称解析（单对象/404/歧义 409）
- 测试 28 项全绿（零差异/跨项目隔离/即时生效/P99<1ms）+ 突变 2/2；
  全量 835 过零回归
- 待 R6：前端角色管理页/权限矩阵编辑器/我的权限面板 + users/me/permissions
  快照契约扩展（AUTH-005 待回改）+ usePermission 并集分支

## R3 入口（AUTH-009 SSO——风险最高轮）

原「R2 入口」段落如下（已由上方实录收编）：

- 规格：docs/sprint-8-enterprise-org/AUTH-008-custom-roles.md（687 行）
- CustomRole + project_role_assignment（42 码并集）+ effective_codes() 单一
  判定入口 + 缓存；零差异门禁（custom_codes=[] 行为与标准版完全一致）；
  判定 <1ms 性能门禁；GUEST 越界预检 BR-16
- 顺带评估：S6-A#1 公开项目 WS_ONLY 只读通道（判定层同域）
