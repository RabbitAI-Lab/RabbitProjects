"""租户治理平台侧端点（AUTH-012 §4.4 前段，P4 R1）。

挂实例层 ``/api/v1/instances/``（平台租户为实例级资源，api-conventions
§2.4 不嵌 workspace）；权限码 system.tenant.manage = SystemAdmin 且
is_tenant_ops=True（rbac 附录 B 登记口径）。全端点前置 BR-11 门控：
TENANT_GOVERNANCE_ENABLED=False → 501 SERVER_NOT_IMPLEMENTED（UT-12）。

处置状态机（§4.3/§4.4）：
  freeze 两签——actions/(freeze) 落第一签 → freeze-approval/ 异人二签才
  置 is_frozen + Redis frozen:{tenant}；解除同等级（releases/ 发起 +
  freeze-approval/ 二签，同人 403，BR-03/04）。
  deny 同步档在导出端点判定（§4.5 第 5 强制点），不经本文件。
L1 最小知情（BR-07）：事件序列化只出统计与 ID；业务内容字段经 L2 工单
（GovernanceTicket approved 未过期）由序列化层放行——治理域无 L3 路径。
"""

from __future__ import annotations

import logging

from django.conf import settings as dj_settings
from django.core.cache import cache
from django.utils import timezone
from rest_framework.views import APIView

from plane.app.permissions import IsAuthenticatedAndActive
from plane.audit.recorder import record
from plane.base.exception import AppException
from plane.base.response import success_response
from plane.db.models import (
    FileAsset,
    GovernanceTicket,
    RiskAppeal,
    RiskEvent,
    SystemAdmin,
    Tenant,
    Workspace,
)

logger = logging.getLogger("plane.api.governance")

#: 租户列表排序白名单（§4.4 行 1；非白名单 400 VALIDATION_INVALID_PARAM）
TENANT_ORDERING_WHITELIST = {"-created_at", "created_at", "-is_frozen", "is_frozen", "name"}
EVENT_ORDERING_WHITELIST = {"-created_at", "created_at", "severity", "-severity", "status"}
EVENT_FILTER_KEYS = {"rule", "status", "tenant", "severity"}

#: 冻结 Redis 标记键（中间件快路径消费，§4.3 冻结实现）
FROZEN_KEY = "frozen:{tenant_id}"
#: 限流降速标记键（execute_action throttle 档写入）
THROTTLE_KEY = "risk:throttle:{tenant_id}"


# ── 守门与辅助 ──────────────────────────────────────────────────────


def require_governance() -> None:
    """BR-11 私有化门控：False 一律 501（治理 API 整族不可用）。"""
    if not getattr(dj_settings, "TENANT_GOVERNANCE_ENABLED", False):
        raise AppException("SERVER_NOT_IMPLEMENTED", message="当前部署形态未启用租户治理")


def require_tenant_ops(request) -> None:
    """system.tenant.manage：SystemAdmin 存在性 + tenant_ops 授权位。"""
    if not SystemAdmin.objects.filter(user=request.user, is_active=True, is_tenant_ops=True).exists():
        raise AppException("PERM_DENIED", message="需要租户运营身份（tenant_ops）")


def _gov_record(request, action: str, *, obj, detail, workspace=None):
    """治理动作落审计（governance 域注册表；系统级视角 workspace 可空）。"""
    record(
        event_key=f"gov.{action}:{obj.get('id')}:{request.user.id}:{timezone.now().timestamp()}",
        category="governance",
        action=action,
        workspace_id=(workspace.id if workspace else None),
        actor=request.user,
        obj=obj,
        detail=detail,
    )


def _tenant_or_404(tenant_id):
    tenant = Tenant.objects.filter(pk=tenant_id).first()
    if tenant is None:
        from rest_framework.exceptions import NotFound

        raise NotFound("RESOURCE_NOT_FOUND")
    return tenant


