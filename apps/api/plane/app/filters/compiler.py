"""筛选 DSL 全量编译器（TASK-011 §4.3 —— 唯一事实源）。

职责（对应规格条款）：
1. ``validate_dsl``：四重校验（BR-01 结构：深度 ≤3 / 条件 ≤20 / 值列表 ≤50 →
   BR-02 字段白名单（探测免疫）→ BR-03 操作符 × 类型（§2.3 超集表）→
   BR-04 值域，cf 值复用 TASK-008 校验器口径）；
2. ``compile``：递归布尔树 → Django Q（逻辑节点 AND/OR；条件节点先占位符解析
   （BR-05，编译期解析、原样持久化）再 cf_/内置分派——cf 走 JSONB（GIN 友好），
   内置走 ORM lookup）；
3. ``merge_containment_conditions``：同层 AND 的多个 cf 等值合并为单 ``@>``
   （BR-07，GIN bitmap AND 一次；语义等价由 UT-09 随机树守护）；
4. ``build_issue_queryset``：三源层级组装（§1.3/BR-06：权限恒最外层 → 项目域 →
   视图 filters → 临时 filters 恒取 AND，BR-12；``distinct()`` 收尾）；
5. ``resolved_applied``：占位符解析后的 applied 回显（BR-17）。

安全边界（BR-02）：``BUILTIN_FIELD_PATHS`` 白名单常量表是内置字段唯一寻址来源，
DSL 无任何语法可达关联遍历——``project__workspace__owner__password`` 式探测在
编译期即不可能；cf 路径同样限定在「已定义且启用中的 ``cf_`` key」。
"""
from __future__ import annotations

import re
import uuid as uuid_module
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from django.db.models import Q, QuerySet
from django.db.models.expressions import RawSQL
from django.utils import timezone

from plane.base.exception import AppException
from plane.utils.exceptions import CustomFieldValidationError, field_error

if TYPE_CHECKING:
    from django.db.models import Model

    from plane.db.models import Project, User

# ─────────────────────────────────────────────────────────────────────
# 上限常量与白名单（§4.3.1；api-conventions.md §5.3 锚点）
# ─────────────────────────────────────────────────────────────────────
MAX_FILTER_DEPTH = 3  # 逻辑嵌套层级上限（根组 = 第 1 层）
MAX_CONDITIONS = 20  # 条件节点总数上限
MAX_IN_VALUES = 50  # 单值列表长度上限（in / not_in / contains_* 等）

#: 内置字段白名单常量表（BR-02）：key → {path, type}。path = ORM 查询路径，
#: type 供「操作符 × 类型」校验（§2.3）。DSL 只能表达过滤、不能表达关联遍历，
#: 本表即全部可达路径。
BUILTIN_FIELD_PATHS: dict[str, dict[str, str]] = {
    "name": {"path": "name", "type": "text"},
    "state": {"path": "state", "type": "select_ref"},
    "state.group": {"path": "state__group", "type": "select"},
    # issue_type 值域：类型 UUID，或 __requirement__ / __bug__ / __test__ 类型名
    # 占位符（编译期按项目内 IssueType.name 解析为 UUID——§4.1.1；不设
    # issue_type.name 点号寻址键，白名单仍只有 issue_type 单键）
    "issue_type": {"path": "issue_type", "type": "select_ref"},
    "priority": {"path": "priority", "type": "select"},
    "assignees": {"path": "assignees__id", "type": "member_multi"},
    "labels": {"path": "labels__id", "type": "multi_select_ref"},
    "created_by": {"path": "created_by", "type": "member"},
    "start_date": {"path": "start_date", "type": "date"},
    "target_date": {"path": "target_date", "type": "date"},
    "created_at": {"path": "created_at", "type": "date"},
    "estimate": {"path": "estimate_minutes", "type": "number"},
    "sequence_id": {"path": "sequence_id", "type": "number"},
    "parent": {"path": "parent", "type": "select_ref"},
    # blocked 是注解列而非物理列：查询装配时注入 _is_blocked =
    # Exists(IssueLink: issue=pk ∧ is_blocked_by ∧ 未删)——与 TASK-005 ?blocked=true
    # 同向同语义（issue 侧镜像行存在 ⇒ pk 被阻塞），编译器映射到该注解名
    "blocked": {"path": "_is_blocked", "type": "checkbox"},
}

#: 操作符 × 类型表（§2.3 超集；Sprint-3 回改口径：url 并入 text 集、
#: auto_increment 扩全数字集、member/member_multi 取并集）
OPERATORS_BY_TYPE: dict[str, frozenset[str]] = {
    "text": frozenset({"contains", "not_contains", "eq", "neq", "is_empty", "is_not_empty"}),
    "textarea": frozenset({"contains", "not_contains", "eq", "neq", "is_empty", "is_not_empty"}),
    "url": frozenset({"contains", "not_contains", "eq", "neq", "is_empty", "is_not_empty"}),
    "number": frozenset({"eq", "neq", "gt", "gte", "lt", "lte", "between", "is_empty"}),
    "auto_increment": frozenset({"eq", "neq", "gt", "gte", "lt", "lte", "between", "is_empty"}),
    "select": frozenset({"in", "not_in", "is_empty", "is_not_empty"}),
    "multi_select": frozenset({"contains_any", "contains_all", "not_contains", "is_empty"}),
    "date": frozenset({"eq", "before", "after", "between", "is_empty"}),
    "member": frozenset({"in", "not_in", "contains_any", "contains_all", "is_empty"}),
    "member_multi": frozenset({"in", "not_in", "contains_any", "contains_all", "is_empty"}),
    "checkbox": frozenset({"eq"}),
    "currency": frozenset({"gte", "lte", "between"}),
    # 内置 FK 族（state / issue_type / parent）——§2.3「state（内置 FK 族）」行
    "select_ref": frozenset({"in", "not_in"}),
    # 内置 M2M 引用族（labels）——UUID 列表语义与 member 族同构（in = 任一命中）
    "multi_select_ref": frozenset({"in", "not_in", "contains_any", "contains_all", "is_empty"}),
}

