"""P4 字段三类型端点（TASK-014 §4.5，P4 R3）。

挂 ``workspaces/{slug}/issue-properties/`` 基座（TASK-008 §4.2 家族）：
  · POST formula/      公式字段创建（DSL 解析 + 环检测 + 复杂度上限）
  · POST cascade/      多级级联（2-5 级 · 整树 ≤5000 · levels 结构）
  · POST relation/     跨项目关联（目标项目白名单 + display_props + max_links）
  · POST validate-expression/   表达式校验（保存前实时反馈）
  · POST projects/{pid}/issues/{iid}/preview-expression/   真实数据预览（3 行）

权限：issue.field.manage（rbac §8.2 实码，WS_ADMIN+——require_role ADMIN 同档）。
"""

from __future__ import annotations

import logging

from rest_framework.views import APIView

from plane.app.permissions import IsAuthenticatedAndActive, require_role
from plane.app.views._access import get_workspace_or_404
from plane.audit.recorder import record
from plane.base.exception import AppException
from plane.base.response import success_response
from plane.db.models import CustomFieldDefinition, WorkspaceRole

logger = logging.getLogger("plane.api.fields")

#: P4 三类型白名单（枚举即代码零 DDL——dynamic-fields §3.1 预留）
P4_ENTERPRISE_TYPES = frozenset({"formula", "cascade_multi", "relation_xproject"})

_DISPLAY_PROPS_WHITELIST = frozenset({"state", "priority", "target_date", "assignees", "sub_issues_count"})


def _require_field_admin(request, view) -> None:
    require_role(request, view, WorkspaceRole.ADMIN)


def _ws_or_404(request, slug):
    return get_workspace_or_404(slug, request.user)[0]


def _audit(request, action: str, obj: dict, ws_id, detail: dict):
    import hashlib
    import uuid as _uuid

    record(
        event_key=hashlib.sha256(
            f"fields.p4.{action}:{obj.get('id')}:{request.user.id}:{_uuid.uuid4()}".encode()
        ).hexdigest()[:80],
        category="workflow",
        action="state_changed",
        workspace_id=ws_id,
        actor=request.user,
        obj=obj,
        detail=detail,
    )


def _common_definition_fields(request, ws, p):
    for field in ("name", "field_key"):
        if not str(p.get(field) or "").strip():
            raise AppException(
                "VALIDATION_INVALID_PARAM", message=f"{field} 必填", details=[{"field": field, "code": "REQUIRED"}]
            )
    key = str(p["field_key"]).strip()
    if not key.startswith("cf_"):
        raise AppException(
            "VALIDATION_INVALID_PARAM",
            message="field_key 必须 cf_ 前缀（snake_case）",
            details=[{"field": "field_key", "code": "INVALID"}],
        )
    if CustomFieldDefinition.objects.filter(workspace=ws, field_key=key, deleted_at__isnull=True).exists():
        raise AppException(
            "VALIDATION_INVALID_PARAM", message="field_key 已存在", details=[{"field": "field_key", "code": "UNIQUE"}]
        )
    return {"name": str(p["name"])[:128], "field_key": key}