def _event_or_404(event_id):
    ev = RiskEvent.objects.select_related("tenant").filter(pk=event_id).first()
    if ev is None:
        from rest_framework.exceptions import NotFound

        raise NotFound("RESOURCE_NOT_FOUND")
    return ev


def _serialize_event(ev, *, l2_visible: set | None = None) -> dict:
    """事件 L1 序列化（BR-07）：统计与 ID；L2 工单命中时按 scope 放行业务
    字段（l2_visible 为该事件已授权字段集合——由调用方查有效工单组装）。"""
    data = {
        "id": str(ev.id),
        "rule_code": ev.rule_code,
        "severity": ev.severity,
        "status": ev.status,
        "tenant": {"id": str(ev.tenant_id), "name": ev.tenant.name},
        "evidence": ev.evidence or {},  # 引擎侧只落 L1 统计与 ID（BR-05）
        "actions": ev.actions or [],
        "freeze_approval": ev.freeze_approval,
        "created_at": ev.created_at,
    }
    if l2_visible:
        data["l2_fields"] = sorted(l2_visible)
    return data


def _l2_scope_for(ev) -> set:
    """事件当前有效 L2 授权字段集：approved 且未过期的工单 scope 并集。"""
    now = timezone.now()
    rows = GovernanceTicket.objects.filter(
        risk_event=ev, status=GovernanceTicket.Status.APPROVED, expires_at__gt=now
    ).values_list("scope", flat=True)
    fields: set = set()
    for scope in rows:
        for f in (scope or {}).get("fields", []):
            fields.add(f)
    return fields


def _cursor_page(qs, request, serializer):
    """三段式游标分页（api-conventions §6.2/§6.3 九字段 meta——audit 同款）。"""
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
    want_count = request.query_params.get("count", "true") != "false"
    total = qs.count() if want_count else None
    rows = list(qs[offset : offset + per_page])
    data = [serializer(r) for r in rows]
    meta = {
        "count": len(rows),
        "total_count": total if total is not None else len(rows),
        "total_pages": (max((total or 0), 1) + per_page - 1) // per_page if total is not None else 1,
        "page": offset // per_page + 1,
        "per_page": per_page,
        "next_cursor": f"{per_page}:{offset + per_page}:0" if len(rows) == per_page else None,
        "prev_cursor": f"{per_page}:{max(offset - per_page, 0)}:1" if offset > 0 else None,
        "next_page_results": len(rows) == per_page,
        "prev_page_results": offset > 0,
    }
    return data, meta


def _tenant_water(tenant) -> dict:
    """租户水位聚合（§3.2 列：存储/API/风控/状态；配额经 tier_quota）。"""
    from plane.governance.risk_engine import tier_quota

    quota = tier_quota(tenant)
    ws_ids = list(Workspace.objects.filter(tenant=tenant).values_list("id", flat=True))
    from django.db.models import Sum

    storage_used = FileAsset.objects.filter(workspace_id__in=ws_ids).aggregate(s=Sum("size"))["s"] or 0
    storage_limit = quota["storage_bytes"] or 0
    open_events = RiskEvent.objects.filter(tenant=tenant, status=RiskEvent.Status.OPEN).count()
    try:
        throttled = cache.get(THROTTLE_KEY.format(tenant_id=tenant.id)) is not None
    except Exception:  # noqa: BLE001 —— Redis 失联按未限流
        throttled = False
    return {
        "storage": {
            "used": storage_used,
            "limit": storage_limit,
            "ratio": round(storage_used / storage_limit, 4) if storage_limit else None,
        },
        "api_rate_per_minute": quota["api_rate_per_minute"],
        "open_risk_events": open_events,
        "is_frozen": tenant.is_frozen,
        "is_throttled": throttled,
    }


# ── 租户面 ─────────────────────────────────────────────────────────