#: 内置键的操作符收窄覆盖（§2.3 尾两行：内置枚举仅 in/not_in——priority 不继承
#: select 行的 is_empty/is_not_empty）
BUILTIN_OPERATOR_OVERRIDES: dict[str, frozenset[str]] = {
    "priority": frozenset({"in", "not_in"}),
}

#: 类型名占位符 → 项目内 IssueType.name（§4.1.1；编译期解析为 UUID）
TYPE_NAME_PLACEHOLDERS: dict[str, str] = {
    "__requirement__": "需求",
    "__bug__": "缺陷",
    "__test__": "测试",
}

#: 日期快捷占位符（§2.3 date 行）：n 泛化自架构 §5.2 冻结的 next_7_days，n=1~90
NEXT_N_DAYS_RE = re.compile(r"^next_(\d+)_days$")
NEXT_N_DAYS_MIN, NEXT_N_DAYS_MAX = 1, 90

#: BR-07 等值合并的合成条件键（同层 AND 多个 cf eq → 单 @>）
MERGED_CONTAINMENT_FIELD = "__custom_fields__"

_DATE_TOKENS = frozenset({"today", "this_week", "this_month", "overdue"})


# ─────────────────────────────────────────────────────────────────────
# 编译上下文
# ─────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class CompileContext:
    """编译上下文：项目域（cf schema + 类型解析）、当前用户（@me）、时区占位。"""

    project: Project
    user: User
    #: key → schema（内置 = BUILTIN_FIELD_PATHS 条目；cf = get_cached_schema 的
    #: serialize_definition 条目，含 type/options）——占位符解析与合并判定共用
    field_schema: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def build(cls, *, project: Project, user: User) -> CompileContext:
        from plane.db.services.field_schema import get_cached_schema

        schema: dict[str, dict[str, Any]] = {
            k: dict(v) for k, v in BUILTIN_FIELD_PATHS.items()
        }
        for item in get_cached_schema(project):
            schema[item["key"]] = item
        return cls(project=project, user=user, field_schema=schema)


def _is_uuid_str(v: Any) -> bool:
    try:
        uuid_module.UUID(str(v))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


# ─────────────────────────────────────────────────────────────────────
# 错误构造（结构/白名单/操作符 → VALIDATION_INVALID_PARAM；cf 值域 →
# VALIDATION_CUSTOM_FIELD_INVALID——对齐 §2.5 异常表与 api-conventions §5.3）
# ─────────────────────────────────────────────────────────────────────
def _invalid_param(code: str, message: str) -> AppException:
    return AppException(
        "VALIDATION_INVALID_PARAM",
        message="筛选条件不合法",
        details=[{"field": "filters", "code": code, "message": message}],
    )


def _cf_invalid(cf_key: str, code: str, message: str) -> CustomFieldValidationError:
    return CustomFieldValidationError([field_error(cf_key, code, message)])


# ─────────────────────────────────────────────────────────────────────
# 日期占位符（BR-05）：today/this_week/this_month/overdue/next_n_days(n=1~90)
# 口径：django timezone localdate（服务器当前时区）；用户时区口径留待 P3
# 视图治理（需 UserProfile 时区字段，当前无该数据源——注明不实现的原因）。
# ─────────────────────────────────────────────────────────────────────
def _resolve_relative_date(token: str) -> tuple[date | None, date | None]:
    """日期快捷占位符 → (start, end) 闭区间；overdue 上界含今天、无下界。

    ``next_n_days`` 的 n 越界（<1 或 >90）抛 400 VALIDATION_INVALID_PARAM。
    """
    today = timezone.localdate()
    if token == "today":
        return today, today
    if token == "this_week":
        start = today - timedelta(days=today.weekday())
        return start, start + timedelta(days=6)
    if token == "this_month":
        start = today.replace(day=1)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1) - timedelta(days=1)
        else:
            end = start.replace(month=start.month + 1) - timedelta(days=1)
        return start, end
    if token == "overdue":
        return None, today
    if (m := NEXT_N_DAYS_RE.match(token)) is not None:
        n = int(m.group(1))
        if not (NEXT_N_DAYS_MIN <= n <= NEXT_N_DAYS_MAX):
            raise _invalid_param("INVALID", f"{token} 的 n 须在 {NEXT_N_DAYS_MIN}~{NEXT_N_DAYS_MAX} 之间")
        return today, today + timedelta(days=n)
    return None, None


def _is_date_token(v: Any) -> bool:
    return isinstance(v, str) and (v in _DATE_TOKENS or NEXT_N_DAYS_RE.match(v) is not None)


def _date_bounds(raw: Any) -> tuple[date | None, date | None]:
    """date 条件值 → (lo, hi)：元素为 ISO 日期或快捷占位符（占位符天然是区间）。

    between 两元素取 [v0, v1]；单元素（占位符或 ISO）即区间本身。
    非法日期格式抛 400（编译侧防御——保存路径已在 validate_dsl 拦截）。
    """
    items = raw if isinstance(raw, list) else [raw]
    lo: date | None = None
    hi: date | None = None
    for i, item in enumerate(items):
        if item is None:
            continue
        if _is_date_token(item):
            tlo, thi = _resolve_relative_date(item)
        else:
            try:
                d = date.fromisoformat(str(item))
            except (TypeError, ValueError) as err:
                raise _invalid_param("INVALID_DATE", "日期格式须为 YYYY-MM-DD 或快捷占位符") from err
            tlo = thi = d
        if i == 0:
            lo, hi = tlo, thi
        else:
            lo = lo or tlo
            hi = thi or hi
    return lo, hi


