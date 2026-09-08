"""自定义字段值校验与合并（TASK-008 §4.3.2，架构 dynamic-fields-design.md §3.4）。

12 种基础类型的 ``validate_field_value`` + 整体校验 ``validate_custom_fields``
（未知 key 拒绝 BR-07 → 逐字段校验 → 默认值填充 → 空值不落 key）+
PATCH 合并语义 ``merge_custom_fields``（显式 null 清空）+ ``next_auto_increment``
（advisory lock 取号，BR-09）。

存储纪律（架构 §1.3 三条铁律）：
1. 值为空**不写 key**（不是写 null）—— ``custom_fields ? 'cf_x'`` 即「有值」；
2. 值用 JSON 原生类型（数字存 number、日期存 ISO 字符串、选项存 value 不存 label）；
3. auto_increment 由服务端分配，客户端赋值被拒（READ_ONLY）。
"""
from __future__ import annotations

import logging
import uuid as uuid_module
import zlib
from datetime import date
from typing import TYPE_CHECKING, Any

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import URLValidator
from django.db import connection

from plane.utils.exceptions import CustomFieldValidationError, field_error

if TYPE_CHECKING:
    from plane.db.models import (
        CustomFieldDefinition,
        Project,
    )


#: TASK-012（Sprint-7 R2）：四类型校验器在文件靠后位置定义，需文件级延迟 import
# 避免部分 Django 启动顺序的解析告警——inline 函数内 import 会与 case _ 分支的
# lazy 路径形成二次解析。
from plane.db.models import FileAsset as _FileAsset  # noqa: E402
from plane.db.models import Issue as _Issue

logger = logging.getLogger(__name__)

#: 文本长度上限（TASK-008 §2.6 边界表）
TEXT_MAX_LENGTH = 512
TEXTAREA_MAX_LENGTH = 20000
#: 支持的 URL 协议
URL_SCHEMES = ["http", "https"]


def _err(definition: CustomFieldDefinition, code: str, message: str) -> CustomFieldValidationError:
    """按 TASK-008 §2.5 表构造 400 VALIDATION_CUSTOM_FIELD_INVALID 的单字段错误。"""
    return CustomFieldValidationError([field_error(definition.field_key, code, message)])


def _is_project_member(project_id: uuid_module.UUID | None, value: Any) -> bool:
    """member/member_multi 的项目成员校验；无项目上下文（如默认值预检）时只做 UUID 格式判定。"""
    try:
        uid = uuid_module.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return False
    if project_id is None:
        return True
    from plane.db.models import ProjectMember

    return ProjectMember.objects.filter(project_id=project_id, member_id=uid, is_active=True).exists()