class TenantListView(APIView):
    """GET /instances/tenants/ —— 总览列表（水位聚合 + ordering 白名单）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def get(self, request):
        require_governance()
        require_tenant_ops(request)
        q = request.query_params
        unknown = set(q) - {"search", "tier", "state", "ordering", "per_page", "cursor", "count"}
        if unknown:
            raise AppException(
                "VALIDATION_INVALID_PARAM",
                message="非法查询参数",
                details=[{"field": f, "code": "INVALID", "message": f"{f} 不在筛选白名单"} for f in sorted(unknown)],
            )
        ordering = q.get("ordering", "-created_at")
        if ordering not in TENANT_ORDERING_WHITELIST:
            raise AppException(
                "VALIDATION_INVALID_PARAM",
                message="非法排序字段",
                details=[{"field": "ordering", "code": "INVALID", "message": f"{ordering} 不在排序白名单"}],
            )
        # 实例级资源非用户域：tenant_ops 全租户视角经 unsafe_all 显式例外
        # （AUTH-006 BR-08——平台治理总览，AUTH-012 §4.4 守门在 require_tenant_ops）
        qs = Tenant.objects.unsafe_all(
            reason="tenant_ops 平台治理总览（AUTH-012 §4.4，实例级资源非用户域）")
        search = q.get("search", "").strip()
        if search:
            qs = qs.filter(name__icontains=search)
        tier = q.get("tier")
        if tier:
            qs = qs.filter(tier=tier)
        state = q.get("state")
        if state == "frozen":
            qs = qs.filter(is_frozen=True)
        elif state == "ok":
            qs = qs.filter(is_frozen=False)
        qs = qs.order_by(ordering, "-created_at")

        def serialize(t):
            return {
                "id": str(t.id),
                "name": t.name,
                "tier": t.tier,
                "seats": t.seats,
                "created_at": t.created_at,
                **_tenant_water(t),
            }

        data, meta = _cursor_page(qs, request, serialize)
        return success_response(data, meta=meta)


class TenantDetailView(APIView):
    """GET /instances/tenants/{id}/ —— 详情（配额/下挂 WS/近期事件 L1）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def get(self, request, tenant_id):
        require_governance()
        require_tenant_ops(request)
        tenant = _tenant_or_404(tenant_id)
        workspaces = [
            {
                "id": str(w.id),
                "name": w.name,
                "slug": w.slug,
                "created_at": w.created_at,
            }
            for w in Workspace.objects.filter(tenant=tenant)
        ]
        events = [_serialize_event(e) for e in RiskEvent.objects.filter(tenant=tenant).order_by("-created_at")[:20]]
        return success_response(
            {
                "id": str(tenant.id),
                "name": tenant.name,
                "tier": tenant.tier,
                "seats": tenant.seats,
                "created_at": tenant.created_at,
                "frozen": {
                    "is_frozen": tenant.is_frozen,
                    "frozen_at": tenant.frozen_at,
                    "reason": tenant.frozen_reason,
                },
                "water": _tenant_water(tenant),
                "workspaces": workspaces,
                "recent_events": events,
            }
        )


#: 配额 PATCH 可调列（§2.3；tier/seats 走 Tenant 本体）
_QUOTA_FIELDS = (
    "storage_bytes",
    "member_limit",
    "api_rate_per_minute",
    "export_rows_per_day",
    "webhook_limit",
    "project_limit",
)


