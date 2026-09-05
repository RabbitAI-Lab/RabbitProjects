"""逐字段 diff 生成（TASK-010 §4.3.1，架构 §2.10 全量落地 + 三处已裁决偏离）。

偏离（架构文档已按 TASK-010 §4.3.1 注回改统一）：① description 以 ``__modified__``
标记落库（BR-06 不落全文）；② custom_fields 的 field 命名为 ``cf_<key>``；
③ TRACKED_SCALAR_FIELDS 含 ``estimate_minutes``。
"""
from __future__ import annotations

import re
import time
import uuid

from plane.db.models import IssueActivity

TRACKED_SCALAR_FIELDS = ("name", "priority", "start_date", "target_date", "estimate_minutes")
TRACKED_FK_FIELDS = ("state", "issue_type", "parent")
TRACKED_M2M_FIELDS = ("assignees", "labels")
DESCRIPTION_MARKER = "__modified__"
SENSITIVE_PATTERNS = re.compile(r"password|token|secret|webhook_url", re.I)
VALUE_MAX_LEN = 500

FIELD_LABELS: dict[str, str] = {
    "name": "标题",
    "priority": "优先级",
    "start_date": "开始时间",
    "target_date": "截止时间",
    "estimate_minutes": "估算工时",
    "state": "状态",
    "issue_type": "任务类型",
    "parent": "父工作项",
    "assignees": "执行人",
    "labels": "标签",
    "description": "描述",
    "relation": "关联",
    "worklog": "工时",
    "archived_at": "归档",
}


def clip(field: str, text: str | None) -> str | None:
    """BR-11 敏感脱敏 + 500 截断：字段名 OR 值任一命中即整体 ***。"""
    if text is None:
        return None
    if SENSITIVE_PATTERNS.search(field or "") or SENSITIVE_PATTERNS.search(str(text)):
        return "***"
    text = str(text)
    return text[:VALUE_MAX_LEN] + ("…" if len(text) > VALUE_MAX_LEN else "")


def field_label(field: str) -> str:
    """cf_* → Schema 显示名（端点层解析注入）；内置字段走 FIELD_LABELS。"""
    return FIELD_LABELS.get(field, field)


def build_activities(*, issue_id: uuid.UUID, actor_id: uuid.UUID, before: dict,
                     after: dict, epoch: float | None = None) -> list[IssueActivity]:
    """比对前后快照逐字段生成（BR-02/03/06/11）。before/after 由 Service 事务内收集。"""
    epoch = epoch if epoch is not None else time.time() * 1000
    rows: list[IssueActivity] = []

    def mk(field, old, new, comment):
        rows.append(IssueActivity(
            issue_id=issue_id, actor_id=actor_id, verb="updated", field=field,
            old_value=clip(field, None if old is None else str(old)),
            new_value=clip(field, None if new is None else str(new)),
            comment=comment, epoch=epoch))

    for field in TRACKED_SCALAR_FIELDS:
        if before.get(field) != after.get(field):
            mk(field, before.get(field), after.get(field), f"更新了 {field_label(field)}")
    if before.get("description_html") != after.get("description_html"):
        rows.append(IssueActivity(
            issue_id=issue_id, actor_id=actor_id, verb="updated", field="description",
            old_value=DESCRIPTION_MARKER, new_value=DESCRIPTION_MARKER,
            comment="更新了描述", epoch=epoch))
    for field in TRACKED_FK_FIELDS:
        old_o, new_o = before.get(field), after.get(field)
        old_id = getattr(old_o, "id", None) if old_o is not None else None
        new_id = getattr(new_o, "id", None) if new_o is not None else None
        old_name = getattr(old_o, "name", None) if old_o is not None else None
        new_name = getattr(new_o, "name", None) if new_o is not None else None
        if old_id != new_id:
            rows.append(IssueActivity(
                issue_id=issue_id, actor_id=actor_id, verb="updated", field=field,
                old_value=clip(field, old_name),
                new_value=clip(field, new_name),
                old_identifier=str(old_id) if old_id else None,
                new_identifier=str(new_id) if new_id else None,
                comment=f"将 {field_label(field)} 从 {old_name or '空'} 改为 {new_name or '空'}",
                epoch=epoch))
    for field in TRACKED_M2M_FIELDS:
        old_ids, new_ids = set(before.get(field) or ()), set(after.get(field) or ())
        for uid in sorted(new_ids - old_ids):
            mk(field, None, uid, f"添加了 {field_label(field)}")
        for uid in sorted(old_ids - new_ids):
            mk(field, uid, None, f"移除了 {field_label(field)}")
    for key in set(before.get("custom_fields") or {}) | set(after.get("custom_fields") or {}):
        ov = (before.get("custom_fields") or {}).get(key)
        nv = (after.get("custom_fields") or {}).get(key)
        if ov != nv:
            mk(f"cf_{key}", ov, nv, f"更新了 {key}")
    return rows
