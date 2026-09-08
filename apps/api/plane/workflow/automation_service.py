"""自动化服务（WF-003）——规则保存校验、Dry Run、worker 入口、缓存。

缓存（§4.2 零 DB 命中口径）：
- 启用规则集 automation:rules:{project_id}（miss 回源 PG，rebuild_active_rules）
- 项目级设置 automation:settings:{project_id}（同样 miss 回源）
"""
from __future__ import annotations

import logging
import time

from django.core.cache import cache
from django.db import transaction

from plane.db.models import AutomationRule, AutomationRun, AutomationSetting, Issue
from plane.workflow.automation import (
    MAX_ACTIONS,
    MAX_CONDITIONS,
    TRIGGER_TYPES,
    _allow_rule_chain,
    eval_conditions,
    event_gate,
    match_trigger,
)
from plane.workflow.automation_actions import ACTION_REGISTRY, execute_actions

logger = logging.getLogger(__name__)

RULE_CACHE_TTL = 600  # 10 分钟；保存/启停走 post_save/delete 信号失效
SETTINGS_CACHE_TTL = 600


class AutomationError(Exception):
    """WF-003 错误聚合（视图层转信封）。"""

    def __init__(self, code: str, status: int, sub: str | None = None, message: str = ""):
        self.code, self.status, self.sub, self.message = code, status, sub, message
        super().__init__(message)


# ── 规则保存校验（§4.1 BR-02/§2.7）────────────────────────────

def validate_rule_definition(payload: dict) -> list[dict]:
    """返回 details[] 项（空=通过）。trigger.type 必注册；actions 1..5；
    conditions ≤20；动态 type 集中分发。"""

    issues: list[dict] = []
    trigger = payload.get("trigger") or {}
    t_type = trigger.get("type")
    if t_type not in TRIGGER_TYPES:
        issues.append({"field": "trigger.type", "code": "NOT_A_CHOICE",  # noqa: B904--校验聚合非异常链
                       "message": f"未知触发器 {t_type!r}，合法枚举 {list(TRIGGER_TYPES)}"})
    if t_type == "due_approaching":
        cfg = trigger.get("config") or {}
        h = cfg.get("hours_before")
        if not isinstance(h, int) or not (1 <= h <= 168):
            issues.append({"field": "trigger.config.hours_before", "code": "INVALID",
                           "message": "hours_before 须为 1..168 整数"})  # noqa: B904 -- 校验聚合

    conds = payload.get("conditions") or []
    if len(conds) > MAX_CONDITIONS:
        issues.append({"field": "conditions", "code": "LIMIT",
                       "message": f"条件下限 ≤ {MAX_CONDITIONS}"})
    for i, c in enumerate(conds):
        for f in ("field", "operator"):
            if f not in c:
                issues.append({"field": f"conditions[{i}].{f}", "code": "REQUIRED",
                              "message": f"必填 {f}"})

    actions = payload.get("actions") or []
    if not actions:
        issues.append({"field": "actions", "code": "REQUIRED", "message": "动作不能为空"})
    if len(actions) > MAX_ACTIONS:
        issues.append({"field": "actions", "code": "LIMIT",
                       "message": f"单规则动作 ≤ {MAX_ACTIONS}"})
    for i, a in enumerate(actions):
        if a.get("type") not in ACTION_REGISTRY:
            issues.append({"field": f"actions[{i}].type", "code": "NOT_A_CHOICE",
                           "message": f"未知动作类型 {a.get('type')!r}，合法枚举 {list(ACTION_REGISTRY)}"})
    return issues


# ── 缓存（§4.2）───────────────────────────────────────────────

def _rules_cache_key(project_id) -> str:
    return f"automation:rules:{project_id}"


def get_active_rules(project_id) -> list[AutomationRule]:
    cached = cache.get(_rules_cache_key(project_id))
    if cached is not None:
        return cached
    qs = list(AutomationRule.objects.filter(
        project_id=project_id, is_active=True, deleted_at__isnull=True
    ).order_by("created_at"))
    cache.set(_rules_cache_key(project_id), qs, RULE_CACHE_TTL)
    return qs


def invalidate_rules_cache(project_id) -> None:
    cache.delete(_rules_cache_key(project_id))


def get_settings(project_id) -> AutomationSetting | None:
    key = f"automation:settings:{project_id}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    try:
        s = AutomationSetting.objects.get(project_id=project_id)
    except AutomationSetting.DoesNotExist:
        s = None
    cache.set(key, s, SETTINGS_CACHE_TTL)
    return s


