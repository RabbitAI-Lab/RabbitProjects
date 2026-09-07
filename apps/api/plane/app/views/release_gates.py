"""发布门禁端点（QA-001 §4.4——Sprint-6 T6-06，系统管理员面 /instances/）。

  GET    /api/v1/instances/release-gates/                          列表
  GET    /api/v1/instances/release-gates/{id}/                     详情（含事件时间线）
  POST   /api/v1/instances/release-gates/                          创建（CI 首步，幂等）
  POST   /api/v1/instances/release-gates/{id}/gate-events/         CI 回调写门禁
  POST   /api/v1/instances/release-gates/{id}/checklist/{key}/sign/  签署/反签
  POST   /api/v1/instances/release-gates/{id}/verdict/             评审裁决

权限：system.release.manage——仅 SystemAdmin 表 active 成员。CI Token 机器用户
以 SystemAdmin 账号承载（Token 鉴权基建归 Sprint-7+，ADR-0027 同族登记口径）。
非成员一律 403 PERM_DENIED（instances 面存在性无业务敏感性，§4.4 错误表）。
"""
from __future__ import annotations

from rest_framework.views import APIView

from plane.app.permissions import IsAuthenticated
from plane.base.exception import AppException
from plane.base.response import created_response, success_response
from plane.db.models import ReleaseGate, SystemAdmin
from plane.db.services import release_gate as svc


def _assert_release_manage(request) -> None:
    """system.release.manage：仅 SystemAdmin active 成员（QA-001 §4.4）。"""
    if SystemAdmin.objects.filter(user=request.user, is_active=True).exists():
        return
    raise AppException("PERM_DENIED",
                       message="需要发布管理权限（system.release.manage）")


def _get_gate_or_404(gate_id: str) -> ReleaseGate:
    gate = ReleaseGate.objects.filter(pk=gate_id).first()
    if gate is None:
        raise AppException("RESOURCE_NOT_FOUND", message="发布尝试不存在")
    return gate


def _serialize(gate: ReleaseGate, *, with_events: bool = False) -> dict:
    data = {
        "id": str(gate.id), "version": gate.version,
        "commit_sha": gate.commit_sha,
        "gates": {g.removeprefix("gate_"): getattr(gate, g) for g in svc.GATES},
        "artifacts": gate.artifacts, "checklist": gate.checklist,
        "verdict": gate.verdict, "created_at": gate.created_at.isoformat(),
    }
    if with_events:
        data["events"] = [{
            "id": e.id, "event_type": e.event_type, "payload": e.payload,
            "actor": (e.actor.display_name if e.actor else None),
            "created_at": e.created_at.isoformat(),
        } for e in gate.events.all()]
    return data


class ReleaseGateListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        _assert_release_manage(request)
        rows = ReleaseGate.objects.prefetch_related("events")[:50]
        return success_response([_serialize(g) for g in rows],
                                meta={"count": len(rows), "total_count":
                                      ReleaseGate.objects.count()})


class ReleaseGateDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, gate_id: str):
        _assert_release_manage(request)
        return success_response(_serialize(_get_gate_or_404(gate_id),
                                           with_events=True))


class ReleaseGateCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        _assert_release_manage(request)
        version = (request.data or {}).get("version") or ""
        commit_sha = (request.data or {}).get("commit_sha") or ""
        if not version or not commit_sha:
            raise AppException("VALIDATION_ERROR", message="请求参数校验失败",
                                details=[{"field": "version", "code": "REQUIRED",
                                          "message": "version 与 commit_sha 必填"}])
        gate = svc.create_release_gate(version=version, commit_sha=commit_sha,
                                       actor=request.user)
        return created_response(_serialize(gate, with_events=True),
                                location=request.build_absolute_uri(
                                    f"/api/v1/instances/release-gates/{gate.id}/"))


class ReleaseGateEventView(APIView):
    """CI 回调写门禁状态（Idempotency-Key 由 CI 侧保证语义；服务层快照+事件
    同事务追加，重放即幂等覆写同值）。"""

    permission_classes = [IsAuthenticated]

    def post(self, request, gate_id: str):
        _assert_release_manage(request)
        gate = _get_gate_or_404(gate_id)
        payload = request.data or {}
        gate, event_id = svc.update_gate(
            gate, gate_name=payload.get("gate") or "",
            status=payload.get("status") or "",
            artifacts=payload.get("artifacts"), actor=request.user)
        return success_response({"id": str(gate.id), "event_id": event_id,
                                 **{g.removeprefix("gate_"): getattr(gate, g)
                                    for g in svc.GATES
                                    if g == "gate_" + (payload.get("gate") or "")}})


class ReleaseGateSignView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, gate_id: str, key: str):
        _assert_release_manage(request)
        gate = _get_gate_or_404(gate_id)
        payload = request.data or {}
        gate = svc.sign_checklist(gate, key=key,
                                  signed=bool(payload.get("signed")),
                                  note=str(payload.get("note") or ""),
                                  actor=request.user)
        return success_response(_serialize(gate))


class ReleaseGateVerdictView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, gate_id: str):
        _assert_release_manage(request)
        gate = _get_gate_or_404(gate_id)
        payload = request.data or {}
        gate = svc.set_verdict(gate, verdict=str(payload.get("verdict") or ""),
                               note=str(payload.get("note") or ""),
                               actor=request.user)
        return success_response(_serialize(gate, with_events=True))
