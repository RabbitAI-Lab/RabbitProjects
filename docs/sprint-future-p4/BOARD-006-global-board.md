# 跨项目全局看板

| 文档编号 | BOARD-006 | 所属迭代 | P4（v2 纳入） | 模块 | M5-BOARD |
| --- | --- | --- | --- | --- | --- |
| 文档状态 | 已实现稿（R16，自含精简） | 前置 | BOARD-001 看板基座 | 日期 | 2026-09-12 |

> **范围**：跨项目聚合看板——按状态组聚合用户可见全部项目的任务计数泳道；
> 卡片下钻跳源项目看板（不在聚合卡上直接拖拽——跨项目写归源端点）。

## BR
- BR-01 泳道 = 状态语义组（unstarted/started/completed/cancelled）四列
- BR-02 权限 = 用户可见项目集（accessible_by 逐空间）；卡片仅计数+Top 样例

## 端点
GET `/api/v1/workspaces/{slug}/global-board/` →
`{lanes: [{key, count, sample: [{issue_id, name, project}]}]}`

## 用例
- UT-01 四泳道计数正确（跨项目聚合）
- UT-02 不可见项目不进聚合
