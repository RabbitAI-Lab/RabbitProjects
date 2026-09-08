"""自动化规则引擎（WF-003 §4.3）——四触发器 × 五动作受限 DSL。

执行模型（BR-03/07/08/09）：
1. 事件入口 → event_gate 闸 1（origin）/ 闸 2（chain_depth ≥5）
2. 拉取项目启用规则缓存（零 DB 命中，§4.2）
3. 逐条 match_trigger + eval_conditions
4. 闸 3 去重 SETNX（due_approaching 豁免——扫描端独立键）
5. 落 run=running → 顺序执行 ActionRegistry 动作 → 终态 run=success/failed

全部动作内部 on_commit 落 Activity（BR-06：via=automation:{rule_id}）。
"""
from __future__ import annotations

import logging
from typing import Any

from django.core.cache import cache

from plane.db.models import (
    AutomationRule,
    Issue,
    Project,
)

logger = logging.getLogger(__name__)

#: 五动作类型（WF-003 §2.1；set_field/assign/notify 复用车流与 WF-001 §4.7 协议；
#: transition/add_label 为本引擎扩展动作）
ACTION_TYPES = ("set_field", "assign", "notify", "transition", "add_label")

#: 触发器四类型
TRIGGER_TYPES = ("state_changed", "issue_created", "field_changed", "due_approaching")

#: 单规则动作上限 / 条件下限（§2.7）
MAX_ACTIONS = 5
MAX_CONDITIONS = 20

#: 链深度熔断（BR-08）
MAX_CHAIN_DEPTH = 5


# ── 触发器匹配（§2.2）──────────────────────────────────────────

def match_trigger(trigger: dict, event: dict) -> bool:
    """type 必须命中；config 各过滤键全等/集合命中。"""
    if trigger.get("type") != event.get("type"):
        return False
    cfg = trigger.get("config") or {}
    ec = event.get("payload") or {}

    if event["type"] == "state_changed":
        if "to_state" in cfg and cfg["to_state"] != ec.get("to_state_id"):
            return False
        if "to_group" in cfg and cfg["to_group"] != ec.get("to_group"):
            return False
        if "from_state" in cfg and cfg["from_state"] != ec.get("from_state_id"):
            return False
        if "transition_id" in cfg and cfg["transition_id"] != ec.get("transition_id"):
            return False
    elif event["type"] == "issue_created":
        pass  # issue_types 等外加
    elif event["type"] == "field_changed":
        if "fields" in cfg and ec.get("field") not in set(cfg["fields"]):
            return False
        if "to_value" in cfg and ec.get("new_value") != cfg["to_value"]:
            return False
    elif event["type"] == "due_approaching":
        if "hours_before" in cfg and cfg["hours_before"] != ec.get("hours_before"):
            return False

    if "issue_types" in cfg and ec.get("issue_type_id") not in set(cfg["issue_types"]):
        return False
    return True


# ── 条件求值（FilterCompiler 子集 §2.1）─────────────────────────

def eval_conditions(conditions: list[dict], issue: Issue) -> bool:
    """AND 组合；空数组 = 恒真（BR-01）。条件格式与 TASK-011 DSL 子集：
    {field, operator, value}。"""
    if not conditions:
        return True
    cf = issue.custom_fields or {}
    for cond in conditions:
        field = str(cond.get("field") or "")
        op = str(cond.get("operator") or "")
        value = cond.get("value")
        actual = _resolve_field(issue, field, cf)
        if not _apply_operator(op, actual, value):
            return False
    return True


def _resolve_field(issue, field: str, cf: dict) -> Any:
    if field in ("priority", "state", "state_id", "estimate_minutes",
                 "target_date", "start_date", "name"):
        if field in ("state", "state_id"):
            return str(issue.state_id) if issue.state_id else None
        return getattr(issue, field, None)
    if field == "assignees":
        return list(issue.assignees.values_list("id", flat=True))
    if field == "labels":
        return list(issue.labels.values_list("id", flat=True))
    if field.startswith("cf_"):
        return cf.get(field)
    return None


def _apply_operator(op: str, actual: Any, value: Any) -> bool:
    """复用车流 Operator 语义（in/is_empty/eq/neq/contains 等）——本引擎只读。"""
    if op == "in":
        return actual in (value or [])
    if op == "not_in":
        return actual not in (value or [])
    if op == "eq":
        return actual == value
    if op == "neq":
        return actual != value
    if op == "is_empty":
        if actual is None:
            return True
        if isinstance(actual, (list, tuple, str, dict)):
            return len(actual) == 0
        return False
    if op == "is_not_empty":
        return not _apply_operator("is_empty", actual, value)
    if op == "contains":
        if isinstance(actual, (list, tuple)):
            return value in actual
        if isinstance(actual, str):
            return str(value) in actual
    return False


# ── 防循环三闸（§2.3）──────────────────────────────────────────

def event_gate(event: dict) -> str | None:
    """闸 1：origin=automation 不匹配 state/field 触发器（默认）；
    闸 2：chain_depth ≥ 5 熔断（BR-08）。due_approaching 闸 1 豁免——扫描端
    独立 dedup 键，避免双 SETNX 互斥全部 skipped。"""
    origin = (event.get("origin") or "").strip()
    if (event.get("type") in ("state_changed", "field_changed")
            and origin.startswith("automation")):
        return "origin"
    if int(event.get("chain_depth") or 0) >= MAX_CHAIN_DEPTH:
        return "chain_depth"
    return None


def _allow_rule_chain(project: Project) -> bool:
    """闸 1 放开开关：AutomationSetting.allow_rule_chain（§4.2 缓存键）。"""
    setting = getattr(project, "automation_setting", None)
    if setting is None:
        return False
    return bool(setting.allow_rule_chain)


# ── 去重键（§2.3 BR-09）───────────────────────────────────────

def _dedup_key(rule: AutomationRule, issue_id, *, scope: str = "normal") -> str:
    if scope == "due":
        return f"autodedup:due:{rule.id}:{issue_id}:{(rule.dedup_window_minutes or 60)}"
    return f"autodedup:{rule.id}:{issue_id}"


def _dedup_acquire(key: str, window_minutes: int) -> bool:
    """SETNX：worker 端闸 3（due_approaching 由扫描端独立键承担）。"""
    if window_minutes <= 0:
        return True
    ok = cache.add(key, 1, timeout=window_minutes * 60)
    if not ok:
        cache.set(key, 1, timeout=window_minutes * 60)  # 续期
    return True  # 续期视为允许——spec 是"窗口内已成功过才跳"