# ─────────────────────────────────────────────────────────────────────
# 类型名占位符（§4.1.1）：__requirement__ 等按项目内 IssueType.name 解析 UUID
# ─────────────────────────────────────────────────────────────────────
def _resolve_type_name(project: Project, name_or_placeholder: str) -> str | None:
    """类型名/占位符 → IssueType UUID 字符串；无匹配 → None（条件命中零行）。"""
    from plane.db.models import IssueType

    target: str | None = TYPE_NAME_PLACEHOLDERS.get(name_or_placeholder, name_or_placeholder)
    row = (
        IssueType.objects.filter(
            workspace_id=project.workspace_id, name=target, deleted_at__isnull=True
        )
        .values_list("id", flat=True)
        .first()
    )
    return str(row) if row is not None else None


# ─────────────────────────────────────────────────────────────────────
# 四重校验（BR-01~04）
# ─────────────────────────────────────────────────────────────────────
def _is_logic_node(node: Any) -> bool:
    return isinstance(node, dict) and ("op" in node or "conditions" in node)


def _allowed_operators(field_key: str, schema: dict[str, dict[str, Any]]) -> frozenset[str]:
    ftype = schema[field_key]["type"]
    return BUILTIN_OPERATOR_OVERRIDES.get(field_key, OPERATORS_BY_TYPE.get(ftype, frozenset()))


def _check_structure(node: Any, *, depth: int, budget: list[int]) -> None:
    """BR-01：结构（逻辑/条件节点形态、深度 ≤3、条件 ≤20、值列表 ≤50）。"""
    if not isinstance(node, dict):
        raise _invalid_param("INVALID", "条件节点必须为对象")
    if _is_logic_node(node):
        if depth > MAX_FILTER_DEPTH:
            raise _invalid_param("TOO_DEEP", f"嵌套层级超过 {MAX_FILTER_DEPTH} 层上限")
        logic_op = str(node.get("op", "AND")).upper()
        if logic_op not in ("AND", "OR"):
            raise _invalid_param("INVALID", f"非法逻辑操作符 {node.get('op')}")
        conditions = node.get("conditions", [])
        if conditions is None:
            conditions = []
        if not isinstance(conditions, list):
            raise _invalid_param("INVALID", "conditions 必须为数组")
        for child in conditions:
            _check_structure(child, depth=depth + 1, budget=budget)
        return
    # 条件节点：三键齐全（value 仅 is_empty/is_not_empty 可缺省）
    budget[0] += 1
    if budget[0] > MAX_CONDITIONS:
        raise _invalid_param("TOO_MANY", f"条件节点总数超过 {MAX_CONDITIONS} 上限")
    f = node.get("field")
    op = node.get("operator")
    value = node.get("value")
    if not isinstance(f, str) or not f:
        raise _invalid_param("INVALID", "条件缺少 field")
    if not isinstance(op, str) or not op:
        raise _invalid_param("INVALID", f"条件 {f} 缺少 operator")
    if value is None and op not in ("is_empty", "is_not_empty"):
        raise _invalid_param("INVALID", f"条件 {f} 缺少 value")
    if isinstance(value, list) and len(value) > MAX_IN_VALUES:
        raise _invalid_param("TOO_MANY", f"条件 {f} 的取值数上限 {MAX_IN_VALUES}")


def _scalarize(value: Any, *, field_key: str) -> Any:
    """标量操作符的值归一：允许裸标量或单元素列表（扁平期向后兼容形态）。"""
    if isinstance(value, list):
        if len(value) != 1:
            raise _invalid_param("INVALID", f"条件 {field_key} 需要单值")
        return value[0]
    return value


def _builtin_option_values(field_key: str) -> set[str] | None:
    """内置选项域：priority 枚举 / state.group 组枚举；其余返回 None（非选项域）。"""
    from plane.db.models import Issue, State

    if field_key == "priority":
        return set(Issue.Priority.values)
    if field_key == "state.group":
        return set(State.Group.values)
    return None


