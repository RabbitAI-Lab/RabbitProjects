"""目录同步管理端点（AUTH-011 §4.5，P4 R2）。

挂 ``/api/v1/workspaces/{slug}/directory/``——统一信封 + `directory.manage`
（WS_ADMIN+，rbac 附录 B 登记——实现落 require_role ADMIN，AUTH-007
department.manage 同档先例）。BR-01 单通道互斥在启用路径判定；BR-07 映射
变更强制干跑（存在未确认干跑 → 409 RESOURCE_STATE_INVALID）。
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid as _uuid
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework.views import APIView

from plane.app.permissions import IsAuthenticatedAndActive, require_role
from plane.app.views._access import get_workspace_or_404
from plane.audit.recorder import record
from plane.base.exception import AppException
from plane.base.response import success_response
from plane.db.models import (
    DirectoryPendingAction,
    DirectorySyncRun,
    DirectoryUserMapping,
    LdapDirectoryConfig,
    ScimConnector,
    WorkspaceRole,
)

logger = logging.getLogger("plane.api.directory")


def _require_directory_admin(request, view) -> None:
    """directory.manage（WS_ADMIN+；UT-19：WS_MEMBER 403 PERM_WORKSPACE_ADMIN_REQUIRED）。"""
    require_role(request, view, WorkspaceRole.ADMIN)


def _ws_or_404(request, slug):
    return get_workspace_or_404(slug, request.user)[0]


def _other_channel_enabled(ws) -> str | None:
    if ScimConnector.objects.filter(workspace=ws, is_enabled=True).exists():
        return "scim"
    if LdapDirectoryConfig.objects.filter(workspace=ws, is_enabled=True).exists():
        return "ldap"
    return None


def _dir_record(request, action: str, obj, detail: dict, workspace_id=None):
    record(
        event_key=hashlib.sha256(
            f"directory.mgmt.{action}:{obj.get('id', _uuid.uuid4())}:{request.user.id}:{_uuid.uuid4()}".encode()
        ).hexdigest()[:80],
        category="member",
        action=f"directory_{action}",
        workspace_id=workspace_id,
        actor=request.user,
        obj=obj,
        detail=detail,
    )


def _unconfirmed_dry_run(ws):
    return (
        DirectorySyncRun.objects.filter(workspace=ws, is_dry_run=True, status="dry_run", expires_at__gt=timezone.now())
        .order_by("-created_at")
        .first()
    )


# ── 通道总览 ───────────────────────────────────────────────────────


class DirectoryOverviewView(APIView):
    """GET .../directory/ —— 通道状态 + 最近 run + 待办计数（§3.2）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def get(self, request, slug):
        _require_directory_admin(request, self)
        ws = _ws_or_404(request, slug)
        ldap = LdapDirectoryConfig.objects.filter(workspace=ws).first()
        scim = ScimConnector.objects.filter(workspace=ws).first()
        last_run = DirectorySyncRun.objects.filter(workspace=ws).order_by("-created_at").first()
        pending = DirectoryPendingAction.objects.filter(workspace=ws)
        return success_response(
            {
                "channel": ("ldap" if ldap and ldap.is_enabled else "scim" if scim and scim.is_enabled else None),
                "ldap": None
                if ldap is None
                else {
                    "id": str(ldap.id),
                    "name": ldap.name,
                    "server_uri": ldap.server_uri,
                    "base_dn": ldap.base_dn,
                    "is_enabled": ldap.is_enabled,
                    "sync_interval_minutes": ldap.sync_interval_minutes,
                    "sync_cursor": ldap.sync_cursor,
                },
                "scim": None
                if scim is None
                else {
                    "id": str(scim.id),
                    "name": scim.name,
                    "is_enabled": scim.is_enabled,
                    "token_prefix": scim.token_prefix,
                    "last_used_at": scim.last_used_at,
                },
                "last_run": None
                if last_run is None
                else {
                    "id": str(last_run.id),
                    "status": last_run.status,
                    "counts": last_run.counts,
                    "created_at": last_run.created_at,
                    "full_sync": last_run.full_sync,
                },
                "todos": {
                    "pending_provision": pending.filter(kind="pending_provision", status="pending").count(),
                    "manual_review": pending.filter(kind="manual_review", status="pending").count(),
                },
            }
        )


