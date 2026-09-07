"""admin 运维端点（INFRA-005 §4.2——Sprint-6 T6-05 后端面）。

  GET  /api/v1/instances/backups/            备份记录列表（system.backup.view）
  POST /api/v1/instances/backups/            立即备份（system.backup.manage；
                                            10 分钟内限 1 次 → 429 信封）
  GET  /api/v1/instances/rate-limit/summary/ 限流概览（system.ratelimit.view：
                                            冻结配额快照 + 降级旗标）

权限载体：SystemAdmin active 成员（与 release-gates/dead-letters 同族）。
drill 触发与产物下载预签名两端口顺延（restore-drill.sh 已承载演练、产物经
MinIO mc 取——known-debt 登记 Sprint-7 随 admin 控制台深化一并）。
"""
from __future__ import annotations

from django.utils import timezone
from rest_framework.views import APIView

from plane.app.permissions import IsAuthenticated
from plane.base.exception import AppException
from plane.base.response import success_response
from plane.base.throttling import BASE_THROTTLES, ReportRateThrottle
from plane.db.models import BackupRun, SystemAdmin


def _assert_system(request, code: str) -> None:
    if SystemAdmin.objects.filter(user=request.user, is_active=True).exists():
        return
    raise AppException("PERM_DENIED", message=f"需要系统运维权限（{code}）")


class BackupRunListView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [*BASE_THROTTLES, ReportRateThrottle]

    def get(self, request):
        _assert_system(request, "system.backup.view")
        rows = (BackupRun.objects.order_by("-started_at")[:50])
        data = [{
            "id": str(r.id), "kind": r.kind, "status": r.status,
            "started_at": r.started_at.isoformat(),
            "finished_at": (r.finished_at.isoformat() if r.finished_at else None),
            "dump_size_bytes": r.dump_size_bytes,
            "checksum_sha256": r.checksum_sha256[:16],
            "object_key": r.object_key, "error": r.error[:200],
            "drill_report": r.drill_report,
        } for r in rows]
        streak = list(BackupRun.objects.order_by("-started_at")
                      .values_list("status", flat=True)[:2])
        return success_response(data, meta={
            "count": len(data),
            "failure_streak": 2 if streak == ["failed", "failed"] else 0,
        })


class BackupRunTriggerView(APIView):
    """立即备份（10 分钟内限 1 次——防手滑连点打爆 worker；429 信封口径）。"""
    permission_classes = [IsAuthenticated]
    throttle_classes = [*BASE_THROTTLES, ReportRateThrottle]

    def post(self, request):
        _assert_system(request, "system.backup.manage")
        recent = BackupRun.objects.filter(
            started_at__gte=timezone.now() - timezone.timedelta(minutes=10),
        ).exclude(status=BackupRun.Status.FAILED).first()
        if recent is not None:
            wait = 600 - int((timezone.now() - recent.started_at).total_seconds())
            raise AppException(
                "RATE_LIMIT_EXCEEDED",
                message=f"备份操作过于频繁，请在 {max(wait, 1) // 60 + 1} 分钟后重试",
                details=[{"field": "retry_after", "code": "RETRY_AFTER",
                          "message": str(max(wait, 1))}])
        from plane.bgtasks.backup import daily_backup
        daily_backup.delay()          # worker 执行（manual 语义由 kind 字段承载
        return success_response(      # 时序图口径；端点 202 快回包）
            {"queued": True}, status_code=202)


class RateLimitSummaryView(APIView):
    """限流概览：冻结配额快照（api-conventions §7.2 单源镜像——数值漂移由
    TC 断言防；edge_blocked/app_blocked 计数归日志索引，P2 口径=配置态）。"""
    permission_classes = [IsAuthenticated]
    throttle_classes = [*BASE_THROTTLES, ReportRateThrottle]

    def get(self, request):
        _assert_system(request, "system.ratelimit.view")
        from plane.base.throttling import (
            AnonRateThrottle,
            AuthBurstRateThrottle,
            BulkRateThrottle,
            PresignRateThrottle,
            ReportRateThrottle,
            SearchRateThrottle,
            ShareUnlockRateThrottle,
            UserRateThrottle,
        )
        snapshot = {
            "user_per_min": UserRateThrottle.rate,
            "anon_per_min": AnonRateThrottle.rate,
            "auth_burst_per_min": AuthBurstRateThrottle.rate,
            "report_per_min": ReportRateThrottle.rate,
            "search_per_min": SearchRateThrottle.rate,
            "presign_per_min": PresignRateThrottle.rate,
            "bulk_per_min": BulkRateThrottle.rate,
            "share_unlock": ShareUnlockRateThrottle.rate,
            "edge": {"api_per_min": 300, "auth_per_min": 10, "public_per_min": 30},
            "source": "api-conventions §7.2（冻结）",
        }
        return success_response({
            "config_snapshot": snapshot, "degraded": False,
            "l2_enabled": bool(getattr(__import__("django.conf", fromlist=["settings"])
                                       .settings, "RATE_LIMIT_ENABLED", False)),
        }, meta={"generated_at": timezone.now().isoformat()})