def validate_field_value(
    definition: CustomFieldDefinition, value: Any, *, project: Project | None = None,
    issue: _Issue | None = None,
) -> Any:
    """校验并规范化单个自定义字段值，返回可直接写入 JSONB 的 Python 对象。

    校验失败抛 ``CustomFieldValidationError``（400 VALIDATION_CUSTOM_FIELD_INVALID）；
    空值（None/""）在非必填时返回 None（不落 key，BR-07），必填时 400 REQUIRED。
    issue（BR-05）：仅 relation 类型消费——禁自引用判定；其余类型忽略。
    """
    issue_ctx = issue
    FT = definition.FieldType  # noqa: N806 —— 对齐架构文档 §3.4 写法

    # ---- 空值分支 ----
    if value is None or value == "":
        if definition.is_required:
            raise _err(definition, "REQUIRED", f"「{definition.name}」为必填字段")
        return None

    match definition.field_type:
        # ---- text / textarea：str + 长度 ----
        case FT.TEXT | FT.TEXTAREA:
            if not isinstance(value, str):
                raise _err(definition, "INVALID", "必须为文本")
            max_len = TEXT_MAX_LENGTH if definition.field_type == FT.TEXT else TEXTAREA_MAX_LENGTH
            if len(value) > max_len:
                raise _err(definition, "TOO_LONG", f"长度不能超过 {max_len}")
            return value

        # ---- number：拒 bool、要 int/float（不存字符串，保证 ->>::numeric 可靠）----
        case FT.NUMBER:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise _err(definition, "INVALID", "必须为数字")
            return value

        # ---- select：value ∈ options.values ----
        case FT.SELECT:
            valid = {opt["value"] for opt in definition.options}
            if value not in valid:
                raise _err(definition, "NOT_A_CHOICE", f"非法选项 {value}，可选值：{' / '.join(sorted(valid))}")
            return value

        # ---- multi_select：list ⊆ options.values，去重保序 ----
        case FT.MULTI_SELECT:
            if not isinstance(value, list):
                raise _err(definition, "INVALID", "必须为数组")
            valid = {opt["value"] for opt in definition.options}
            invalid = set(value) - valid
            if invalid:
                raise _err(definition, "NOT_A_CHOICE", f"非法选项：{' / '.join(sorted(invalid))}")
            return sorted(set(value), key=value.index)

        # ---- date：YYYY-MM-DD（ISO 字符串字典序 = 时间序）----
        case FT.DATE:
            try:
                date.fromisoformat(str(value))
            except (TypeError, ValueError):
                raise _err(definition, "INVALID_DATE", "日期格式须为 YYYY-MM-DD") from None
            return str(value)

        # ---- member：项目成员校验 ----
        case FT.MEMBER:
            if not _is_project_member(project.id if project else None, value):
                raise _err(definition, "DOES_NOT_EXIST", "该用户不是项目成员")
            return str(value)

        # ---- member_multi：逐个成员校验 + 去重保序 ----
        case FT.MEMBER_MULTI:
            if not isinstance(value, list):
                raise _err(definition, "INVALID", "必须为数组")
            for uid in value:
                if not _is_project_member(project.id if project else None, uid):
                    raise _err(definition, "DOES_NOT_EXIST", f"用户 {uid} 不是项目成员")
            return [str(uid) for uid in dict.fromkeys(value)]

        # ---- checkbox：严格 bool ----
        case FT.CHECKBOX:
            if not isinstance(value, bool):
                raise _err(definition, "INVALID", "必须为布尔值")
            return value

        # ---- url：协议校验（INVALID_URL，§2.5）----
        case FT.URL:
            validator = URLValidator(schemes=URL_SCHEMES)
            try:
                validator(str(value))
            except DjangoValidationError:
                raise _err(definition, "INVALID_URL", "URL 格式非法（仅支持 http/https）") from None
            return str(value)

        # ---- currency：{"amount": number, "currency": "CNY"} ----
        case FT.CURRENCY:
            if not isinstance(value, dict) or "amount" not in value:
                raise _err(definition, "INVALID", '格式须为 {"amount": 数字, "currency": "CNY"}')
            amount = value["amount"]
            if isinstance(amount, bool) or not isinstance(amount, (int, float)):
                raise _err(definition, "INVALID", "amount 必须为数字")
            return {"amount": amount, "currency": value.get("currency", "CNY")}

        # ---- auto_increment：拒客户端赋值（BR-09，系统生成）----
        case FT.AUTO_INCREMENT:
            raise _err(definition, "READ_ONLY", "自增编号由系统生成，不接受客户端赋值")

        # ---- TASK-012（Sprint-7 R2）：四高级类型显式分支 ----
        case FT.CASCADE:
            return _validate_cascade(value, definition)
        case FT.RELATION:
            return _validate_relation(value, definition, project, issue_ctx)
        case FT.DATE_RANGE:
            return _validate_date_range(value, definition)
        case FT.ATTACHMENT:
            return _validate_attachment(value, definition)

        case _:
            raise _err(definition, "INVALID", f"暂不支持的字段类型 {definition.field_type}")


# ── TASK-012（Sprint-7 R2）：四高级类型校验器与配置校验 ──────────────

def _validate_cascade(value: Any, definition: CustomFieldDefinition) -> Any:
    """级联值 = 逐级 value 数组（长度=级数）；父链连续（BR-04）。"""
    levels = (definition.cascade_config or {}).get("levels") or []
    if not isinstance(value, list) or len(value) != len(levels):
        raise _err(definition, "INVALID", f"级联值须为长度 {len(levels)} 的逐级 value 数组")
    parent = None
    for i, v in enumerate(value):
        options = {o["value"]: o for o in (levels[i].get("options") or [])}
        if v not in options:
            raise _err(definition, "INVALID", f"第 {i + 1} 级值不在选项集内")
        if i > 0 and options[v].get("parent_value") != parent:
            raise _err(definition, "INVALID", "级联路径不连续（父值不匹配）")
        parent = v
    return value