class FormulaFieldCreateView(APIView):
    """POST .../issue-properties/formula/ —— 公式字段（BR-01/02/03）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def post(self, request, slug):
        _require_field_admin(request, self)
        ws = _ws_or_404(request, slug)
        p = request.data or {}
        expr = str(p.get("formula") or "").strip()
        from plane.formula.dsl import FormulaComplexityError, FormulaSyntaxError, collect_refs, infer_result_type, parse

        try:
            node = parse(expr)
            result_type = infer_result_type(node)
        except FormulaSyntaxError as exc:
            raise AppException(
                "VALIDATION_CUSTOM_FIELD_INVALID",
                message=f"公式非法：{exc}",
                details=[{"field": "formula", "code": "INVALID", "message": str(exc)}],
            ) from None
        except FormulaComplexityError as exc:
            raise AppException(
                "VALIDATION_CUSTOM_FIELD_INVALID",
                message=f"复杂度超限：{exc}",
                details=[{"field": "formula", "code": "LIMIT", "message": str(exc)}],
            ) from None
        # 环检测（BR-02）：加入该字段后的依赖图
        from plane.formula.derived import build_dep_graph, detect_cycle

        refs = collect_refs(node)
        graph = build_dep_graph(ws.id)
        graph[str(p.get("field_key") or "cf_new")] = refs
        cycle = detect_cycle(graph)
        if cycle:
            raise AppException(
                "RESOURCE_CIRCULAR_DEPENDENCY",
                message="公式依赖成环",
                details=[{"field": "formula", "code": "CYCLE", "message": " → ".join(cycle)}],
            )
        fields = _common_definition_fields(request, ws, p)
        # field_key 占位键修正（环检测用了占位名）
        definition = CustomFieldDefinition.objects.create(
            workspace=ws,
            name=fields["name"],
            field_key=fields["field_key"],
            field_type="formula",
            formula=expr,
            project_id=p.get("project_id") or None,
            created_by=request.user,
        )
        _audit(
            request,
            "created",
            {"type": "field", "id": str(definition.id), "name": fields["name"]},
            ws.id,
            {"kind": "formula", "refs": sorted(refs)},
        )
        return success_response(
            {
                "id": str(definition.id),
                "field_key": fields["field_key"],
                "result_type": result_type,
                "refs": sorted(refs),
            },
            status_code=201,
        )


class CascadeFieldCreateView(APIView):
    """POST .../issue-properties/cascade/ —— 多级级联（2-5 级 · ≤5000 节点）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def post(self, request, slug):
        _require_field_admin(request, self)
        ws = _ws_or_404(request, slug)
        p = request.data or {}
        levels = p.get("levels")
        if not isinstance(levels, list) or not (2 <= len(levels) <= 5):
            raise AppException(
                "VALIDATION_CUSTOM_FIELD_INVALID",
                message="级联层级须 2-5 级",
                details=[
                    {
                        "field": "levels",
                        "code": "INVALID",
                        "message": f"收到 {len(levels) if isinstance(levels, list) else '?'} 级",
                    }
                ],
            )
        node_count = 0
        for lv in levels:
            opts = lv.get("options") if isinstance(lv, dict) else None
            if not isinstance(lv, dict) or not lv.get("name") or not isinstance(opts, list) or not opts:
                raise AppException(
                    "VALIDATION_CUSTOM_FIELD_INVALID",
                    message="每级须 {name, options[]} 且选项非空",
                    details=[{"field": "levels", "code": "INVALID"}],
                )
            for o in opts:
                if not isinstance(o, dict) or not o.get("value") or not o.get("label"):
                    raise AppException(
                        "VALIDATION_CUSTOM_FIELD_INVALID",
                        message="选项须含 label 与 value（存 value 不存 label）",
                        details=[{"field": "levels", "code": "INVALID"}],
                    )
                node_count += 1
        if node_count > 5000:
            raise AppException(
                "RESOURCE_LIMIT_EXCEEDED",
                message="级联树超 5000 节点（BR-09）",
                details=[{"field": "levels", "code": "LIMIT", "message": f"{node_count} 节点"}],
            )
        fields = _common_definition_fields(request, ws, p)
        definition = CustomFieldDefinition.objects.create(
            workspace=ws,
            name=fields["name"],
            field_key=fields["field_key"],
            field_type="cascade_multi",
            cascade_config={"levels": levels},
            project_id=p.get("project_id") or None,
            created_by=request.user,
        )
        _audit(
            request,
            "created",
            {"type": "field", "id": str(definition.id)},
            ws.id,
            {"kind": "cascade", "levels": len(levels), "nodes": node_count},
        )
        return success_response(
            {"id": str(definition.id), "field_key": fields["field_key"], "levels": len(levels), "nodes": node_count},
            status_code=201,
        )


