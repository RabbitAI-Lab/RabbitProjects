# ADR-0015 · Sprint-2 TASK-008 自定义字段实现偏差登记

| 项 | 内容 |
| --- | --- |
| 状态 | 已接受（Accepted） |
| 日期 | 2026-09-05 |
| 背景 | TASK-008（基础自定义字段）落地过程中发现的任务规格/架构文档矛盾与实现口径取舍 |
| 关联 | `docs/sprint-2-task-full/TASK-008-custom-fields-basic.md`、`docs/architecture/dynamic-fields-design.md`、`docs/architecture/api-conventions.md` |
| 前置 | 4 处增强偏差已按用户裁决**反向统一到架构文档**（单 WS 50 字段上限 BR-10、field_type 创建后不可变 BR-06、P2_ALLOWED_TYPES 白名单、clean() 公式校验 P2 不启用——见 dynamic-fields-design.md §2.4 / §3.1 / §6.5 / §10 P2 清单），不在本文重复登记 |

## A-1 `details.field` 口径矛盾：`property.<id>` vs `cf_<key>`（api-conventions §8.4 ↔ TASK-008 §4.2.2）

**矛盾**：api-conventions §8.4 对 `VALIDATION_CUSTOM_FIELD_INVALID` 注明
「`details.field` 为 `property.<property_id>`」；TASK-008 §4.2.2 的冻结响应示例
却用字段键名（`"field": "cf_severity"`），且 §3.2 错误映射约定「`details[].field`
= key → 控件 error slot」以前端按 key 定位为设计依据。

**实现取舍**：按 TASK-008 冻结示例实现（`details.field = cf_<field_key>`）——
TASK-011 / 动态表单两方依赖的是该示例，且值校验发生在写入路径（此时只有 key，
没有筛选参数形态的 property id）。

**后续**：TASK-011 交付 FilterCompiler 时统一回改 api-conventions §8.4 的注记
（值路径用 key、筛选路径用 `property.<id>`），或将其标注为「筛选 DSL 专用」。

## A-2 IT-04「仅 property.x=null → 400」按结构保证口径实现为安全放行

**矛盾**：TASK-008 §5.2 IT-04 预期「仅 `property.x=null` 无其他条件 → 400（强制
组合其他条件）」，但 §4.3.6 自己的口径是「调用方保证——FilterSet 先应用
project_id 等」。`?property.` 只存在于项目内列表端点（`project_id` 过滤结构性
存在，`idx_issue_active_by_project` 先行缩小到单项目），不存在「跨项目裸 null
筛选」的可达路径。

**实现取舍**：项目内 `?property.<id>=null` 直接编译为 `NOT (custom_fields ? key)`
并正常返回（flow T8-24 锚定行集正确性）；不制造 400。IT-04 的 400 语义留给
P3 全局跨项目筛选端点（届时该端点不存在结构性 project 条件）。

## A-3 `issue.field.manage` 权限点未在 PERMISSION_MATRIX 登记（BR-15 简化）

BR-15 引用 AUTH-005 的 `issue.field.manage` 权限码（默认 PROJ_ADMIN、可授权
CONTRIBUTOR），但权限矩阵 P1 子集（`plane/constants/permissions.py`）未登记该
权限点，且前端镜像（apps/web，并行任务禁改）无法同步。实现按**默认角色判定**：
项目端点 `PROJ_ADMIN+`（含隐式 WS_ADMIN+ 提升），全局字段创建/修改额外要求
WS Admin（`WorkspaceRole.ADMIN`）。细粒度授权（授权 CONTRIBUTOR 建字段）在
AUTH-009（自定义角色）迭代登记权限点后开启——端点形状不变，只替换门槛判定。

## A-4 规格排序 SQL 未限定表名 → 列表查询 AmbiguousColumn（A 类：规格 SQL 缺陷）

TASK-008 §4.3.5 / 架构文档 §4.2 的排序表达式写作 `custom_fields->>%s`（裸列名）。
列表端点的 queryset 带 `issue_count_annotations()` 计数 annotate（含 issues
自连接），裸 `custom_fields` 直接 `column reference is ambiguous` 500。实现将
**排序表达式统一限定 `issues.custom_fields`**（`plane/db/services/custom_fields.py
ORDER_EXPRESSIONS`）；`CREATE INDEX` DDL 单表上下文不需要限定、但表达式需自带
成对括号，故索引 DDL 表达式独立成表（`plane/bgtasks/field_index.py
INDEX_EXPRESSIONS`）。锚定：flow T8-26/27/28（排序三断言）。

## A-5 `plane/celery.py` 任务注册修复（工程前置）

`app.autodiscover_tasks()` 按 Django app 找 `<app>.tasks`，而本项目任务都在
`plane.bgtasks.*`（非 app 内 `tasks.py`）——web 进程在调用点 import 后注册、
worker 进程从未 import，任务全部 `Received unregistered task`（T8 首次真实消费
worker 时暴露；队列里 TASK-004~007 期间的积压任务同样报此错）。修复：celery.py
显式 `include=[...bgtasks 全部模块]`，**新增任务模块必须同步登记**（注释已注明）。

## A-6 `/api/v1/tasks/{task_id}/` 状态端点未交付（202 响应仅回传 URL 字符串）

DELETE 字段返回 202 `{task_id, state, status_url}` 契约按 TASK-008 §4.2.3 冻结
实现；`status_url` 指向的系统任务进度端点属 INFRA-004 §13.1 交付物，本任务未
实现——URL 目前不可访问（不 404 于路由层即视为字符串）。broker 不可达时 DELETE
降级为同步清理（state=completed），不阻断删除语义。

## A-7 UT-13 并发取号简化为串行 10 次

advisory lock 取号的**并发**验证（10 任务并发编号 1~10 无重）需要多进程/多连接
夹具，与 pytest「TestCase 事务回滚、复用 dev 库」的约束冲突。按迭代口径简化为
**串行 10 次取号不重**（`tests/test_custom_fields.py::test_ut13_auto_increment_serial_ten_unique`）；
无重号的并发保证由 `pg_advisory_xact_lock(int32(project), crc32(key))` 的锁语义
+ 与 `create_issue_svc` 同事务的结构保证（架构文档 §4.4），留压测门禁（§8 性能
验收）做真实并发复核。

## A-8 12 类型不含 email/phone（flow 锚点勘误）

TASK-008 交付的 12 类型集合（架构 §2.3 表）不含 `email` / `phone` 类型（格式
校验需求由 `url` 类型的 `INVALID_URL` 与通用 `INVALID` 子码覆盖）；flow 骨架的
计划锚点注释误写「email/phone」。实现按 12 类型执行，flow 注释已随 T8 段落地
一并修正。

## A-9 `prune_views_referencing_field` 为预置空实现

任务体已注册（worker 可消费、幂等返回 0），但 `IssueView` 模型归 TASK-011——
视图引用剔除逻辑在视图上线后激活（TASK-008 §4.3.3 自身即如此约定，此处仅作
登记备查）。
