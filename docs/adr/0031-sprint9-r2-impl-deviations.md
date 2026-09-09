# ADR-0031：Sprint-9 R2 实现偏差登记（PROJ-004 / RPT-003）

日期：2026-09-09　|　状态：已实施　|　关联提交：R2a（PROJ-004）/ R2b（RPT-003）

## A. 规格与实现的字段/结构偏差

### A-1 `Issue.cycle_id`「P0 预留列」物理未落地（RPT-003 §4.2 / sprint-overview §3）

- **规格口径**：「该列 P0 建 Issue 表时已随 migration 预留，本 sprint 零 issues 表 DDL」。
- **核对事实（R0 基线）**：`unified-issue-model.md:237` 确有 `cycle_id FK "预留, nullable"`
  声明，但模型代码（`issue.py`）与 dev PG `issues` 表均无该列——预留仅停留在架构
  文档层面，从未物理落地。
- **处置**：0033 迁移补列（`Cycle FK, SET_NULL, null=True`）。规格「零 issues 表
  DDL」说法不成立；架构文档声明与实现自此对齐。
- **回改**：RPT-003 §4.2 注释块与 sprint-overview §3 相应措辞随文档回改轮修正。

### A-2 规格 diff 假设 `Issue.workspace_id` 存在（PROJ-004 §4.4）

- **规格口径**：跨项目放开校验 `issue.workspace_id != related.workspace_id`。
- **实现事实**：Issue 无 workspace 冗余列（workspace 经 project 隐含，
  unified-issue-model §4），该属性不存在。
- **处置**：比较走两侧 `issue.project.workspace_id`（语义等价，多一跳但
  `select_related("project")` 已在位）。

## B. 权限矩阵补齐（非偏差，登记性质）

RPT-003 §4.5 声称 `cycle.manage / report.read / report.export /
project.setting.manage` 为「rbac §8.2 既有码（零新增）」——rbac 文档确已登记
（§8.2 1096/1107/1108/1054 行），但 `plane/constants/permissions.py` 实现矩阵
漏落。本轮补齐四码（阈值按 rbac ⚠️ 可配置列的默认列取值，同 file.share 模式），
与文档口径一致，非行为变更。

## C. lint_access AC-06 规则修正

原规则匹配视图层任意 `.status` 直改，误伤 RPT-003 Cycle 自有状态机
（planned→active→completed 由 start/complete 端点驱动，属规格定义行为，
非 PROJ-003 Project 生命周期域）。收窄为仅匹配 `project.status`；扫描器
selftest 反例仍全命中（守卫力不降）。
