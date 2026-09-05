# ADR-0014 · Sprint-2 实现偏差登记（A 类：规格自带缺陷）

| 项 | 内容 |
| --- | --- |
| 状态 | 已接受（Accepted） |
| 日期 | 2026-09-05 |
| 背景 | Sprint-2 实现过程中由真实联调（sprint-2-flow.py）发现规格自带 SQL 缺陷 |
| 关联 | TASK-006 §4.3.2、tests/test_worklog.py::test_subtree_summary_no_join_amplification |

## A-1 `SUBTREE_WORKLOG_SQL` 的 LEFT JOIN 放大（TASK-006 §4.3.2）

**缺陷**：规格给出的上卷 SQL 以 `FROM target t LEFT JOIN work_logs w` 后同时
`SUM(w.minutes)` 与 `SUM(t.estimate_minutes)`——一个节点有 N 笔工时时，
`t.estimate_minutes` 被 JOIN 放大 N 倍（实测：单节点 estimate=480 × 4 笔工时 =
1920）。TASK-006 §4.3.1 自己在列表 annotate 处锚定了「JOIN 放大」风险
（UT-09），但 §4.3.2 的子树上卷 SQL 未按同一口径设计。

**实现修正**：改为两个独立标量子查询——`spent` 由 `work_logs WHERE issue_id IN
(target)` 聚合，`estimated` 由 `target` 自身聚合，两数互不串扰。

**锚定**：`tests/test_worklog.py::test_subtree_summary_no_join_amplification`
（3 笔工时 + 父子两节点 estimate 480/120 → 600 而非 ×4）；flow T6-15
（subtree stats spent=300 / estimate=480 无放大）。

**规格回改**：TASK-006 §4.3.2 的 SQL 已同步替换为修正版（本文提交一并完成）。

## A-2 pytest 测试库直连 dev PG（工程基建偏差）

Django 5.1 + 自定义 User 在 PG 上**建测试库**有已知问题（CLAUDE.md 坑 #1 的
migrate 阶段），故 `settings/test.py` 把 `TEST.NAME` 指向 dev 库本体并覆盖
`django_db_setup` 为 no-op，由 TestCase 事务回滚保证零残留；**禁止**
TransactionTestCase（flush 会清 dev 库）。真提交语义用例一律走
`tests/jmeter/sprint-2-flow.py`（HTTP 侧自建自清）。

另：dev 库 `django_content_type` 残留 Django 5 已移除的 `name NOT NULL` 列
（历史手工灌 DDL 后遗症），已 `DROP COLUMN` 修复，`manage.py migrate` 全量
恢复干净（`No migrations to apply`）。
