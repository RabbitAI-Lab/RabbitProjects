"""live 实时票据服务（COLLAB-004 §4.2/§4.3.3，api-conventions.md §9.5）。

职责：
  - ``issue_realtime_token``  换票：rooms 由服务端按可见性裁决（§4.2.1——前端声明
    位置、服务端裁决权限），RS256 私钥仅 api 持有，TTL LIVE_TICKET_TTL（默认 120s）；
  - ``renew_realtime_token``  续签：旧 jti 轮换（BR-02 90s 静默续签）；房间集以旧票
    为准，请求可增删 issue_rooms 重走可见性校验；
  - ``verify_rooms``  live 周期复核（BR-03）：批量校验 {sub, rooms} → 仅返失效项。

claims 契约（§4.1.1）：sub / rooms[]（≤10）/ ws={sub}:{client_tab_id}（BR-12 去重
与重连幂等键）/ iat / exp / jti。验签端（live）只持公钥，algorithms 锁 RS256。
"""
from __future__ import annotations

import logging
import uuid as uuid_module
from datetime import timedelta
from typing import Any

import jwt
from django.conf import settings
from django.utils import timezone

from plane.base.exception import AppException
from plane.base.middleware import ulid_new
from plane.db.models import (
    Issue,
    Project,
    ProjectMember,
    WorkspaceMember,
)
from plane.db.models.roles import WorkspaceRole

logger = logging.getLogger(__name__)

#: 票据 rooms 声明上限（§2.6 边界表；= project 1 + user 1 + issue ≤8）
MAX_ROOMS_PER_TICKET = 10

#: 续签提示值（秒，BR-02：留 30s 轮换余量；TTL 小于 120 时按 3/4 收缩）
RENEW_AFTER_SECONDS = 90

#: 算法面（禁 none / HS256 混淆——live 侧 algorithms:["RS256"] 对配对）
ALGORITHM = "RS256"


def normalize_pem(raw: str) -> str:
    """PEM 归一：兼容单行 ``\\n`` 转义（.env 单行形态）与真实多行两种注入形态。"""
    if not raw:
        return ""
    return raw.replace("\\n", "\n").strip()


def _private_key() -> str:
    key = normalize_pem(settings.LIVE_JWT_PRIVATE_KEY)
    if not key:
        raise AppException(
            "SERVER_LIVE_SERVICE_UNAVAILABLE",
            message="实时票据签发密钥未配置（LIVE_JWT_PRIVATE_KEY）",
        )
    return key


def _public_key() -> str:
    key = normalize_pem(settings.LIVE_JWT_PUBLIC_KEY)
    if not key:
        raise AppException(
            "SERVER_LIVE_SERVICE_UNAVAILABLE",
            message="实时票据验签密钥未配置（LIVE_JWT_PUBLIC_KEY）",
        )
    return key


