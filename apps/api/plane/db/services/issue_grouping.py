"""分组端点泛化（BOARD-003 §4.3.2 —— DimensionGroupView 的服务层核心）。

铁律（§2.3 / BR-06）：
  - 分组列从**配置源**生成（State 表 / 优先级枚举固定表 / active 成员表 / Label 表 /
    cf options），禁止 SELECT DISTINCT 扫 issues；
  - 空组恒在（0 计数列保留）；
  - assignee / label / cf 三维度有 ``__none__`` 哨兵列（未指派 / 无标签 / 未填值），
    恒在且排最末；state 无（状态必填兜底）、priority 的 none 是合法枚举值列。
契约（BR-16，逐条沿 BOARD-002）：响应 ``data`` 键 = 裸列值键、组内 25 +
total_results（筛选后）+ unfiltered_total_results（筛选前）、组内序固定。
"""
from __future__ import annotations

import uuid as uuid_module

from django.db.models import Count, Q, QuerySet

from plane.base.exception import AppException
from plane.db.models import CustomFieldDefinition, Label, ProjectMember, State
from plane.db.services.field_schema import get_cached_schema
from plane.db.services.view_service import GROUPABLE_BUILTIN

NONE_KEY = "__none__"
#: 优先级五档固定列（枚举固定表，BR-06；none 为合法枚举值列——非哨兵）
PRIORITY_COLUMNS = [
    ("urgent", "紧急", "#EF4444"),
    ("high", "高", "#F97316"),
    ("medium", "中", "#F59E0B"),
    ("low", "低", "#3B82F6"),
    ("none", "无", "#6B7280"),
]


def dimension_definition(project, dimension: str) -> CustomFieldDefinition | None:
    """cf_* 维度取定义（groupable 校验在 resolve_dimension 层）。"""
    schema = {item["key"]: item for item in get_cached_schema(project)}
    item = schema.get(dimension)
    if item is None:
        return None
    return CustomFieldDefinition.objects.filter(
        workspace_id=project.workspace_id,
        field_key=dimension,
        deleted_at__isnull=True,
    ).first()


def resolve_dimension(project, dimension: str) -> str:
    """group_by 白名单校验：内置四维直通，cf_* 须为 groupable 生效字段。

    Schema 键别名归一（state/assignees/labels → state_id/assignee_id/label_id，
    BR-05 / UT-19 口径）也在此收口。非法 → 400 VALIDATION_INVALID_PARAM。
    """
    alias = {"state": "state_id", "assignees": "assignee_id", "labels": "label_id"}
    dimension = alias.get(dimension, dimension)
    if dimension in GROUPABLE_BUILTIN:
        return dimension
    if dimension.startswith("cf_"):
        schema = {item["key"]: item for item in get_cached_schema(project)}
        item = schema.get(dimension)
        if item is None:
            raise AppException(
                "VALIDATION_INVALID_PARAM",
                message="查询参数不合法",
                details=[
                    {
                        "field": "group_by",
                        "code": "INVALID",
                        "message": f"{dimension} 不是生效的自定义字段",
                    }
                ],
            )
        if not item.get("groupable"):
            raise AppException(
                "VALIDATION_INVALID_PARAM",
                message="查询参数不合法",
                details=[
                    {
                        "field": "group_by",
                        "code": "INVALID",
                        "message": f"{dimension} 为 {item.get('type')} 类型，不支持分组",
                    }
                ],
            )
        return dimension
    raise AppException(
        "VALIDATION_INVALID_PARAM",
        message="查询参数不合法",
        details=[{"field": "group_by", "code": "INVALID", "message": f"不支持的分组维度 {dimension}"}],
    )


def get_group_columns(project, dimension: str) -> list[dict]:
    """列集合（仅服务端枚举与过滤用；响应不内嵌组元数据——列头由前端配置源渲染，BR-16）。"""
    if dimension == "state_id":
        # 无 __none__（状态必填，默认态兜底）
        return [
            {"key": str(s.id), "label": s.name, "color": s.color}
            for s in State.objects.filter(project=project, deleted_at__isnull=True).order_by("sort_order", "created_at")
        ]
    if dimension == "priority":
        return [{"key": k, "label": label, "color": color} for k, label, color in PRIORITY_COLUMNS]
    if dimension == "assignee_id":
        members = (
            ProjectMember.objects.filter(project=project, is_active=True, deleted_at__isnull=True)
            .select_related("member")
            .order_by("created_at")
        )
        cols = [
            {"key": str(m.member_id), "label": m.member.display_name, "color": None} for m in members
        ]
        return cols + [{"key": NONE_KEY, "label": "未指派", "color": "#9CA3AF", "none": True}]
    if dimension == "label_id":
        labels = Label.objects.filter(project=project, deleted_at__isnull=True).order_by("sort_order", "created_at")
        cols = [{"key": str(lb.id), "label": lb.name, "color": lb.color} for lb in labels]
        return cols + [{"key": NONE_KEY, "label": "无标签", "color": "#9CA3AF", "none": True}]
    # cf_*：options 生成器（TASK-008 §4.3.6 kanban_groups 的内联兑现——零 DISTINCT）
    definition = dimension_definition(project, dimension)
    if definition is None:
        return []
    options = sorted(definition.options or [], key=lambda o: o.get("sort_order", 0))
    cols = [
        {"key": str(o.get("value")), "label": o.get("label"), "color": o.get("color")} for o in options
    ]
    return cols + [{"key": NONE_KEY, "label": "未填值", "color": "#9CA3AF", "none": True, "cf": True}]


