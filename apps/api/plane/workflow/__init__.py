"""工作流域（M11-WF）——引擎与审批的领域服务层。

WF-001 引擎（services.WorkflowService）与 WF-002 审批（approval.ApprovalService）
在本包内互相引用（审批终审回填经引擎单事务入口），与 db/services 的 CRUD 服务
分层隔离（api-conventions §2.1）。
"""