class TenantQuotaView(APIView):
    """PATCH /instances/tenants/{id}/quota/ —— 配额调整（BR-12 审计新旧值）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def patch(self, request, tenant_id):
        require_governance()
        require_tenant_ops(request)
        tenant = _tenant_or_404(tenant_id)
        payload = request.data or {}
        unknown = set(payload) - {*_QUOTA_FIELDS, "tier", "seats"}
        if unknown:
            raise AppException(
                "VALIDATION_INVALID_PARAM",
                message="非法配额字段",
                details=[{"field": f, "code": "INVALID", "message": f"{f} 不可调整"} for f in sorted(unknown)],
            )
        before = {"tier": tenant.tier, "seats": tenant.seats}
        if "tier" in payload:
            if payload["tier"] not in {c for c, _ in Tenant.Plan.choices}:
                raise AppException(
                    "VALIDATION_INVALID_PARAM",
                    message="非法套餐档位",
                    details=[{"field": "tier", "code": "NOT_A_CHOICE", "message": payload["tier"]}],
                )
            tenant.tier = payload["tier"]
        if "seats" in payload:
            try:
                tenant.seats = int(payload["seats"])
            except (TypeError, ValueError):
                raise AppException(
                    "VALIDATION_INVALID_PARAM",
                    message="seats 必须为整数",
                    details=[{"field": "seats", "code": "INVALID"}],
                ) from None
        tenant.save()
        from plane.db.models import TenantQuota

        quota = TenantQuota.objects.filter(tenant=tenant).first()
        if quota is None:
            quota = TenantQuota.objects.create(tenant=tenant, created_by=request.user)
        for f in _QUOTA_FIELDS:
            if f in payload:
                setattr(quota, f, payload[f])
        quota.updated_by = request.user
        quota.save()
        after = {"tier": tenant.tier, "seats": tenant.seats, **{f: getattr(quota, f) for f in _QUOTA_FIELDS}}
        _gov_record(
            request,
            "quota_changed",
            obj={"type": "tenant", "id": str(tenant.id), "name": tenant.name},
            detail={"before": before, "after": after},
        )
        return success_response({"tenant_id": str(tenant.id), "quota": after})


# ── 风控事件面 ─────────────────────────────────────────────────────


class RiskEventListView(APIView):
    """GET /instances/risk-events/ —— 事件流（?rule=&status=&tenant=&severity=
    + ordering/per_page/cursor；rule 走 idx_risk_event_rule，L1 字段集）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def get(self, request):
        require_governance()
        require_tenant_ops(request)
        q = request.query_params
        unknown = set(q) - EVENT_FILTER_KEYS - {"ordering", "per_page", "cursor", "count"}
        if unknown:
            raise AppException(
                "VALIDATION_INVALID_PARAM",
                message="非法筛选参数",
                details=[{"field": f, "code": "INVALID", "message": f"{f} 不在筛选白名单"} for f in sorted(unknown)],
            )
        ordering = q.get("ordering", "-created_at")
        if ordering not in EVENT_ORDERING_WHITELIST:
            raise AppException(
                "VALIDATION_INVALID_PARAM",
                message="非法排序字段",
                details=[{"field": "ordering", "code": "INVALID", "message": f"{ordering} 不在排序白名单"}],
            )
        qs = RiskEvent.objects.select_related("tenant").all()
        if q.get("rule"):
            qs = qs.filter(rule_code=q["rule"])
        if q.get("status"):
            qs = qs.filter(status=q["status"])
        if q.get("severity"):
            qs = qs.filter(severity=q["severity"])
        if q.get("tenant"):
            qs = qs.filter(tenant_id=q["tenant"])
        qs = qs.order_by(ordering, "-created_at")

        def serialize(ev):
            return _serialize_event(ev, l2_visible=_l2_scope_for(ev) or None)

        data, meta = _cursor_page(qs, request, serialize)
        return success_response(data, meta=meta)


#: 合法处置动作（§4.4 行 5；deny 同步档不经本端点）
_DISPOSE_ACTIONS = {"alert", "throttle", "freeze", "dismiss"}