def _validate_relation(value: Any, definition: CustomFieldDefinition,
                       project: Project | None,
                       issue: _Issue | None = None) -> Any:
    """关联工作项：同项目、未删除、非自身（BR-05）；≤20 去重。"""
    if not isinstance(value, list):
        raise _err(definition, "INVALID", "必须为数组（Issue UUID）")
    if len(value) > 20:
        raise _err(definition, "TOO_LONG", "关联工作项至多 20 个")
    try:
        ids = [uuid_module.UUID(str(v)) for v in dict.fromkeys(value)]
    except (ValueError, TypeError):
        raise _err(definition, "INVALID", "关联工作项须为 UUID 数组") from None
    if issue is not None and issue.id in ids:  # 禁自引用（BR-05）
        raise _err(definition, "INVALID", "不能关联任务自身")
    qs = _Issue.objects.filter(id__in=ids, deleted_at__isnull=True)
    if project is not None:
        qs = qs.filter(project=project)
    found = set(qs.values_list("id", flat=True))
    missing = sorted(str(i) for i in ids if i not in found)
    if missing:
        raise _err(definition, "DOES_NOT_EXIST",
                   f"关联工作项不存在或跨项目：{', '.join(missing)}")
    return [str(i) for i in ids]


def _validate_date_range(value: Any, definition: CustomFieldDefinition) -> Any:
    """日期区间：双键必填、ISO、start ≤ end（BR-06）。"""
    if not isinstance(value, dict) or "start" not in value or "end" not in value:
        raise _err(definition, "REQUIRED", "区间须同时含 start 与 end")
    try:
        start, end = date.fromisoformat(str(value["start"])), date.fromisoformat(str(value["end"]))
    except (TypeError, ValueError):
        raise _err(definition, "INVALID_DATE", "日期须为 YYYY-MM-DD") from None
    if start > end:
        raise _err(definition, "INVALID_DATE_RANGE", "start 须不晚于 end")
    return {"start": value["start"], "end": value["end"]}


def _validate_attachment(value: Any, definition: CustomFieldDefinition) -> Any:
    """附件：FileAsset 存在、同空间、已完成上传（BR-07）；≤10 去重。"""
    if not isinstance(value, list):
        raise _err(definition, "INVALID", "必须为数组（FileAsset UUID）")
    if len(value) > 10:
        raise _err(definition, "TOO_LONG", "附件至多 10 个")
    try:
        ids = [uuid_module.UUID(str(v)) for v in dict.fromkeys(value)]
    except (ValueError, TypeError):
        raise _err(definition, "INVALID", "附件须为 UUID 数组") from None
    from plane.db.models import FileAsset

    found = _FileAsset.objects.filter(
        id__in=ids, workspace_id=definition.workspace_id,
        status=FileAsset.Status.UPLOADED, deleted_at__isnull=True)
    if found.count() != len(set(ids)):
        raise _err(definition, "DOES_NOT_EXIST", "附件不存在、未完成上传或跨工作空间")
    return [str(i) for i in ids]


def backfill_cascade_options(definition: CustomFieldDefinition) -> None:
    """cascade 类型：拍平全级选项为 TASK-008 §4.1.1 可接受占位（创建/改配服务侧
    调用；TASK-008 待回改两处登记见 TASK-012 §4.3 注）。"""
    levels = (definition.cascade_config or {}).get("levels") or []
    definition.options = [{"label": o["label"], "value": o["value"]}
                          for level in levels for o in (level.get("options") or [])]


#: 角色码白名单（rbac §2.2/§2.3 小写形；ws_* 惰性保留——Sprint 8 AUTH-008 复用）
ROLE_CODES = frozenset({"ws_owner", "ws_admin", "ws_member", "ws_guest",
                        "proj_admin", "proj_contributor", "proj_commenter", "proj_viewer"})
