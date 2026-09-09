"""全站审计检索/导出/实例端点（AUTH-010 §4.2，Sprint-8 R4）。

- 检索：audit.read（WS_ADMIN+）；workspace 强制过滤（BR-03）+ cursor 分页
  + 筛选白名单（actor/category/action/object_type/ip/search/created_at）。
- 导出：同步 CSV 流式（WF-006 approval-audit/export 直下范式；202 两段式
  随 S9 与 known-debt A#1 统一接入——偏差登记 inflight）+ audit.exported
  自审计（BR-08）+ 链断冻结（§2.4）+ 二次确认密码（BR-04 双授权）。
- 实例级：SystemAdmin 双授权 + 自审计（BR-16）。
"""
from __future__ import annotations

import csv
import logging
from functools import wraps

from django.http import HttpResponse
from rest_framework.views import APIView

from plane.app.permissions import IsAuthenticatedAndActive
from plane.app.views._access import get_workspace_or_404
from plane.audit.recorder import record
from plane.base.exception import AppException
from plane.db.models import AuditLog, SystemAdmin
from plane.db.models.roles import WorkspaceRole

logger = logging.getLogger("plane.api.audit")

#: 可筛选字段（与索引清单比对口径：actor_id→idx_audit_actor 等，§5.3）
FILTER_KEYS = {"actor", "category", "action", "object_type", "ip", "search",
               "created_at"}
ORDERING_WHITELIST = {"created_at", "-created_at", "ip", "-ip",
                       "category", "-category"}


def _audit_read(view_method):
    """audit.read 门槛（WS_ADMIN+；403 PERM_ROLE_INSUFFICIENT）。"""

    @wraps(view_method)
    def wrapper(self, request, *args, **kwargs):
        from plane.app.permissions import require_role

        require_role(request, self, WorkspaceRole.ADMIN)
        return view_method(self, request, *args, **kwargs)

    return wrapper


def _filtered_queryset(request, ws_id):
    qs = AuditLog.objects.filter(workspace_id=ws_id)
    q = request.query_params
    unknown = set(q) - FILTER_KEYS - {"per_page", "cursor", "ordering", "count"}
    if unknown:
        raise AppException(
            "VALIDATION_ERROR",
            message=f"非法筛选参数：{sorted(unknown)}",
            details=[{"field": f, "code": "INVALID",
                      "message": "不在筛选白名单"} for f in sorted(unknown)],
        )
    for key in ("actor", "category", "action", "object_type", "ip"):
        v = q.get(key)
        if v:
            field = "actor_id" if key == "actor" else key
            qs = qs.filter(**{f"{field}__iexact": v})
    search = q.get("search")
    if search:
        qs = qs.filter(object_snapshot__name__icontains=search)
    created = q.get("created_at")
    if created:
        for part in created.split(";"):
            if part == "between" or not part:
                continue
            if ":" in part:
                op, _, val = part.partition(":")
            else:
                op, val = "", part
            if op == "after":
                qs = qs.filter(created_at__gte=val)
            elif op == "before":
                qs = qs.filter(created_at__lte=val)
    ordering = q.get("ordering", "-created_at")
    for field in ordering.split(","):
        if field and field.strip() not in ORDERING_WHITELIST:
            raise AppException(
                "VALIDATION_ERROR", message="非法排序字段",
                details=[{"field": "ordering", "code": "INVALID_PARAM",
                          "message": f"{field} 不在排序白名单"}],
            )
    return qs.order_by(*[f.strip() for f in ordering.split(",") if f.strip()],
                       "-id")