def group_filter_q(dimension: str, column_key: str) -> Q:
    """列键 → 组过滤器（与 get_group_columns 的列集合一一对应）。"""
    if column_key == NONE_KEY:
        if dimension == "assignee_id":
            return Q(issue_assignees__isnull=True)
        if dimension == "label_id":
            return Q(issue_labels__isnull=True)
        # cf_*：未填值 = 无该键（可拖入 = 删键，BR-14）
        return Q(**{f"custom_fields__{dimension}__isnull": True})
    if dimension == "state_id":
        return Q(state_id=column_key)
    if dimension == "priority":
        return Q(priority=column_key)
    if dimension == "assignee_id":
        return Q(issue_assignees__assignee_id=column_key)
    if dimension == "label_id":
        return Q(issue_labels__label_id=column_key)
    # cf_* select：@> 单值包含（GIN 友好）
    return Q(**{f"custom_fields__{dimension}": column_key})


def column_counts(base_qs: QuerySet, dimension: str, project) -> dict[str, int]:
    """单维度一次 GROUP BY 取每列计数（M2M 维度经中间表；__none__ 差集补齐）。

    SQL 预算：state/priority/cf 各 1 条；assignee/label 2 条（列计数 + 有值差集）。
    cf 维度选项外的历史值行不入任何列（选项即列域，TASK-011 联调口径）。
    """
    if dimension in ("state_id", "priority"):
        field = "state_id" if dimension == "state_id" else "priority"
        counts = dict(
            base_qs.order_by().values_list(field).annotate(n=Count("id", distinct=True))
        )
        return {str(k): int(v) for k, v in counts.items()}
    if dimension in ("assignee_id", "label_id"):
        through = "issue_assignees__assignee_id" if dimension == "assignee_id" else "issue_labels__label_id"
        counts = {
            str(k): int(v)
            for k, v in base_qs.order_by()
            .values_list(through)
            .annotate(n=Count("id", distinct=True))
            if k is not None
        }
        total = base_qs.count()
        with_any = base_qs.filter(**{through + "__isnull": False}).count()
        counts[NONE_KEY] = max(total - with_any, 0)
        return counts
    # cf_*：JSONB 键提取分组（->> 语义；未填键为 None → __none__）
    key_path = f"custom_fields__{dimension}"
    counts = {
        str(k): int(v)
        for k, v in base_qs.order_by().values_list(key_path).annotate(n=Count("id", distinct=True))
        if k is not None
    }
    definition = dimension_definition(project, dimension)
    valid_keys = {str(o.get("value")) for o in (definition.options or [])} if definition else set()
    counts = {k: v for k, v in counts.items() if k in valid_keys}
    total = base_qs.count()
    filled = sum(counts.values())
    counts[NONE_KEY] = max(total - filled, 0)
    return counts


def in_column_ordering(dimension: str) -> tuple[str, ...]:
    """组内序固定（§2.3 铁律 2）：priority 维与拖拽序解耦（语义权重），其余沿拖拽序。"""
    if dimension == "priority":
        return ("-priority", "sort_order", "-created_at", "-id")
    return ("sort_order", "-created_at", "-id")


def drop_filter_keys(dimension: str) -> tuple[str, ...]:
    """动态互斥：分组维度对应的 URL 筛选参数出域（BOARD-002「减 state_id」的泛化）。"""
    return {
        "state_id": ("state_id",),
        "priority": ("priority",),
        "assignee_id": ("assignee_ids",),
        "label_id": ("label_id", "label_ids"),
    }.get(dimension, ())


def parse_uuid_or_none(raw: str):
    try:
        return uuid_module.UUID(str(raw))
    except (ValueError, AttributeError, TypeError):
        return None
