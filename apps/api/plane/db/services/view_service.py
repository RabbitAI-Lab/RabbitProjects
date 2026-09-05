"""视图校验服务（BOARD-003 §4.3.1 —— 保存防线 BR-01~09 + 读取降级 BR-08）。

保存防线在写路径拦截非法载荷；读取降级在字段停用后剔除引用并回退
（resolve_view 供分组端点 / 列表端点消费，TASK-011 编译器同族接管占位符解析）。
"""
from __future__ import annotations

from plane.base.exception import AppException
from plane.db.models import IssueView
from plane.db.services.field_schema import get_cached_schema
from plane.settings.features import MAX_VIEWS_PER_PROJECT

#: 分组维度白名单：内置四维 + Schema groupable=true 的 select 类 cf_*（BR-05）
GROUPABLE_BUILTIN = frozenset({"state_id", "priority", "assignee_id", "label_id"})
#: 内置筛选字段域（TASK-011 BUILTIN_FIELD_PATHS 的扁平视图口径；编译器落地后统一收编）
BUILTIN_FILTER_FIELDS = frozenset(
    {
        "name",
        "state",
        "state.group",
        "issue_type",
        "priority",
        "assignees",
        "labels",
        "created_by",
        "start_date",
        "target_date",
        "created_at",
        "estimate",
        "sequence_id",
        "parent",
        "blocked",
    }
)
#: P2 扁平条件数上限（BR-07；TASK-011 放开嵌套后仍保留 20 节点全局上限）
FLAT_MAX_CONDITIONS = 20
#: 单值列表长度上限（api-conventions §5.3 MAX_IN_VALUES）
MAX_IN_VALUES = 50
#: 卡片开关固定 7 项（§3.3 同一集合；cf_* 动态追加，未知键静默剔除 BR-09）
CARD_FIELD_KEYS = frozenset(
    {"labels", "sub_issues", "attachments", "estimate", "priority", "timer", "target_date"}
)
#: display_props.icon 取值域（§3.4 八枚 emoji 预设）
ICON_POOL = frozenset({"✨", "📦", "🐛", "👤", "📅", "🧪", "🔥", "🚒"})
#: display_props 顶层键域（未知键静默忽略——读时配置的向前兼容）
DISPLAY_PROP_KEYS = frozenset(
    {"icon", "group_by", "order_by", "columns", "card_fields", "show_empty_groups", "sub_group_by"}
)


def _schema_index(project) -> dict[str, dict]:
    """Schema key → 定义项（自定义字段，get_cached_schema 返回的 custom[] 项）。"""
    return {item["key"]: item for item in get_cached_schema(project)}


def _cf_active(schema_index: dict[str, dict], key: str) -> bool:
    item = schema_index.get(key)
    return bool(item)


def _groupable(schema_index: dict[str, dict], group_by: str) -> bool:
    if group_by in GROUPABLE_BUILTIN:
        return True
    item = schema_index.get(group_by)
    return bool(item and item.get("groupable"))