def _check_condition_values(
    field_key: str,
    operator: str,
    value: Any,
    *,
    schema: dict[str, dict[str, Any]],
    member_ids: set[Any],
) -> None:
    """BR-04 值域 + 取值形态：select ∈ options、member ∈ 项目成员 ∪ @me、
    日期 ISO/占位符、数字数值、checkbox 布尔——cf 走 VALIDATION_CUSTOM_FIELD_INVALID。"""
    if operator in ("is_empty", "is_not_empty"):
        return
    ftype = schema[field_key]["type"]
    is_cf = field_key.startswith("cf_")

    def err(code: str, message: str) -> AppException:
        # cf 值域错误复用 TASK-008 子码口径（VALIDATION_CUSTOM_FIELD_INVALID）
        return _cf_invalid(field_key, code, message) if is_cf else _invalid_param(code, message)

    # ---- 形态：多值操作符要列表；between 要 1~2 元素；标量操作符要标量 ----
    list_ops = {"in", "not_in", "contains_any", "contains_all"} | (
        {"not_contains"} if ftype == "multi_select" else set()
    )
    if operator in list_ops:
        if not isinstance(value, list):
            raise err("INVALID", f"条件 {field_key} 的 value 必须为数组")
    elif operator == "between":
        if not isinstance(value, list) or len(value) not in (1, 2):
            raise err("INVALID", f"条件 {field_key} 的 between 需要 1~2 个值")
    else:
        value = _scalarize(value, field_key=field_key)

    # ---- 按类型的元素校验 ----
    if ftype in ("text", "textarea", "url"):
        if operator not in ("eq", "neq", "contains", "not_contains"):
            return  # is_empty 族已在入口返回
        if not isinstance(value, str):
            raise err("INVALID", f"条件 {field_key} 的值必须为文本")
    elif ftype in ("number", "auto_increment", "currency"):
        vals = value if isinstance(value, list) else [value]
        for v in vals:
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise err("INVALID", f"条件 {field_key} 的值必须为数字")
    elif ftype == "checkbox":
        if not isinstance(value, bool):
            raise err("INVALID", f"条件 {field_key} 的值必须为布尔值")
    elif ftype in ("select", "multi_select"):
        valid = _builtin_option_values(field_key)
        if valid is None:
            valid = {o.get("value") for o in schema[field_key].get("options", [])}
        for v in value:
            if v not in valid:
                raise err("NOT_A_CHOICE", f"非法选项 {v}（字段 {field_key}）")
    elif ftype == "date":
        vals = value if isinstance(value, list) else [value]
        for v in vals:
            if _is_date_token(v):
                _resolve_relative_date(v)  # n 越界在此拦截（next_91_days → 400）
            else:
                try:
                    date.fromisoformat(str(v))
                except (TypeError, ValueError) as exc:
                    raise err("INVALID_DATE", "日期格式须为 YYYY-MM-DD 或快捷占位符") from exc
    elif ftype in ("member", "member_multi"):
        for v in value:
            if v == "@me":
                continue
            if not _is_uuid_str(v) or (uuid_module.UUID(str(v)) not in member_ids):
                raise err("DOES_NOT_EXIST", f"用户 {v} 不是项目成员")
    elif ftype == "select_ref":
        # state / parent 严格 UUID；issue_type 另接受类型名/占位符（§4.1.1）
        for v in value:
            if field_key == "issue_type":
                if _is_uuid_str(v) or isinstance(v, str):
                    continue
                raise err("INVALID", f"条件 {field_key} 的值必须为字符串")
            if not _is_uuid_str(v):
                raise err("INVALID_UUID", f"UUID 格式非法：{v}（字段 {field_key}）")
    elif ftype == "multi_select_ref":
        for v in value:
            if not _is_uuid_str(v):
                raise err("INVALID_UUID", f"UUID 格式非法：{v}（字段 {field_key}）")


def _check_fields_and_values(
    node: Any,
    *,
    schema: dict[str, dict[str, Any]],
    member_ids: set[Any],
) -> None:
    """BR-02 白名单 → BR-03 操作符 × 类型 → BR-04 值域（递归）。"""
    if not _is_logic_node(node):
        f, op = node.get("field"), node.get("operator")
        if f not in schema:
            # 探测免疫：白名单外一律拒绝（§4.2.2 失败样例口径）
            raise AppException(
                "VALIDATION_INVALID_PARAM",
                message="筛选条件包含未知或不可筛选的字段",
                details=[{"field": "filters", "code": "NOT_A_CHOICE", "message": f"字段 {f} 不可用"}],
            )
        allowed = _allowed_operators(f, schema)
        if op not in allowed:
            raise _invalid_param("NOT_A_CHOICE", f"字段 {f} 不支持操作符 {op}")
        _check_condition_values(f, op, node.get("value"), schema=schema, member_ids=member_ids)
        return
    for child in node.get("conditions") or []:
        _check_fields_and_values(child, schema=schema, member_ids=member_ids)


def _project_member_ids(project: Project) -> set[Any]:
    """值域校验用的项目成员集合：active ProjectMember ∪ 隐式管理员
    （WS OWNER/ADMIN 无成员行时按项目 ADMIN 对待——get_project_or_404 同口径）。"""
    from plane.db.models import ProjectMember, WorkspaceMember
    from plane.db.models.roles import WorkspaceRole

    ids = set(
        ProjectMember.objects.filter(
            project_id=project.id, is_active=True, deleted_at__isnull=True
        ).values_list("member_id", flat=True)
    )
    ids |= set(
        WorkspaceMember.objects.filter(
            workspace_id=project.workspace_id,
            is_active=True,
            deleted_at__isnull=True,
            role__gte=WorkspaceRole.ADMIN,
        ).values_list("member_id", flat=True)
    )
    return ids


def validate_dsl(tree: Any, *, project: Project, user: User | None = None) -> None:
    """四重校验入口（BR-01~04）。``{}`` / None 为合法空树（「全部」裸态）。

    结构/白名单/操作符 → 400 VALIDATION_INVALID_PARAM；cf 值域 →
    400 VALIDATION_CUSTOM_FIELD_INVALID（§2.5 异常表）。user 仅作未来用户级
    占位符扩展的锚点（@me 在值域校验中恒合法，编译期才解析）。
    """
    if tree in (None, {}):
        return
    if not isinstance(tree, dict):
        raise _invalid_param("INVALID", "filters 必须为对象")
    ctx_schema = CompileContext.build(project=project, user=user or _system_user(project)).field_schema
    budget = [0]
    _check_structure(tree, depth=1, budget=budget)
    _check_fields_and_values(tree, schema=ctx_schema, member_ids=_project_member_ids(project))


def _system_user(project: Project) -> User:
    """validate_dsl 无 user 时的占位上下文（仅取 project.created_by，不参与校验）。"""
    from plane.db.models import User

    return User(pk=project.created_by_id)


