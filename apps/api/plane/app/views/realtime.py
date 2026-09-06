"""live 实时票据三端点（COLLAB-004 §4.2，api-conventions.md §9.5/§9.7）。

  POST …/workspaces/{slug}/projects/{pid}/realtime-token/     换票（project.read）
  POST /api/v1/users/me/realtime-token/renew/                 静默续签（旧 jti 轮换）
  POST /api/v1/internal/realtime/verify-rooms/                live→api 周期复核
                                                              （X-Internal-Key 服务间认证）

换票 rooms 由服务端装配（§4.2.1：前端声明「我在哪」，服务端裁决「你能听哪」）；
issue 不可见 → 403 PERM_DENIED 拒整票（存在性隐藏不适用于换票——任务房间本就以
可见为前提）。file_rooms（FILE-003 §4.4 第四类房间 file:{asset_id}）同拒整票
语义：file.read + can_view_file 单入口裁决。verify-rooms 仅内网：proxy 对
/api/v1/internal/ 前缀不路由（第二道防线），本端点再以 X-Internal-Key 收口
（缺失/不符 403 PERM_DENIED）。
"""
from __future__ import annotations

import uuid as uuid_module

from django.conf import settings
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from plane.app.permissions import IsAuthenticated
from plane.app.views._access import get_project_or_404
from plane.base.exception import AppException
from plane.base.response import success_response
from plane.db.models import ProjectRole
from plane.db.services.realtime_ticket import (
    MAX_ROOMS_PER_TICKET,
    issue_realtime_token,
    renew_realtime_token,
    verify_rooms,
)

#: client_tab_id：前端每标签页 crypto.randomUUID()（§4.2.1）——服务端按不透明
#: 字符串处理，仅做长度/字符集卫生校验（拼入票据 ws 声明）。
MAX_CLIENT_TAB_ID_LEN = 64


def _validate_client_tab_id(raw) -> str:
    value = str(raw or "").strip()
    if not value or len(value) > MAX_CLIENT_TAB_ID_LEN:
        raise AppException(
            "VALIDATION_INVALID_PARAM",
            message="client_tab_id 必须为 1~64 字符的标签页标识",
            details=[{"field": "client_tab_id", "code": "INVALID",
                      "message": "每标签页生成一次的 UUID（§4.2.1）"}],
        )
    return value


def _validate_issue_rooms(raw, *, field: str = "issue_rooms",
                          label: str = "任务") -> list[str]:
    """issue_rooms / file_rooms 列表校验：可省略（= []），元素须为 UUID 字符串。"""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise AppException(
            "VALIDATION_INVALID_PARAM",
            message="请求参数不合法",
            details=[{"field": field, "code": "INVALID",
                      "message": f"{field} 须为{label} ID 数组"}],
        )
    ids: list[str] = []
    for item in raw:
        try:
            ids.append(str(uuid_module.UUID(str(item))))
        except (ValueError, AttributeError, TypeError):
            raise AppException(
                "VALIDATION_INVALID_PARAM",
                message="请求参数不合法",
                details=[{"field": field, "code": "INVALID",
                          "message": f"非法{label} ID：{item!s:.64}"}],
            ) from None
    if len(ids) > MAX_ROOMS_PER_TICKET - 2:
        raise AppException(
            "VALIDATION_INVALID_PARAM",
            message="请求参数不合法",
            details=[{"field": field, "code": "LIMIT",
                      "message": f"单张票据 rooms 声明上限 {MAX_ROOMS_PER_TICKET}"}],
        )
    return ids


class RealtimeTokenView(APIView):
    """POST …/projects/{pid}/realtime-token/ —— 换票（§4.2.1）。"""

    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(
            kwargs["slug"], kwargs["project_id"], request.user)
        # project.read（VIEWER+）：与动态流端点同款显式冗余防护（rbac §5.2）
        if (getattr(project, "current_user_role", None) or 0) < ProjectRole.VIEWER:
            raise AppException("PERM_ROLE_INSUFFICIENT", message="当前角色权限不足")

        body = request.data or {}
        client_tab_id = _validate_client_tab_id(body.get("client_tab_id"))
        issue_ids = _validate_issue_rooms(body.get("issue_rooms"))
        # file_rooms（FILE-003 §4.4 第四类房间）：预览抽屉/版本面板上下文声明的
        # 文件资产——可见性在 service 侧 can_view_file 单入口裁决。
        file_ids = _validate_issue_rooms(body.get("file_rooms"), field="file_rooms",
                                         label="文件")
        return success_response(issue_realtime_token(
            user=request.user, project=project,
            issue_ids=issue_ids, client_tab_id=client_tab_id,
            file_asset_ids=file_ids,
        ))


class RealtimeTokenRenewView(APIView):
    """POST /api/v1/users/me/realtime-token/renew/ —— 旧 jti 轮换续签（BR-02）。

    请求体与换票同构（§4.2.1 注）：``{token, client_tab_id, issue_rooms?,
    file_rooms?}``——issue_rooms / file_rooms 缺省 = 沿用旧票房间集；携带则以清单
    为准（增删均重走可见性校验）。
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        body = request.data or {}
        old_token = str(body.get("token") or "")
        if not old_token:
            raise AppException(
                "VALIDATION_INVALID_PARAM",
                message="请求参数不合法",
                details=[{"field": "token", "code": "REQUIRED",
                          "message": "续签须携带当前实时票据"}],
            )
        client_tab_id = _validate_client_tab_id(body.get("client_tab_id"))
        issue_rooms = None
        if "issue_rooms" in body:
            issue_rooms = _validate_issue_rooms(body.get("issue_rooms"))
        file_rooms = None
        if "file_rooms" in body:
            file_rooms = _validate_issue_rooms(
                body.get("file_rooms"), field="file_rooms", label="文件")
        return success_response(renew_realtime_token(
            user=request.user, old_token=old_token,
            client_tab_id=client_tab_id, issue_rooms=issue_rooms,
            file_rooms=file_rooms,
        ))


class InternalVerifyRoomsView(APIView):
    """POST /api/v1/internal/realtime/verify-rooms/ —— live 60s 批量复核（BR-03）。

    服务间认证（§9.7）：``X-Internal-Key`` 共享密钥；缺失/不符 403 PERM_DENIED。
    不走 Session 认证（无 cookie / CSRF 面），fail-closed：INTERNAL_KEY 未配置时
    一律拒绝。
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        expected = settings.INTERNAL_KEY
        provided = request.headers.get("X-Internal-Key", "")
        if not expected or provided != expected:
            raise AppException("PERM_DENIED", message="服务间认证失败")

        tickets = (request.data or {}).get("tickets")
        if not isinstance(tickets, list) or len(tickets) > 500:
            raise AppException(
                "VALIDATION_INVALID_PARAM",
                message="请求参数不合法",
                details=[{"field": "tickets", "code": "INVALID",
                          "message": "tickets 须为 {sub, rooms} 数组（≤500）"}],
            )
        normalized: list[dict] = []
        for item in tickets:
            if not isinstance(item, dict) or not item.get("sub") \
                    or not isinstance(item.get("rooms"), list):
                raise AppException(
                    "VALIDATION_INVALID_PARAM",
                    message="请求参数不合法",
                    details=[{"field": "tickets", "code": "INVALID",
                              "message": "每个 ticket 须含 sub 与 rooms[]"}],
                )
            normalized.append({"sub": str(item["sub"]),
                               "rooms": [str(r) for r in item["rooms"]]})
        return success_response({"invalid": verify_rooms(normalized)})
