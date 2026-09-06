"""视图校验服务（BOARD-003 §4.3.1 —— 保存防线 BR-01~09 + 读取降级 BR-08）。

保存防线在写路径拦截非法载荷；读取降级在字段停用后剔除引用并回退
（resolve_view 供分组端点 / 列表端点消费）。TASK-011（T3-07）起：
- filters 校验由 ``app/filters/compiler.validate_dsl`` 全量 DSL 校验超集接管
  （嵌套 ≤3 放开、扁平形态向后兼容），P2 扁平校验退役；
- 条件编译由 ``app/filters/compiler.compile`` 收编，``compile_view_filters``
  降级为薄包装转发（保持旧 import 兼容）。
"""
from __future__ import annotations

from django.db.models import Q

from plane.base.exception import AppException
from plane.db.models import IssueView
from plane.db.services.field_schema import get_cached_schema
from plane.settings.features import MAX_VIEWS_PER_PROJECT

#: 分组维度白名单：内置四维 + Schema groupable=true 的 select 类 cf_*（BR-05）
GROUPABLE_BUILTIN = frozenset({"state_id", "priority", "assignee_id", "label_id"})
#: 卡片开关固定 7 项（§3.3 同一集合；cf_* 动态追加，未知键静默剔除 BR-09）
CARD_FIELD_KEYS = frozenset(
    {"labels", "sub_issues", "attachments", "estimate", "priority", "timer", "target_date"}
)
#: display_props.icon 取值域（§3.4 八枚 emoji 预设）
ICON_POOL = frozenset({"✨", "📦", "🐛", "👤", "📅", "🧪", "🔥", "🚒"})
#: display_props 顶层键域（未知键静默忽略——读时配置的向前兼容）。
#: collapsed 为 GANTT-001 BR-10 增补：甘特折叠状态持久化（issue id 字符串数组）。
DISPLAY_PROP_KEYS = frozenset(
    {"icon", "group_by", "order_by", "columns", "card_fields", "show_empty_groups", "sub_group_by",
     "collapsed"}
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


def _validate_filters(filters: dict, *, project, user) -> None:
    """BR-01~04：filters 全量 DSL 校验（TASK-011 超集接管 P2 扁平校验——
    嵌套 ≤3 / 条件 ≤20 / 白名单 / 操作符 × 类型 / 值域，扁平形态向后兼容）。"""
    from plane.app.filters.compiler import validate_dsl

    validate_dsl(filters, project=project, user=user)


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


def validate_view_payload(
    *, project, payload: dict, instance: IssueView | None, user=None
) -> None:
    """保存防线：BR-01~09 全景（数量上限 / access 白名单 / layout 白名单 /
    filters 全量 DSL 校验（BR-09 复跑 BR-01~04）/ group_by 在 groupable 域 /
    card_fields 键域 / icon 预设）。user 供 DSL 值域校验锚点（@me 编译期解析）。"""
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
        collapsed = display_props.get("collapsed")  # GANTT-001 BR-10：折叠状态持久化
        if collapsed is not None and (
            not isinstance(collapsed, list) or not all(isinstance(c, str) for c in collapsed)
        ):
            raise AppException(
                "VALIDATION_ERROR",
                message="请求参数校验失败",
                details=[{"field": "display_props.collapsed", "code": "INVALID",
                          "message": "collapsed 必须为字符串数组（折叠的 issue id）"}],
            )
        _validate_group_by(schema_index, display_props.get("group_by"))
        _sanitize_card_fields(display_props, schema_index=schema_index)

    _validate_filters(payload.get("filters") or {}, project=project, user=user)


def _prune_inactive_cf(node: dict, schema_index: dict[str, dict], flags: list[str]) -> dict | None:
    """递归剔除引用停用字段的条件（BR-08 降级而非报错）；组内条件清空则整组剔除。

    返回 None 表示该节点应剔除；根组恒保留（可能为空 conditions → 无操作）。
    """
    if not ("op" in node or "conditions" in node):
        f = node.get("field", "")
        if f.startswith("cf_") and not _cf_active(schema_index, f):
            flags.append(f"{f} 已停用，条件已剔除")
            return None
        return node
    kept = []
    for child in node.get("conditions") or []:
        if not isinstance(child, dict):
            continue
        pruned = _prune_inactive_cf(child, schema_index, flags)
        if pruned is not None:
            kept.append(pruned)
    return {"op": str(node.get("op", "AND")).upper(), "conditions": kept}


def resolve_view(view: IssueView, *, project, user) -> tuple[dict, dict | None]:
    """读取降级：filters（递归嵌套树）/ group_by 引用的字段停用时剔除并回退
    （BR-08 / §2.6）。返回 (生效 filters 树, degraded 提示)；占位符在编译期由
    TASK-011 编译器解析（compiler.compile，BR-05）。"""
    schema_index = _schema_index(project)
    degraded: dict | None = None

    flags: list[str] = []
    tree = view.filters or {}
    pruned = _prune_inactive_cf(tree, schema_index, flags) if tree else None
    if pruned is None:
        pruned = {"op": "AND", "conditions": []}
    if flags:
        degraded = {"filters": flags[0]}

    group_by = (view.display_props or {}).get("group_by") or "state_id"
    if group_by != "state_id" and not _groupable(schema_index, group_by):  # BR-05 回退
        degraded = degraded or {}
        degraded["group_by"] = f"{group_by} 已停用，已回退为按状态分组"
        group_by = "state_id"
    return pruned, degraded


# ─────────────────────────────────────────────────────────────────────
# 条件编译（读取侧）——TASK-011 全量编译器已收编，此处仅存薄包装
# ─────────────────────────────────────────────────────────────────────
def compile_view_filters(project, user, conditions: list[dict]) -> tuple[Q, dict]:
    """薄包装（TASK-011 T3-07 收编）：转发 ``app/filters/compiler`` 全量编译器。

    旧签名（扁平 conditions 列表 → Q + 原始值 applied 回显）保持兼容；
    列表端点已直接消费 compiler.compile / echo_conditions。
    """
    from plane.app.filters.compiler import CompileContext, echo_conditions
    from plane.app.filters.compiler import compile as compile_dsl

    tree = {"op": "AND", "conditions": list(conditions or [])}
    ctx = CompileContext.build(project=project, user=user)
    return compile_dsl(tree, ctx), echo_conditions(tree)