# ── LDAP 配置 ──────────────────────────────────────────────────────


class LdapConfigView(APIView):
    """POST 创建 / PATCH 修改（映射变更强制干跑，BR-07）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def post(self, request, slug):
        _require_directory_admin(request, self)
        ws = _ws_or_404(request, slug)
        p = request.data or {}
        other = _other_channel_enabled(ws)
        enable = bool(p.get("is_enabled", False))
        if enable and other == "scim":
            raise AppException(
                "RESOURCE_STATE_INVALID",
                message="已启用 SCIM 通道，须先停用并完成归属移交干跑后才能启用 LDAP",
                details=[{"field": "channel", "code": "STATE", "message": "当前启用通道: scim"}],
            )
        for field in ("name", "server_uri", "bind_dn", "bind_secret_ref", "base_dn"):
            if not str(p.get(field) or "").strip():
                raise AppException(
                    "VALIDATION_INVALID_PARAM", message=f"{field} 必填", details=[{"field": field, "code": "REQUIRED"}]
                )
        uri = str(p["server_uri"]).strip()
        if uri.startswith("ldap://") and not p.get("use_starttls"):
            raise AppException(
                "VALIDATION_INVALID_PARAM",
                message="明文 ldap:// 拒绝保存（强制 ldaps:// 或 StartTLS，§4.7-2）",
                details=[{"field": "server_uri", "code": "INVALID"}],
            )
        config = LdapDirectoryConfig.objects.create(
            workspace=ws,
            name=str(p["name"])[:64],
            server_uri=uri,
            bind_dn=str(p["bind_dn"]),
            bind_secret_ref=str(p["bind_secret_ref"]),
            base_dn=str(p["base_dn"]),
            user_filter=str(p.get("user_filter") or "(&(objectClass=user)(mail=*))"),
            attribute_map=p.get("attribute_map") or {},
            group_map=p.get("group_map") or [],
            sync_interval_minutes=min(int(p.get("sync_interval_minutes", 15) or 15), 1440) or 15,
            use_starttls=bool(p.get("use_start_tls", False)),
            is_enabled=enable,
            created_by=request.user,
        )
        return success_response({"id": str(config.id), "is_enabled": enable}, status_code=201)

    def patch(self, request, slug):
        _require_directory_admin(request, self)
        ws = _ws_or_404(request, slug)
        config = LdapDirectoryConfig.objects.filter(workspace=ws).first()
        if config is None:
            from rest_framework.exceptions import NotFound

            raise NotFound("RESOURCE_NOT_FOUND")
        p = request.data or {}
        map_changed = any(k in p for k in ("attribute_map", "group_map", "user_filter"))
        if map_changed:
            dry = _unconfirmed_dry_run(ws)
            if dry is not None:
                raise AppException(
                    "RESOURCE_STATE_INVALID",
                    message="映射变更需先完成干跑确认",
                    details=[
                        {
                            "field": "group_map",
                            "code": "REQUIRED",
                            "message": f"存在未确认的干跑 {str(dry.id)[:8]}…，请先确认或放弃",
                        }
                    ],
                )
        for field in ("name", "user_filter", "sync_interval_minutes", "attribute_map", "group_map"):
            if field in p:
                setattr(config, field, p[field])
        if "is_enabled" in p:
            enable = bool(p["is_enabled"])
            if enable and config.is_enabled is False:
                other = _other_channel_enabled(ws)
                if other == "scim":
                    raise AppException(
                        "RESOURCE_STATE_INVALID",
                        message="已启用 SCIM 通道，须先停用并完成归属移交干跑后才能启用 LDAP",
                        details=[{"field": "channel", "code": "STATE", "message": "当前启用通道: scim"}],
                    )
            config.is_enabled = enable
        config.updated_by = request.user
        config.save()
        return success_response({"id": str(config.id), "is_enabled": config.is_enabled})


class LdapTestView(APIView):
    """POST .../ldap/test/ —— 连接测试（bind+base+filter 样本 5 条脱敏）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def post(self, request, slug):
        _require_directory_admin(request, self)
        ws = _ws_or_404(request, slug)
        config = LdapDirectoryConfig.objects.filter(workspace=ws).first()
        if config is None:
            from rest_framework.exceptions import NotFound

            raise NotFound("RESOURCE_NOT_FOUND")
        from plane.directory.tasks import LdapClient, LdapConnectionError

        try:
            entries = LdapClient(config).paged_search(full_sync=True)[:5]
        except LdapConnectionError as exc:
            raise AppException("VALIDATION_ERROR", message=f"连接失败：{exc}") from None
        return success_response(
            {
                "ok": True,
                "sample": [
                    {"external_id": e["external_id"][:8] + "…", "email": e["email"][:3] + "***" if e["email"] else ""}
                    for e in entries
                ],
            }
        )


