"""派生字段失效传播与重算（TASK-014 §2.4/§4.3，P4 R3）。

策略：**同步失效标记 + 异步重算 + 读时兜底**——依赖变更 on_commit 打
Redis 脏标记（derived:dirty:{issue} SADD 受影响公式键），异步任务批量
重算（select_for_update 行锁防读-改-写丢更新）；读请求命中脏标记时同步
兜底重算该单行（P95 兜底 < 300ms 预算，§1.3-2）。求值失败值键不落
custom_fields（TASK-008「空值不落键」纪律）且 _meta.formula 记 error
（BR-05）；重算成功后清除脏键与 dirty 态（§4.1 dirty 生命周期闭环）。
"""

from __future__ import annotations

import logging

from django.core.cache import cache
from django.db import transaction

logger = logging.getLogger("plane.formula")

DIRTY_KEY = "derived:dirty:{issue_id}"
#: 环检测/依赖图最大深度保险丝（配置环早于深度爆栈拦截）
DEP_GRAPH_FUSE = 256


def _dirty_key(issue_id) -> str:
    return DIRTY_KEY.format(issue_id=issue_id)


# ── 依赖图 ──────────────────────────────────────────────────────────


def build_dep_graph(workspace_id) -> dict[str, set[str]]:
    """公式字段的依赖图 {field_key: {被引用的 cf_ 键}}（工作空间域）。"""
    from plane.db.models import CustomFieldDefinition

    graph: dict[str, set[str]] = {}
    rows = CustomFieldDefinition.objects.filter(
        workspace_id=workspace_id, field_type="formula", deleted_at__isnull=True
    ).values_list("field_key", "formula")
    from plane.formula.dsl import FormulaSyntaxError, collect_refs, parse

    for key, src in rows:
        try:
            graph[key] = collect_refs(parse(src or ""))
        except FormulaSyntaxError:
            graph[key] = set()  # 历史坏公式不阻断图构建（求值期显式报错）
    return graph


def detect_cycle(graph: dict[str, set[str]]) -> list[str] | None:
    """DFS 环检测；返回环路径（field_key 列表）或 None。"""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {k: WHITE for k in graph}
    stack: list[str] = []

    def visit(node: str, depth: int) -> list[str] | None:
        if depth > DEP_GRAPH_FUSE:
            return [node, "…"]
        color[node] = GRAY
        stack.append(node)
        for dep in graph.get(node, ()):  # 依赖图中不存在的键（手填字段）忽略
            if dep not in color:
                continue
            if color[dep] == GRAY:
                idx = stack.index(dep)
                return [*stack[idx:], dep]
            if color[dep] == WHITE:
                cycle = visit(dep, depth + 1)
                if cycle:
                    return cycle
        stack.pop()
        color[node] = BLACK
        return None

    for k in graph:
        if color[k] == WHITE:
            cycle = visit(k, 0)
            if cycle:
                return cycle
    return None


def affected_formulas(graph: dict[str, set[str]], changed_keys: set[str]) -> set[str]:
    """从变更键出发沿依赖图 DFS 收集受影响公式字段集。"""
    reverse: dict[str, set[str]] = {}
    for key, deps in graph.items():
        for dep in deps:
            reverse.setdefault(dep, set()).add(key)
    seen: set[str] = set()
    frontier = list(changed_keys)
    while frontier:
        cur = frontier.pop()
        for formula in reverse.get(cur, ()):
            if formula not in seen:
                seen.add(formula)
                frontier.append(formula)
                if len(seen) > DEP_GRAPH_FUSE:
                    return seen
    return seen


# ── 失效与重算 ──────────────────────────────────────────────────────


def invalidate(issue_id, changed_keys: set[str]) -> set[str]:
    """打脏标记（on_commit 语境调用；changed 为本次写入/删除的 cf_ 键）。"""
    from plane.db.models import Issue

    issue = Issue.objects.filter(pk=issue_id).values_list("project_id", "project__workspace_id").first()
    if issue is None:
        return set()
    _, workspace_id = issue
    graph = build_dep_graph(workspace_id)
    affected = affected_formulas(graph, changed_keys)
    if affected:
        try:
            cache.sadd(_dirty_key(issue_id), *affected)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 —— locmem 无 sadd
            _ = cache.get(_dirty_key(issue_id)) or []
            cache.set(_dirty_key(issue_id), sorted(set(_) | affected), timeout=3600)
    return affected