# ─────────────────────────────────────────────────────────────────────
# 占位符解析（BR-05：编译期解析、原样持久化）
# ─────────────────────────────────────────────────────────────────────
def _resolve_one(
    field_key: str,
    v: Any,
    schema: dict[str, Any],
    project: Project,
    user: User,
    placeholders: dict[str, Any] | None,
) -> Any:
    if v == "@me" and schema["type"] in ("member", "member_multi"):
        if placeholders is not None:
            placeholders.setdefault("@me", f"@{getattr(user, 'display_name', '')}")
        return str(user.id)
    if field_key == "issue_type" and isinstance(v, str) and not _is_uuid_str(v):
        type_id = _resolve_type_name(project, v)
        if placeholders is not None and type_id is not None:
            placeholders.setdefault(v, TYPE_NAME_PLACEHOLDERS.get(v, v))
        return type_id  # None = 无匹配 → 条件命中零行（由调用侧过滤）
    return v


def _resolve_placeholders(
    field_key: str,
    raw: Any,
    schema: dict[str, Any],
    project: Project,
    user: User,
    placeholders: dict[str, Any] | None,
) -> Any:
    if isinstance(raw, list):
        return [
            _resolve_one(field_key, v, schema, project, user, placeholders) for v in raw
        ]
    return _resolve_one(field_key, raw, schema, project, user, placeholders)


# ─────────────────────────────────────────────────────────────────────
# 编译（§4.3.2）
# ─────────────────────────────────────────────────────────────────────
def _or_join(qs: list[Q]) -> Q:
    q = Q()
    for item in qs:
        q |= item
    return q


def _zero_rows() -> Q:
    """类型占位符无匹配 / 空值列表 → 条件命中零行。"""
    return Q(pk__in=[])


_NUMERIC_SQL_OPS = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}


def _cf_compare(
    cf_key: str, op: str, value: Any, ftype: str
) -> Q:
    """cf 大小比较：数字/金额 ::numeric、日期 ISO 文本直比（字典序 = 时间序）；
    恒叠加 has_key 让 GIN 先缩小候选集（架构 §5.3 技巧）。列名限定 issues. 前缀——
    列表查询带计数 annotate 时 issues 可能出现多次（T8-26 同款锚点）。"""
    sql_op = _NUMERIC_SQL_OPS[op]
    if ftype in ("number", "auto_increment"):
        expr, params = f"(issues.custom_fields->>%s)::numeric {sql_op} %s", [cf_key, value]
    elif ftype == "currency":
        expr, params = f"(issues.custom_fields#>>ARRAY[%s,'amount'])::numeric {sql_op} %s", [cf_key, value]
    else:  # date：ISO 字符串文本比较
        expr, params = f"issues.custom_fields->>%s {sql_op} %s", [cf_key, str(value)]
    from django.db.models import BooleanField

    return Q(custom_fields__has_key=cf_key) & Q(
        RawSQL(expr, params, output_field=BooleanField())
    )


def _cf_negated(cf_key: str, positive: Q) -> Q:
    """cf 否定操作符统一口径：键缺失（字段未填）不匹配任何正值，「非 X」自然含未填行
    ——SQL JSON 路径提取对缺失键产生 NULL（~NULL 仍被排除），故先 has_key 再整体取反。"""
    return ~(Q(custom_fields__has_key=cf_key) & positive)


def _m2m_contains_all(field_key: str, value: list) -> Q:
    """内置 M2M「全部命中」：逐值 Exists 子查询 AND。单 Q 内同路径重复条件会让
    Django 复用同一 JOIN 退化为同行矛盾（多值关系陷阱），子查询天然各自独立。"""
    from django.db.models import Exists, OuterRef

    from plane.db.models import IssueAssignee, IssueLabel

    through = IssueAssignee if field_key == "assignees" else IssueLabel
    target = "assignee_id" if field_key == "assignees" else "label_id"
    q = Q()
    for v in value:
        q &= Q(
            Exists(
                through.objects.filter(
                    issue_id=OuterRef("pk"), **{target: v}, deleted_at__isnull=True
                )
            )
        )
    return q