# ── 同步触发与台账 ─────────────────────────────────────────────────


class DirectorySyncView(APIView):
    """POST .../sync/ —— 立即同步/干跑：202 异步（api-conventions §13.1）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def post(self, request, slug):
        _require_directory_admin(request, self)
        ws = _ws_or_404(request, slug)
        dry_run = bool((request.data or {}).get("dry_run", False))
        config = LdapDirectoryConfig.objects.filter(workspace=ws).first()
        if config is None or not config.is_enabled:
            raise AppException("RESOURCE_STATE_INVALID", message="LDAP 通道未启用")
        run = DirectorySyncRun.objects.create(
            workspace=ws,
            channel="ldap",
            is_dry_run=dry_run,
            full_sync=True,
            triggered_by="dry_run" if dry_run else "manual",
            expires_at=timezone.now() + timedelta(hours=24) if dry_run else None,
        )
        from plane.directory.tasks import ldap_sync

        transaction.on_commit(
            lambda: ldap_sync.delay(
                str(config.id), full_sync=True, dry_run=dry_run, triggered_by="dry_run" if dry_run else "manual"
            )
        )
        from rest_framework import status as _st
        from rest_framework.response import Response

        return Response(
            {
                "status": "success",
                "data": {
                    "task_id": str(run.id),
                    "state": "queued",
                    "status_url": f"/api/v1/workspaces/{slug}/directory/runs/{run.id}/",
                    "run_id": str(run.id),
                },
            },
            status=_st.HTTP_202_ACCEPTED,
        )


def _cursor_page(qs, request, serialize):
    try:
        per_page = min(int(request.query_params.get("per_page", 50)), 100)
    except ValueError:
        per_page = 50
    cursor = request.query_params.get("cursor")
    offset = 0
    if cursor:
        try:
            _, offset, _ = cursor.split(":")
            offset = int(offset)
        except (ValueError, AttributeError):
            raise AppException("VALIDATION_INVALID_CURSOR", message="游标非法") from None
    rows = list(qs[offset : offset + per_page])
    data = [serialize(r) for r in rows]
    meta = {
        "count": len(rows),
        "total_count": len(rows) + offset,
        "per_page": per_page,
        "next_cursor": f"{per_page}:{offset + per_page}:0" if len(rows) == per_page else None,
        "prev_cursor": f"{per_page}:{max(offset - per_page, 0)}:1" if offset > 0 else None,
    }
    return success_response(data, meta=meta)


class DirectoryRunsView(APIView):
    """GET .../runs/ —— 台账列表（cursor 分页）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def get(self, request, slug):
        _require_directory_admin(request, self)
        ws = _ws_or_404(request, slug)
        qs = DirectorySyncRun.objects.filter(workspace=ws).order_by("-created_at")
        return _cursor_page(
            qs,
            request,
            lambda r: {
                "id": str(r.id),
                "status": r.status,
                "channel": r.channel,
                "is_dry_run": r.is_dry_run,
                "full_sync": r.full_sync,
                "counts": r.counts,
                "created_at": r.created_at,
                "error": r.error[:200],
            },
        )