def _validate_flat_filters(filters: dict, *, schema_index: dict[str, dict]) -> None:
    """BR-07：P2 仅支持单层 AND 的扁平条件树（TASK-011 放开为 ≤3 层嵌套）。"""
    if filters in (None, {}):
        return
    if not isinstance(filters, dict):
        raise AppException(
            "VALIDATION_ERROR",
            message="请求参数校验失败",
            details=[{"field": "filters", "code": "INVALID", "message": "filters 必须为对象"}],
        )
    op = filters.get("op", "AND")
    if op != "AND":
        raise AppException(
            "VALIDATION_ERROR",
            message="请求参数校验失败",
            details=[
                {
                    "field": "filters",
                    "code": "INVALID",
                    "message": "嵌套条件组将在组合筛选器版本开放，当前仅支持单层 AND",
                }
            ],
        )
    conditions = filters.get("conditions", [])
    if not isinstance(conditions, list):
        raise AppException(
            "VALIDATION_ERROR",
            message="请求参数校验失败",
            details=[{"field": "filters", "code": "INVALID", "message": "conditions 必须为数组"}],
        )
    if len(conditions) > FLAT_MAX_CONDITIONS:
        raise AppException(
            "VALIDATION_ERROR",
            message="请求参数校验失败",
            details=[
                {
                    "field": "filters",
                    "code": "TOO_MANY",
                    "message": f"条件数上限 {FLAT_MAX_CONDITIONS}（当前 {len(conditions)}）",
                }
            ],
        )
    for cond in conditions:
        if not isinstance(cond, dict) or "op" in cond or "conditions" in cond:
            raise AppException(
                "VALIDATION_ERROR",
                message="请求参数校验失败",
                details=[
                    {
                        "field": "filters",
                        "code": "INVALID",
                        "message": "嵌套条件组将在组合筛选器版本开放，当前仅支持单层 AND",
                    }
                ],
            )
        field = cond.get("field")
        operator = cond.get("operator")
        value = cond.get("value")
        if not isinstance(field, str) or not field:
            raise AppException(
                "VALIDATION_ERROR",
                message="请求参数校验失败",
                details=[{"field": "filters", "code": "INVALID", "message": "条件缺少 field"}],
            )
        if field not in BUILTIN_FILTER_FIELDS and not (
            field.startswith("cf_") and _cf_active(schema_index, field)
        ):
            raise AppException(
                "VALIDATION_ERROR",
                message="请求参数校验失败",
                details=[{"field": "filters", "code": "INVALID", "message": f"未知字段 {field}"}],
            )
        if not isinstance(operator, str) or not operator:
            raise AppException(
                "VALIDATION_ERROR",
                message="请求参数校验失败",
                details=[{"field": "filters", "code": "INVALID", "message": f"条件 {field} 缺少 operator"}],
            )
        if value is None and operator not in ("is_empty", "is_not_empty"):
            raise AppException(
                "VALIDATION_ERROR",
                message="请求参数校验失败",
                details=[{"field": "filters", "code": "INVALID", "message": f"条件 {field} 缺少 value"}],
            )
        if value is not None:
            if not isinstance(value, list):
                raise AppException(
                    "VALIDATION_ERROR",
                    message="请求参数校验失败",
                    details=[{"field": "filters", "code": "INVALID", "message": f"条件 {field} 的 value 必须为数组"}],
                )
            if len(value) > MAX_IN_VALUES:
                raise AppException(
                    "VALIDATION_ERROR",
                    message="请求参数校验失败",
                    details=[
                        {
                            "field": "filters",
                            "code": "TOO_MANY",
                            "message": f"条件 {field} 的取值数上限 {MAX_IN_VALUES}",
                        }
                    ],
                )


def _validate_group_by(schema_index: dict[str, dict], group_by) -> None:
    """BR-05：group_by ∈ groupable 白名单（None 合法 = 不分组）。"""
    if group_by is None:
        return
    if not isinstance(group_by, str) or not _groupable(schema_index, group_by):
        raise AppException(
            "VALIDATION_ERROR",
            message="请求参数校验失败",
            details=[
                {
                    "field": "display_props.group_by",
                    "code": "INVALID",
                    "message": f"{group_by} 不在可分组字段白名单内",
                }
            ],
        )


def _sanitize_card_fields(display_props: dict, *, schema_index: dict[str, dict]) -> None:
    """BR-09：card_fields 键域 = 固定 7 项 + 生效 cf_*，未知键静默剔除（就地修改）。"""
    card_fields = display_props.get("card_fields")
    if card_fields is None:
        return
    if not isinstance(card_fields, dict):
        raise AppException(
            "VALIDATION_ERROR",
            message="请求参数校验失败",
            details=[{"field": "display_props.card_fields", "code": "INVALID", "message": "card_fields 必须为对象"}],
        )
    known_cf = {k for k in schema_index if _cf_active(schema_index, k)}
    display_props["card_fields"] = {
        k: bool(v) for k, v in card_fields.items() if k in CARD_FIELD_KEYS or k in known_cf
    }


