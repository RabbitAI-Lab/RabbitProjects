"""自定义字段表达式偏索引任务（TASK-008 §4.3.4，架构 dynamic-fields-design.md §6.2）。

- ``ensure_field_expression_index``：为 ``is_indexed`` 字段 CONCURRENTLY 建偏索引
  ``WHERE custom_fields ? key AND deleted_at IS NULL``（只索引「有值的行」）；
- ``drop_field_expression_index``：取消勾选 / 删除字段时 CONCURRENTLY 删除。

索引名 = ``idx_issue_cf_{md5(field_key)[:16]}`` —— 幂等且 ≤63 字符标识符上限。
这是**索引 DDL 而非表结构 DDL**：不改列定义、不需要 migration、CONCURRENTLY
不阻塞读写——不违反 G1「零 ALTER TABLE」（架构 §6.2 论证）。

注意：CONCURRENTLY 不能在事务块内执行。Celery worker 默认 autocommit 连接
可直接执行；pytest（TestCase 事务包裹）调用会抛
``CREATE INDEX CONCURRENTLY cannot run inside a transaction block``，
测试侧以本地 worker / flow 验证（验收门槛 3）。
"""
from __future__ import annotations

import hashlib
import logging

from celery import shared_task
from django.db import connection

from plane.base.exception import AppException
from plane.settings.features import MAX_INDEXED_CUSTOM_FIELDS_PER_WORKSPACE

logger = logging.getLogger("plane.bgtasks.field_index")

#: field_type → 索引 DDL 表达式（自带成对括号：CREATE INDEX 的表达式语法要求；
#: 单表上下文不需要 issues. 限定——与排序表达式（需限定防自连接歧义）刻意分离）
INDEX_EXPRESSIONS: dict[str, str] = {
    "number": "((custom_fields->>%s)::numeric)",
    "auto_increment": "((custom_fields->>%s)::bigint)",
    "currency": "((custom_fields#>>ARRAY[%s,'amount'])::numeric)",
    "date": "((custom_fields->>%s))",  # ISO 字符串字典序 = 时间序
    "text": "((custom_fields->>%s))",
    "textarea": "((custom_fields->>%s))",
    "url": "((custom_fields->>%s))",
    "select": "((custom_fields->>%s))",
    "checkbox": "((custom_fields->>%s)::bool)",
}


def index_name_for(field_key: str) -> str:
    return f"idx_issue_cf_{hashlib.md5(field_key.encode()).hexdigest()[:16]}"


def _index_expression_for(field_type: str) -> str:
    if field_type not in INDEX_EXPRESSIONS:
        raise ValueError(f"字段类型 {field_type} 不支持表达式索引")
    return INDEX_EXPRESSIONS[field_type]


@shared_task(
    bind=True,
    max_retries=2,
    name="plane.bgtasks.field_index.ensure_field_expression_index",
)
def ensure_field_expression_index(self, definition_id: str) -> str:
    """为标记 is_indexed 的字段创建表达式偏索引（幂等：IF NOT EXISTS）。"""
    from plane.db.models import CustomFieldDefinition

    d = CustomFieldDefinition.objects.get(id=definition_id, deleted_at__isnull=True, is_indexed=True)
    # BR-10 双保险：worker 侧超限直接拒绝（正常路径由 API 层 409 拦截）
    if (
        CustomFieldDefinition.objects.filter(
            workspace_id=d.workspace_id, is_indexed=True, deleted_at__isnull=True
        ).exclude(pk=d.pk).count() >= MAX_INDEXED_CUSTOM_FIELDS_PER_WORKSPACE
    ):
        raise AppException(
            "RESOURCE_LIMIT_EXCEEDED",
            message=f"每个工作空间最多 {MAX_INDEXED_CUSTOM_FIELDS_PER_WORKSPACE} 个索引优化字段",
        )

    name = index_name_for(d.field_key)
    expr = _index_expression_for(d.field_type)
    with connection.cursor() as cursor:
        # 会话级关超时：大表建索引不设 statement_timeout（autocommit 下 SET LOCAL 无效）
        cursor.execute("SET statement_timeout = 0")
        cursor.execute(
            f"""CREATE INDEX CONCURRENTLY IF NOT EXISTS {name}
                ON issues ({expr})
                WHERE custom_fields ? %s AND deleted_at IS NULL""",
            # 表达式占位符 + 偏索引条件占位符（同一 key 出现两次）
            [d.field_key, d.field_key],
        )
    logger.info("field_index.created name=%s key=%s", name, d.field_key)
    return name


@shared_task(name="plane.bgtasks.field_index.drop_field_expression_index")
def drop_field_expression_index(field_key: str) -> str:
    """取消勾选 / 删除字段时 CONCURRENTLY 删除索引（幂等）。"""
    name = index_name_for(field_key)
    with connection.cursor() as cursor:
        cursor.execute("SET statement_timeout = 0")
        cursor.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
    logger.info("field_index.dropped name=%s key=%s", name, field_key)
    return name
