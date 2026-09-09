# Sprint-9 R0 基线（2026-09-09）

> 分支 `sprint-9`（自 main 78487fb 切出）｜codegraph sync 已完成（索引最新）
> 本轮目标：基线冒烟全绿 + 前置依赖核对 + 债务认领登记，为 R1 高保真原型铺路。

## 1. 基线门禁数据

| 门禁 | 结果 | 说明 |
| --- | --- | --- |
| pytest（dev PG） | **896 全绿**（86s） | 修复 1 项作用域断言后全绿（见 §2.1） |
| ruff / mypy | 全绿 | 与 api-ci 平价命令一致 |
| tsc / oxlint | 全绿 | pre-push 与 CI 口径 |
| e2e（Playwright） | **169 过 + 2 跳，0 挂**（5.4m） | web 3001 + API 8000 + live 3000 全栈在跑 |
| run-ci-checks.sh | 55/56 过 | 唯一红 = TC-COVER-004（已知债，见 §4） |
| 容器/服务 | pg/redis/mq/minio/keycloak 五容器 Up；celery `-Q activity,celery,workflow,notifications,audit` | S8 终态口径不变 |

## 2. 基线修复（2 笔，均为门禁脚本/断言与现状脱节）

### 2.1 `test_departments.py::test_grant_preview_zero_write` 全表 count 误红

- 现象：全量与单跑均挂（`DepartmentGrantBatch.objects.count()` 期望 0 实得 2）
- 根因：**CLAUDE.md 坑 18 同款**——dev PG 残留 2 行批次（张三演示空间，2026-09-09 15:00 前后 S8 演示/验收操作遗留，非软删）；断言用全表 count 撞残留
- 修复：断言改作用域过滤 `filter(workspace=env["ws"])`（env fixture 每次独立空间）+ 物理清残留 2 行
- 验证：departments 35/35 过 → 全量 896 全绿

### 2.2 `TC-INF4-006 错误码注册表规模` 锚定滞后

- 根因：S8 R3 SSO 域加 2 码（75→77），pytest 侧锚定（`test_smoke.py`）已同步 77，`run-ci-checks.sh` 静态检查漏改仍锚 75
- 修复：脚本锚定 75→77，与 `test_smoke.py` 一致；TC-INF4-007 双向差集仍绿（文案覆盖无缺口）

## 3. 前置依赖核对（sprint-overview §3「进入前必须满足」逐项）

| 依赖 | 规格要求 | 核对结论 |
| --- | --- | --- |
| TASK-010 状态事件管道 | IssueActivity 状态变更事件完整 | ✅ `IssueActivity` 模型 + `state_changed` 投递在库（views/issues.py / tasks） |
| TASK-005 依赖图无环 | `_reaches`/`assert_completable` 稳定 | ✅ 在库（guards.py 等引用） |
| FILE-004 分享链接/权限 | Wiki 权限委托前提 | ✅ `file_share.py` 在库 |
| AUTH-008 自定义角色 | Wiki/项目集细粒度权限前提 | ✅ S8 交付（42 码目录 + 并集提升） |
| COLLAB-004 WebSocket | 协同编辑实时前提 | ✅ live 3000 在跑，e2e realtime spec 绿 |
| WF-003 自动化规则引擎 | 预警自动化转发前提 | ✅ `automation_tasks.py` 在库（事件接线为 S7 A#3 债，R3 消费） |
| Cycle 迭代模型（P0 预留列） | overview §3：「`Issue.cycle_id` 为架构 §7.4 P0 预留列、零 issues 表 DDL」 | ⚠️ **偏差**：架构文档 `unified-issue-model.md:237` 确有预留声明，但**模型代码与 dev PG issues 表均无 cycle 列**（从未物理落地）。RPT-003「零 issues 表 DDL」不成立——实现需一次加列迁移（FK→Cycle，SET_NULL），随 R2 落地时登记 ADR |

## 4. 债务认领表（S9 轮次映射）

| 债 | 内容 | 消费轮 |
| --- | --- | --- |
| S7 A#1 + S8 A#1 | WF-006/审计导出异步两段式（task_id/status_url + MinIO） | R4（RPT-004 导出统一接入） |
| S7 A#3 | automation_match 事件总线接线（state_changed on_commit 投递） | R3（GANTT-003 预警=首个生产场景） |
| S7 A#4 | WorkflowTemplate 图快照 jsonschema 保存校验 | R3（顺手件） |
| S8 A#2 | SAML 真容器断言联调（Keycloak SAML IdP） | R6 验收轮 |
| S8 A#5 | users/me/permissions 码集并集下发 + usePermission 并集分支 | R4（前端轮统一） |
| S8 A#6 | 认领页 next 透传细节 | R6 验收视频轮校准 |
| S8 A#8 | SSO e2e Keycloak fixture 化 | R6 视容量（不阻塞 V1.0） |
| **TC-COVER-004** | 前端 functions 覆盖 21.96% < 25% 阈值（S7/S8 新增 stores/index、permission、realtime 两文件 0% 稀释；S6 A#4 已登记未消费） | R4/R5 前端轮补单测拉回 |
| 顺延（不动） | S8 A#4 泳道拖拽（触发时先补原型）、A#7 org 拖拽、A#9 S5~7 存量原型债（纪律条款：触达先补原型） | — |

## 5. R1 入口

高保真原型 `docs/design/sprint-9-hifi-prototype.html`（DRAFT→评审→FROZEN，硬门槛），17 表面：
项目集 4（汇总面板/里程碑/依赖图/外部依赖块）+ 报表 4（迭代管理/燃尽/速率/累积流）+ 健康度 3（评分卡/下钻/负载热力）+ Wiki 3（主界面/版本/检索）+ 甘特 3（关键链高亮/浮动时间卡/预警配置）。延续 0~4/8 冻结 token/外壳/dock 风格。
