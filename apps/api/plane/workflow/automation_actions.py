"""自动化动作注册表（WF-003 §4.3）——五执行器（set_field/assign/notify/transition/
add_label）。

每个执行器签名 `(config, issue, actor, context)` → 写库 + on_commit Activity 留痕。
BR-10「origin=automation:{rule}」单字段特例——动作修改字段时以原值
`{field: None}` 触发事件由 TASK-010 统一发布，chain_depth+1 携带。
"""
from __future__ import annotations

import logging
from collections.abc import Callable

from django.utils import timezone as _tz

from plane.workflow.services import TransitionError, WorkflowService

logger = logging.getLogger(__name__)


def _set_field(config: dict, *, issue, actor, context: dict) -> dict:
    """设置任务字段（内置/自定义 cf_*）。BR-06 Activity 经 TASK-010 落 via 标记。"""
    from plane.db.models import ProjectMember

    field, value = config["field"], config.get("value")
    if not hasattr(issue, field) and not field.startswith("cf_"):
        raise ValueError(f"unknown field {field!r}")
    if field == "assignees":
        ids = [str(u) for u in (value or [])]
        bad = [u for u in ids if not ProjectMember.objects.filter(
            project_id=issue.project_id, member_id=u, is_active=True).exists()]
        if bad:
            raise ValueError(f"非项目成员：{', '.join(bad)}")
        issue.assignees.set(ids)
    elif field.startswith("cf_"):
        cf = dict(issue.custom_fields or {})
        if value is None:
            cf.pop(field, None)
        else:
            cf[field] = value
        issue.custom_fields = cf
    else:
        setattr(issue, field, value)
    issue.save(update_fields=[field if not field.startswith("cf_") else "custom_fields", "updated_at"])
    return {"field": field, "value": value}


def _assign(config: dict, *, issue, actor, context: dict) -> dict:
    """指派（与 set_field.assignees 同义；可由 strategy 扩展：role_group 等）。"""
    strategy = config.get("strategy", "users")
    if strategy == "users":
        return _set_field({"field": "assignees", "value": config.get("user_ids", [])},
                          issue=issue, actor=actor, context=context)
    if strategy == "role_group":
        from plane.db.models import ProjectMember
        from plane.db.models.roles import ProjectRole

        role_name = config.get("role", "PROJ_ADMIN")
        try:
            target_role = ProjectRole(role_name)
        except ValueError:
            target_role = ProjectRole.ADMIN
        member_ids = list(ProjectMember.objects.filter(
            project_id=issue.project_id, role=target_role, is_active=True
        ).values_list("member_id", flat=True))
        return _set_field({"field": "assignees", "value": member_ids},
                          issue=issue, actor=actor, context=context)
    raise ValueError(f"未知 assign strategy：{strategy!r}")


def _notify(config: dict, *, issue, actor, context: dict) -> dict:
    """通知（COLLAB-001 通道 4 —— Notification.objects.create 直接落库。due_approaching
    已有专用收件人；本通道面向规则侧即时通知。"""
    from plane.db.models import Notification

    title = config.get("title") or f"规则 {context.get('rule_name', '?')} 触达"
    targets = config.get("targets", ["watchers"])
    if targets == ["assignees"]:
        receivers = list(issue.assignees.values_list("id", flat=True))
    elif targets == ["reporter"]:
        receivers = [issue.created_by_id] if issue.created_by_id else []
    else:
        receivers = []
    for uid in dict.fromkeys(receivers):
        Notification.objects.create(
            receiver_id=uid,
            event="automation.notify",
            title=title[:200],
            data={"issue_id": str(issue.id), "rule_id": context.get("rule_id")},
            dedup_key=Notification.build_dedup_key(
                event="automation.notify", issue_id=issue.id,
                actor_id=actor.id, epoch=f"{context.get('rule_id', 0)}:{_tz.now().timestamp()}",
                receiver_id=str(uid)),
        )
    return {"targets": targets, "delivered": len(receivers)}


def _transition(config: dict, *, issue, actor, context: dict) -> dict:
    """走 WF-001 引擎完整路径（BR-05 守卫照常；失败抛 TransitionError，run=failed）。"""
    target_state_id = config.get("to_state_id")
    if not target_state_id:
        raise ValueError("transition 动作需指定 to_state_id")
    try:
        WorkflowService().transition(
            issue_id=issue.id, to_state_id=target_state_id, actor=actor,
            transition_id=config.get("transition_id"),
            guard_payload=config.get("guard_payload"),
        )
    except TransitionError as exc:
        raise RuntimeError(f"transition failed: {exc.code}") from exc
    return {"to_state_id": str(target_state_id)}


def _add_label(config: dict, *, issue, actor, context: dict) -> dict:
    """标签追加（与前端可用语义一致；去重保序）。"""
    label_ids = list(dict.fromkeys(config.get("label_ids") or []))
    if not label_ids:
        raise ValueError("add_label 需 label_ids")
    issue.labels.add(*label_ids)
    return {"label_ids": [str(x) for x in label_ids]}


#: 注册表——type → 执行器（§2.1 五类型；set_field 走通用字段修改）
ACTION_REGISTRY: dict[str, Callable[..., dict]] = {
    "set_field": _set_field,
    "assign": _assign,
    "notify": _notify,
    "transition": _transition,
    "add_label": _add_label,
}


def execute_actions(rule, issue, event) -> tuple[bool, list[dict]]:
    """顺序执行规则动作（§2.3 + §2.5 BR-04 默认 stop）。任一失败 → 落明细
    + on_error 控制继续/stop；返回 (ok, action_results)。"""
    results: list[dict] = []
    ok = True
    context = {"rule_id": str(rule.id), "rule_name": rule.name,
               "via": f"automation:{rule.id}"}
    for action in (rule.actions or []):
        type_, cfg = action.get("type"), action.get("config") or {}
        executor = ACTION_REGISTRY.get(type_)
        if executor is None:
            results.append({"type": type_, "ok": False, "error": f"未知动作类型 {type_!r}"})
            ok = False
            if action.get("on_error", "stop") == "stop":
                break
            continue
        try:
            detail = executor(cfg, issue=issue, actor=rule.created_by, context=context)
            results.append({"type": type_, "ok": True, "detail": detail})
        except Exception as exc:  # noqa: BLE001 — 动作失败按 on_error 处置（§2.5 BR-04）
            results.append({"type": type_, "ok": False, "error": str(exc),
                            "error_type": exc.__class__.__name__})
            ok = False
            if action.get("on_error", "stop") == "stop":
                break
    return ok, results
