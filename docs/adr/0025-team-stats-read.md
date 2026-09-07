# ADR-0025 · 新增权限 Key team.stats.read

| 项 | 内容 |
| --- | --- |
| 状态 | 已接受（Accepted） |
| 日期 | 2026-09-07 |
| 背景 | 团队成员活跃度聚合属工作空间治理面，语义 ≠ 项目报表（report.read）。 |
| 决策 | 工作空间级读权限 Key `team.stats.read`，门槛 WS_ADMIN+；不复用 report.read（「按报表语义授权、按治理面语义消费」错位）。 |
| 关联 | `docs/sprint-5-integration-standard/TEAM-003-team-archive-config.md` §6.4（占位编号 ADR-0004，按仓库序列顺延落位） |

## 后续动作

- rbac §8.1 Report 分区新增行 + 前端 WORKSPACE_PERMISSION_MATRIX 同步补条目（前端矩阵随 T5-09 落地）+ CI 一致性测试断言——rbac 行已随本 ADR 同步回改。
