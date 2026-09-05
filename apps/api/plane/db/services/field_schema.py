"""字段作用域解析 + Schema 缓存（TASK-008 §4.3.1，架构 dynamic-fields-design.md §3.3）。

- ``resolve_fields``：全局 ∪ 项目私有、同 key 私有覆盖全局、applicable_types 过滤；
- ``get_cached_schema``：Redis 缓存（key ``field_schema:v1:{ws}:{proj}``，TTL 3600），
  Redis 不可用时降级直查（log warning，不阻断主流程）；
- ``invalidate_field_schema_cache``：post_save/post_delete 信号主动失效（BR-13）。
  全局字段变更 → 该 WS 全部项目 key —— 用 SCAN 模式匹配并**逐批直接删除**
  （每批命中即删，满足「scan 后续 TTL 或删除」的口径，杜绝 SCAN 游标漂移期间
  读到已失效数据的窗口）。

ETag：payload 的 md5 摘要（``W/"cfd-v1-{digest}"``）—— 定义变更即失效。
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid as uuid_module
from typing import TYPE_CHECKING, Any

from django.db.models import Q

from plane.db.models import CustomFieldDefinition, State

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from plane.db.models import Project

logger = logging.getLogger(__name__)

FIELD_SCHEMA_CACHE_KEY = "field_schema:v1:{workspace_id}:{project_id}"
FIELD_SCHEMA_CACHE_TTL = 3600  # 秒；字段定义读极多写极少，1h TTL + 写时失效（TASK-008 §6.3 决策 2）

_redis_client: Any = None
_redis_unavailable = False


# ─────────────────────────────────────────────────────────────────────
# Redis 客户端（进程级懒加载；不可用标记后不再反复重连放大延迟）
# ─────────────────────────────────────────────────────────────────────
def _redis():
    global _redis_client, _redis_unavailable
    if _redis_unavailable:
        return None
    if _redis_client is None:
        try:
            import redis
            from django.conf import settings

            _redis_client = redis.Redis.from_url(
                settings.REDIS_URL,
                decode_responses=True,
                socket_timeout=1,
                socket_connect_timeout=1,
            )
            _redis_client.ping()
        except Exception as exc:  # noqa: BLE001 —— 降级路径：任何 Redis 故障都不得阻断主流程
            _redis_unavailable = True
            logger.warning("field_schema.redis_unavailable degrade=direct_query exc=%s", exc)
            return None
    return _redis_client


def reset_redis_state() -> None:
    """测试辅助：重置进程级 Redis 状态（每次用例独立判定可用性）。"""
    global _redis_client, _redis_unavailable
    _redis_client = None
    _redis_unavailable = False


# ─────────────────────────────────────────────────────────────────────
# 作用域解析（架构 §3.3）
# ─────────────────────────────────────────────────────────────────────
def resolve_fields(
    project: Project,
    issue_type_id: uuid_module.UUID | None = None,
) -> list[CustomFieldDefinition]:
    """解析某项目（可选某类型）下生效的字段列表。

    全局 ∪ 项目私有，同 key 私有覆盖全局（对标 Ones 两级治理）；
    结果按 (sort_order, created_at) 排序，供表单、筛选器、列设置共同消费。
    """
    definitions = CustomFieldDefinition.objects.filter(
        workspace_id=project.workspace_id,
        is_active=True,
        deleted_at__isnull=True,
    ).filter(Q(project__isnull=True) | Q(project_id=project.id))

    merged: dict[str, CustomFieldDefinition] = {}
    for d in sorted(definitions, key=lambda x: (x.sort_order, x.created_at)):
        existing = merged.get(d.field_key)
        if existing is None or d.project_id is not None:
            merged[d.field_key] = d

    result = list(merged.values())
    if issue_type_id is not None:
        type_key = str(issue_type_id)
        result = [d for d in result if not d.applicable_types or type_key in d.applicable_types]
    return sorted(result, key=lambda d: (d.sort_order, d.created_at))


# ─────────────────────────────────────────────────────────────────────
# Schema 序列化（filterable/sortable/groupable 后端推导，契约冻结条款 2）
# ─────────────────────────────────────────────────────────────────────
#: 多值类型不可排序（值是数组，无全序语义）
_NOT_SORTABLE_TYPES = CustomFieldDefinition.MULTI_VALUE_TYPES
#: 高基数类型不可分组（text/textarea/number/url/currency/date/auto_increment）
_GROUPABLE_TYPES = frozenset({"select", "multi_select", "member", "member_multi", "checkbox"})


def serialize_definition(d: CustomFieldDefinition) -> dict:
    """Schema API / 管理 CRUD 共用的定义序列化（custom[] 项结构，§4.2.1 契约冻结）。"""
    options = sorted(d.options or [], key=lambda o: o.get("sort_order", 0))
    return {
        "id": str(d.id),
        "key": d.field_key,
        "name": d.name,
        "type": d.field_type,
        "required": d.is_required,
        "description": d.description,
        "scope": "project" if d.project_id else "global",
        "project_id": str(d.project_id) if d.project_id else None,
        "sort_order": d.sort_order,
        "default_value": d.default_value,
        "options": options,
        "applicable_types": d.applicable_types or [],
        "filterable": True,
        "sortable": d.field_type not in _NOT_SORTABLE_TYPES,
        "groupable": d.field_type in _GROUPABLE_TYPES,
        "indexed": d.is_indexed,
    }


def _builtin_schema(project: Project) -> list[dict]:
    """内置字段 schema（硬编码常量 + 项目 states 动态 options，架构 §5.1 尾注）。"""
    from plane.db.models import Issue

    states = State.objects.filter(project=project, deleted_at__isnull=True).order_by("sort_order", "created_at")
    priority_colors = {"urgent": "#EF4444", "high": "#F97316", "medium": "#F59E0B", "low": "#3B82F6", "none": "#6B7280"}
    return [
        {"key": "name", "name": "标题", "type": "text",
         "filterable": True, "sortable": True, "groupable": False},
        {"key": "state", "name": "状态", "type": "select_ref",
         "filterable": True, "sortable": True, "groupable": True,
         "options": [{"label": s.name, "value": str(s.id), "color": s.color, "group": s.group} for s in states]},
        {"key": "priority", "name": "优先级", "type": "select",
         "filterable": True, "sortable": True, "groupable": True,
         "options": [{"label": label, "value": value, "color": priority_colors[value]}
                     for value, label in Issue.Priority.choices]},
        {"key": "assignees", "name": "负责人", "type": "member_multi",
         "filterable": True, "sortable": False, "groupable": True},
        {"key": "labels", "name": "标签", "type": "multi_select_ref",
         "filterable": True, "sortable": False, "groupable": True},
        {"key": "target_date", "name": "截止时间", "type": "date",
         "filterable": True, "sortable": True, "groupable": False},
    ]


def build_field_schema(
    project: Project,
    issue_type_id: uuid_module.UUID | None = None,
) -> dict:
    """组装 {builtin[], custom[]}（含 issue_type 过滤）—— ETag 与缓存共用同一载荷。"""
    cached = get_cached_schema(project)
    type_key = str(issue_type_id) if issue_type_id is not None else None
    custom = [
        item for item in cached
        if not type_key or not item.get("applicable_types") or type_key in item["applicable_types"]
    ]
    return {"builtin": _builtin_schema(project), "custom": custom}


def schema_etag(payload: dict) -> str:
    """ETag = 载荷 md5 摘要（弱校验 W/），定义变更（含 options/sort_order）即失效。"""
    digest = hashlib.md5(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()
    return f'W/"cfd-v1-{digest}"'


# ─────────────────────────────────────────────────────────────────────
# 缓存读写与主动失效（BR-13）
# ─────────────────────────────────────────────────────────────────────
def get_cached_schema(project: Project) -> list[dict]:
    """读缓存（miss 时回源 + 写入 TTL 3600）；Redis 不可用时每次直查。"""
    key = FIELD_SCHEMA_CACHE_KEY.format(workspace_id=project.workspace_id, project_id=project.id)
    client = _redis()
    if client is not None:
        try:
            cached = client.get(key)
            if cached is not None:
                return json.loads(cached)
        except Exception as exc:  # noqa: BLE001 —— 降级直查
            logger.warning("field_schema.cache_get_failed degrade=direct_query key=%s exc=%s", key, exc)
    data = [serialize_definition(d) for d in resolve_fields(project)]
    if client is not None:
        try:
            client.set(key, json.dumps(data, ensure_ascii=False), ex=FIELD_SCHEMA_CACHE_TTL)
        except Exception as exc:  # noqa: BLE001 —— 写失败不影响结果正确性
            logger.warning("field_schema.cache_set_failed key=%s exc=%s", key, exc)
    return data


def invalidate_field_schema_cache(instance: CustomFieldDefinition, **kwargs) -> None:
    """post_save/post_delete 信号接收器（BR-13）：定义变更 → 主动失效。

    - 项目私有字段 → 删单 key；
    - 全局字段 → SCAN ``field_schema:v1:{ws}:*`` 模式匹配该 WS 全部项目 key，
      **每批命中立即删除**（等价于「续 1 次 TTL」的更强口径：键直接消失，
      SCAN 游标漂移窗口内不存在可读到的旧值）。
    """
    client = _redis()
    if client is None:
        return
    try:
        if instance.project_id:
            client.delete(
                FIELD_SCHEMA_CACHE_KEY.format(workspace_id=instance.workspace_id, project_id=instance.project_id)
            )
        else:
            pattern = f"field_schema:v1:{instance.workspace_id}:*"
            batch: list[str] = []
            for hit in client.scan_iter(match=pattern, count=100):
                batch.append(hit)
                if len(batch) >= 100:  # 逐批直接删除，不留续期窗口
                    client.delete(*batch)
                    batch = []
            if batch:
                client.delete(*batch)
    except Exception as exc:  # noqa: BLE001 —— 失效失败不阻断写路径（TTL 1h 兜底）
        logger.warning("field_schema.cache_invalidate_failed id=%s exc=%s", instance.pk, exc)


def register_signals() -> None:
    """在 DbConfig.ready() 中调用 —— 保证信号只注册一次。"""
    from django.db.models.signals import post_delete, post_save

    post_save.connect(
        invalidate_field_schema_cache, sender=CustomFieldDefinition, dispatch_uid="cfd_invalidate_save"
    )
    post_delete.connect(
        invalidate_field_schema_cache, sender=CustomFieldDefinition, dispatch_uid="cfd_invalidate_delete"
    )


def fields_for_scope(workspace_id, project_id=None, *, active_only: bool = True) -> QuerySet[CustomFieldDefinition]:
    """管理列表查询：全局 / 项目私有 / 全部（?scope= 复用）。"""
    qs = CustomFieldDefinition.objects.filter(workspace_id=workspace_id, deleted_at__isnull=True)
    if active_only:
        qs = qs.filter(is_active=True)
    if project_id is None:
        qs = qs.filter(project__isnull=True)
    else:
        qs = qs.filter(project_id=project_id)
    return qs.order_by("sort_order", "created_at")