def validate_view_payload(*, project, payload: dict, instance: IssueView | None) -> None:
    """保存防线：BR-01~09 全景（数量上限 / access 白名单 / layout 白名单 /
    filters 扁平结构 / group_by 在 groupable 域 / card_fields 键域 / icon 预设）。"""
    schema_index = _schema_index(project)
    if instance is None:
        count = IssueView.objects.filter(project=project, deleted_at__isnull=True).count()
        if count >= MAX_VIEWS_PER_PROJECT:  # BR-02
            raise AppException(
                "RESOURCE_LIMIT_EXCEEDED",
                message="视图数量已达上限",
                details=[
                    {
                        "field": "name",
                        "code": "LIMIT",
                        "message": f"单项目最多 {MAX_VIEWS_PER_PROJECT} 个视图（含内置）",
                    }
                ],
            )
    if payload.get("access", "personal") != "personal":  # BR-01（P3 开 shared）
        raise AppException(
            "VALIDATION_ERROR",
            message="请求参数校验失败",
            details=[{"field": "access", "code": "INVALID", "message": "共享视图将在团队版开放"}],
        )
    if "layout" in payload and payload["layout"] not in IssueView.Layout.values:
        raise AppException(
            "VALIDATION_ERROR",
            message="请求参数校验失败",
            details=[{"field": "layout", "code": "INVALID", "message": "layout 须为 list/kanban/gantt/table"}],
        )

    display_props = payload.get("display_props")
    if display_props is not None:
        if not isinstance(display_props, dict):
            raise AppException(
                "VALIDATION_ERROR",
                message="请求参数校验失败",
                details=[{"field": "display_props", "code": "INVALID", "message": "display_props 必须为对象"}],
            )
        # 键域过滤 + card_fields 净化后写回 payload（调用方落库取净化结果）
        display_props = {k: v for k, v in display_props.items() if k in DISPLAY_PROP_KEYS}
        payload["display_props"] = display_props
        icon = display_props.get("icon")
        if icon is not None and icon not in ICON_POOL:
            raise AppException(
                "VALIDATION_ERROR",
                message="请求参数校验失败",
                details=[
                    {
                        "field": "display_props.icon",
                        "code": "INVALID",
                        "message": "icon 取值须为八枚 emoji 预设之一（✨📦🐛👤📅🧪🔥🚒）",
                    }
                ],
            )
        columns = display_props.get("columns")
        if columns is not None and (not isinstance(columns, list) or not all(isinstance(c, str) for c in columns)):
            raise AppException(
                "VALIDATION_ERROR",
                message="请求参数校验失败",
                details=[{"field": "display_props.columns", "code": "INVALID", "message": "columns 必须为字符串数组"}],
            )
        _validate_group_by(schema_index, display_props.get("group_by"))
        _sanitize_card_fields(display_props, schema_index=schema_index)

    _validate_flat_filters(payload.get("filters") or {}, schema_index=schema_index)  # BR-07


def resolve_view(view: IssueView, *, project, user) -> tuple[dict, dict | None]:
    """读取降级：filters / group_by 引用的字段停用时剔除并回退（BR-08 / §2.6）。

    返回 (生效 filters, degraded 提示)；@me 占位符在编译期由 TASK-011 的编译器
    解析（本迭代 assignees-in-@me 由扁平编译子集展开）。
    """
    schema_index = _schema_index(project)
    degraded: dict | None = None

    conditions = []
    for cond in (view.filters or {}).get("conditions", []):
        field = cond.get("field", "")
        if field.startswith("cf_") and not _cf_active(schema_index, field):
            degraded = degraded or {"filters": f"{field} 已停用，条件已剔除"}
            continue
        conditions.append(cond)

    group_by = (view.display_props or {}).get("group_by") or "state_id"
    if group_by != "state_id" and not _groupable(schema_index, group_by):  # BR-05 回退
        degraded = degraded or {}
        degraded["group_by"] = f"{group_by} 已停用，已回退为按状态分组"
        group_by = "state_id"
    return {"op": "AND", "conditions": conditions}, degraded