class RiskEventActionsView(APIView):
    """POST /instances/risk-events/{id}/actions/ —— 人工处置。
    freeze=第一签（落 freeze_approval 待二签，不置 is_frozen，BR-04）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def post(self, request, event_id):
        require_governance()
        require_tenant_ops(request)
        ev = _event_or_404(event_id)
        payload = request.data or {}
        action = str(payload.get("action") or "")
        note = str(payload.get("note") or "")
        if action not in _DISPOSE_ACTIONS:
            raise AppException(
                "VALIDATION_INVALID_PARAM",
                message="非法处置动作",
                details=[
                    {
                        "field": "action",
                        "code": "NOT_A_CHOICE",
                        "message": f"{action}（合法：{sorted(_DISPOSE_ACTIONS)}）",
                    }
                ],
            )
        tid = str(ev.tenant_id)
        now_iso = timezone.now().isoformat()
        if action == "freeze":
            ev.freeze_approval = {
                "initiator": str(request.user.id),
                "initiator_name": request.user.display_name,
                "pending": "freeze",
                "requested_at": now_iso,
                "note": note,
            }
            ev.actions = [
                *ev.actions,
                {"action": "freeze_requested", "by": str(request.user.id), "at": now_iso, "note": note},
            ]
            _gov_record(
                request,
                "freeze_requested",
                obj={"type": "risk_event", "id": str(ev.id)},
                detail={"tenant": tid, "rule": ev.rule_code},
                workspace=None,
            )
        else:
            if action == "throttle":
                try:
                    cache.add(THROTTLE_KEY.format(tenant_id=tid), 0.1, timeout=86400)
                except Exception:  # noqa: BLE001
                    logger.warning("governance.throttle_set_failed tenant=%s", tid)
            if action == "dismiss":
                ev.status = RiskEvent.Status.DISMISSED
            else:
                ev.status = RiskEvent.Status.ACTIONED
            ev.actions = [*ev.actions, {"action": action, "by": str(request.user.id), "at": now_iso, "note": note}]
            _gov_record(
                request,
                "disposed",
                obj={"type": "risk_event", "id": str(ev.id)},
                detail={"tenant": tid, "dispose": action},
            )
        ev.updated_by = request.user
        ev.save()
        return success_response(
            {
                "event_id": str(ev.id),
                "status": ev.status,
                "actions": ev.actions,
                "freeze_pending": bool(ev.freeze_approval and ev.freeze_approval.get("pending")),
            }
        )


class FreezeApprovalView(APIView):
    """POST /instances/risk-events/{id}/freeze-approval/ —— 冻结/解除第二签。
    同人 403（BR-04）；二签生效置/撤 is_frozen + Redis frozen:{tenant}。"""

    permission_classes = [IsAuthenticatedAndActive]

    def post(self, request, event_id):
        require_governance()
        require_tenant_ops(request)
        ev = _event_or_404(event_id)
        approval = ev.freeze_approval or {}
        pending = approval.get("pending")
        if not pending:
            raise AppException("RESOURCE_STATE_INVALID", message="该事件无待二签的冻结/解除")
        initiator = approval.get("initiator")
        if str(request.user.id) == initiator:
            raise AppException(
                "PERM_DENIED",
                message="冻结审批人不得与发起人相同",
                details=[{"field": "approver", "code": "INVALID", "message": "发起人与审批人均为同一运营"}],
            )
        tenant = ev.tenant
        tid = str(tenant.id)
        now_iso = timezone.now().isoformat()
        try:
            if pending == "freeze":
                tenant.is_frozen = True
                tenant.frozen_at = timezone.now()
                tenant.frozen_reason = str(approval.get("note") or "")[:255]
                cache.set(FROZEN_KEY.format(tenant_id=tid), 1, timeout=None)
            else:  # release 二签
                tenant.is_frozen = False
                tenant.frozen_at = None
                tenant.frozen_reason = ""
                cache.delete(FROZEN_KEY.format(tenant_id=tid))
        except Exception:  # noqa: BLE001 —— Redis 失联不阻断 DB 状态
            logger.warning("governance.frozen_key_degraded tenant=%s", tid)
        tenant.save()
        approval.update(
            {
                "approver": str(request.user.id),
                "approver_name": request.user.display_name,
                "approved_at": now_iso,
                "pending": None,
            }
        )
        ev.freeze_approval = approval
        ev.status = RiskEvent.Status.ACTIONED
        ev.actions = [
            *ev.actions,
            {
                "action": f"{pending}_approved",
                "by": str(request.user.id),
                "at": now_iso,
                "note": str(request.data.get("note") or ""),
            },
        ]
        ev.updated_by = request.user
        ev.save()
        _gov_record(
            request,
            "freeze_approved",
            obj={"type": "risk_event", "id": str(ev.id)},
            detail={"tenant": tid, "signed": pending, "is_frozen": tenant.is_frozen},
        )
        return success_response(
            {
                "event_id": str(ev.id),
                "tenant_id": tid,
                "is_frozen": tenant.is_frozen,
                "signed": pending,
            }
        )


class ReleaseView(APIView):
    """POST /instances/risk-events/{id}/releases/ —— 解除处置（BR-03 可逆）。
    限流即时解除（200）；冻结发起解除需二签（复用 freeze-approval/ 通道）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def post(self, request, event_id):
        require_governance()
        require_tenant_ops(request)
        ev = _event_or_404(event_id)
        tenant = ev.tenant
        tid = str(tenant.id)
        note = str(request.data.get("note") or "")
        now_iso = timezone.now().isoformat()
        released_now = False
        if tenant.is_frozen:
            ev.freeze_approval = {
                "initiator": str(request.user.id),
                "initiator_name": request.user.display_name,
                "pending": "release",
                "requested_at": now_iso,
                "note": note,
            }
            ev.actions = [
                *ev.actions,
                {"action": "release_requested", "by": str(request.user.id), "at": now_iso, "note": note},
            ]
        else:
            # 限流/告警即时解除
            try:
                cache.delete(THROTTLE_KEY.format(tenant_id=tid))
            except Exception:  # noqa: BLE001
                pass
            ev.status = RiskEvent.Status.ACTIONED
            ev.actions = [*ev.actions, {"action": "released", "by": str(request.user.id), "at": now_iso, "note": note}]
            released_now = True
        ev.updated_by = request.user
        ev.save()
        _gov_record(
            request,
            "released",
            obj={"type": "risk_event", "id": str(ev.id)},
            detail={"tenant": tid, "immediate": released_now},
        )
        return success_response(
            {
                "event_id": str(ev.id),
                "released": released_now,
                "pending_second_sign": not released_now,
            }
        )