def _cursor_page(qs, request):
    """三段式游标分页（api-conventions §6.2/§6.3 九字段 meta）。"""
    try:
        per_page = min(int(request.query_params.get("per_page", 100)), 100)
    except ValueError:
        per_page = 100
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
    rows = list(qs[offset:offset + per_page])
    data = [{
        "id": str(r.id), "event_key": r.event_key,
        "category": r.category, "action": r.action,
        "actor": r.actor_snapshot or {},
        "object": r.object_snapshot or {},
        "detail": r.detail or {},
        "ip": str(r.ip) if r.ip else None,
        "created_at": r.created_at,
    } for r in rows]
    meta = {
        "count": len(rows),
        "total_count": total if total is not None else len(rows),
        "total_pages": (max((total or 0), 1) + per_page - 1) // per_page
        if total is not None else 1,
        "page": offset // per_page + 1, "per_page": per_page,
        "next_cursor": f"{per_page}:{offset + per_page}:0"
        if len(rows) == per_page else None,
        "prev_cursor": f"{per_page}:{max(offset - per_page, 0)}:1"
        if offset > 0 else None,
        "next_page_results": len(rows) == per_page,
        "prev_page_results": offset > 0,
    }
    return data, meta


class AuditLogListView(APIView):
    """GET .../workspaces/{slug}/audit-logs/ —— 组合检索 + cursor 分页。"""

    permission_classes = [IsAuthenticatedAndActive]

    @_audit_read
    def get(self, request, slug):
        ws, _ = get_workspace_or_404(slug, request.user)
        data, meta = _cursor_page(_filtered_queryset(request, ws.id), request)
        from plane.base.response import success_response
        return success_response(data, meta=meta)


class AuditCatalogView(APIView):
    """GET .../audit-logs/catalog/ —— category/action 注册表（筛选器数据源）。"""

    permission_classes = [IsAuthenticatedAndActive]

    @_audit_read
    def get(self, request, slug):
        from plane.audit.registry import EVENT_REGISTRY
        from plane.base.response import success_response

        get_workspace_or_404(slug, request.user)
        data = [{"category": c, "actions": [
            {"action": a, "label": label} for a, label in actions.items()]}
            for c, actions in EVENT_REGISTRY.items()]
        return success_response(data, meta={"count": len(data),
                                            "total_count": len(data)})


AUDIT_SYNC_EXPORT_ROW_LIMIT = 20_000   # 同步流式上界；超出转 ExportTask 异步两段式