def renew_after_seconds() -> int:
    """renew_after 提示值：min(90, TTL 的 3/4)；TTL=120 → 90（BR-02 契约）。"""
    ttl = int(settings.LIVE_TICKET_TTL)
    return min(RENEW_AFTER_SECONDS, max(ttl * 3 // 4, 10))


# ─────────────────────────────────────────────────────────────────────
# 房间可见性（换票时校验，§1.3 订阅规则表）
# ─────────────────────────────────────────────────────────────────────
def project_room(project_id) -> str:
    return f"project:{project_id}"


def issue_room(issue_id) -> str:
    return f"issue:{issue_id}"


def user_room(user_id) -> str:
    return f"user:{user_id}"


def _user_can_read_project(user, project_id) -> bool:
    """project.read 判定（与 _access.get_project_or_404 / permissions.py 同口径）：
    SystemAdmin / WS_ADMIN+ 隐式全可见（rbac §7.4）/ 其余显式 active ProjectMember。
    """
    from plane.db.models import SystemAdmin

    if SystemAdmin.objects.filter(user=user, is_active=True).exists():
        return True
    member = WorkspaceMember.objects.filter(
        workspace__projects=project_id, member=user, is_active=True,
    ).first()
    if member is not None and member.role >= WorkspaceRole.ADMIN:
        return True
    return ProjectMember.objects.filter(
        project_id=project_id, member=user, is_active=True,
    ).exists()


def _visible_issue_ids(user, project_id, issue_ids: list[str]) -> set[str]:
    """任务房间可见性（同详情权限）：issue 存活且属于该项目，且用户可读该项目。
    调用方已过 project.read 闸——此处以 project 归属 + 存活为判定集。
    """
    ids = [str(i) for i in issue_ids]
    if not ids:
        return set()
    found = Issue.objects.filter(
        id__in=ids, project_id=project_id, deleted_at__isnull=True,
    ).values_list("id", flat=True)
    return {str(i) for i in found}


# ─────────────────────────────────────────────────────────────────────
# 签发 / 续签
# ─────────────────────────────────────────────────────────────────────
def issue_realtime_token(
    *, user, project: Project, issue_ids: list[str], client_tab_id: str,
) -> dict[str, Any]:
    """换票（§4.2.1）：rooms 服务端装配——project:{pid} 恒附 + issue 逐个可见性
    校验（不可见 → 403 PERM_DENIED 拒整票：存在性隐藏不适用于换票）+ user 恒附。
    """
    if len(issue_ids) > MAX_ROOMS_PER_TICKET - 2:
        issue_cap = MAX_ROOMS_PER_TICKET - 2
        raise AppException(
            "VALIDATION_INVALID_PARAM",
            message="任务房间数超过上限",
            details=[{
                "field": "issue_rooms", "code": "LIMIT",
                "message": f"单张票据 rooms 声明上限 {MAX_ROOMS_PER_TICKET}（issue_rooms ≤ {issue_cap}）",
            }],
        )
    visible = _visible_issue_ids(user, project.id, issue_ids)
    for iid in issue_ids:
        if str(iid) not in visible:
            raise AppException(
                "PERM_DENIED",
                message="该任务不可访问，换票被拒绝",
                details=[{"field": "issue_rooms", "code": "PERM_DENIED",
                          "message": f"任务 {iid} 不可访问"}],
            )
    rooms = (
        [project_room(project.id)]
        + [issue_room(iid) for iid in issue_ids]
        + [user_room(user.id)]
    )
    return _issue_claims(user_id=user.id, rooms=rooms, client_tab_id=client_tab_id)


def renew_realtime_token(
    *, user, old_token: str, client_tab_id: str,
    issue_rooms: list[str] | None = None,
) -> dict[str, Any]:
    """续签（§4.2 #2，BR-02 旧 jti 轮换）。

    - 旧票验签（公钥 + exp）：无效/过期 → 401 AUTH_TOKEN_EXPIRED（前端重换票）；
    - sub 必须是本人（他票续签拒绝 PERM_DENIED）；
    - 房间集以旧票为准；请求携带 issue_rooms 时增删重走可见性校验
      （删 = 旧票 issue 房间不在新清单；增 = 新清单多出的逐个校验）；
    - 新 jti、新 exp——旧票到期自然失效，jti 轮换可追踪。
    """
    try:
        claims: dict[str, Any] = jwt.decode(
            old_token, _public_key(), algorithms=[ALGORITHM],
            options={"require": ["sub", "exp", "rooms"]},
        )
    except jwt.ExpiredSignatureError:
        raise AppException("AUTH_TOKEN_EXPIRED", message="实时票据已过期，请重新换票") from None
    except jwt.InvalidTokenError:
        raise AppException("AUTH_TOKEN_EXPIRED", message="实时票据无效，请重新换票") from None

    if claims.get("sub") != str(user.id):
        raise AppException("PERM_DENIED", message="不能续签他人的实时票据")

    old_rooms: list[str] = list(claims.get("rooms") or [])
    project_ids = [r.split(":", 1)[1] for r in old_rooms if r.startswith("project:")]
    if not project_ids:
        raise AppException("AUTH_TOKEN_EXPIRED", message="实时票据缺少项目房间声明") from None

    # 房间集推导：project / user 房间沿用旧票；issue 房间按请求增删。
    base_project_rooms = [r for r in old_rooms if r.startswith("project:")]
    user_room_old = user_room(user.id)
    if issue_rooms is None:
        issue_ids = [r.split(":", 1)[1] for r in old_rooms if r.startswith("issue:")]
    else:
        if len(issue_rooms) > MAX_ROOMS_PER_TICKET - 2:
            raise AppException(
                "VALIDATION_INVALID_PARAM",
                message="任务房间数超过上限",
                details=[{"field": "issue_rooms", "code": "LIMIT",
                          "message": f"issue_rooms ≤ {MAX_ROOMS_PER_TICKET - 2}"}],
            )
        issue_ids = [str(i) for i in issue_rooms]

    # issue 可见性重校验（对首个 project 域——多 project 域仅换票端点支持，
    # 续签房间集以旧票为准，issue 增删只在该域内进行）。
    pid = project_ids[0]
    if not _user_can_read_project(user, pid):
        raise AppException("PERM_DENIED", message="项目访问权限已变更，续签被拒绝")
    visible = _visible_issue_ids(user, pid, issue_ids)
    for iid in issue_ids:
        if str(iid) not in visible:
            raise AppException(
                "PERM_DENIED",
                message="该任务不可访问，续签被拒绝",
                details=[{"field": "issue_rooms", "code": "PERM_DENIED",
                          "message": f"任务 {iid} 不可访问"}],
            )
    rooms = (
        base_project_rooms
        + [issue_room(iid) for iid in issue_ids]
        + [user_room_old]
    )
    return _issue_claims(user_id=user.id, rooms=rooms, client_tab_id=client_tab_id)


def _issue_claims(*, user_id, rooms: list[str], client_tab_id: str) -> dict[str, Any]:
    """签发共用基座（换票/续签同构）：claims + 信封响应体。"""
    now = timezone.now()
    ttl = int(settings.LIVE_TICKET_TTL)
    expires_at = now + timedelta(seconds=ttl)
    token = jwt.encode(
        {
            "sub": str(user_id),
            "rooms": rooms,
            "ws": f"{user_id}:{client_tab_id}",   # BR-12 去重 / 重连幂等键（§4.2.1）
            "iat": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
            "jti": ulid_new(),                     # 续签轮换追踪（§4.1.1）
        },
        _private_key(), algorithm=ALGORITHM,
    )
    return {
        "token": token,
        "rooms": rooms,
        "expires_at": expires_at.isoformat(),
        "renew_after": renew_after_seconds(),
    }


# ─────────────────────────────────────────────────────────────────────
# verify-rooms（BR-03 周期复核；live→api 内部端点消费）
# ─────────────────────────────────────────────────────────────────────
def verify_rooms(tickets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """批量校验 ``[{sub, rooms}]`` → 仅返失效项 ``[{sub, rooms: [失效房间]}]``。

    规则（§4.2 契约注）：
      - ``user:{sub}`` 恒有效（本人房间，票据 sub 即本人）；
      - ``project:{pid}`` → project.read 重校验（WS_ADMIN+ / active 成员）；
      - ``issue:{iid}`` → 任务可见性重校验（存活 + 所属项目可读）；
      - sub 解析不到用户（已注销/伪造）→ 其非 user 房间全部失效。
    """
    invalid: list[dict[str, Any]] = []
    for ticket in tickets:
        sub = str(ticket.get("sub") or "")
        rooms = [str(r) for r in (ticket.get("rooms") or []) if r]
        if not sub or not rooms:
            continue
        bad = [r for r in rooms if not _room_still_valid(sub, r)]
        if bad:
            invalid.append({"sub": sub, "rooms": bad})
    return invalid


def _room_still_valid(sub: str, room: str) -> bool:
    from plane.db.models import User

    try:
        user = User.objects.get(id=sub, is_active=True)
    except (User.DoesNotExist, ValueError, TypeError):
        # 用户不存在/已注销：user 房间契约恒有效（live 自会随连接清理），其余失效
        return room.startswith("user:")

    kind, _, rid = room.partition(":")
    if kind == "user":
        return rid == sub                     # 本人房间恒有效
    if kind == "project":
        if not _is_uuid(rid):
            return False
        return Project.objects.filter(
            id=rid, deleted_at__isnull=True,
        ).exists() and _user_can_read_project(user, rid)
    if kind == "issue":
        if not _is_uuid(rid):
            return False
        issue = Issue.objects.filter(
            id=rid, deleted_at__isnull=True,
        ).only("project_id").first()
        return issue is not None and _user_can_read_project(user, issue.project_id)
    return False


def _is_uuid(raw: str) -> bool:
    try:
        uuid_module.UUID(raw)
        return True
    except (ValueError, AttributeError, TypeError):
        return False