def invalidate_settings_cache(project_id) -> None:
    cache.delete(f"automation:settings:{project_id}")


# ── Dry Run（§2.4 BR-11 零写）────────────────────────────────

def dry_run(*, rule: AutomationRule, issue: Issue, event: dict | None = None) -> dict:
    """不落 run、不落 Activity、不发通知——仅评估命中/动作/闸状态。"""
    evt = event or {"type": rule.trigger.get("type"), "project_id": str(issue.project_id),
                    "issue_id": str(issue.id), "payload": {}, "chain_depth": 0, "origin": ""}
    matched = match_trigger(rule.trigger, evt) and eval_conditions(rule.conditions or [], issue)
    gate = event_gate({**evt, "project_id": str(issue.project_id)})
    if gate and not _allow_rule_chain(issue.project):
        gate = gate  # 闸 1 锁死
    elif gate:
        gate = None  # 闸 1 放开
    plan = []
    for a in rule.actions or []:
        type_, cfg = a.get("type"), a.get("config") or {}
        plan.append({"type": type_, "config": cfg, "valid": type_ in ACTION_REGISTRY})
    return {
        "rule_id": str(rule.id), "issue_id": str(issue.id),
        "matched": bool(matched), "gate": gate,
        "plan": plan,
    }


# ── Worker 入口（§4.3）───────────────────────────────────────

def run_event(event: dict) -> dict:
    """自动化 worker 入口——事件流处理 + 防循环 + 动作执行 + run 落库。

    返回聚合（便于测试断言与日志）。
    """

    project_id = event["project_id"]
    issue_id = event.get("issue_id")
    settings = get_settings(project_id)
    allow_chain = bool(settings and settings.allow_rule_chain)
    chain_depth = int(event.get("chain_depth") or 0)
    # 闸 1/2
    gate = event_gate(event)
    if gate == "chain_depth":
        # BR-08 熔断 + 告警（生产告警通道，dev 日志）
        logger.error("automation.chain_depth.exceeded project=%s issue=%s", project_id, issue_id)
    if gate and not allow_chain:
        # 拦截（不匹配规则）
        return {"gate": gate, "matched": 0, "skipped": 0, "ran": 0}
    rules = get_active_rules(project_id)
    issue = None
    if issue_id:
        try:
            issue = Issue.objects.get(id=issue_id)
        except Issue.DoesNotExist:
            return {"gate": gate, "matched": 0, "skipped": 0, "ran": 0, "miss_issue": True}
    matched_n = skipped_n = ran_n = 0

    def _summary() -> dict[str, int | str | None]:
        return {"gate": gate, "matched": matched_n, "skipped": skipped_n, "ran": ran_n}
    for rule in rules:
        if not match_trigger(rule.trigger, event):
            continue
        if issue is None or not eval_conditions(rule.conditions or [], issue):
            continue
        matched_n += 1
        if gate:
            AutomationRun.objects.create(
                rule=rule, issue=issue, event=event, status=AutomationRun.Status.SKIPPED,
                skip_reason=gate)
            skipped_n += 1
            continue
        # 闸 3：去重（due_approaching 由扫描端独立键处理）
        from plane.workflow.automation import _dedup_acquire, _dedup_key
        dedup_key = _dedup_key(rule, issue.id)
        if not _dedup_acquire(dedup_key, rule.dedup_window_minutes):
            AutomationRun.objects.create(
                rule=rule, issue=issue, event=event, status=AutomationRun.Status.SKIPPED,
                skip_reason="dedup")
            skipped_n += 1
            continue
        started = time.time()
        run = AutomationRun.objects.create(
            rule=rule, issue=issue, event=event, status=AutomationRun.Status.RUNNING)
        with transaction.atomic():
            ok, results = execute_actions(rule, issue, event)
            run.status = AutomationRun.Status.SUCCESS if ok else AutomationRun.Status.FAILED
            run.action_results = results
            run.duration_ms = int((time.time() - started) * 1000)
            run.save(update_fields=["status", "action_results", "duration_ms"])
        # BR-13 连续失败熔断
        if not ok:
            rule.consecutive_failures = (rule.consecutive_failures or 0) + 1
            if rule.consecutive_failures >= 10:
                rule.is_active = False
            rule.save(update_fields=["consecutive_failures", "is_active"])
        else:
            if rule.consecutive_failures:
                rule.consecutive_failures = 0
                rule.save(update_fields=["consecutive_failures"])
        ran_n += 1
    return _summary()
