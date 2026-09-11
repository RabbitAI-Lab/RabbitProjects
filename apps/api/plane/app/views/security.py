"""客户侧「我的租户安全」端点（AUTH-012 §4.4 后段，P4 R1）。

挂 ``/api/v1/workspaces/{slug}/security/``——客户 WS_ADMIN 的本租户视角：
事件（与平台 L1 同口径、完整视角）、阈值调紧（BR-06 只紧不松）、误报
申诉、L2 工单批准。前置 BR-11 门控同平台侧。
权限口径：事件/申诉 = audit.read（WS_ADMIN+，rbac §8.1 既有码，与
audit_logs 域 _audit_read 同判定）；阈值 = workspace.setting.manage；
L2 批准 = security.l2.approve（按 rbac 附录 B 登记——本实现落
WS_ADMIN 角色，与 audit.read 同档）。
"""

from __future__ import annotations

import logging

from django.utils import timezone
from rest_framework.views import APIView

from plane.app.permissions import IsAuthenticatedAndActive, require_role
from plane.app.views._access import get_workspace_or_404
from plane.app.views.governance import require_governance
from plane.audit.recorder import record
from plane.base.exception import AppException
from plane.base.response import success_response
from plane.db.models import (
    GovernanceTicket,
    RiskAppeal,
    RiskEvent,
    RiskRule,
    WorkspaceRole,
)

logger = logging.getLogger("plane.api.governance")


def _require_ws_admin(request, view) -> None:
    """客户侧三端点共用门槛（audit.read / setting.manage / l2.approve 在
    WS_ADMIN+ 档同判定；RBAC 附录 B 登记口径）。"""
    require_role(request, view, WorkspaceRole.ADMIN)


def _governed_tenant(request, slug):
    """守门 + 取工作空间归属租户；未治理（tenant=NULL）→ 501 同 BR-11。"""
    require_governance()
    ws, _ = get_workspace_or_404(slug, request.user)
    if ws.tenant_id is None:
        raise AppException("SERVER_NOT_IMPLEMENTED", message="该工作空间未纳入租户治理")
    return ws, ws.tenant


class SecurityEventsView(APIView):
    """GET /workspaces/{slug}/security/events/ —— 本租户风控事件（完整视角，
    与平台 L1 同源同字段）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def get(self, request, slug):
        _require_ws_admin(request, self)
        ws, tenant = _governed_tenant(request, slug)
        events = [_serialize(e) for e in RiskEvent.objects.filter(tenant=tenant).order_by("-created_at")[:100]]
        return success_response(events, meta={"count": len(events), "tenant_id": str(tenant.id)})


def _serialize(ev) -> dict:
    """客户侧事件字段（与平台 L1 同口径；证据为引擎侧 L1 统计）。"""
    return {
        "id": str(ev.id),
        "rule_code": ev.rule_code,
        "severity": ev.severity,
        "status": ev.status,
        "evidence": ev.evidence or {},
        "actions": ev.actions or [],
        "created_at": ev.created_at,
    }


class SecurityRulesView(APIView):
    """PATCH /workspaces/{slug}/security/rules/{code}/ —— 阈值调紧（BR-06：
    服务端比对平台默认，更宽 400 TOO_LARGE；更紧落租户覆盖行即时生效）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def patch(self, request, slug, code):
        _require_ws_admin(request, self)
        ws, tenant = _governed_tenant(request, slug)
        platform = RiskRule.objects.filter(code=code, tenant__isnull=True, is_enabled=True).first()
        if platform is None:
            from rest_framework.exceptions import NotFound

            raise NotFound("RESOURCE_NOT_FOUND")
        threshold = request.data.get("threshold")
        if not isinstance(threshold, dict) or not threshold:
            raise AppException(
                "VALIDATION_INVALID_PARAM",
                message="threshold 必须为非空对象",
                details=[{"field": "threshold", "code": "INVALID"}],
            )
        # 只紧不松：请求的每个数值维度都必须严格更小（计数/距离/比率同向）
        for key, value in threshold.items():
            base = (platform.threshold or {}).get(key)
            if isinstance(base, (int, float)) and isinstance(value, (int, float)):
                if value > base:
                    raise AppException(
                        "VALIDATION_INVALID_PARAM",
                        message="阈值只能调紧（更严格），平台默认值不可放宽",
                        details=[
                            {
                                "field": f"threshold.{key}",
                                "code": "TOO_LARGE",
                                "message": f"{value} > 平台默认 {base}（BR-06）",
                            }
                        ],
                    )
        rule, _created = RiskRule.objects.update_or_create(
            code=code,
            tenant=tenant,
            defaults={
                "threshold": threshold,
                "action": platform.action,
                "is_enabled": True,
                "updated_by": request.user,
            },
        )
        import hashlib
        import uuid as _uuid

        record(
            event_key=hashlib.sha256(
                f"gov.rule_tightened:{code}:{request.user.id}:{_uuid.uuid4()}".encode()
            ).hexdigest()[:80],
            category="governance",
            action="rule_tightened",
            workspace_id=ws.id,
            actor=request.user,
            obj={"type": "risk_rule", "id": str(rule.id)},
            detail={"code": code, "threshold": threshold, "platform_default": platform.threshold},
        )
        return success_response(
            {
                "code": code,
                "threshold": rule.threshold,
                "platform_default": platform.threshold,
                "note": "此操作只会更严格（BR-06），已即时生效",
            }
        )


