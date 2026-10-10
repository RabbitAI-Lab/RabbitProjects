# 跨项目资源统一调度

| 文档编号 | PROJ-005 | 所属迭代 | P4（v2 纳入） | 模块 | M3-PROJ |
| --- | --- | --- | --- | --- | --- |
| 文档状态 | 已实现稿（R17，自含精简） | 前置 | GANTT-005 资源负载 | 日期 | 2026-09-12 |

> **范围**：跨项目成员负载查询 + 调度建议（超载/闲置成员清单 + 可平衡
> 指派建议）——只读建议层，执行归各项目指派端点。

## BR
- BR-01 负载口径同 GANTT-005（周投入/容量）；建议只读零写路径
- BR-02 权限 team.stats.read（WS_MEMBER+）

## 端点
GET `/api/v1/workspaces/{slug}/resource-scheduling/` →
`{overloaded: [...], underutilized: [...], suggestions: [{from, to, minutes}]}`

## 用例
- UT-01 超载/闲置分桶正确
- UT-02 建议生成（从超载到闲置的平衡对）
