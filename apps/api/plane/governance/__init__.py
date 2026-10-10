"""租户治理子包（AUTH-012，P4 R1）。

对齐 plane/audit/ 先例：risk_engine 承载规则引擎核心（同步可测），
tasks 承载 celery 异步面（ingest 扇出消费 + 处置执行器）。
门控 TENANT_GOVERNANCE_ENABLED（BR-11）：False 时 ingest 直接返回、
任务体短路，私有化零变化。
"""
