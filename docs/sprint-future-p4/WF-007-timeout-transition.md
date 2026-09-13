# 超时自动流转

| 文档编号 | WF-007 | 所属迭代 | P4（v2 纳入） | 模块 | M11-WF |
| --- | --- | --- | --- | --- | --- |
| 文档状态 | 已实现稿（R17，自含精简） | 前置 | WF-001 引擎 | 日期 | 2026-09-12 |

> **范围**：状态停留超时自动流转（如「待审批 >72h → 自动升级/催办」）——
> beat 扫描 + 引擎守卫执行（复用 TASK-005 断言链，不绕过守卫）。

## BR
- BR-01 规则 = {workflow, from_state, hours, to_state}；仅 WF 管理员配
- BR-02 执行走引擎（守卫失败留原态并记跳过原因）；全程审计

## 端点/任务
CRUD `/api/v1/workspaces/{slug}/projects/{pid}/workflow/timeout-rules/`；
beat `wf_timeout_sweep()`（每小时）。

## 用例
- UT-01 超时任务流转到目标态
- UT-02 守卫阻断时留原态记原因