# ── L2 工单面（BR-07 最小知情闸门）─────────────────────────────────


class L2TicketCreateView(APIView):
    """POST /instances/l2-tickets/ —— 运营发起 L2 业务内容授权工单。"""

    permission_classes = [IsAuthenticatedAndActive]

    def post(self, request):
        require_governance()
        require_tenant_ops(request)
        payload = request.data or {}
        for field, code in (("risk_event_id", "REQUIRED"), ("scope", "REQUIRED"), ("approve_channel", "REQUIRED")):
            if not payload.get(field):
                raise AppException(
                    "VALIDATION_INVALID_PARAM", message=f"{field} 必填", details=[{"field": field, "code": code}]
                )
        channel = payload["approve_channel"]
        if channel not in {"online", "written"}:
            raise AppException(
                "VALIDATION_INVALID_PARAM",
                message="非法授权通道",
                details=[{"field": "approve_channel", "code": "NOT_A_CHOICE", "message": channel}],
            )
        ev = _event_or_404(payload["risk_event_id"])
        ticket = GovernanceTicket.objects.create(
            tenant=ev.tenant,
            risk_event=ev,
            requested_by=request.user,
            scope=payload["scope"],
            approve_channel=channel,
            note=str(payload.get("note") or "")[:255],
            created_by=request.user,
        )
        _gov_record(
            request,
            "l2_requested",
            obj={"type": "l2_ticket", "id": str(ticket.id)},
            detail={"tenant": str(ev.tenant_id), "event": str(ev.id), "channel": channel},
        )
        return success_response(
            {
                "ticket_id": str(ticket.id),
                "status": ticket.status,
                "approve_channel": channel,
            }
        )