def _compile_custom(cf_key: str, operator: str, value: Any, schema: dict[str, Any]) -> Q:
    """cf_* → JSONB 查询：能用 @>/has_key 就用（走 GIN），比较才用表达式。"""
    ftype = schema["type"]

    if ftype == "date" and operator in ("eq", "before", "after", "between"):
        # 日期族：占位符/ISO 统一解析为 (lo, hi) 后按操作符取界
        # （date 条件在 _compile_condition 侧未做占位符替换，value 即原始 DSL 值）
        lo, hi = _date_bounds(value)
        if lo is None and hi is None:
            return Q()
        match operator:
            case "eq":
                bound = lo if lo is not None else hi
                return Q(**{f"custom_fields__{cf_key}": bound.isoformat()}) if bound else Q()
            case "before":
                # overdue（无下界）→ 含今天；有下界（如 this_week）→ 早于区间起点
                if lo is None:
                    return _cf_compare(cf_key, "lte", hi.isoformat(), ftype) if hi else Q()
                return _cf_compare(cf_key, "lt", lo.isoformat(), ftype)
            case "after":
                return _cf_compare(cf_key, "gt", hi.isoformat(), ftype) if hi else Q()
            case "between":
                q = Q(custom_fields__has_key=cf_key)
                if lo is not None:
                    q &= _cf_compare(cf_key, "gte", lo.isoformat(), ftype)
                if hi is not None:
                    q &= _cf_compare(cf_key, "lte", hi.isoformat(), ftype)
                return q
        return Q()  # pragma: no cover —— mypy 穷尽性兜底

    match operator:
        case "eq":
            return Q(**{f"custom_fields__{cf_key}": value})
        case "neq":
            return _cf_negated(cf_key, Q(**{f"custom_fields__{cf_key}": value}))
        case "in":
            return _or_join([Q(**{f"custom_fields__{cf_key}": v}) for v in value])
        case "not_in":
            return _cf_negated(cf_key, _or_join([Q(**{f"custom_fields__{cf_key}": v}) for v in value]))
        case "contains_any":  # 多值字段任一命中（每分支均可走 GIN）
            return _or_join([Q(**{f"custom_fields__{cf_key}__contains": [v]}) for v in value])
        case "contains_all":  # 单次 @> 即为 AND 语义
            return Q(**{f"custom_fields__{cf_key}__contains": value})
        case "not_contains" if ftype == "multi_select":
            return _cf_negated(
                cf_key, _or_join([Q(**{f"custom_fields__{cf_key}__contains": [v]}) for v in value])
            )
        case "contains":  # 文本族模糊
            return Q(**{f"custom_fields__{cf_key}__icontains": value})
        case "not_contains":
            return _cf_negated(cf_key, Q(**{f"custom_fields__{cf_key}__icontains": value}))
        case "gt" | "gte" | "lt" | "lte":
            return _cf_compare(cf_key, operator, value, ftype)
        case "between":
            return _cf_compare(cf_key, "gte", value[0], ftype) & _cf_compare(
                cf_key, "lte", value[1], ftype
            )
        case "is_empty":  # 空值不落 key（存储纪律）——「为空」= 键不存在
            return ~Q(custom_fields__has_key=cf_key)
        case "is_not_empty":
            return Q(custom_fields__has_key=cf_key)
        case _:
            raise _invalid_param("NOT_A_CHOICE", f"字段类型 {ftype} 不支持操作符 {operator}")


def _date_bounds_with_placeholders(
    raw: Any, placeholders: dict[str, Any] | None
) -> tuple[date | None, date | None]:
    """date 值 → (lo, hi)，占位符同时记录到 resolved_placeholders（BR-17 回显）。"""
    lo, hi = _date_bounds(raw)
    if placeholders is not None:
        for item in (raw if isinstance(raw, list) else [raw]):
            if _is_date_token(item):
                placeholders.setdefault(
                    item, [lo.isoformat() if lo else None, hi.isoformat() if hi else None]
                )
    return lo, hi


def _compile_builtin_date(path: str, operator: str, raw: Any) -> Q:
    """内置日期列（start_date/target_date/created_at）：占位符 → 区间后按操作符取界。"""
    lo, hi = _date_bounds(raw)
    match operator:
        case "eq":
            return Q(**{path: lo or hi})
        case "before":
            if lo is None:  # overdue：含今天
                return Q(**{f"{path}__lte": hi})
            return Q(**{f"{path}__lt": lo})
        case "after":
            return Q(**{f"{path}__gt": hi})
        case "between":
            q = Q()
            if lo is not None:
                q &= Q(**{f"{path}__gte": lo})
            if hi is not None:
                q &= Q(**{f"{path}__lte": hi})
            return q
        case "is_empty":
            return Q(**{f"{path}__isnull": True})
        case _:  # pragma: no cover —— 校验先行，不可达
            raise _invalid_param("NOT_A_CHOICE", f"日期字段不支持操作符 {operator}")


def _compile_builtin(field_key: str, operator: str, value: Any, schema: dict[str, Any]) -> Q:
    """内置字段 → 标准 ORM lookup（B-Tree / JOIN；架构 §5.3 样例）。"""
    path = BUILTIN_FIELD_PATHS[field_key]["path"]
    ftype = schema["type"]

    if ftype in ("member_multi", "multi_select_ref"):
        match operator:
            case "in" | "contains_any":  # 任一命中（同构语义）
                return Q(**{f"{path}__in": value})
            case "not_in":
                return ~Q(**{f"{path}__in": value})
            case "contains_all":  # 全部命中：逐值 Exists 子查询 AND（多值 JOIN 陷阱规避）
                return _m2m_contains_all(field_key, value)
            case "is_empty":
                return Q(**{f"{path}__isnull": True})
            case _:
                raise _invalid_param("NOT_A_CHOICE", f"字段 {field_key} 不支持操作符 {operator}")

    match operator:
        case "in":
            return Q(**{f"{path}__in": value})
        case "not_in":
            return ~Q(**{f"{path}__in": value})
        case "eq":
            return Q(**{path: value})
        case "neq":
            return ~Q(**{path: value})
        case "contains":
            return Q(**{f"{path}__icontains": value})
        case "not_contains":
            return ~Q(**{f"{path}__icontains": value})
        case "gt" | "gte" | "lt" | "lte":
            return Q(**{f"{path}__{operator}": value})
        case "between":
            return Q(**{f"{path}__range": (value[0], value[1])})
        case "is_empty":
            return Q(**{f"{path}__isnull": True})
        case "is_not_empty":
            return Q(**{f"{path}__isnull": False})
        case _:
            raise _invalid_param("NOT_A_CHOICE", f"字段 {field_key} 不支持操作符 {operator}")


