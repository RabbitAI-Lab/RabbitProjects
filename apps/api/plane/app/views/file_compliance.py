"""文件合规端点（FILE-006 §4.4，P4 R6）。

四级策略 PATCH（继承 = 清空全部覆盖字段即软删该层行；version 乐观锁）、
生效解析查询、LegalHold 双人放置/双人解除、DLP 规则 CRUD（配额 20 +
编译回溯验证）。权限：file.compliance.manage（WS_ADMIN+——require_role
ADMIN 同档）；生效查询项目成员可读。
"""

from __future__ import annotations

import hashlib
import time

from django.utils import timezone
from rest_framework.views import APIView

from plane.app.permissions import IsAuthenticatedAndActive, require_role
from plane.app.views._access import get_workspace_or_404
from plane.audit.recorder import record
from plane.base.exception import AppException
from plane.base.response import success_response
from plane.db.models import (
    CompliancePolicy,
    DlpRule,
    FileAsset,
    LegalHold,
    WorkspaceRole,
)

_POLICY_CHOICES = {
    "watermark": {"off", "preview_only", "preview_and_download"},
    "download": {"allow", "deny", "desensitized"},
    "share_link": {"allow", "deny", "password_required"},
}


def _require_admin(request, view):
    require_role(request, view, WorkspaceRole.ADMIN)


def _ws_or_404(request, slug):
    return get_workspace_or_404(slug, request.user)[0]


def _audit(request, action: str, obj: dict, ws_id, detail: dict):
    record(
        event_key=hashlib.sha256(
            f"compliance.{action}:{obj.get('id')}:{request.user.id}:{time.time()}".encode()
        ).hexdigest()[:80],
        category="workflow",
        action="state_changed",
        workspace_id=ws_id,
        actor=request.user,
        obj=obj,
        detail=detail,
    )


def _scope_from_params(ws, p):
    """四级域定位（project_id/folder_id/asset_id 任一；缺省=workspace 级）。"""
    if p.get("asset_id"):
        asset = FileAsset.objects.filter(pk=p["asset_id"], workspace=ws).first()
        if asset is None:
            raise AppException(
                "VALIDATION_ERROR",
                message="asset 不在本空间",
                details=[{"field": "asset_id", "code": "DOES_NOT_EXIST"}],
            )
        return {"asset": asset}
    if p.get("folder_id"):
        from plane.db.models import FileFolder

        folder = FileFolder.objects.filter(pk=p["folder_id"], workspace=ws).first()
        if folder is None:
            raise AppException(
                "VALIDATION_ERROR",
                message="folder 不在本空间",
                details=[{"field": "folder_id", "code": "DOES_NOT_EXIST"}],
            )
        return {"folder": folder}
    if p.get("project_id"):
        from plane.db.models import Project

        project = Project.objects.filter(pk=p["project_id"], workspace=ws).first()
        if project is None:
            raise AppException(
                "VALIDATION_ERROR",
                message="project 不在本空间",
                details=[{"field": "project_id", "code": "DOES_NOT_EXIST"}],
            )
        return {"project": project}
    return {"workspace": ws}


class CompliancePolicyView(APIView):
    """PATCH .../file-compliance/policy/ —— 四级策略（一域一策 + 乐观锁）；
    PATCH 空覆盖集 = 恢复继承（软删该层行，§4.8）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def patch(self, request, slug):
        _require_admin(request, self)
        ws = _ws_or_404(request, slug)
        p = request.data or {}
        scope = _scope_from_params(ws, p)
        unknown = set(p) - {
            *_POLICY_CHOICES,
            "retention_days",
            "dark_watermark",
            "version",
            "project_id",
            "folder_id",
            "asset_id",
        }
        if unknown:
            raise AppException(
                "VALIDATION_ERROR",
                message="非法策略字段",
                details=[{"field": f, "code": "INVALID"} for f in sorted(unknown)],
            )
        for field, allowed in _POLICY_CHOICES.items():
            if field in p and p[field] is not None and p[field] not in allowed:
                raise AppException(
                    "VALIDATION_ERROR",
                    message=f"{field} 取值非法",
                    details=[{"field": field, "code": "INVALID", "message": sorted(allowed)}],
                )
        existing = CompliancePolicy.objects.filter(
            deleted_at__isnull=True, **{f"{k}_id": v.id for k, v in scope.items()}
        ).first()
        overrides = {
            f: p.get(f) for f in ("watermark", "download", "share_link", "retention_days", "dark_watermark") if f in p
        }
        if not any(v is not None for v in overrides.values()):
            # 恢复继承 = 清空/软删该层行
            if existing:
                existing.deleted_at = timezone.now()
                existing.updated_by = request.user
                existing.save(update_fields=["deleted_at", "updated_by", "updated_at"])
                self._invalidate(ws, scope)
            return success_response({"inherited": True})
        # 乐观锁（version 未携 LWW 兜底——§4.8 声明）
        if existing:
            if "version" in p and int(p["version"] or 0) != existing.version:
                raise AppException("RESOURCE_STATE_INVALID", message="策略已被他人修改（version 冲突，§4.8 乐观锁）")
            for f, v in overrides.items():
                setattr(existing, f, v)
            existing.version += 1
            existing.updated_by = request.user
            existing.save()
        else:
            existing = CompliancePolicy.objects.create(created_by=request.user, **scope, **overrides)
        self._invalidate(ws, scope)
        _audit(
            request,
            "updated",
            {"type": "compliance_policy", "id": str(existing.id)},
            ws.id,
            {"scope": list(scope), **{k: str(v) for k, v in overrides.items()}},
        )
        return success_response(
            {
                "id": str(existing.id),
                "version": existing.version,
                **{f: getattr(existing, f) for f in _POLICY_CHOICES},
                "retention_days": existing.retention_days,
                "dark_watermark": existing.dark_watermark,
            }
        )

    @staticmethod
    def _invalidate(ws, scope):
        from plane.db.services.file_compliance import invalidate_policy

        if "asset" in scope:
            invalidate_policy(scope["asset"].id)
        else:
            for asset in FileAsset.objects.filter(workspace=ws)[:200]:
                invalidate_policy(asset.id)


class ComplianceEffectiveView(APIView):
    """GET .../file-compliance/effective/?asset_id= —— 生效解析（成员可读）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def get(self, request, slug):
        ws = _ws_or_404(request, slug)
        asset = FileAsset.objects.filter(pk=request.query_params.get("asset_id") or "", workspace=ws).first()
        if asset is None:
            raise AppException(
                "VALIDATION_ERROR", message="asset_id 无效", details=[{"field": "asset_id", "code": "DOES_NOT_EXIST"}]
            )
        from plane.db.services.file_compliance import resolve_policy

        return success_response(resolve_policy(asset))