_GRANT_RE = __import__("re").compile(
    r"^(role:[a-z_]+|user:[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})$")


def validate_permission_config(cfg: dict) -> list[dict]:
    """permission_config 保存校验（BR-08/BR-17）：返回 details[] 项（空=通过）。"""
    if not isinstance(cfg, dict):
        return [{"field": "permission_config", "code": "INVALID", "message": "须为对象"}]
    allowed_keys = {"read", "write", "required_for"}
    issues: list[dict] = []
    for key in set(cfg) - allowed_keys:
        issues.append({"field": f"permission_config.{key}", "code": "NOT_A_CHOICE",
                       "message": f"未知键 {key}（启用前不放开）"})
    for key in ("read", "write", "required_for"):
        members = cfg.get(key)
        if members is None:
            continue
        if not isinstance(members, list):
            issues.append({"field": f"permission_config.{key}", "code": "INVALID",
                           "message": "须为数组"})
            continue
        for i, m in enumerate(members):
            if not isinstance(m, str) or not _GRANT_RE.fullmatch(m):
                issues.append({"field": f"permission_config.{key}[{i}]", "code": "NOT_A_CHOICE",
                               "message": f"成员须为 role:<角色码> 或 user:<UUID>：{m!r}"})
                continue
            if m.startswith("role:"):
                if m.split(":", 1)[1] not in ROLE_CODES:
                    issues.append({"field": f"permission_config.{key}[{i}]", "code": "NOT_A_CHOICE",
                                   "message": f"未知角色码 {m}"})
                if key == "required_for":
                    continue  # required_for 仅 role:（BR-08）——user: 前缀已被正则分开报
            if key == "required_for" and m.startswith("user:"):
                issues.append({"field": f"permission_config.required_for[{i}]", "code": "NOT_A_CHOICE",
                               "message": "required_for 仅接受 role: 成员"})
    return issues


def validate_cascade_config(cfg: dict) -> list[dict]:
    """cascade_config 保存校验（BR-03/04）：级数 2-3、每级 ≤100、整树 ≤300、
    value 全树唯一、父链完整。返回 details[] 项（空=通过）。"""
    if not isinstance(cfg, dict):
        return [{"field": "cascade_config", "code": "INVALID", "message": "须为对象"}]
    levels = cfg.get("levels")
    if not isinstance(levels, list) or not (2 <= len(levels) <= 3):
        return [{"field": "cascade_config.levels", "code": "INVALID",
                 "message": "级数须为 2-3 级（BR-03）"}]
    issues: list[dict] = []
    seen_values: set[str] = set()
    prev_level_values: set[str] = set()
    total = 0
    for li, level in enumerate(levels):
        if not isinstance(level, dict) or not (level.get("name") or "").strip():
            issues.append({"field": f"cascade_config.levels[{li}].name", "code": "REQUIRED",
                           "message": "级名称必填"})
        options = level.get("options") or []
        if len(options) > 100:
            issues.append({"field": f"cascade_config.levels[{li}].options", "code": "LIMIT",
                           "message": "每级选项 ≤100（BR-03）"})
        cur_values: set[str] = set()
        for oi, o in enumerate(options):
            v, label = o.get("value"), o.get("label")
            if not v or not label:
                issues.append({"field": f"cascade_config.levels[{li}].options[{oi}]",
                               "code": "REQUIRED", "message": "选项 label/value 必填"})
                continue
            if v in seen_values:
                issues.append({"field": f"cascade_config.levels[{li}].options[{oi}]",
                               "code": "UNIQUE", "message": f"选项 value 全树唯一：{v}"})
            seen_values.add(v)
            cur_values.add(v)
            if li > 0:
                pv = o.get("parent_value")
                if not pv or pv not in prev_level_values:
                    issues.append({"field": f"cascade_config.levels[{li}].options[{oi}].parent_value",
                                   "code": "DOES_NOT_EXIST",
                                   "message": f"父值不存在于上一级选项集：{pv!r}（BR-04）"})
        prev_level_values = cur_values
        total += len(options)
    if total > 300:
        issues.append({"field": "cascade_config.levels", "code": "LIMIT",
                       "message": "选项树整体 ≤300 项（BR-03）"})
    return issues


