"""自定义字段管理端点（TASK-008 §4.2）。

  GET    /workspaces/{slug}/projects/{pid}/field-schema/?issue_type=<uuid>   Schema API（ETag/304，VIEWER+）
  GET    /workspaces/{slug}/projects/{pid}/issue-properties/?scope=all|global|project  列表（PROJ_ADMIN）
  POST   /workspaces/{slug}/projects/{pid}/issue-properties/                 创建项目私有字段（PROJ_ADMIN，201）
  PATCH  /workspaces/{slug}/projects/{pid}/issue-properties/{id}/            编辑（BR-01/04/06 不可变保护，200）
  DELETE /workspaces/{slug}/projects/{pid}/issue-properties/{id}/            删除（软删 + 异步清理，202）
  PATCH  /workspaces/{slug}/projects/{pid}/issue-properties/{id}/sort-order/ 拖拽排序（浮点插值，200）
  GET    /workspaces/{slug}/issue-properties/                                全局字段列表（WS Admin）
  POST   /workspaces/{slug}/issue-properties/                                创建全局字段（WS Admin，201）

权限（BR-15）：项目端点 PROJ_ADMIN+（issue.field.manage 默认角色）；全局字段的
创建与修改额外要求 WS Admin。上限（BR-10）：50 字段/WS、10 个 is_indexed。
"""
from __future__ import annotations

import logging
import uuid as uuid_module

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import connection
from django.db.models import Q
from django.db.utils import IntegrityError
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from plane.app.permissions import IsAuthenticated
from plane.app.views._access import get_project_or_404, get_workspace_or_404
from plane.base.exception import AppException
from plane.base.response import created_response, success_response
from plane.db.models import CustomFieldDefinition, IssueType, ProjectRole, WorkspaceRole
from plane.db.models.custom_field import FIELD_KEY_RE
from plane.db.services.custom_fields import validate_field_value
from plane.db.services.field_schema import (
    build_field_schema,
    fields_for_scope,
    schema_etag,
    serialize_definition,
)
from plane.db.services.sort_order import calculate_sort_order, needs_rebalance
from plane.settings.features import (
    MAX_CUSTOM_FIELDS_PER_WORKSPACE,
    MAX_INDEXED_CUSTOM_FIELDS_PER_WORKSPACE,
)
from plane.utils.exceptions import AppValidationError, CustomFieldValidationError, field_error

logger = logging.getLogger(__name__)

OPTION_MAX_COUNT = 100      # 选项数 / 字段上限（§2.6）
OPTION_TEXT_MAX = 64        # 选项 label / value 长度上限（§2.6）
NAME_MAX = 128              # 字段名长度上限（§2.6）
DESCRIPTION_MAX = 2000


