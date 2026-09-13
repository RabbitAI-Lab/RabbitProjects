# 跨项目甘特汇总

| 元信息项 | 内容 |
| --- | --- |
| 文档编号 | GANTT-004 |
| 所属迭代 | P4（v2 纳入 2026-09-10） |
| 所属模块 | M6-GANTT |
| 文档状态 | 已实现稿（R15，自含精简） |
| 前置依赖 | `GANTT-001`（单项目甘特取数地基）、`PROJ-004`（项目集组合树） |
| 最后更新日期 | 2026-09-12 |

> **范围**：项目集级甘特——以项目为行泳道汇总其下任务计划区间（最早开始~最晚截止），
> 里程碑菱形叠加；不做跨项目任务级连线（归 GANTT-006 锁定视图）。

## 2. BR

| 编号 | 规则 |
| --- | --- |
| BR-01 | 项目行区间 = 该项目未删任务 min(start)/max(target)；无日期任务不入区间 |
| BR-02 | 里程碑按 PortfolioMilestone 日期渲染，晚于项目截止即超期标记 |
| BR-03 | 读权限 = 项目集成员（portfolio.read 既有码口径） |

## 4. 端点

GET `/api/v1/workspaces/{slug}/portfolios/{id}/gantt/` →
`{projects: [{project_id, name, range: {start, target}, open_count}], milestones: [...]}`

## 5. 用例
| UT-01 | 项目行区间聚合正确（min/max 语义、无日期跳过） |
| UT-02 | 里程碑超期标记 |
