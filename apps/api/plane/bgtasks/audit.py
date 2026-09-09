"""兼容转发：R1~R3 的 record_audit 埋点统一接入 AUTH-010 真管道。

旧占位实现已由 plane.audit.recorder.record_audit 替代（兼容签名不变）。
"""
from plane.audit.recorder import record_audit

__all__ = ["record_audit"]