def _compile_condition(cond: dict[str, Any], ctx: CompileContext) -> Q:
    field_key = cond.get("field")
    if not isinstance(field_key, str):
        raise _invalid_param("INVALID", "条件缺少 field")
    if field_key == MERGED_CONTAINMENT_FIELD:  # BR-07 合成条件：单 @>（GIN bitmap AND）
        return Q(custom_fields__contains=cond.get("value") or {})
    schema = ctx.field_schema.get(field_key)
    if schema is None or (field_key not in BUILTIN_FIELD_PATHS and not field_key.startswith("cf_")):
        raise AppException(
            "VALIDATION_INVALID_PARAM",
            message="筛选条件包含未知或不可筛选的字段",
            details=[{"field": "filters", "code": "NOT_A_CHOICE", "message": f"字段 {field_key} 不可用"}],
        )
    operator = cond.get("operator")
    if not isinstance(operator, str):
        raise _invalid_param("INVALID", f"条件 {field_key} 缺少 operator")
    ftype = schema["type"]

    # ---- 日期族先走占位符区间解析（占位符只在编译期展开，DSL 原样持久化）----
    if ftype == "date":
        if field_key.startswith("cf_"):
            return _compile_custom(field_key, operator, cond.get("value"), schema)
        return _compile_builtin_date(BUILTIN_FIELD_PATHS[field_key]["path"], operator, cond.get("value"))

    value = _resolve_placeholders(
        field_key, cond.get("value"), schema, ctx.project, ctx.user, None
    )
    # 类型占位符无匹配（None）与空列表 → 零行（不放宽为「匹配全部」）
    if isinstance(value, list):
        value = [v for v in value if v is not None]
        if not value:
            return _zero_rows()
    elif value is None and operator not in ("is_empty", "is_not_empty"):
        return _zero_rows()

    if field_key.startswith("cf_"):
        return _compile_custom(field_key, operator, value, schema)
    return _compile_builtin(field_key, operator, value, schema)


def _mergeable_eq(child: dict[str, Any], schema: dict[str, dict[str, Any]]) -> bool:
    """BR-07 可合并判定：cf eq + 标量值 + 非 date（date eq 值可为占位符，不可入 @>）。"""
    ftype = schema.get(child.get("field", ""), {}).get("type")
    return ftype not in ("date", None) and type(child.get("value")) in (str, int, float, bool)


def _merge_eq_children(
    children: list[Any], schema: dict[str, dict[str, Any]]
) -> list[Any]:
    """同层 AND 的 cf 等值合并（BR-07）：≥2 个可合并条件 → 单 ``__custom_fields__``
    合成条件（编译为单 ``@>``）；同 key 重复等值不合并（保留矛盾语义）。"""
    candidates: list[int] = []
    seen: set[str] = set()
    for idx, child in enumerate(children):
        if (
            isinstance(child, dict)
            and isinstance(child.get("field"), str)
            and child["field"].startswith("cf_")
            and child.get("operator") == "eq"
            and child["field"] not in seen
            and _mergeable_eq(child, schema)
        ):
            candidates.append(idx)
            seen.add(child["field"])
    if len(candidates) < 2:
        return children
    payload = {children[i]["field"]: children[i]["value"] for i in candidates}
    dropped = set(candidates)
    out = [c for i, c in enumerate(children) if i not in dropped]
    out.append({"field": MERGED_CONTAINMENT_FIELD, "operator": "contains_json", "value": payload})
    return out