def _unknown_key_error(unknown: set[str]) -> CustomFieldValidationError:
    return CustomFieldValidationError([
        field_error(k, "INVALID", f"未定义或不适用于当前类型的字段：{k}") for k in sorted(unknown)
    ])


def validate_custom_fields(
    project: Project,
    issue_type_id: uuid_module.UUID | None,
    payload: dict,
    issue: _Issue | None = None,
) -> dict:
    """创建路径整体校验：未知 key 拒绝（BR-07）→ 逐字段校验 → 默认值填充 → 必填拦截（BR-08）→ 空值不落 key。"""
    from plane.db.services.field_schema import resolve_fields

    if not isinstance(payload, dict):
        raise CustomFieldValidationError([field_error("custom_fields", "INVALID", "custom_fields 必须为对象")])

    definitions = {d.field_key: d for d in resolve_fields(project, issue_type_id)}

    unknown = set(payload) - set(definitions)
    if unknown:
        raise _unknown_key_error(unknown)

    cleaned: dict[str, Any] = {}
    for key, d in definitions.items():
        if key in payload:
            value = validate_field_value(d, payload[key], project=project, issue=issue)
        elif d.default_value is not None:
            value = d.default_value
        elif d.is_required:
            raise _err(d, "REQUIRED", f"「{d.name}」为必填字段")
        else:
            continue
        if value is not None:
            cleaned[key] = value  # None 不落库，保持 JSONB 精简
    return cleaned


def merge_custom_fields(
    project: Project,
    issue_type_id: uuid_module.UUID | None,
    current: dict,
    payload: dict,
    issue: _Issue | None = None,
) -> dict:
    """PATCH 合并语义（TASK-008 §4.2.4）：

    - 请求体中的 key 覆盖、未提及的 key 保留（合并而非替换）；
    - ``{"cf_x": null}`` 显式清空（key 移除）；必填字段清空 → 400 REQUIRED（§2.5）；
    - 未知 key / 不适用当前类型的 key 一律拒绝（BR-07）；
    - 必填存量不追溯（BR-08）：仅校验本次提交中出现（或被清空）的 key。
    """
    from plane.db.services.field_schema import resolve_fields

    if not isinstance(payload, dict):
        raise CustomFieldValidationError([field_error("custom_fields", "INVALID", "custom_fields 必须为对象")])

    definitions = {d.field_key: d for d in resolve_fields(project, issue_type_id)}
    unknown = set(payload) - set(definitions)
    if unknown:
        raise _unknown_key_error(unknown)

    merged = dict(current or {})
    for key, value in payload.items():
        d = definitions[key]
        if value is None or value == "":
            # 显式清空：必填字段不允许清空（§2.5 REQUIRED 行）
            if d.is_required:
                raise _err(d, "REQUIRED", f"「{d.name}」为必填字段，不能清空")
            merged.pop(key, None)
            continue
        cleaned = validate_field_value(d, value, project=project, issue=issue)
        if cleaned is None:
            merged.pop(key, None)
        else:
            merged[key] = cleaned
    return merged


# ─────────────────────────────────────────────────────────────────────
# 类型感知排序表达式（TASK-008 §4.3.5；bgtasks 索引任务与 issue_query 排序共用）
# ─────────────────────────────────────────────────────────────────────
#: field_type → B-Tree 表达式（架构 §6.2：数字 ::numeric / 日期文本直排 / 金额嵌套路径）。
#: select 为特例（选项配置序 array_position），不在本表。
#: 列名显式限定 issues. 前缀 —— 列表查询带计数 annotate 自连接（issues 出现多次），
#: 不限定会 AmbiguousColumn（TASK-008 T8-26 突变锚点）。
ORDER_EXPRESSIONS: dict[str, str] = {
    "number": "(issues.custom_fields->>%s)::numeric",
    "auto_increment": "(issues.custom_fields->>%s)::bigint",
    "currency": "(issues.custom_fields#>>ARRAY[%s,'amount'])::numeric",
    "date": "issues.custom_fields->>%s",  # ISO 字符串字典序 = 时间序
    "text": "issues.custom_fields->>%s",
    "textarea": "issues.custom_fields->>%s",
    "url": "issues.custom_fields->>%s",
    "select": "issues.custom_fields->>%s",
    "checkbox": "(issues.custom_fields->>%s)::bool",
}


