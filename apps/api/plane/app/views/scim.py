"""SCIM 2.0 服务端（AUTH-011 §2.5，P4 R2）。

挂根级 ``/scim/v2/``——RFC 7643/7644 协议端点：**豁免统一响应信封**
（AUTH-011 §2.5⑥，ResponseEnvelopeMiddleware 已按前缀放行），请求/响应
使用 SCIM schema，错误返回 SCIM Errors 结构（数字 status + detail）。

认证：Bearer ScimToken（SHA-256 落库仅哈希）；无效 401 AUTH_INVALID_TOKEN。
幂等：POST /Users 按 userName（=邮箱）查重，存在返回 200 既有（Okta 容忍）；
DELETE 拒绝物理删除、按禁用处理 204（BR-05）；PATCH 仅支持 replace
active/name/emails 三类 op。
"""

from __future__ import annotations

import hashlib
import logging

from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from plane.base.response import success_response  # noqa: F401 —— 同族导入口径
from plane.db.models import DirectoryUserMapping, ScimConnector

logger = logging.getLogger("plane.api.scim")

SCIM_SCHEMA_USER = "urn:ietf:params:scim:schemas:core:2.0:User"
SCIM_SCHEMA_LIST = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
SCIM_SCHEMA_ERROR = "urn:ietf:params:scim:api:messages:2.0:Error"
SCIM_SCHEMA_GROUP = "urn:ietf:params:scim:schemas:core:2.0:Group"


def _scim_error(status: int, detail: str, scim_type: str | None = None):
    body = {"schemas": [SCIM_SCHEMA_ERROR], "status": status, "detail": detail}
    if scim_type:
        body["scimType"] = scim_type
    return Response(body, status=status)


def _auth_connector(request):
    """Bearer Token → ScimConnector；失败返回 (None, 401 Response)。"""
    header = request.META.get("HTTP_AUTHORIZATION", "")
    if not header.startswith("Bearer "):
        return None, _scim_error(401, "缺少 Bearer Token", "invalidToken")
    token = header[7:].strip()
    connector = ScimConnector.objects.filter(
        token_hash=hashlib.sha256(token.encode()).hexdigest(), is_enabled=True
    ).first()
    if connector is None:
        return None, _scim_error(401, "Token 无效或已吊销", "invalidToken")
    ScimConnector.objects.filter(pk=connector.pk).update(last_used_at=timezone.now())
    return connector, None


def _scim_user_payload(mapping) -> dict:
    user = mapping.user
    return {
        "schemas": [SCIM_SCHEMA_USER],
        "id": str(mapping.id),
        "externalId": mapping.external_id,
        "userName": user.email,
        "active": user.is_active,
        "name": {"formatted": user.display_name, "displayName": user.display_name},
        "emails": [{"value": user.email, "primary": True}],
        "meta": {"resourceType": "User", "location": f"/scim/v2/Users/{mapping.id}"},
    }


class _ScimBase(APIView):
    """协议端点公共基类：豁免 DRF 认证/权限（自管 Bearer）。"""

    permission_classes = [AllowAny]
    authentication_classes: list = []

    def finalize_response(self, request, *args, **kwargs):
        resp = super().finalize_response(request, *args, **kwargs)
        return resp

    def get_authenticate_header(self, request):
        return 'Bearer realm="scim"'


class ScimUsersView(_ScimBase):
    """POST /scim/v2/Users（开通，幂等）+ GET 列表。"""

    def post(self, request):
        connector, err = _auth_connector(request)
        if err:
            return err
        p = request.data or {}
        user_name = str(p.get("userName") or "").strip().lower()
        if not user_name:
            return _scim_error(400, "userName（邮箱）必填", "invalidValue")
        external_id = (
            str(p.get("externalId") or "")
            or str(p.get("id") or "")
            or f"scim-{hashlib.sha256(user_name.encode()).hexdigest()[:16]}"
        )
        display = (p.get("name") or {}).get("displayName") or user_name.split("@")[0]
        active = p.get("active", True)
        from plane.directory.services import DirectorySyncService

        svc = DirectorySyncService(connector.workspace, "scim")
        existing = (
            DirectoryUserMapping.objects.filter(workspace=connector.workspace, channel="scim", external_id=external_id)
            .select_related("user")
            .first()
        )
        if existing is not None:  # 幂等：返回既有 200
            return Response(_scim_user_payload(existing), status=200)
        svc.reconcile(
            [{"external_id": external_id, "email": user_name, "display_name": display, "active": active}],
            full_sync=False,
        )
        mapping = (
            DirectoryUserMapping.objects.filter(workspace=connector.workspace, channel="scim", external_id=external_id)
            .select_related("user")
            .first()
        )
        if mapping is None:  # 席位满 → 协议 507/409
            return _scim_error(409, "席位不足，账号进入待开通队列（管理员处置）", "uniqueness")
        return Response(_scim_user_payload(mapping), status=201)

    def get(self, request):
        connector, err = _auth_connector(request)
        if err:
            return err
        rows = DirectoryUserMapping.objects.filter(workspace=connector.workspace, channel="scim").select_related(
            "user"
        )[:100]
        return Response(
            {
                "schemas": [SCIM_SCHEMA_LIST],
                "totalResults": rows.count(),
                "Resources": [_scim_user_payload(r) for r in rows],
            }
        )