class RelationFieldCreateView(APIView):
    """POST .../issue-properties/relation/ —— 跨项目关联（BR-07/08）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def post(self, request, slug):
        _require_field_admin(request, self)
        ws = _ws_or_404(request, slug)
        p = request.data or {}
        targets = p.get("target_project_ids")
        if not isinstance(targets, list) or not targets:
            raise AppException(
                "VALIDATION_CUSTOM_FIELD_INVALID",
                message="target_project_ids 必填（目标项目清单）",
                details=[{"field": "target_project_ids", "code": "REQUIRED"}],
            )
        from plane.db.models import Project

        valid = {
            str(v)
            for v in Project.objects.filter(workspace=ws, deleted_at__isnull=True, pk__in=targets).values_list(
                "id", flat=True
            )
        }
        invalid = [str(t) for t in targets if str(t) not in valid]
        if invalid:
            raise AppException(
                "VALIDATION_CUSTOM_FIELD_INVALID",
                message="目标项目越域",
                details=[
                    {
                        "field": "target_project_ids",
                        "code": "DOES_NOT_EXIST",
                        "message": f"{invalid[:3]} 不在本工作空间",
                    }
                ],
            )
        props = p.get("display_props") or ["state", "priority"]
        unknown = set(props) - _DISPLAY_PROPS_WHITELIST
        if unknown:
            raise AppException(
                "VALIDATION_CUSTOM_FIELD_INVALID",
                message="回显属性不在白名单",
                details=[{"field": "display_props", "code": "INVALID", "message": sorted(unknown)}],
            )
        max_links = min(int(p.get("max_links", 50) or 50), 200)
        fields = _common_definition_fields(request, ws, p)
        definition = CustomFieldDefinition.objects.create(
            workspace=ws,
            name=fields["name"],
            field_key=fields["field_key"],
            field_type="relation_xproject",
            cascade_config={
                "target_project_ids": [str(t) for t in targets],
                "display_props": props,
                "multiple": bool(p.get("multiple", True)),
                "max_links": max_links,
            },
            project_id=p.get("project_id") or None,
            created_by=request.user,
        )
        _audit(
            request,
            "created",
            {"type": "field", "id": str(definition.id)},
            ws.id,
            {"kind": "relation", "targets": len(targets)},
        )
        return success_response(
            {
                "id": str(definition.id),
                "field_key": fields["field_key"],
                "target_count": len(targets),
                "max_links": max_links,
            },
            status_code=201,
        )


class ValidateExpressionView(APIView):
    """POST .../issue-properties/validate-expression/ —— 校验（L3 限流 10/min）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def post(self, request, slug):
        _require_field_admin(request, self)
        _ws_or_404(request, slug)  # 越域 slug 早 404（守门）
        expr = str((request.data or {}).get("formula") or "")
        from plane.formula.dsl import FormulaComplexityError, FormulaSyntaxError, collect_refs, infer_result_type, parse

        try:
            node = parse(expr)
            result_type = infer_result_type(node)
        except FormulaSyntaxError as exc:
            return success_response({"valid": False, "error": str(exc)})
        except FormulaComplexityError as exc:
            return success_response({"valid": False, "error": str(exc), "complexity": True})
        return success_response({"valid": True, "result_type": result_type, "refs": sorted(collect_refs(node))})


class PreviewExpressionView(APIView):
    """POST .../projects/{pid}/issues/{iid}/preview-expression/ —— 真实预览。"""

    permission_classes = [IsAuthenticatedAndActive]

    def post(self, request, slug, project_id, issue_id):
        _require_field_admin(request, self)
        ws = _ws_or_404(request, slug)
        from plane.db.models import Issue

        issue = Issue.objects.filter(pk=issue_id, project_id=project_id, project__workspace=ws).first()
        if issue is None:
            from rest_framework.exceptions import NotFound

            raise NotFound("RESOURCE_NOT_FOUND")
        expr = str((request.data or {}).get("formula") or "")
        from plane.formula.derived import _issue_props, _sub_values
        from plane.formula.dsl import EvalContext, FormulaError, evaluate, parse

        try:
            node = parse(expr)
            custom = dict(issue.custom_fields or {})
            custom.pop("_meta", None)
            value = evaluate(
                node,
                EvalContext(
                    props=_issue_props(issue),
                    custom=custom,
                    sub_count=_sub_values(issue)["count"],
                    sub_done_count=_sub_values(issue)["done_count"],
                    sub_values=_sub_values(issue)["values"],
                ),
            )
        except FormulaError as exc:
            return success_response({"ok": False, "error": str(exc)[:200]})
        return success_response({"ok": True, "value": value, "issue": {"id": str(issue.id), "name": issue.name}})
