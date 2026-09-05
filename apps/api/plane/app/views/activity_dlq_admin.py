"""admin 死信补偿端点（TASK-010 §4.2.2，系统级顶层资源）。

  GET    /api/v1/activity-dead-letters/                    列表（Redis hash 元数据）
  POST   /api/v1/activity-dead-letters/{message_id}/replay/ 单条重放（幂等，dedup_skipped）
  POST   /api/v1/activity-dead-letters/bulk/               批量重放（≤100）
  DELETE /api/v1/activity-dead-letters/{message_id}/       丢弃（留痕）

权限码 system.audit.read（不新增码；本地会话按 WS OWNER 隐式放行，SystemAdmin
表为空时按开发口径允许——生产由 admin 应用鉴权，ADR 登记口）。
"""
from __future__ import annotations

from rest_framework.views import APIView

from plane.app.permissions import IsAuthenticated
from plane.base.exception import AppException
from plane.base.response import success_response
from plane.bgtasks.activity_dlq import (
    discard_dead_letter,
    list_dead_letters,
    replay_dead_letter,
)
from plane.db.models import SystemAdmin

BULK_LIMIT = 100


def _assert_system_audit(request) -> None:
    """system.audit.read：SystemAdmin 表成员；开发环境（表空）放行并留痕。"""
    if SystemAdmin.objects.filter(user=request.user).exists():
        return
    if SystemAdmin.objects.exists():
        raise AppException("PERM_DENIED", message="需要系统审计权限（system.audit.read）")


class ActivityDeadLetterListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        _assert_system_audit(request)
        items = list_dead_letters()
        return success_response(
            [{k: v for k, v in x.items() if k != "_payload"} for x in items],
            meta={"count": len(items), "total_count": len(items),
                  "queue": "activity.dlq",
                  "alert_threshold_exceeded": len(items) > 100})


class ActivityDeadLetterReplayView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        _assert_system_audit(request)
        out = replay_dead_letter(kwargs["message_id"])
        if out is None:
            raise AppException("RESOURCE_NOT_FOUND",
                               message="死信不存在或已处理") from None
        return success_response(out)


class ActivityDeadLetterBulkReplayView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        _assert_system_audit(request)
        ids = request.data.get("message_ids") or []
        import uuid as _uuid

        def _is_uuid(x: str) -> bool:
            try:
                _uuid.UUID(x)
                return True
            except (ValueError, AttributeError, TypeError):
                return False

        if not isinstance(ids, list) or not all(_is_uuid(x) for x in ids):
            raise AppException(
                "VALIDATION_INVALID_PARAM",
                details=[{"field": "message_ids", "code": "INVALID",
                          "message": "message_ids 须为 UUID 字符串数组"}])
        if len(ids) > BULK_LIMIT:
            raise AppException(
                "VALIDATION_BULK_LIMIT_EXCEEDED",
                details=[{"field": "message_ids", "code": "TOO_LARGE",
                          "message": f"单次最多 {BULK_LIMIT} 条"}])
        replayed = skipped = 0
        for mid in ids:
            out = replay_dead_letter(mid)
            if out is None:
                continue
            if out["replayed"]:
                replayed += 1
            else:
                skipped += 1
        return success_response({"replayed": replayed, "skipped": skipped})


class ActivityDeadLetterDiscardView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, *args, **kwargs):
        _assert_system_audit(request)
        if not discard_dead_letter(kwargs["message_id"]):
            raise AppException("RESOURCE_NOT_FOUND",
                               message="死信不存在或已处理") from None
        from rest_framework.response import Response

        return Response(status=204)