class SecurityAppealView(APIView):
    """POST /workspaces/{slug}/security/events/{id}/appeals/ —— 误报申诉
    （落 RiskAppeal pending，运营复核走 instances/risk-appeals/{id}/review/）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def post(self, request, slug, event_id):
        _require_ws_admin(request, self)
        ws, tenant = _governed_tenant(request, slug)
        ev = RiskEvent.objects.filter(pk=event_id, tenant=tenant).first()
        if ev is None:
            from rest_framework.exceptions import NotFound

            raise NotFound("RESOURCE_NOT_FOUND")
        reason = str(request.data.get("reason") or "").strip()
        if not reason:
            raise AppException(
                "VALIDATION_INVALID_PARAM", message="申诉理由必填", details=[{"field": "reason", "code": "REQUIRED"}]
            )
        appeal = RiskAppeal.objects.create(
            event=ev, tenant=tenant, applicant=request.user, reason=reason, created_by=request.user
        )
        import hashlib
        import uuid as _uuid

        record(
            event_key=hashlib.sha256(
                f"gov.appeal_filed:{ev.id}:{request.user.id}:{_uuid.uuid4()}".encode()
            ).hexdigest()[:80],
            category="governance",
            action="appeal_filed",
            workspace_id=ws.id,
            actor=request.user,
            obj={"type": "risk_appeal", "id": str(appeal.id)},
            detail={"event": str(ev.id), "rule": ev.rule_code},
        )
        return success_response(
            {
                "appeal_id": str(appeal.id),
                "status": appeal.status,
                "event_id": str(ev.id),
            }
        )


class L2TicketApprovalView(APIView):
    """POST /workspaces/{slug}/security/l2-tickets/{id}/approval/ —— 客户
    批准（24h）/驳回平台 L2 工单（BR-07 二级知情闸门的客户侧入口）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def post(self, request, slug, ticket_id):
        _require_ws_admin(request, self)
        ws, tenant = _governed_tenant(request, slug)
        ticket = GovernanceTicket.objects.filter(pk=ticket_id, tenant=tenant).first()
        if ticket is None:
            from rest_framework.exceptions import NotFound

            raise NotFound("RESOURCE_NOT_FOUND")
        if ticket.status != GovernanceTicket.Status.PENDING:
            raise AppException("RESOURCE_STATE_INVALID", message="该工单已裁定")
        decision = str(request.data.get("decision") or "")
        if decision not in {"approve", "reject"}:
            raise AppException(
                "VALIDATION_INVALID_PARAM",
                message="非法裁定",
                details=[{"field": "decision", "code": "NOT_A_CHOICE", "message": decision}],
            )
        if decision == "approve":
            from datetime import timedelta

            granted = timezone.now()
            ticket.status = GovernanceTicket.Status.APPROVED
            ticket.approver = request.user
            ticket.granted_at = granted
            ticket.expires_at = granted + timedelta(hours=24)
        else:
            ticket.status = GovernanceTicket.Status.REJECTED
        ticket.note = str(request.data.get("note") or "")[:255]
        ticket.updated_by = request.user
        ticket.save()
        import hashlib
        import uuid as _uuid

        record(
            event_key=hashlib.sha256(
                f"gov.l2_decided:{ticket.id}:{request.user.id}:{_uuid.uuid4()}".encode()
            ).hexdigest()[:80],
            category="governance",
            action="l2_decided",
            workspace_id=ws.id,
            actor=request.user,
            obj={"type": "l2_ticket", "id": str(ticket.id)},
            detail={"decision": decision, "expires_at": ticket.expires_at.isoformat() if ticket.expires_at else None},
        )
        return success_response(
            {
                "ticket_id": str(ticket.id),
                "status": ticket.status,
                "expires_at": ticket.expires_at,
            }
        )
