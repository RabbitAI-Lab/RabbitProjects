"""字段删除的异步清理任务（TASK-008 §4.3.3，架构 dynamic-fields-design.md §3.5）。

- ``cleanup_deleted_field_values`` —— 分批（2000）从 ``issues.custom_fields``
  移除残留 key：``custom_fields ? key`` 走 GIN 索引（每批 SELECT 是索引扫描），
  ``-`` 操作符删 key 后 GIN 随之更新；避免长事务与 WAL 洪峰。
- ``prune_views_referencing_field`` —— 视图引用剔除（降级而非报错，
  TASK-011 §4.3.4 激活：filters 递归嵌套剔除 + display_props 三处去引用）。
"""
from __future__ import annotations

import logging

from celery import shared_task
from django.db import connection

logger = logging.getLogger("plane.bgtasks.field_cleanup")

BATCH_SIZE = 2000


def _scope_clause(definition) -> tuple[str, list]:
    """全局字段按 workspace 圈定；项目字段按 project（与视图层 _scope_clause 同口径）。"""
    if definition.project_id is not None:
        return "project_id = %s", [str(definition.project_id)]
    return (
        "project_id IN (SELECT id FROM projects WHERE workspace_id = %s AND deleted_at IS NULL)",
        [str(definition.workspace_id)],
    )


@shared_task(
    bind=True,
    max_retries=3,
    name="plane.bgtasks.field_cleanup.cleanup_deleted_field_values",
)
def cleanup_deleted_field_values(self, definition_id: str, batch_size: int = BATCH_SIZE) -> int:
    """BR-11：删除字段后清理 JSONB 残留 key —— 分批 UPDATE，幂等（无残留时首批即 0 行）。"""
    from plane.db.models import CustomFieldDefinition

    d = CustomFieldDefinition.all_objects.get(id=definition_id)
    scope_sql, params = _scope_clause(d)
    key = d.field_key
    total = 0
    while True:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                WITH target AS (
                    SELECT id FROM issues
                     WHERE {scope_sql}
                       AND custom_fields ? %s
                       AND deleted_at IS NULL
                     LIMIT %s
                )
                UPDATE issues i
                   SET custom_fields = i.custom_fields - %s,
                       updated_at = now()
                  FROM target t
                 WHERE i.id = t.id
                """,
                [*params, key, batch_size, key],
            )
            affected = cursor.rowcount
        total += affected
        if affected < batch_size:
            break
    if total:
        logger.info("field_cleanup.removed key=%s rows=%s", key, total)
    return total


@shared_task(name="plane.bgtasks.field_cleanup.prune_views_referencing_field")
def prune_views_referencing_field(definition_id: str) -> int:
    """视图引用剔除（BR-15 / TASK-011 §4.3.4 激活；降级而非报错）。

    - filters：递归（嵌套树）剔除 ``field == key`` 的条件，组内条件清空则整组剔除；
    - display_props：``group_by == key`` 回退 ``state_id``（BR-05 默认维度）；
      ``sub_group_by`` / ``order_by``（含 ``-key``）置空；``columns`` / ``card_fields`` 去键；
    - ``updated_at`` 触碰即前端「视图已自动调整」黄条依据（§4.3.4）；
    - 幂等：无引用不落库；返回本次处理的行数。作用域：项目私有字段 → 该项目
      IssueView；全局字段 → workspace 全部视图（含跨项目视图，架构 §5.6）。
    """
    from plane.db.models import CustomFieldDefinition, IssueView

    definition = CustomFieldDefinition.all_objects.filter(pk=definition_id).first()
    if definition is None:
        return 0
    key = definition.field_key
    views = IssueView.objects.filter(workspace_id=definition.workspace_id, deleted_at__isnull=True)
    if definition.project_id is not None:
        views = views.filter(project_id=definition.project_id)

    affected = 0
    for view in views.iterator():
        original_filters = view.filters or {}
        original_props = view.display_props or {}
        new_filters = _strip_field_from_tree(original_filters, key) if original_filters else original_filters
        props = dict(original_props)
        changed = new_filters != original_filters
        for prop in ("sub_group_by", "order_by"):
            if props.get(prop) in (key, f"-{key}"):
                props[prop] = None
                changed = True
        if props.get("group_by") == key:
            props["group_by"] = "state_id"  # 分组键回退（display_props.group_by 读侧同款默认）
            changed = True
        if key in (props.get("columns") or []):
            props["columns"] = [c for c in props["columns"] if c != key]
            changed = True
        if isinstance(props.get("card_fields"), dict) and key in props["card_fields"]:
            props["card_fields"] = {k: v for k, v in props["card_fields"].items() if k != key}
            changed = True
        if changed:
            view.filters = new_filters
            view.display_props = props
            view.save(update_fields=["filters", "display_props", "updated_at"])
            affected += 1
    if affected:
        logger.info("field_cleanup.pruned_views key=%s views=%s", key, affected)
    return affected


def _strip_field_from_tree(node: dict, key: str) -> dict:
    """递归剔除引用 ``key`` 的条件节点；空组剔除、根组保留（空 conditions = 无操作）。"""
    is_logic = "op" in node or "conditions" in node
    if not is_logic:
        return node  # 条件节点：field != key 的保留（== key 的由父层过滤）
    kept = []
    for child in node.get("conditions") or []:
        if not isinstance(child, dict):
            continue
        if "op" in child or "conditions" in child:
            pruned = _strip_field_from_tree(child, key)
            if pruned.get("conditions"):
                kept.append(pruned)
        elif child.get("field") != key:
            kept.append(child)
    return {"op": str(node.get("op", "AND")).upper(), "conditions": kept}
