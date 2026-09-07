# ADR-0024 · 全局标签独立建表（WorkspaceLabel）

| 项 | 内容 |
| --- | --- |
| 状态 | 已接受（Accepted） |
| 日期 | 2026-09-07 |
| 背景 | unified-issue-model §2.7 P3 计划经 Label.workspace 可空外键支持组织统一下发；Sprint-5 提前至 P2。 |
| 决策 | 独立建 `workspace_labels` 表而非复用 Label——避免双外键可空导致 (workspace, project) 互斥校验复杂化与 Label.origin 污染基线模型；覆盖链路以 `Label.overrides_global_id`（UUID 列）承载。 |
| 关联 | `docs/sprint-5-integration-standard/TEAM-003-team-archive-config.md` §6.4（占位编号 ADR-0003，按仓库序列顺延落位） |

## 后续动作

- unified-issue-model §2.7 内联标注「P2 实现已偏离：独立 WorkspaceLabel 表（TEAM-003 + ADR-0024）」——已随本 ADR 同步回改。
- P3 组织级标签下发收敛方案待架构组重新评估。