class DirectoryRunDetailView(APIView):
    """GET .../runs/{id}/ —— 台账明细。"""

    permission_classes = [IsAuthenticatedAndActive]

    def get(self, request, slug, run_id):
        _require_directory_admin(request, self)
        ws = _ws_or_404(request, slug)
        run = DirectorySyncRun.objects.filter(pk=run_id, workspace=ws).first()
        if run is None:
            from rest_framework.exceptions import NotFound

            raise NotFound("RESOURCE_NOT_FOUND")
        return success_response(
            {
                "id": str(run.id),
                "status": run.status,
                "channel": run.channel,
                "is_dry_run": run.is_dry_run,
                "full_sync": run.full_sync,
                "triggered_by": run.triggered_by,
                "counts": run.counts,
                "detail": run.detail,
                "error": run.error,
                "created_at": run.created_at,
                "expires_at": run.expires_at,
            }
        )


class DirectoryRunConfirmView(APIView):
    """POST .../runs/{id}/confirm/ —— 确认干跑并真实执行（BR-07）；
    应用后对影响清单内映射重置 absence_count=0（缺席豁免）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def post(self, request, slug, run_id):
        _require_directory_admin(request, self)
        ws = _ws_or_404(request, slug)
        run = DirectorySyncRun.objects.filter(pk=run_id, workspace=ws).first()
        if run is None:
            from rest_framework.exceptions import NotFound

            raise NotFound("RESOURCE_NOT_FOUND")
        if not run.is_dry_run or run.status != "dry_run":
            raise AppException("RESOURCE_STATE_INVALID", message="仅干跑结果可确认")
        if run.expires_at and run.expires_at <= timezone.now():
            raise AppException("RESOURCE_STATE_INVALID", message="干跑已过期（24h），请重新发起")
        config = LdapDirectoryConfig.objects.filter(workspace=ws).first()
        if config is None:
            raise AppException("RESOURCE_STATE_INVALID", message="通道未启用")
        # 影响清单内映射缺席豁免（BR-04/BR-07）
        emails = {d.get("email") for d in run.detail or [] if d.get("action") in ("created", "updated")}
        DirectoryUserMapping.objects.filter(workspace=ws, email_snapshot__in={e for e in emails if e}).update(
            absence_count=0
        )
        from plane.directory.tasks import ldap_sync

        transaction.on_commit(
            lambda: ldap_sync.delay(str(config.id), full_sync=True, dry_run=False, triggered_by="manual")
        )
        run.status = "confirmed"
        run.confirmed_by = request.user
        run.save(update_fields=["status", "confirmed_by", "updated_at"])
        _dir_record(
            request, "updated", {"type": "sync_run", "id": str(run.id)}, {"confirmed": True}, workspace_id=ws.id
        )
        return success_response({"run_id": str(run.id), "status": "confirmed"})


# ── 待办队列 ───────────────────────────────────────────────────────

_PENDING_KINDS = {"pending_provision", "manual_review"}
_PENDING_STATUS = {"pending", "resolved", "dismissed", "expired"}
_PENDING_ORDERING = {"created_at", "-created_at"}


class PendingActionsView(APIView):
    """GET .../pending-actions/?kind=&status= —— 白名单筛选 + cursor 分页
    （per_page>100 截断且 meta.degraded=true，UT-22）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def get(self, request, slug):
        _require_directory_admin(request, self)
        ws = _ws_or_404(request, slug)
        q = request.query_params
        unknown = set(q) - {"kind", "status", "cursor", "per_page", "count", "ordering"}
        if unknown:
            raise AppException(
                "VALIDATION_INVALID_PARAM",
                message="非法筛选参数",
                details=[{"field": f, "code": "INVALID"} for f in sorted(unknown)],
            )
        ordering = q.get("ordering", "-created_at")
        if ordering not in _PENDING_ORDERING:
            raise AppException(
                "VALIDATION_INVALID_PARAM", message="非法排序字段", details=[{"field": "ordering", "code": "INVALID"}]
            )
        qs = DirectoryPendingAction.objects.filter(workspace=ws)
        if q.get("kind"):
            if q["kind"] not in _PENDING_KINDS:
                raise AppException(
                    "VALIDATION_INVALID_PARAM", message="非法 kind", details=[{"field": "kind", "code": "INVALID"}]
                )
            qs = qs.filter(kind=q["kind"])
        if q.get("status"):
            if q["status"] not in _PENDING_STATUS:
                raise AppException(
                    "VALIDATION_INVALID_PARAM", message="非法 status", details=[{"field": "status", "code": "INVALID"}]
                )
            qs = qs.filter(status=q["status"])
        qs = qs.order_by(ordering, "-created_at")
        try:
            raw_per = int(q.get("per_page", 50))
        except ValueError:
            raw_per = 50
        per_page = min(raw_per, 100)
        cursor = q.get("cursor")
        offset = 0
        if cursor:
            try:
                _, offset, _ = cursor.split(":")
                offset = int(offset)
            except (ValueError, AttributeError):
                raise AppException("VALIDATION_INVALID_CURSOR", message="游标非法") from None
        rows = list(qs[offset : offset + per_page])
        data = [
            {
                "id": str(r.id),
                "kind": r.kind,
                "status": r.status,
                "dedup_key": r.dedup_key,
                "payload": r.payload,
                "resolved_by": str(r.resolved_by_id) if r.resolved_by_id else None,
                "resolved_action": r.resolved_action,
                "created_at": r.created_at,
            }
            for r in rows
        ]
        meta = {
            "count": len(rows),
            "per_page": per_page,
            "degraded": raw_per > 100,
            "next_cursor": f"{per_page}:{offset + per_page}:0" if len(rows) == per_page else None,
            "prev_cursor": f"{per_page}:{max(offset - per_page, 0)}:1" if offset > 0 else None,
        }
        return success_response(data, meta=meta)