# ─────────────────────────────────────────────────────────────────────
# Schema API（§4.2.1 核心契约：builtin+custom、能力推导、ETag/304）
# ─────────────────────────────────────────────────────────────────────
class FieldSchemaView(APIView):
    """GET …/projects/{pid}/field-schema/?issue_type=<uuid> —— PROJ_VIEWER(5)+。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)

        issue_type_id: uuid_module.UUID | None = None
        raw_type = request.query_params.get("issue_type")
        if raw_type:
            try:
                issue_type_id = uuid_module.UUID(raw_type)
            except ValueError as err:
                raise AppValidationError(
                    [field_error("issue_type", "INVALID_UUID", f"UUID 格式非法：{raw_type}")]
                ) from err

        # TASK-012 §4.4：按请求者角色注入 access + hidden 字段四键骨架（仅 id/key/type/access，
        # 隐藏字段的存在性可识别但配置与值不下发；BR-10 实现偏差已登记）
        from plane.db.services.field_permissions import FieldPermissionService

        payload = build_field_schema(project, issue_type_id)
        # TASK-012 §4.4：按请求者角色为 custom[] 每项注入 access 四态标注
        # （ETag 仍只锁定定义集；access 在视图层二次 resolve，零 Schema API 缓存污染）
        # Q 在 django.db.models（plane.db.models 不导出——函数内 import 漏网曾致全端点 500）
        from django.db.models import Q

        from plane.db.models import CustomFieldDefinition

        if payload.get("custom"):
            cf_defs = list(CustomFieldDefinition.objects
                            .filter(workspace_id=project.workspace_id)
                            .filter(Q(applicable_types__contains=[str(issue_type_id)])
                                    if issue_type_id else Q())
                            .filter(Q(project=project) | Q(project__isnull=True))
                            .order_by("sort_order", "created_at"))
            _access = FieldPermissionService().cached_resolve(
                request, request.user, project, cf_defs)
            for item in payload["custom"]:
                _k = item.get("key")
                if _k and _k in _access:
                    item["access"] = _access[_k]
        etag = schema_etag(payload)
        if _etag_matches(request.headers.get("If-None-Match"), etag):
            # 未变：304 空体（Envelope 中间件对 304 显式放行，C1 例外 BR-02）
            return Response(status=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})
        from django.utils import timezone

        return success_response(
            payload,
            meta={"etag": etag, "generated_at": timezone.now().isoformat()},
            headers={"ETag": etag},
        )


def _etag_matches(if_none_match: str | None, etag: str) -> bool:
    """If-None-Match 匹配（支持逗号分隔多值与 `*`，RFC 7232 宽松实现）。"""
    if not if_none_match:
        return False
    if if_none_match.strip() == "*":
        return True
    return any(part.strip() == etag for part in if_none_match.split(","))


# ─────────────────────────────────────────────────────────────────────
# 载荷校验（POST / PATCH 共用）
# ─────────────────────────────────────────────────────────────────────
def _validate_options(
    raw, errors: list[dict], existing_values: set[str] | None = None
) -> list[dict] | None:
    """选项配置校验（BR-03/04）：label/value 必填、value 唯一、≤100 项、存量 value 不可删除。"""
    if raw is None:
        return None
    if not isinstance(raw, list):
        errors.append(field_error("options", "INVALID", "options 必须为数组"))
        return None
    if len(raw) > OPTION_MAX_COUNT:
        errors.append(field_error("options", "TOO_LARGE", f"单个字段最多 {OPTION_MAX_COUNT} 个选项"))
        return None
    values: list[str] = []
    cleaned: list[dict] = []
    for i, opt in enumerate(raw):
        if not isinstance(opt, dict):
            errors.append(field_error(f"options[{i}]", "INVALID", "选项必须为对象"))
            return None
        label, value = opt.get("label"), opt.get("value")
        if not isinstance(label, str) or not label or len(label) > OPTION_TEXT_MAX:
            errors.append(field_error(f"options[{i}].label", "REQUIRED", "选项 label 必填且 ≤64 字符"))
            return None
        if not isinstance(value, str) or not value or len(value) > OPTION_TEXT_MAX:
            errors.append(field_error(f"options[{i}].value", "REQUIRED", "选项 value 必填且 ≤64 字符"))
            return None
        if value in values:
            errors.append(field_error("options", "UNIQUE", f"选项 value 重复：{value}"))
            return None
        values.append(value)
        item: dict = {"label": label, "value": value, "sort_order": opt.get("sort_order", len(cleaned) + 1)}
        if opt.get("color") is not None:
            item["color"] = str(opt["color"])
        cleaned.append(item)
    if existing_values is not None and (missing := existing_values - set(values)):
        # BR-04：选项 value 创建后不可修改（删除存量 value = 存量数据悬空）
        errors.append(field_error(
            "options", "READ_ONLY", f"选项 value 创建后不可删除：{' / '.join(sorted(missing))}"))
        return None
    return cleaned


def _validate_applicable_types(raw, workspace_id, errors: list[dict]) -> list[str] | None:
    """适用任务类型：UUID 列表 + WS 内 active 类型存在性。"""
    if raw is None:
        return None
    if not isinstance(raw, list):
        errors.append(field_error("applicable_types", "INVALID", "applicable_types 必须为数组"))
        return None
    cleaned: list[str] = []
    for item in raw:
        try:
            tid = str(uuid_module.UUID(str(item)))
        except (ValueError, TypeError):
            errors.append(field_error("applicable_types", "INVALID_UUID", f"UUID 格式非法：{item}"))
            return None
        if tid not in cleaned:
            cleaned.append(tid)
    valid = {
        str(v) for v in IssueType.objects.filter(
            workspace_id=workspace_id, is_active=True, deleted_at__isnull=True
        ).values_list("id", flat=True)
    }
    if invalid := (set(cleaned) - valid):
        errors.append(field_error(
            "applicable_types", "DOES_NOT_EXIST", f"任务类型不存在或已停用：{' / '.join(sorted(invalid))}"))
        return None
    return cleaned


def _check_limits(
    workspace_id, *, creating: bool, is_indexed: bool, instance: CustomFieldDefinition | None = None
) -> None:
    """BR-10：50 字段/WS（启用中）、10 个 is_indexed（启用中）。"""
    base = CustomFieldDefinition.objects.filter(
        workspace_id=workspace_id, is_active=True, deleted_at__isnull=True
    )
    if instance is not None:
        base = base.exclude(pk=instance.pk)
    if creating and base.count() >= MAX_CUSTOM_FIELDS_PER_WORKSPACE:
        raise AppException(
            "RESOURCE_LIMIT_EXCEEDED",
            message=f"单个工作空间最多 {MAX_CUSTOM_FIELDS_PER_WORKSPACE} 个启用字段",
            details=[{
                "field": "field_key", "code": "LIMIT",
                "message": f"启用字段已达上限（{MAX_CUSTOM_FIELDS_PER_WORKSPACE}），请先停用其他字段",
                "limit": MAX_CUSTOM_FIELDS_PER_WORKSPACE,
            }],
        )
    if is_indexed and base.filter(is_indexed=True).count() >= MAX_INDEXED_CUSTOM_FIELDS_PER_WORKSPACE:
        raise AppException(
            "RESOURCE_LIMIT_EXCEEDED",
            message=f"每个工作空间最多 {MAX_INDEXED_CUSTOM_FIELDS_PER_WORKSPACE} 个索引优化字段",
            details=[{
                "field": "is_indexed", "code": "LIMIT",
                "message": f"索引优化字段已达上限（{MAX_INDEXED_CUSTOM_FIELDS_PER_WORKSPACE}），请先取消其他字段",
                "limit": MAX_INDEXED_CUSTOM_FIELDS_PER_WORKSPACE,
            }],
        )


def _unique_conflict(field_key: str, scope_hint: str) -> AppException:
    return AppException(
        "RESOURCE_ALREADY_EXISTS",
        message="已存在同名字段",
        details=[{"field": "field_key", "code": "UNIQUE",
                  "message": f"{field_key} 已在{scope_hint}定义"}],
    )


# ─────────────────────────────────────────────────────────────────────
# 创建（项目私有 / WS 全局共用）
# ─────────────────────────────────────────────────────────────────────
def _create_definition(request, workspace_id, slug: str, *, project=None) -> Response:
    payload = request.data or {}
    errors: list[dict] = []

    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append(field_error("name", "REQUIRED", "字段名称为必填项"))
    elif len(name) > NAME_MAX:
        errors.append(field_error("name", "TOO_LONG", f"字段名称最长 {NAME_MAX} 字符"))

    field_key = payload.get("field_key")
    if not isinstance(field_key, str) or not FIELD_KEY_RE.fullmatch(field_key):
        errors.append(field_error("field_key", "INVALID", "字段键名必须为 cf_ 前缀的 snake_case（cf_[a-z][a-z0-9_]）"))

    field_type = payload.get("field_type")
    _allowed = CustomFieldDefinition.P2_ALLOWED_TYPES | CustomFieldDefinition.P3_ENTERPRISE_TYPES
    if field_type not in _allowed:
        errors.append(field_error(
            "field_type", "NOT_A_CHOICE",
            f"字段类型非法（12 基础 + 4 高级）：{field_type}"))

    description = payload.get("description") or ""
    if not isinstance(description, str) or len(description) > DESCRIPTION_MAX:
        errors.append(field_error("description", "TOO_LONG", f"帮助说明最长 {DESCRIPTION_MAX} 字符"))

    is_required = bool(payload.get("is_required", False))
    is_indexed = bool(payload.get("is_indexed", False))
    if field_type == CustomFieldDefinition.FieldType.AUTO_INCREMENT:
        is_required = False  # 自增编号系统必填语义，不开放人工必填开关

    options = _validate_options(payload.get("options"), errors)
    cascade_config = payload.get("cascade_config") or {}
    permission_config = payload.get("permission_config") or {}
    if field_type in CustomFieldDefinition.OPTION_REQUIRED_TYPES and not options:
        if field_type == CustomFieldDefinition.FieldType.CASCADE:
            # cascade 选项全入 cascade_config（TASK-012 §4.3：options 仅校验占位，
            # 保存前 backfill 回填；TASK-008 §2.6「≤100/字段」对拍平占位豁免）
            from plane.db.services.custom_fields import validate_cascade_config

            errors.extend(validate_cascade_config(cascade_config))
        else:
            errors.append(field_error("options", "REQUIRED", "下拉类型必须配置至少一个选项（BR-03）"))
    if field_type == CustomFieldDefinition.FieldType.CASCADE:
        from plane.db.services.custom_fields import backfill_cascade_options

        _cascade_def = CustomFieldDefinition(field_type=field_type, cascade_config=cascade_config)
        backfill_cascade_options(_cascade_def)
        options = _cascade_def.options
    # TASK-012 BR-08/BR-17：permission_config 三集合成员校验 + readonly×is_required 拦截
    from plane.db.services.custom_fields import validate_permission_config

    _pc_issues = validate_permission_config(permission_config)
    if is_required:
        write = set(permission_config.get("write", []) or []) if permission_config else set()
        read = set(permission_config.get("read", []) or []) if permission_config else set()
        if read and write and (read - write):  # 有可读不可写角色 → readonly 组合（BR-17）
            _pc_issues.append({"field": "permission_config", "code": "INVALID",
                               "message": "is_required 字段不得使任一可读角色落入 readonly（BR-17）"})
    errors.extend(_pc_issues)

    applicable_types = _validate_applicable_types(
        payload.get("applicable_types", []), workspace_id, errors)

    _raise(errors)

    # _raise 保证此刻 name / field_key / field_type 均已通过必填与格式校验
    assert isinstance(name, str) and isinstance(field_key, str) and isinstance(field_type, str)

    d = CustomFieldDefinition(
        workspace_id=workspace_id,
        project=project,
        name=name.strip(),
        field_key=field_key,
        field_type=field_type,
        description=description,
        is_required=is_required,
        is_indexed=is_indexed,
        options=options or [],
        applicable_types=applicable_types or [],
        cascade_config=cascade_config if field_type == CustomFieldDefinition.FieldType.CASCADE else {},
        permission_config=permission_config,
        created_by=request.user,
        updated_by=request.user,
    )
    # BR-05：default_value 复用值校验器（member 无项目上下文时仅查格式）
    default_value = payload.get("default_value")
    if default_value is not None:
        try:
            validate_field_value(d, default_value, project=project)
        except CustomFieldValidationError as exc:
            raise AppValidationError(exc.extra_details) from None
        d.default_value = default_value

    _check_limits(workspace_id, creating=True, is_indexed=is_indexed)

    # BR-02：作用域内 key 唯一（服务层预检 + DB 偏条件约束兜底）。
    # 创建项目私有字段时，与「本项目私有」或「全局」同 key 均冲突（resolve 私有覆盖全局，
    # 但创建侧直接拒绝以防歧义）；创建全局字段时与既有全局同 key 冲突。
    if project is not None:
        dup_exists = CustomFieldDefinition.objects.filter(
            workspace_id=workspace_id, field_key=field_key, deleted_at__isnull=True
        ).filter(Q(project__isnull=True) | Q(project_id=project.id)).exists()
        scope_hint = "当前作用域"
    else:
        dup_exists = CustomFieldDefinition.objects.filter(
            workspace_id=workspace_id, field_key=field_key,
            project__isnull=True, deleted_at__isnull=True,
        ).exists()
        scope_hint = "工作空间全局字段"
    if dup_exists:
        raise _unique_conflict(field_key, scope_hint)

    try:
        d.save()
    except IntegrityError as err:
        raise _unique_conflict(field_key, scope_hint) from err
    except DjangoValidationError as err:
        raise AppValidationError(_flatten_django_validation(err)) from None

    if is_indexed:
        _dispatch_index_task(d)

    location = (
        f"/api/v1/workspaces/{slug}/"
        + (f"projects/{project.id}/" if project is not None else "")
        + f"issue-properties/{d.id}/"
    )
    return created_response(serialize_definition(d) | {"is_active": d.is_active}, location=location)


def _raise(errors: list[dict]) -> None:
    if errors:
        raise AppValidationError(errors)


def _flatten_django_validation(err: DjangoValidationError) -> list[dict]:
    """Django 模型校验（full_clean/clean/save 防线）→ 标准 details 条目。"""
    out: list[dict] = []
    detail = getattr(err, "error_dict", None)
    if detail:
        for field, msgs in detail.items():
            joined = "；".join(str(m.message if hasattr(m, "message") else m) for m in msgs)
            code = next((m.code or "INVALID" for m in msgs if hasattr(m, "code")), "INVALID")
            out.append(field_error(field or "custom_fields", code.upper(), joined[:200]))
    else:
        out.append(field_error("custom_fields", "INVALID", str(err.message if hasattr(err, "message") else err)[:200]))
    return out


# ─────────────────────────────────────────────────────────────────────
# 项目字段：列表 / 创建
# ─────────────────────────────────────────────────────────────────────
class IssuePropertyListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        if project.current_user_role < ProjectRole.ADMIN:
            raise AppException("PERM_ROLE_INSUFFICIENT", message="需要项目管理员权限")
        scope = request.query_params.get("scope") or "all"
        if scope not in ("all", "global", "project"):
            raise AppValidationError(
                [field_error("scope", "NOT_A_CHOICE", "scope 仅支持 all|global|project")]
            )
        if scope == "global":
            qs = fields_for_scope(project.workspace_id, None, active_only=False)
        elif scope == "project":
            qs = fields_for_scope(project.workspace_id, project.id, active_only=False)
        else:
            qs = (
                CustomFieldDefinition.objects.filter(
                    workspace_id=project.workspace_id, deleted_at__isnull=True
                ).filter(Q(project__isnull=True) | Q(project_id=project.id))
                .order_by("sort_order", "created_at")
            )
        data = [serialize_definition(d) | {"is_active": d.is_active} for d in qs]
        return success_response(
            data,
            meta={
                "count": len(data), "total_count": len(data), "page": 1,
                "per_page": 100, "next_cursor": None, "prev_cursor": None,
                "next_page_results": False, "prev_page_results": False, "total_pages": 1,
                "scope": scope,
                "limits": {
                    "max_fields": MAX_CUSTOM_FIELDS_PER_WORKSPACE,
                    "max_indexed": MAX_INDEXED_CUSTOM_FIELDS_PER_WORKSPACE,
                },
            },
        )

    def post(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        if project.current_user_role < ProjectRole.ADMIN:
            raise AppException("PERM_ROLE_INSUFFICIENT", message="需要项目管理员权限")
        return _create_definition(request, project.workspace_id, kwargs["slug"], project=project)


# ─────────────────────────────────────────────────────────────────────
# 项目字段：编辑 / 删除
# ─────────────────────────────────────────────────────────────────────
def _get_scoped_definition(kwargs, workspace_id, project) -> CustomFieldDefinition:
    """按 id 取定义：项目私有须属于本项目；全局字段（同 WS）可见。不可见一律 404。"""
    try:
        d = CustomFieldDefinition.objects.get(
            id=kwargs["property_id"], workspace_id=workspace_id, deleted_at__isnull=True
        )
    except (CustomFieldDefinition.DoesNotExist, ValueError, TypeError):
        raise NotFound("RESOURCE_NOT_FOUND") from None
    if d.project_id is not None and d.project_id != project.id:
        raise NotFound("RESOURCE_NOT_FOUND") from None
    return d


def _require_manage_right(project, ws_member, d: CustomFieldDefinition) -> None:
    """BR-15：项目字段 PROJ_ADMIN；全局字段额外要求 WS 级配置权（WS Admin）。"""
    if project.current_user_role < ProjectRole.ADMIN:
        raise AppException("PERM_ROLE_INSUFFICIENT", message="需要项目管理员权限")
    if d.project_id is None and ws_member.role < WorkspaceRole.ADMIN:
        raise AppException("PERM_ROLE_INSUFFICIENT", message="全局字段需要工作空间管理员权限")


class IssuePropertyDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, *args, **kwargs):
        project, _, ws_member = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        d = _get_scoped_definition(kwargs, project.workspace_id, project)
        _require_manage_right(project, ws_member, d)
        payload = request.data or {}
        errors: list[dict] = []

        # BR-01 / BR-06：field_key 与 field_type 创建后不可变（§2.5 READ_ONLY 子码）
        for immutable in ("field_key", "field_type"):
            if immutable in payload and payload[immutable] != getattr(d, immutable):
                errors.append(field_error(immutable, "READ_ONLY", f"{immutable} 创建后不可修改"))
        _raise(errors)

        if "name" in payload:
            name = payload["name"]
            if not isinstance(name, str) or not name.strip():
                raise AppValidationError([field_error("name", "REQUIRED", "字段名称为必填项")])
            if len(name) > NAME_MAX:
                raise AppValidationError([field_error("name", "TOO_LONG", f"字段名称最长 {NAME_MAX} 字符")])
            d.name = name.strip()
        if "description" in payload:
            desc = payload.get("description") or ""
            if not isinstance(desc, str) or len(desc) > DESCRIPTION_MAX:
                raise AppValidationError(
                    [field_error("description", "TOO_LONG", f"帮助说明最长 {DESCRIPTION_MAX} 字符")])
            d.description = desc

        was_indexed, was_active = d.is_indexed, d.is_active
        if "is_active" in payload:
            d.is_active = bool(payload["is_active"])
        if "is_required" in payload:
            d.is_required = bool(payload["is_required"])
            if d.field_type == CustomFieldDefinition.FieldType.AUTO_INCREMENT:
                d.is_required = False
        if "is_indexed" in payload:
            d.is_indexed = bool(payload["is_indexed"])

        if "options" in payload:
            existing_values = {o["value"] for o in (d.options or [])}
            options = _validate_options(payload.get("options"), errors, existing_values)
            if d.field_type in CustomFieldDefinition.OPTION_REQUIRED_TYPES and not options:
                errors.append(field_error("options", "REQUIRED", "下拉类型必须配置至少一个选项"))
            _raise(errors)
            d.options = options or []

        if "applicable_types" in payload:
            applicable = _validate_applicable_types(
                payload.get("applicable_types"), project.workspace_id, errors)
            _raise(errors)
            d.applicable_types = applicable or []

        if "default_value" in payload:
            dv = payload.get("default_value")
            if dv is not None:
                try:
                    validate_field_value(d, dv, project=project if d.project_id else None)
                except CustomFieldValidationError as exc:
                    raise AppValidationError(exc.extra_details) from None
            d.default_value = dv

        # BR-10：重新启用 / 新增索引标记时复检上限
        _check_limits(
            d.workspace_id,
            creating=(not was_active and d.is_active),
            is_indexed=(d.is_indexed and not was_indexed),
            instance=d,
        )
        d.updated_by = request.user
        try:
            d.save()
        except IntegrityError as err:
            raise AppException("SERVER_ERROR", message=str(err)[:200]) from err
        except DjangoValidationError as err:
            raise AppValidationError(_flatten_django_validation(err)) from None

        if d.is_indexed and not was_indexed:
            _dispatch_index_task(d)
        if was_indexed and not d.is_indexed:
            _dispatch_drop_index_task(d.field_key)

        return success_response(serialize_definition(d) | {"is_active": d.is_active})

    def delete(self, request, *args, **kwargs):
        project, _, ws_member = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        d = _get_scoped_definition(kwargs, project.workspace_id, project)
        _require_manage_right(project, ws_member, d)
        return _delete_definition(request, d)


def _delete_definition(request, d: CustomFieldDefinition) -> Response:
    """软删 + 异步清理（BR-11）→ 202 {task_id, state, affected_issues, status_url}。"""
    from plane.bgtasks.field_cleanup import cleanup_deleted_field_values, prune_views_referencing_field
    from plane.bgtasks.field_index import drop_field_expression_index

    field_key = d.field_key
    scope_sql, params = _scope_clause(d)
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT COUNT(*) FROM issues WHERE deleted_at IS NULL AND {scope_sql} AND custom_fields ? %s",
            [*params, field_key],
        )
        affected = int(cursor.fetchone()[0])

    d.soft_delete(actor_id=request.user.id)
    if d.is_indexed:
        _dispatch_drop_index_task(field_key)

    task_id: str
    state = "queued"
    try:
        result = cleanup_deleted_field_values.delay(str(d.id))
        task_id = str(result.id)
        prune_views_referencing_field.delay(str(d.id))
        drop_field_expression_index.delay(field_key)
    except Exception as exc:  # noqa: BLE001 —— broker 不可达降级为同步清理（任务幂等）
        logger.warning("field_delete.broker_unavailable fallback=sync exc=%s", exc)
        cleanup_deleted_field_values(str(d.id))
        prune_views_referencing_field(str(d.id))
        drop_field_expression_index(field_key)
        task_id = _fallback_task_id()
        state = "completed"

    return success_response(
        {
            "task_id": task_id,
            "state": state,
            "affected_issues": affected,
            "status_url": f"/api/v1/tasks/{task_id}/",
            "field_key": field_key,
        },
        status_code=status.HTTP_202_ACCEPTED,
    )


def _fallback_task_id() -> str:
    from ulid import ULID

    return str(ULID())


def _scope_clause(d: CustomFieldDefinition) -> tuple[str, list]:
    """全局字段按 workspace 圈定清理范围；项目字段按 project（cleanup 任务共用）。"""
    if d.project_id is not None:
        return "project_id = %s", [str(d.project_id)]
    return (
        "project_id IN (SELECT id FROM projects WHERE workspace_id = %s AND deleted_at IS NULL)",
        [str(d.workspace_id)],
    )


def _dispatch_index_task(d: CustomFieldDefinition) -> None:
    from plane.bgtasks.field_index import ensure_field_expression_index

    try:
        ensure_field_expression_index.delay(str(d.id))
    except Exception as exc:  # noqa: BLE001 —— 异步优化项失败不阻断字段创建
        logger.warning("field_index.dispatch_failed id=%s exc=%s", d.pk, exc)


def _dispatch_drop_index_task(field_key: str) -> None:
    from plane.bgtasks.field_index import drop_field_expression_index

    try:
        drop_field_expression_index.delay(field_key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("field_index.drop_dispatch_failed key=%s exc=%s", field_key, exc)


# ─────────────────────────────────────────────────────────────────────
# 拖拽排序（浮点插值，BOARD-001 同算法同服务）
# ─────────────────────────────────────────────────────────────────────
class IssuePropertySortOrderView(APIView):
    """PATCH …/issue-properties/{id}/sort-order/  body {prev_id?, next_id?}。"""

    permission_classes = [IsAuthenticated]

    def patch(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        if project.current_user_role < ProjectRole.ADMIN:
            raise AppException("PERM_ROLE_INSUFFICIENT", message="需要项目管理员权限")
        d = _get_scoped_definition(kwargs, project.workspace_id, project)

        payload = request.data or {}
        prev_id, next_id = payload.get("prev_id"), payload.get("next_id")
        orders: list[float | None] = [None, None]
        for i, ref in enumerate((prev_id, next_id)):
            if ref in (None, ""):
                continue
            field_name = "prev_id" if i == 0 else "next_id"
            try:
                ref_uuid = uuid_module.UUID(str(ref))
            except ValueError as err:
                raise AppValidationError(
                    [field_error(field_name, "INVALID_UUID", f"UUID 格式非法：{ref}")]
                ) from err
            ref_d = CustomFieldDefinition.objects.filter(
                id=ref_uuid, workspace_id=project.workspace_id, deleted_at__isnull=True
            ).first()
            if ref_d is None or (d.project_id is None) != (ref_d.project_id is None):
                # 排序只在同一作用域内插值（全局区与项目区互不混排）
                raise AppValidationError(
                    [field_error(field_name, "DOES_NOT_EXIST", "相邻字段不存在或不在同一作用域")]
                )
            orders[i] = ref_d.sort_order

        prev_order, next_order = orders
        new_order = calculate_sort_order(prev_order=prev_order, next_order=next_order)
        if prev_order is not None and next_order is not None and needs_rebalance(prev_order, next_order):
            new_order = _renormalize_scope(d, prev_id, next_id)

        d.sort_order = new_order
        d.updated_by = request.user
        d.save(update_fields=["sort_order", "updated_by", "updated_at"])
        return success_response({"id": str(d.id), "field_key": d.field_key, "sort_order": new_order})


def _renormalize_scope(d: CustomFieldDefinition, prev_id, next_id) -> float:
    """间隙 < 1e-6 时整域重排（65535 等差），再按新相邻序插值——保住「请求→顺序」确定性。"""
    qs = CustomFieldDefinition.objects.filter(
        workspace_id=d.workspace_id, deleted_at__isnull=True, is_active=True
    )
    qs = qs.filter(project__isnull=True) if d.project_id is None else qs.filter(project_id=d.project_id)
    siblings = list(qs.order_by("sort_order", "created_at").exclude(pk=d.pk))
    for idx, sib in enumerate(siblings, start=1):
        sib.sort_order = idx * 65535.0
    CustomFieldDefinition.objects.bulk_update(siblings, ["sort_order"])

    prev_order = next((s.sort_order for s in siblings if str(s.id) == str(prev_id)), None) if prev_id else None
    next_order = next((s.sort_order for s in siblings if str(s.id) == str(next_id)), None) if next_id else None
    return calculate_sort_order(prev_order=prev_order, next_order=next_order)


# ─────────────────────────────────────────────────────────────────────
# WS 全局字段：列表 / 创建（WS Admin，BR-15）
# ─────────────────────────────────────────────────────────────────────
class WorkspaceIssuePropertyListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        ws, member = get_workspace_or_404(kwargs["slug"], request.user)
        if member.role < WorkspaceRole.ADMIN:
            raise AppException("PERM_ROLE_INSUFFICIENT", message="需要工作空间管理员权限")
        qs = fields_for_scope(ws.id, None, active_only=False)
        data = [serialize_definition(d) | {"is_active": d.is_active} for d in qs]
        return success_response(data, meta={"count": len(data), "total_count": len(data)})

    def post(self, request, *args, **kwargs):
        ws, member = get_workspace_or_404(kwargs["slug"], request.user)
        if member.role < WorkspaceRole.ADMIN:
            raise AppException("PERM_ROLE_INSUFFICIENT", message="需要工作空间管理员权限")
        return _create_definition(request, ws.id, kwargs["slug"], project=None)
