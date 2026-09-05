"""字段删除的异步清理任务（TASK-008 §4.3.3，架构 dynamic-fields-design.md §3.5）。

- ``cleanup_deleted_field_values`` —— 分批（2000）从 ``issues.custom_fields``
  移除残留 key：``custom_fields ? key`` 走 GIN 索引（每批 SELECT 是索引扫描），
  ``-`` 操作符删 key 后 GIN 随之更新；避免长事务与 WAL 洪峰。
- ``prune_views_referencing_field`` —— 视图引用剔除（降级而非报错）。
  TASK-011 视图（IssueView）上线前为预置空实现，幂等。
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
    """视图引用剔除（降级而非报错）：移除 filters/display_props 中该 key 的引用。

    TASK-011 视图模型（IssueView）上线前的预置任务体——返回 0（无视图可清理），
    上线后在此扩展为真正的剔除 + 提示标记（架构 §5.6）。
    """
    from plane.db.models import CustomFieldDefinition

    CustomFieldDefinition.all_objects.filter(pk=definition_id).exists()  # 幂等触发：定义存在性检查
    return 0