class PendingActionResolveView(APIView):
    """POST .../pending-actions/{id}/resolve/ —— 四 action 处置闭环（UT-20/21）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def post(self, request, slug, action_id):
        _require_directory_admin(request, self)
        ws = _ws_or_404(request, slug)
        action = DirectoryPendingAction.objects.filter(pk=action_id, workspace=ws).first()
        if action is None:
            from rest_framework.exceptions import NotFound

            raise NotFound("RESOURCE_NOT_FOUND")
        if action.status != "pending":  # 幂等重放 409（UT-21）
            raise AppException("RESOURCE_STATE_INVALID", message=f"该待办已 {action.status}，不可重复处置")
        decision = str((request.data or {}).get("action") or "")
        if decision not in {"provision", "merge", "dismiss", "expand_seats"}:
            raise AppException(
                "VALIDATION_INVALID_PARAM",
                message="非法处置动作",
                details=[{"field": "action", "code": "NOT_A_CHOICE", "message": decision}],
            )
        note = str((request.data or {}).get("note") or "")[:255]
        if decision in ("provision", "expand_seats"):
            payload = action.payload or {}
            from plane.directory.services import DirectorySyncService

            svc = DirectorySyncService(ws, "ldap", run_id=str(action.source_run_id))
            if not svc._seats_available():
                raise AppException("RESOURCE_STATE_INVALID", message="席位仍不足——请先完成扩容（BR-03 不静默失败）")
            svc._create_or_merge(
                {
                    "external_id": payload.get("external_id", action.dedup_key),
                    "email": payload.get("email", ""),
                    "display_name": payload.get("display_name", ""),
                },
                (payload.get("email") or "").strip().lower(),
            )
        elif decision == "merge":
            target_id = str((request.data or {}).get("merge_target_id") or "")
            mapping = DirectoryUserMapping.objects.filter(pk=target_id, workspace=ws).first()
            if mapping is None:
                raise AppException(
                    "VALIDATION_INVALID_PARAM",
                    message="merge_target_id 无效",
                    details=[{"field": "merge_target_id", "code": "DOES_NOT_EXIST"}],
                )
            payload = action.payload or {}
            new_email = payload.get("new_email", "")
            mapping.email_snapshot = new_email
            mapping.identity_source = "merged"
            mapping.absence_count = 0
            mapping.save(update_fields=["email_snapshot", "identity_source", "absence_count", "updated_at"])
        # dismiss：零账号动作
        action.status = "resolved"
        action.resolved_by = request.user
        action.resolved_action = decision
        action.save(update_fields=["status", "resolved_by", "resolved_action", "updated_at"])
        _dir_record(
            request,
            "resolved",
            {"type": "pending_action", "id": str(action.id)},
            {"decision": decision, "note": note},
            workspace_id=ws.id,
        )
        return success_response({"id": str(action.id), "status": "resolved", "action": decision})


class MappingProtectView(APIView):
    """PATCH .../mappings/{id}/ —— 保护开关（BR-06 第二腿，UT-24）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def patch(self, request, slug, mapping_id):
        _require_directory_admin(request, self)
        ws = _ws_or_404(request, slug)
        mapping = DirectoryUserMapping.objects.filter(pk=mapping_id, workspace=ws).first()
        if mapping is None:
            from rest_framework.exceptions import NotFound

            raise NotFound("RESOURCE_NOT_FOUND")
        value = (request.data or {}).get("is_sync_protected")
        if not isinstance(value, bool):
            raise AppException(
                "VALIDATION_INVALID_PARAM",
                message="is_sync_protected 必须为布尔",
                details=[{"field": "is_sync_protected", "code": "INVALID"}],
            )
        mapping.is_sync_protected = value
        mapping.updated_by = request.user
        mapping.save(update_fields=["is_sync_protected", "updated_by", "updated_at"])
        return success_response({"id": str(mapping.id), "is_sync_protected": value})