class ScimUserDetailView(_ScimBase):
    """GET/PUT/PATCH/DELETE /scim/v2/Users/{id}（映射 id）。"""

    def _mapping(self, connector, scim_id):
        return (
            DirectoryUserMapping.objects.filter(pk=scim_id, workspace=connector.workspace, channel="scim")
            .select_related("user")
            .first()
        )

    def get(self, request, scim_id):
        connector, err = _auth_connector(request)
        if err:
            return err
        mapping = self._mapping(connector, scim_id)
        if mapping is None:
            return _scim_error(404, "资源不存在", None)
        return Response(_scim_user_payload(mapping))

    def put(self, request, scim_id):
        connector, err = _auth_connector(request)
        if err:
            return err
        mapping = self._mapping(connector, scim_id)
        if mapping is None:
            return _scim_error(404, "资源不存在", None)
        p = request.data or {}
        return self._apply(mapping, p, full_replace=True)

    def patch(self, request, scim_id):
        connector, err = _auth_connector(request)
        if err:
            return err
        mapping = self._mapping(connector, scim_id)
        if mapping is None:
            return _scim_error(404, "资源不存在", None)
        p = request.data or {}
        ops = p.get("Operations")
        if not isinstance(ops, list):
            return _scim_error(400, "Operations 必填（RFC 7644 PatchOp）", "invalidValue")
        for op in ops:
            if str(op.get("op") or "").lower() != "replace":
                return _scim_error(400, "仅支持 replace op（§2.5）", "invalidValue")
            path = str(op.get("path") or "")
            value = op.get("value")
            if path == "active" or (isinstance(value, dict) and "active" in value):
                active = value.get("active") if isinstance(value, dict) else value
                self._set_active(mapping, bool(active))
            elif path in ("name", "name.formatted", "displayName") or (
                isinstance(value, dict) and "displayName" in value
            ):
                new_name = (value.get("displayName") if isinstance(value, dict) else value) or ""
                if new_name:
                    mapping.user.display_name = str(new_name)
                    mapping.user.save(update_fields=["display_name"])
            elif path == "emails" or (isinstance(value, dict) and "emails" in value):
                # 邮箱变更：不自动归并（§2.6），返回 400 引导 IdP 管理员确认
                return _scim_error(400, "邮箱变更需经人工裁决（BR-10/§2.6）", "mutability")
            else:
                return _scim_error(400, f"不支持的 replace 路径 {path!r}", "invalidValue")
        mapping.refresh_from_db()
        return Response(_scim_user_payload(mapping))

    def delete(self, request, scim_id):
        connector, err = _auth_connector(request)
        if err:
            return err
        mapping = self._mapping(connector, scim_id)
        if mapping is None:
            return _scim_error(404, "资源不存在", None)
        self._set_active(mapping, False)  # BR-05：按禁用处理
        return Response(status=204)

    def _apply(self, mapping, p, *, full_replace: bool):
        user_name = str(p.get("userName") or mapping.user.email).strip().lower()
        if user_name != mapping.user.email.lower():
            return _scim_error(400, "邮箱变更需经人工裁决（BR-10/§2.6）", "mutability")
        display = (p.get("name") or {}).get("displayName")
        if display:
            mapping.user.display_name = str(display)
            mapping.user.save(update_fields=["display_name"])
        self._set_active(mapping, bool(p.get("active", True)))
        mapping.refresh_from_db()
        return Response(_scim_user_payload(mapping))

    @staticmethod
    def _set_active(mapping, active: bool):
        if bool(mapping.user.is_active) == active:
            return
        if not active:
            from plane.directory.services import DirectorySyncService

            svc = DirectorySyncService(mapping.workspace, "scim")
            svc._disable(mapping, source="scim_inactive")
        else:
            mapping.user.is_active = True
            mapping.user.save(update_fields=["is_active"])
            mapping.disabled_at_source = ""
            mapping.save(update_fields=["disabled_at_source", "updated_at"])


class ScimGroupsView(_ScimBase):
    """POST/GET /scim/v2/Groups —— 组推送（成员差集 → 部门归属，BR-11 幂等）。

    组语义按 connector.group_map 的 {"dn", "department"} 匹配落
    WorkspaceMember.department_id（单值，AUTH-007）；角色映射暂以 display
    提示承载（CustomRole 挂接随组映射细化演进）。"""

    def post(self, request):
        connector, err = _auth_connector(request)
        if err:
            return err
        p = request.data or {}
        display = str(p.get("displayName") or "")
        if not display:
            return _scim_error(400, "displayName 必填", "invalidValue")
        members = [
            str(m.get("value") or m.get("display") or "") for m in (p.get("members") or []) if isinstance(m, dict)
        ]
        department = None
        for entry in connector.group_map or []:
            if entry.get("dn") == display:
                department = entry.get("department")
                break
        from plane.db.models import Department, WorkspaceMember

        dept = None
        if department:
            dept = Department.objects.filter(
                workspace=connector.workspace, name=department, deleted_at__isnull=True
            ).first()
        applied = 0
        for ident in members:
            qs = WorkspaceMember.objects.filter(
                workspace=connector.workspace, is_active=True, deleted_at__isnull=True
            ).filter(member__email__iexact=ident)
            if dept is not None:
                qs.update(department=dept)
                applied += 1
        return Response(
            {
                "schemas": [SCIM_SCHEMA_GROUP],
                "id": hashlib.sha256(display.encode()).hexdigest()[:16],
                "displayName": display,
                "members": [{"value": m} for m in members],
                "meta": {"resourceType": "Group", "applied_department": bool(dept), "applied": applied},
            },
            status=201,
        )

    def get(self, request):
        connector, err = _auth_connector(request)
        if err:
            return err
        return Response(
            {
                "schemas": [SCIM_SCHEMA_LIST],
                "totalResults": len(connector.group_map or []),
                "Resources": [
                    {"id": hashlib.sha256(str(g.get("dn", "")).encode()).hexdigest()[:16], "displayName": g.get("dn")}
                    for g in connector.group_map or []
                ],
            }
        )