def select_order_expression(definition: CustomFieldDefinition) -> tuple[str, list]:
    """单选字段按「选项配置序」排序（array_position，非字典序）—— 返回 (sql, params)。"""
    values = [o["value"] for o in sorted(definition.options or [], key=lambda o: o.get("sort_order", 0))]
    return "array_position(%s::text[], issues.custom_fields->>%s)", [values, definition.field_key]


def custom_order_expression(definition: CustomFieldDefinition) -> tuple[str, list]:
    """order_by=cf_<key> 的类型感知核心表达式（方向与 NULLS LAST 由调用方用
    ``expr.asc/desc(nulls_last=True)`` 声明——RawSQL 被 Django 包裹括号后，
    内嵌 ``DESC NULLS LAST`` 文本会产生 ``(expr DESC NULLS LAST)`` 语法错误）。"""
    if definition.field_type == "select":
        return select_order_expression(definition)
    return ORDER_EXPRESSIONS[definition.field_type], [definition.field_key]


# ─────────────────────────────────────────────────────────────────────
# auto_increment 取号（BR-09 / 架构 §4.4）
# ─────────────────────────────────────────────────────────────────────
def _int32(v: int) -> int:
    """压缩到 int32 并映射到非负区间（pg_advisory_xact_lock 双 int32 键空间）。"""
    return v & 0x7FFFFFFF


def next_auto_increment(project_id: uuid_module.UUID, field_key: str) -> int:
    """为 auto_increment 字段分配下一个编号 —— 须在事务内调用（pg_advisory_xact_lock）。

    锁键 = 双 32 位 (project 哈希, crc32(field_key))，与 issue sequence_id 的
    单 int64 锁空间天然隔离（架构 §4.4）：不同字段互不阻塞，
    同字段的并发取号被串行化，MAX()+1 在锁保护下无重号。
    """
    if connection.vendor != "postgresql":
        # 非 PG 环境（不存在）退化为无锁 MAX()+1
        pass
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s, %s)",
            [_int32(project_id.int), _int32(zlib.crc32(field_key.encode()))],
        )
        cursor.execute(
            """
            SELECT COALESCE(MAX((custom_fields->>%s)::bigint), 0) + 1
              FROM issues
             WHERE project_id = %s AND custom_fields ? %s
            """,
            [field_key, str(project_id), field_key],
        )
        return int(cursor.fetchone()[0])


def assign_auto_increments(
    project_id: uuid_module.UUID,
    issue_type_id: uuid_module.UUID | None,
    cleaned: dict[str, Any],
) -> dict[str, Any]:
    """创建路径补齐 auto_increment 字段编号（在事务内、Issue INSERT 之前调用）。

    ``cleaned`` 是 ``validate_custom_fields`` 的产物（auto_increment 的客户端值
    已被拒）；这里对解析结果中每个启用中的自增字段取号写入。
    """
    from plane.db.models import Project
    from plane.db.services.field_schema import resolve_fields

    project = Project.objects.get(pk=project_id)
    out = dict(cleaned)
    for d in resolve_fields(project, issue_type_id):
        if d.field_type == d.FieldType.AUTO_INCREMENT:
            out[d.field_key] = next_auto_increment(project_id, d.field_key)
    return out


def diff_custom_fields(old: dict | None, new: dict | None) -> list[dict]:
    """逐键 diff（BR-14）：返回 [{key, old, new}]，供 Activity(field='cf_<key>') 消费。

    old/new 为 JSON 原生类型；序列化为紧凑 JSON 字符串由调用方落 IssueActivity。
    """
    import json

    old, new = old or {}, new or {}
    keys = {k for k in set(old) | set(new) if old.get(k) != new.get(k)}
    out = []
    for k in sorted(keys):
        out.append({
            "key": k,
            "old": json.dumps(old[k], ensure_ascii=False) if k in old else None,
            "new": json.dumps(new[k], ensure_ascii=False) if k in new else None,
        })
    return out