class L2WrittenConfirmView(APIView):
    """POST /instances/l2-tickets/{id}/written-confirm/ —— 书面授权运营确认
    （written_ref 必填，缺失 400 子码 REQUIRED；通过置 approved 24h）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def post(self, request, ticket_id):
        require_governance()
        require_tenant_ops(request)
        ticket = GovernanceTicket.objects.filter(pk=ticket_id).first()
        if ticket is None:
            from rest_framework.exceptions import NotFound

            raise NotFound("RESOURCE_NOT_FOUND")
        if ticket.status != GovernanceTicket.Status.PENDING or ticket.approve_channel != "written":
            raise AppException("RESOURCE_STATE_INVALID", message="仅书面通道的待批准工单可确认")
        written_ref = str(request.data.get("written_ref") or "").strip()
        if not written_ref:
            raise AppException(
                "VALIDATION_INVALID_PARAM",
                message="书面授权工单号必填",
                details=[{"field": "written_ref", "code": "REQUIRED", "message": "written-confirm 需书面授权工单号"}],
            )
        granted = timezone.now()
        ticket.status = GovernanceTicket.Status.APPROVED
        ticket.written_ref = written_ref[:128]
        ticket.approver = request.user
        ticket.granted_at = granted
        from datetime import timedelta

        ticket.expires_at = granted + timedelta(hours=24)
        ticket.updated_by = request.user
        ticket.save()
        _gov_record(
            request,
            "l2_confirmed",
            obj={"type": "l2_ticket", "id": str(ticket.id)},
            detail={"written_ref": written_ref, "expires_at": ticket.expires_at.isoformat()},
        )
        return success_response(
            {
                "ticket_id": str(ticket.id),
                "status": ticket.status,
                "granted_at": ticket.granted_at,
                "expires_at": ticket.expires_at,
            }
        )


class L2RevocationView(APIView):
    """POST /instances/l2-tickets/{id}/revocation/ —— approved 撤回（任一方）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def post(self, request, ticket_id):
        require_governance()
        require_tenant_ops(request)
        ticket = GovernanceTicket.objects.filter(pk=ticket_id).first()
        if ticket is None:
            from rest_framework.exceptions import NotFound

            raise NotFound("RESOURCE_NOT_FOUND")
        if ticket.status != GovernanceTicket.Status.APPROVED:
            raise AppException("RESOURCE_STATE_INVALID", message="仅已批准工单可撤回")
        ticket.status = GovernanceTicket.Status.REVOKED
        ticket.updated_by = request.user
        ticket.save()
        _gov_record(
            request,
            "l2_revoked",
            obj={"type": "l2_ticket", "id": str(ticket.id)},
            detail={"revoked_by": str(request.user.id)},
        )
        return success_response({"ticket_id": str(ticket.id), "status": ticket.status})


# ── 申诉复核面 ─────────────────────────────────────────────────────