def merge_containment_conditions(
    node: dict[str, Any], *, schema: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """树形态的等值合并（架构 §5.4；compile() 内部逐层做同一变换——本函数供
    调试/测试与 GANTT-001 等外部消费方复用）。"""
    if not _is_logic_node(node):
        return node
    children = [
        merge_containment_conditions(c, schema=schema) if _is_logic_node(c) else c
        for c in (node.get("conditions") or [])
    ]
    if str(node.get("op", "AND")).upper() == "AND":
        children = _merge_eq_children(children, schema)
    return {"op": str(node.get("op", "AND")).upper(), "conditions": children}


def compile(  # noqa: A001 —— 沿架构 §5.3 文档命名（模块内无内建 compile 使用）
    tree: dict[str, Any] | None, ctx: CompileContext, *, optimize: bool = True
) -> Q:
    """DSL 树 → Q 对象。``optimize=False`` 跳过 BR-07 合并（UT-09 等价性对照用）。

    编译前置条件：树已经 ``validate_dsl``（保存路径）或读取降级剪除（resolve_view）；
    未知字段在此 fail-closed 抛 400（fail-open 会静默放宽过滤语义）。
    """
    if not tree:
        return Q()
    return _compile_node(tree, ctx, optimize)


def _compile_node(node: Any, ctx: CompileContext, optimize: bool) -> Q:
    if not _is_logic_node(node):
        if not isinstance(node, dict):
            raise _invalid_param("INVALID", "条件节点必须为对象")
        return _compile_condition(node, ctx)
    op = str(node.get("op", "AND")).upper()
    children = list(node.get("conditions") or [])
    if not children:
        return Q()  # 空组（读取降级后可能）→ 无操作
    if optimize and op == "AND":
        children = _merge_eq_children(children, ctx.field_schema)
    combined = _compile_node(children[0], ctx, optimize)
    for child in children[1:]:
        cq = _compile_node(child, ctx, optimize)
        combined = (combined & cq) if op == "AND" else (combined | cq)
    return combined


# ─────────────────────────────────────────────────────────────────────
# blocked 注解（§4.3.1：白名单 "blocked" 键的注解列）
# ─────────────────────────────────────────────────────────────────────
def blocked_exists_annotation():
    """``_is_blocked`` 注解：与 TASK-005 ``?blocked=true`` 同向同语义——
    issue 侧镜像行（relation_type='is_blocked_by'）存在且前置未完成/未删。"""
    from django.db.models import Exists, OuterRef

    from plane.db.models import IssueLink

    blocked = IssueLink.objects.filter(
        issue_id=OuterRef("pk"),
        relation_type="is_blocked_by",
        deleted_at__isnull=True,
        related_issue__deleted_at__isnull=True,
    ).exclude(related_issue__state__group__in=["completed", "cancelled"])
    return Exists(blocked)


def tree_references_blocked(trees: Sequence[dict[str, Any] | None]) -> bool:
    """树列表中是否有条件引用注解键 ``blocked``（决定是否注入注解列）。"""
    def walk(node: Any) -> bool:
        if not isinstance(node, dict):
            return False
        if _is_logic_node(node):
            return any(walk(c) for c in (node.get("conditions") or []))
        return node.get("field") == "blocked"

    return any(walk(t) for t in trees if t)


# ─────────────────────────────────────────────────────────────────────
# 三源组装（§1.3 / BR-06 / BR-12）
# ─────────────────────────────────────────────────────────────────────
def build_issue_queryset(
    *,
    project: Project,
    user: User,
    view_filters: dict[str, Any] | None = None,
    url_filters: dict[str, Any] | None = None,
    extra_q: Q | None = None,
    include_archived: bool = False,
) -> QuerySet[Model]:
    """层级组装：权限恒最外层（BR-06）→ 项目域 → 视图 filters → 临时 filters。

    - 权限层：P2 项目域查询的项目可见性由端点守门（get_project_or_404 404 隐藏），
      本函数恒叠加 ``project=project AND deleted_at IS NULL``（BR-08 防御：
      is_empty 的 NOT 键存在不可作跨项目唯一条件——项目域恒在，天然满足，
      ①全局层级 P3 激活时须显式禁止单 is_empty 条件）；
    - archived 口径沿 issues list 语义：默认排除归档，``include_archived=True``
      反向查归档（?archived=true）；
    - 三源恒 AND（BR-12）：extra_q（平铺参数）与两棵 DSL 树依次 ``.filter()``；
    - ``distinct()``：M2M / 多值条件去重。
    """
    from plane.db.models import Issue

    ctx = CompileContext.build(project=project, user=user)
    qs = Issue.objects.filter(project=project, deleted_at__isnull=True)
    if not include_archived:
        qs = qs.filter(archived_at__isnull=True)
    qs = qs.select_related("state", "issue_type", "project").prefetch_related(
        "assignees", "labels"
    )
    if extra_q is not None:
        qs = qs.filter(extra_q)
    trees = [t for t in (view_filters, url_filters) if t]
    if tree_references_blocked(trees):
        qs = qs.annotate(_is_blocked=blocked_exists_annotation())
    for tree in trees:
        qs = qs.filter(compile(tree, ctx))
    return qs.distinct()


# ─────────────────────────────────────────────────────────────────────
# applied 回显（BR-17）
# ─────────────────────────────────────────────────────────────────────
def echo_conditions(tree: dict[str, Any] | None) -> dict[str, Any]:
    """原始值回显（不解析占位符）：{field: value}——分组端点 meta.applied 既有口径。"""
    out: dict[str, Any] = {}

    def walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        if _is_logic_node(node):
            for child in node.get("conditions") or []:
                walk(child)
            return
        f = node.get("field")
        if f and f != MERGED_CONTAINMENT_FIELD:
            out[f] = node.get("value")

    walk(tree or {})
    return out


def resolved_applied(tree: dict[str, Any] | None, ctx: CompileContext) -> dict[str, Any]:
    """占位符解析后的 applied 回显（BR-17）：

    - ``conditions``：{field: 解析后值}（filters 源的 applied 用解析后值）；
    - ``resolved_placeholders``：{占位符: 展示值}（@me → @显示名、类型名 → 类型名、
      日期区间 → [start, end]）；
    - ``conditions_count`` / ``groups_count``：条件与逻辑组计数（根组计入）。
    """
    out: dict[str, Any] = {
        "conditions": {},
        "resolved_placeholders": {},
        "conditions_count": 0,
        "groups_count": 0,
    }
    placeholders: dict[str, Any] = out["resolved_placeholders"]

    def walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        if _is_logic_node(node):
            if node.get("conditions"):
                out["groups_count"] += 1
            for child in node.get("conditions") or []:
                walk(child)
            return
        f = node.get("field")
        if not isinstance(f, str) or f == MERGED_CONTAINMENT_FIELD:
            return  # 合成条件/未知字段不回显
        schema = ctx.field_schema.get(f)
        if schema is None:
            return  # 已剔除/未知字段不回显
        if schema["type"] == "date":
            lo, hi = _date_bounds_with_placeholders(node.get("value"), placeholders)
            out["conditions"][f] = [lo.isoformat() if lo else None, hi.isoformat() if hi else None]
        else:
            out["conditions"][f] = _resolve_placeholders(
                f, node.get("value"), schema, ctx.project, ctx.user, placeholders
            )
        out["conditions_count"] += 1

    walk(tree or {})
    return out


def parse_filters_param(raw: str | None) -> dict[str, Any] | None:
    """``?filters=<urlencoded JSON>`` → DSL 树；损坏 JSON → 400（§2.5 异常表末三行）。"""
    import json

    if not raw:
        return None
    try:
        tree = json.loads(raw)
    except json.JSONDecodeError as err:
        raise AppException(
            "VALIDATION_INVALID_PARAM",
            message="filters 不是合法 JSON",
            details=[{"field": "filters", "code": "INVALID", "message": "filters 不是合法 JSON"}],
        ) from err
    if not isinstance(tree, dict):
        raise _invalid_param("INVALID", "filters 必须为 JSON 对象")
    return tree