# ── SCIM 通道管理 ──────────────────────────────────────────────────


class ScimSetupView(APIView):
    """POST .../scim/ —— 启用 SCIM 并签发 Token（201，仅本次返回明文）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def post(self, request, slug):
        _require_directory_admin(request, self)
        ws = _ws_or_404(request, slug)
        if _other_channel_enabled(ws) == "ldap":
            raise AppException(
                "RESOURCE_STATE_INVALID",
                message="已启用 LDAP 通道，须先停用并完成归属移交干跑后才能启用 SCIM",
                details=[{"field": "channel", "code": "STATE", "message": "当前启用通道: ldap"}],
            )
        ScimConnector.objects.filter(workspace=ws).update(is_enabled=False)
        token = f"scim_{secrets.token_urlsafe(24)}"
        connector = ScimConnector.objects.create(
            workspace=ws,
            name=str((request.data or {}).get("name") or "SCIM 通道"),
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            token_prefix=token[:8],
            created_by=request.user,
            attribute_map=(request.data or {}).get("attribute_map") or {},
            group_map=(request.data or {}).get("group_map") or [],
        )
        _dir_record(
            request,
            "created",
            {"type": "scim_connector", "id": str(connector.id)},
            {"token_prefix": connector.token_prefix},
            workspace_id=ws.id,
        )
        return success_response(
            {
                "id": str(connector.id),
                "token": token,  # 仅本次明文（§3.4）
                "token_prefix": connector.token_prefix,
            },
            status_code=201,
        )


class ScimTokenRevokeView(APIView):
    """DELETE .../scim/token/ —— 吊销（重签调 POST …/scim/）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def delete(self, request, slug):
        _require_directory_admin(request, self)
        ws = _ws_or_404(request, slug)
        updated = ScimConnector.objects.filter(workspace=ws).update(is_enabled=False)
        _dir_record(
            request,
            "updated",
            {"type": "scim_connector", "id": "token"},
            {"revoked": bool(updated)},
            workspace_id=ws.id,
        )
        return success_response({"revoked": bool(updated)})