class RiskAppealReviewView(APIView):
    """POST /instances/risk-appeals/{id}/review/ —— 复核唯一写路径。
    accepted → 事件 dismissed + 自动解除限流（冻结仍需二签）；两侧落
    reviewed_by/review_note（rejected 通知申诉人由通知域异步承载）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def post(self, request, appeal_id):
        require_governance()
        require_tenant_ops(request)
        appeal = RiskAppeal.objects.select_related("event", "tenant").filter(pk=appeal_id).first()
        if appeal is None:
            from rest_framework.exceptions import NotFound

            raise NotFound("RESOURCE_NOT_FOUND")
        if appeal.status != RiskAppeal.Status.PENDING:
            raise AppException("RESOURCE_STATE_INVALID", message="该申诉已复核")
        decision = str(request.data.get("decision") or "")
        if decision not in {"accepted", "rejected"}:
            raise AppException(
                "VALIDATION_INVALID_PARAM",
                message="非法复核决定",
                details=[{"field": "decision", "code": "NOT_A_CHOICE", "message": decision}],
            )
        note = str(request.data.get("note") or "")[:255]
        appeal.status = RiskAppeal.Status.ACCEPTED if decision == "accepted" else RiskAppeal.Status.REJECTED
        appeal.reviewed_by = request.user
        appeal.review_note = note
        appeal.updated_by = request.user
        appeal.save()
        ev = appeal.event
        now_iso = timezone.now().isoformat()
        if decision == "accepted":
            ev.status = RiskEvent.Status.DISMISSED
            try:  # 限流自动解除（复用 releases 语义）
                cache.delete(THROTTLE_KEY.format(tenant_id=str(ev.tenant_id)))
            except Exception:  # noqa: BLE001
                pass
            ev.actions = [
                *ev.actions,
                {"action": "appeal_accepted", "by": str(request.user.id), "at": now_iso, "note": note},
            ]
        else:
            ev.actions = [
                *ev.actions,
                {"action": "appeal_rejected", "by": str(request.user.id), "at": now_iso, "note": note},
            ]
        ev.updated_by = request.user
        ev.save()
        _gov_record(
            request,
            "appeal_reviewed",
            obj={"type": "risk_appeal", "id": str(appeal.id)},
            detail={"decision": decision, "event": str(ev.id)},
        )
        return success_response(
            {
                "appeal_id": str(appeal.id),
                "decision": decision,
                "event_status": ev.status,
                "reviewed_by": str(request.user.id),
                "review_note": note,
                "freeze_release_needs_second_sign": appeal.tenant.is_frozen,
            }
        )


# ── 边界报告面（§2.6 / BR-10）──────────────────────────────────────


class BoundaryReportCreateView(APIView):
    """POST /instances/tenants/{id}/boundary-reports/ —— 202 异步产出
    （api-conventions §13.1：task_id + status_url）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def post(self, request, tenant_id):
        require_governance()
        require_tenant_ops(request)
        tenant = _tenant_or_404(tenant_id)
        from plane.governance.tasks import generate_boundary_report

        report = tenant.boundary_reports.create(requested_by=request.user, created_by=request.user)
        generate_boundary_report.delay(str(report.id))
        _gov_record(
            request,
            "boundary_report",
            obj={"type": "boundary_report", "id": str(report.id)},
            detail={"tenant": str(tenant.id)},
        )
        from rest_framework import status as _st
        from rest_framework.response import Response

        return Response(
            {
                "status": "success",
                "data": {
                    "report_id": str(report.id),
                    "task_id": str(report.id),
                    "state": report.state,
                    "status_url": f"/api/v1/instances/tenants/{tenant_id}/boundary-reports/{report.id}/",
                },
            },
            status=_st.HTTP_202_ACCEPTED,
        )


class BoundaryReportDetailView(APIView):
    """GET /instances/tenants/{id}/boundary-reports/{report_id}/ —— 状态查询；
    succeeded 后携带 1h 预签名下载 URL（换发幂等）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def get(self, request, tenant_id, report_id):
        require_governance()
        require_tenant_ops(request)
        _tenant_or_404(tenant_id)
        report = None
        from plane.db.models import BoundaryReport

        report = BoundaryReport.objects.filter(pk=report_id, tenant_id=tenant_id).first()
        if report is None:
            from rest_framework.exceptions import NotFound

            raise NotFound("RESOURCE_NOT_FOUND")
        data = {
            "report_id": str(report.id),
            "state": report.state,
            "cross_tenant_hits": report.cross_tenant_hits,
            "created_at": report.created_at,
        }
        if report.state == "succeeded" and report.file_path:
            from plane.governance.tasks import presign_report

            data["download_url"] = presign_report(report.file_path)
            data["download_expires_in"] = 3600
        return success_response(data)