class AuditExportView(APIView):
    """POST .../audit-logs/exports/ —— CSV 导出（BR-04/BR-08）。

    ≤2 万行同步流式（WF-006 直下范式）；超出 202 异步两段式（task_id/
    status_url + MinIO 预签名）——S9 R4 与 known-debt A#1 统一接入，原偏差
    登记随之闭环。audit.exported 自审计与链断冻结两路径齐备。
    """

    permission_classes = [IsAuthenticatedAndActive]

    @_audit_read
    def post(self, request, slug):
        from django.core.cache import cache

        ws, _ = get_workspace_or_404(slug, request.user)
        if cache.get("audit_chain_broken"):  # §2.4 断链冻结导出
            raise AppException("RESOURCE_STATE_INVALID",
                               message="审计链完整性异常，导出已冻结")
        # BR-04 双授权：二次确认密码
        from django.contrib.auth import authenticate

        password = str(request.data.get("password") or "")
        if authenticate(request, email=request.user.email,
                        password=password) is None:
            raise AppException("AUTH_INVALID_CREDENTIALS", message="二次确认密码错误")
        qs = _filtered_queryset(request, ws.id)
        total = qs.count()
        if total > 500_000:  # §2.2 上限
            raise AppException("RESOURCE_LIMIT_EXCEEDED",
                               message="导出上限 50 万行，请收窄条件")
        # S8 A#1 债收口（Sprint-9）：>2 万行转异步两段式（202 + task_id/status_url
        # + MinIO 预签名，ExportTask 统一范式）；小请求保持同步流式
        if total > AUDIT_SYNC_EXPORT_ROW_LIMIT:
            from plane.db.models import ExportTask
            from plane.db.services.health import run_export_task
            task = ExportTask.objects.create(
                export_type="audit_csv", workspace=ws,
                params={"workspace_id": str(ws.id),
                        "filters": {k: v for k, v in request.query_params.items()
                                    if k in FILTER_KEYS},
                        "total_hint": total},
                created_by=request.user)
            run_export_task.delay(str(task.id))
            from rest_framework import status as _st
            from rest_framework.response import Response as _Resp
            return _Resp(
                {"status": "success",
                 "data": {"task_id": str(task.id), "state": task.status,
                          "status_url": f"/api/v1/workspaces/{slug}/exports/{task.id}/"}},
                status=_st.HTTP_202_ACCEPTED)
        conditions = {k: v for k, v in request.query_params.items()
                      if k in FILTER_KEYS}

        class _Buffer:
            def __init__(self):
                self.rows: list[str] = []

            def write(self, s):
                self.rows.append(s)

        buf = _Buffer()
        writer = csv.writer(buf)
        writer.writerow(["id", "event_key", "category", "action", "actor_id",
                         "actor_name", "object_type", "object_id", "object_name",
                         "detail", "ip", "created_at", "hash"])
        for r in qs.iterator(chunk_size=2000):
            writer.writerow([
                r.id, r.event_key, r.category, r.action, r.actor_id,
                (r.actor_snapshot or {}).get("name", ""),
                r.object_type, r.object_id,
                (r.object_snapshot or {}).get("name", ""),
                r.detail, r.ip, r.created_at.isoformat(), r.hash,
            ])
        # BR-08：导出自身落审计
        import hashlib as _hashlib
        import uuid as _uuid

        record(
            event_key=_hashlib.sha256(
                f"audit.export:{ws.id}:{request.user.id}:{total}:{_uuid.uuid4()}"
                .encode()).hexdigest()[:80],
            category="audit", action="exported",
            workspace_id=ws.id, actor=request.user,
            obj={"type": "audit_export", "id": str(ws.id)},
            detail={"conditions": conditions, "rows": total},
        )
        resp = HttpResponse("".join(buf.rows), content_type="text/csv")
        resp["Content-Disposition"] = (
            f'attachment; filename="audit-log-{ws.slug}.csv"')
        resp["Cache-Control"] = "no-store"
        return resp


class InstanceAuditLogListView(APIView):
    """GET /api/v1/instances/audit-logs/ —— 实例级全站检索（双授权 + BR-16 自审计）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def get(self, request):
        # system.audit.read：SystemAdmin 表成员（activity_dlq_admin 同款口径）
        if not SystemAdmin.objects.filter(user=request.user,
                                          is_active=True).exists():
            raise AppException("PERM_ROLE_INSUFFICIENT", message="需要系统审计员身份")
        # 实例级全站视角：SystemAdmin 双授权守门（BR-16 自审计）；
        # AuditLog 非accessible_by 族（独立分区表），行级边界=workspace_id
        # 过滤（BR-03）+ 本端点的系统级授权
        qs = AuditLog.objects.order_by("-created_at", "-id")
        for key in ("category", "action", "actor", "object_type"):
            v = request.query_params.get(key)
            if v:
                field = "actor_id" if key == "actor" else key
                qs = qs.filter(**{f"{field}__iexact": v})
        data, meta = _cursor_page(qs, request)
        # BR-16：实例级检索自身被审计（系统级事件）
        import hashlib as _hashlib
        import uuid as _uuid

        record(
            event_key=_hashlib.sha256(
                f"audit.instance_query:{request.user.id}:{_uuid.uuid4()}"
                .encode()).hexdigest()[:80],
            category="audit", action="instance_query",
            workspace_id=None, actor=request.user,
            obj={"type": "instance", "id": "system"},
            detail={"filters": dict(request.query_params),
                    "rows": meta["count"]},
        )
        from plane.base.response import success_response
        return success_response(data, meta=meta)