class LegalHoldView(APIView):
    """POST .../file-compliance/legal-holds/ —— 双人放置；
    DELETE {id}/ —— 双人解除（confirm_user_id=第二确认人）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def post(self, request, slug):
        _require_admin(request, self)
        ws = _ws_or_404(request, slug)
        p = request.data or {}
        asset = FileAsset.objects.filter(pk=p.get("asset_id"), workspace=ws).first()
        if asset is None:
            raise AppException(
                "VALIDATION_ERROR", message="asset_id 无效", details=[{"field": "asset_id", "code": "DOES_NOT_EXIST"}]
            )
        confirm_id = str(p.get("confirm_user_id") or "")
        from plane.db.models import User

        confirmer = User.objects.filter(pk=confirm_id).first()
        if confirmer is None or confirmer.id == request.user.id:
            raise AppException("PERM_DENIED", message="法务保留需双人确认（确认人≠发起人，BR-07）")
        if LegalHold.objects.filter(asset=asset, released_at__isnull=True).exists():
            raise AppException("RESOURCE_STATE_INVALID", message="该文件已有生效中的法务保留")
        hold = LegalHold.objects.create(
            asset=asset,
            reason=str(p.get("reason") or "")[:255],
            case_ref=str(p.get("case_ref") or "")[:64],
            placed_by=request.user,
            confirmed_by=confirmer,
            created_by=request.user,
        )
        _audit(
            request,
            "updated",
            {"type": "legal_hold", "id": str(hold.id)},
            ws.id,
            {"asset": str(asset.id), "reason": hold.reason},
        )
        return success_response({"id": str(hold.id)}, status_code=201)

    def delete(self, request, slug, hold_id):
        _require_admin(request, self)
        ws = _ws_or_404(request, slug)
        hold = LegalHold.objects.filter(pk=hold_id, asset__workspace=ws).first()
        if hold is None or hold.released_at:
            from rest_framework.exceptions import NotFound

            raise NotFound("RESOURCE_NOT_FOUND")
        confirm_id = str((request.data or {}).get("confirm_user_id") or "")
        from plane.db.models import User

        confirmer = User.objects.filter(pk=confirm_id).first()
        if confirmer is None or confirmer.id == request.user.id:
            raise AppException("PERM_DENIED", message="解除保留同样需双人确认（UT-09）")
        hold.released_at = timezone.now()
        hold.released_confirmed_by = confirmer
        hold.updated_by = request.user
        hold.save(update_fields=["released_at", "released_confirmed_by", "updated_by", "updated_at"])
        return success_response({"released": True})


class DlpRuleView(APIView):
    """GET/POST .../file-compliance/dlp-rules/ + DELETE {id}（配额 20）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def get(self, request, slug):
        _require_admin(request, self)
        ws = _ws_or_404(request, slug)
        rows = DlpRule.objects.filter(workspace=ws, deleted_at__isnull=True)
        return success_response(
            [
                {
                    "id": str(r.id),
                    "name": r.name,
                    "pattern": r.pattern,
                    "is_enabled": r.is_enabled,
                    "created_at": r.created_at,
                }
                for r in rows
            ]
        )

    def post(self, request, slug):
        _require_admin(request, self)
        ws = _ws_or_404(request, slug)
        p = request.data or {}
        from plane.db.services.file_compliance import DLP_RULE_LIMIT, validate_dlp_pattern

        if DlpRule.objects.filter(workspace=ws, deleted_at__isnull=True).count() >= DLP_RULE_LIMIT:
            raise AppException("RESOURCE_LIMIT_EXCEEDED", message=f"DLP 规则每空间上限 {DLP_RULE_LIMIT} 条（UT-11）")
        pattern = str(p.get("pattern") or "")
        try:
            validate_dlp_pattern(pattern)
        except ValueError as exc:
            raise AppException(
                "VALIDATION_ERROR", message=str(exc), details=[{"field": "pattern", "code": "INVALID"}]
            ) from None
        rule = DlpRule.objects.create(
            workspace=ws,
            name=str(p.get("name") or "")[:64],
            pattern=pattern,
            is_enabled=bool(p.get("is_enabled", True)),
            created_by=request.user,
        )
        return success_response({"id": str(rule.id)}, status_code=201)

    def delete(self, request, slug, rule_id):
        _require_admin(request, self)
        ws = _ws_or_404(request, slug)
        rule = DlpRule.objects.filter(pk=rule_id, workspace=ws).first()
        if rule is None:
            from rest_framework.exceptions import NotFound

            raise NotFound("RESOURCE_NOT_FOUND")
        rule.deleted_at = timezone.now()
        rule.save(update_fields=["deleted_at", "updated_at"])
        return success_response({"deleted": True})
