# 资源负载甘特

| 元信息项 | 内容 |
| --- | --- |
| 文档编号 | GANTT-005 |
| 所属迭代 | P4（v2 纳入 2026-09-10） |
| 所属模块 | M6-GANTT / M4-TASK |
| 文档状态 | 已实现稿（R15，自含精简） |
| 前置依赖 | `TASK-013`（工时）、`RPT-004`（健康度负载口径） |
| 最后更新日期 | 2026-09-12 |

> **范围**：按成员×周的负载热力视图——周投入工时（WorkLogSummary）对周容量
> （ProjectWorklogConfig.weekly_capacity_minutes）的负载率分档（<70% 绿 /
> 70-100% 黄 / >100% 红）；跨项目汇总成员维度。

## 2. BR

| 编号 | 规则 |
| --- | --- |
| BR-01 | 负载率 = Σ周投入 / 周容量；容量缺省 2400min（5d×8h） |
| BR-02 | 分档三色（绿/黄/红），超载行标注超额分钟 |
| BR-03 | 权限 = 空间级 team.stats.read 口径（WS_MEMBER+） |

## 4. 端点

GET `/api/v1/workspaces/{slug}/resource-load/?weeks=8` →
`{rows: [{member_id, name, weeks: [{week_start, minutes, ratio, band}]}]}`

## 5. 用例
| UT-01 | 负载率与分档正确（缺省容量 2400） |
| UT-02 | 多项目工时跨项目汇总到成员维度 |