def dirty_keys(issue_id) -> set[str]:
    try:
        return set(cache.smembers(_dirty_key(issue_id)) or set())  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return set(cache.get(_dirty_key(issue_id)) or [])


def _clear_dirty(issue_id, keys: set[str]) -> None:
    try:
        if keys:
            cache.srem(_dirty_key(issue_id), *keys)  # type: ignore[attr-defined]
        else:
            cache.delete(_dirty_key(issue_id))
    except Exception:  # noqa: BLE001
        remaining = dirty_keys(issue_id) - keys
        if remaining:
            cache.set(_dirty_key(issue_id), sorted(remaining), timeout=3600)
        else:
            cache.delete(_dirty_key(issue_id))


def recompute_issue(issue_id, *, only_keys: set[str] | None = None) -> dict:
    """单任务公式重算（select_for_update 行锁；BR-05 错误不落值键）。

    返回 {field_key: {"ok": bool, "value"/"error": …}} 供任务与读时兜底共用。
    """
    from plane.db.models import CustomFieldDefinition, Issue

    result: dict[str, dict] = {}
    with transaction.atomic():
        issue = Issue.objects.select_for_update().filter(pk=issue_id).first()
        if issue is None:
            return result
        workspace_id = issue.project.workspace_id
        fields = CustomFieldDefinition.objects.filter(
            workspace_id=workspace_id, field_type="formula", deleted_at__isnull=True
        )
        from plane.formula.dsl import EvalContext, FormulaError, evaluate, parse

        props = _issue_props(issue)
        custom = dict(issue.custom_fields or {})
        meta = dict(custom.get("_meta") or {})
        formula_meta = dict(meta.get("formula") or {})
        custom.pop("_meta", None)
        sub_rows = _sub_values(issue)

        changed_meta = False
        for f in fields:
            if only_keys is not None and f.field_key not in only_keys:
                continue
            entry = formula_meta.get(f.field_key) or {}
            try:
                node = parse(f.formula or "")
                value = evaluate(
                    node,
                    EvalContext(
                        props=props,
                        custom=custom,
                        sub_count=sub_rows["count"],
                        sub_done_count=sub_rows["done_count"],
                        sub_values=sub_rows["values"],
                    ),
                )
                custom[f.field_key] = value
                if entry.get("dirty") or entry.get("error"):
                    entry = {"dirty": False}
                    formula_meta[f.field_key] = entry
                    changed_meta = True
                result[f.field_key] = {"ok": True, "value": value}
            except FormulaError as exc:
                custom.pop(f.field_key, None)  # 空值不落键（BR-05）
                formula_meta[f.field_key] = {"dirty": False, "error": str(exc)[:200]}
                changed_meta = True
                result[f.field_key] = {"ok": False, "error": str(exc)[:200]}
        if changed_meta or not formula_meta:
            meta["formula"] = formula_meta
            custom["_meta"] = meta
        issue.custom_fields = custom
        issue.save(update_fields=["custom_fields", "updated_at"])
    _clear_dirty(issue_id, only_keys if only_keys is not None else set(result))
    return result


def materialize_if_dirty(issue) -> dict:
    """读时兜底（§2.4 读路径）：命中脏标记同步重算该单行并返回补正值。"""
    keys = dirty_keys(issue.id)
    if not keys:
        return {}
    try:
        return recompute_issue(issue.id, only_keys=keys)
    except Exception:  # noqa: BLE001 —— 兜底失败不阻断读
        logger.exception("formula.fallback_failed issue=%s", issue.id)
        return {}


def _issue_props(issue) -> dict:
    """内置属性面（prop('name') 可引用集）。"""
    return {
        "priority": issue.priority or None,
        "target_date": issue.target_date.isoformat() if issue.target_date else None,
        "start_date": issue.start_date.isoformat() if issue.start_date else None,
        "state_id": str(issue.state_id) if issue.state_id else None,
        "name": issue.name,
    }


def _sub_values(issue) -> dict:
    """子任务聚合上下文（sub_count/sub_done_count/sub_sum）。"""
    from plane.db.models import Issue

    subs = Issue.objects.filter(parent_id=issue.id, deleted_at__isnull=True).values_list(
        "custom_fields", "completed_at"
    )[:500]
    done = 0
    values = []
    for cf, completed_at in subs:
        if completed_at:
            done += 1
        if cf:
            values.append({k: v for k, v in cf.items() if k != "_meta"})
    return {"count": len(subs), "done_count": done, "values": values}
