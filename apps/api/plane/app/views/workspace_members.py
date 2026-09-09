"""工作空间成员 / 邀请视图（TEAM-002 §4.2）。

10 个端点：
  GET    .../workspaces/{slug}/members/                    列表 + 搜索 + 角色筛选
  PATCH  .../workspaces/{slug}/members/{member_id}/        角色调整（10↔15）
  DELETE .../workspaces/{slug}/members/{member_id}/        移除成员（级联 ProjectMember）
  POST   .../workspaces/{slug}/members/leave/              退出团队
  POST   .../workspaces/{slug}/invitations/                批量邀请（≤ 20 邮箱）
  GET    .../workspaces/{slug}/invitations/                待接受邀请列表
  DELETE .../workspaces/{slug}/invitations/{invite_id}/    撤销邀请
  POST   .../workspaces/{slug}/ownership/transfer/         所有权转让
  GET    /api/v1/invitations/{token}/                      预检（脱敏）
  POST   /api/v1/invitations/{token}/accept/               接受邀请

权限层级收口：
  - members list / detail  → WorkspaceMemberPermission（不同 action 派分）
  - invite / revoke        → require_permission 装饰器（细粒度动作端点）
  - leave                  → require_permission
  - transfer               → require_permission
  - token precheck/accept  → IsAuthenticatedAndActive（业务层判定邮箱匹配）
"""
from __future__ import annotations

import hashlib
import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from plane.app.permissions import (
    IsAuthenticatedAndActive,
    assert_can_manage_member,
    require_permission,
    require_role,
)
from plane.app.serializers.member import (
    WorkspaceInviteLiteSerializer,
    WorkspaceInviteSerializer,
    WorkspaceMemberPatchSerializer,
    WorkspaceMemberSerializer,
    WorkspaceTransferOwnershipSerializer,
    mask_email,
)
from plane.app.views._access import get_workspace_or_404
from plane.base.exception import AppException
from plane.base.response import success_response
from plane.bgtasks.audit import record_audit
from plane.db.models import Department, WorkspaceMember, WorkspaceMemberInvite
from plane.db.models.roles import WorkspaceRole
from plane.db.services.workspace_member import MemberService

logger = logging.getLogger("plane.api.workspace_members")


class _MemberAPIViewPermission(BasePermission):
    """成员管理域 APIView 用权限类 —— 不用 view.action（APIView 无此属性）。

    GET → WS_MEMBER+（workspace.member.read）
    PATCH / DELETE → WS_ADMIN+（workspace.member.manage / workspace.member.remove）
    业务层再叠加层级保护（rbac §7.1）。
    """

    message = "当前角色权限不足"

    def has_permission(self, request, view) -> bool:
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False
        try:
            if request.method in ("GET", "HEAD", "OPTIONS"):
                require_role(request, view, WorkspaceRole.MEMBER)
            else:
                require_role(request, view, WorkspaceRole.ADMIN)
        except Exception:                                  # noqa: BLE001
            raise
        return True


# ────────── 成员列表（GET only — 创建入口在 invitations/） ──────────

class WorkspaceMemberListView(APIView):
    """GET .../workspaces/{slug}/members/ —— 成员列表 + 搜索 + 角色筛选。"""

    permission_classes = [IsAuthenticatedAndActive, _MemberAPIViewPermission]

    def get(self, request, slug):
        ws, member = get_workspace_or_404(slug, request.user)
        # workspace.member.read 由 _MemberAPIViewPermission 守护
        qs = (
            WorkspaceMember.objects.filter(
                workspace=ws, is_active=True, deleted_at__isnull=True,
            )
            .select_related("member")
            .order_by("-role", "created_at")
        )

        # ?search= 前缀匹配 display_name / email（istartswith；不启用 trigram，§2.8）
        search = request.query_params.get("search", "").strip()
        if search:
            from django.db.models import Q

            if len(search) > 64:
                raise AppException(
                    "VALIDATION_ERROR",
                    message="搜索词过长",
                    details=[{"field": "search", "code": "TOO_LONG",
                              "message": "搜索词不超过 64 字符"}],
                )
            qs = qs.filter(
                Q(member__display_name__istartswith=search)
                | Q(member__email__istartswith=search)
            )

        # ?role__gte= 角色筛选（15 = 管理员及以上；10 = 全部）
        role_gte = request.query_params.get("role__gte")
        if role_gte is not None:
            try:
                role_gte_int = int(role_gte)
            except ValueError as exc:
                raise AppException(
                    "VALIDATION_ERROR",
                    message="role__gte 必须是整数",
                    details=[{"field": "role__gte", "code": "INVALID",
                              "message": "role__gte 必须是整数"}],
                ) from exc
            qs = qs.filter(role__gte=role_gte_int)

        # AUTH-007 §2.2：按部门过滤（?department=<id>，可叠 &with_descendants=true）
        department = request.query_params.get("department")
        if department is not None:
            try:
                dept = Department.objects.get(
                    pk=department, workspace=ws, deleted_at__isnull=True,
                )
            except (ValueError, Department.DoesNotExist):
                raise NotFound("RESOURCE_NOT_FOUND") from None
            if request.query_params.get("with_descendants") in ("true", "1"):
                dept_ids = Department.objects.filter(
                    workspace=ws, deleted_at__isnull=True,
                    path__startswith=dept.path,
                ).values_list("id", flat=True)
                qs = qs.filter(department_id__in=dept_ids)
            else:
                qs = qs.filter(department=dept)

        return success_response(WorkspaceMemberSerializer(qs, many=True).data)


# ────────── 成员详情（PATCH 角色 / DELETE 移除） ──────────

class WorkspaceMemberDetailView(APIView):
    """PATCH / DELETE .../workspaces/{slug}/members/{member_id}/"""

    permission_classes = [IsAuthenticatedAndActive, _MemberAPIViewPermission]

    def _get_member(self, ws, member_id):
        try:
            m = WorkspaceMember.objects.select_related("member").get(
                id=member_id, workspace=ws, deleted_at__isnull=True,
            )
        except WorkspaceMember.DoesNotExist:
            raise NotFound("RESOURCE_NOT_FOUND") from None
        return m

    def patch(self, request, slug, member_id):
        ws, _ = get_workspace_or_404(slug, request.user)
        # workspace.member.manage 由 _MemberAPIViewPermission 守护
        s = WorkspaceMemberPatchSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        member = self._get_member(ws, member_id)

        # AUTH-007：挂部门 / 岗位（R2 层级保护同口径——管理成员行即受 §7.1 约束，
        # PM-03：WS_ADMIN 改 WS_OWNER 行 → 403 PERM_ROLE_INSUFFICIENT）
        if any(k in s.validated_data for k in ("department_id", "company_role")):
            operator_role = (
                WorkspaceMember.objects
                .filter(workspace=ws, member=request.user,
                        is_active=True, deleted_at__isnull=True)
                .values_list("role", flat=True).first()
            )
            if operator_role is None:
                raise NotFound("RESOURCE_NOT_FOUND") from None
            if operator_role != WorkspaceRole.OWNER:  # OWNER 全权（rbac §7.1 顶格）
                assert_can_manage_member(
                    operator_role=operator_role, target_role=member.role)
            if "department_id" in s.validated_data:
                new_dept = s.validated_data["department_id"]
                if new_dept is not None:
                    Department.objects.get(
                        pk=new_dept, workspace=ws, deleted_at__isnull=True,
                    )  # 出域 / 已删 → 404
                member.department_id = new_dept
            if "company_role" in s.validated_data:
                member.company_role = s.validated_data["company_role"]
            member.updated_by = request.user
            update_fields = ["updated_by", "updated_at"]
            if "department_id" in s.validated_data:
                update_fields.append("department_id")
            if "company_role" in s.validated_data:
                update_fields.append("company_role")
            member.save(update_fields=update_fields)
            transaction.on_commit(
                lambda: record_audit.delay(
                    "department.member_assigned",
                    actor_id=str(request.user.id),
                    object_id=str(member.id),
                    workspace_id=str(ws.id),
                )
            )

        if "role" in s.validated_data:
            member = MemberService().change_role(
                workspace=ws, member=member,
                new_role=s.validated_data["role"], actor=request.user,
            )
        return success_response(WorkspaceMemberSerializer(member).data)

    def delete(self, request, slug, member_id):
        ws, _ = get_workspace_or_404(slug, request.user)
        # workspace.member.remove 由 _MemberAPIViewPermission 守护
        member = self._get_member(ws, member_id)
        svc = MemberService()
        svc.remove_member(workspace=ws, member=member, actor=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


# ────────── 退出团队（动作子资源） ──────────

class WorkspaceLeaveView(APIView):
    """POST .../workspaces/{slug}/members/leave/ —— 退出团队。"""

    permission_classes = [IsAuthenticatedAndActive]

    @require_permission("workspace.member.leave", scope="workspace")
    def post(self, request, slug):
        ws, _ = get_workspace_or_404(slug, request.user)
        MemberService().leave_workspace(workspace=ws, actor=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


# ────────── 所有权转让 ──────────

class WorkspaceOwnershipTransferView(APIView):
    """POST .../workspaces/{slug}/ownership/transfer/ —— 双重确认原子互换。"""

    permission_classes = [IsAuthenticatedAndActive]

    @require_permission("workspace.transfer", scope="workspace")
    def post(self, request, slug):
        ws, _ = get_workspace_or_404(slug, request.user)
        s = WorkspaceTransferOwnershipSerializer(data=request.data)
        s.is_valid(raise_exception=True)

        # 目标成员必须在本空间 active（取得到才算可见；非本空间 → 404）
        try:
            target = WorkspaceMember.objects.select_related("member").get(
                id=s.validated_data["new_owner_member_id"],
                workspace=ws,
                deleted_at__isnull=True,
            )
        except WorkspaceMember.DoesNotExist:
            raise NotFound("RESOURCE_NOT_FOUND") from None

        result = MemberService().transfer_ownership(
            workspace=ws, target=target, actor=request.user,
            confirm_name=s.validated_data["confirm_name"],
        )
        return success_response(result)


# ────────── 批量邀请 / 待接受邀请列表 ──────────

class WorkspaceInvitationListCreateView(APIView):
    """POST / GET .../workspaces/{slug}/invitations/"""

    permission_classes = [IsAuthenticatedAndActive]

    @require_permission("workspace.member.invite", scope="workspace")
    def post(self, request, slug):
        ws, _ = get_workspace_or_404(slug, request.user)
        s = WorkspaceInviteSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        svc = MemberService()
        raw_results = svc.invite_members(
            workspace=ws, actor=request.user,
            emails=s.validated_data["emails"],
            role=s.validated_data["role"],
        )

        # 构造对外响应：剥离 _token_plain；SMTP 降级模式才回显 invite_links
        invite_links = {}
        results = []
        for r in raw_results:
            entry = {k: v for k, v in r.items() if not k.startswith("_")}
            if r.get("status") == "invited":
                token = r.get("_token_plain")
                entry["invite_id"] = r["invite_id"]
                entry["expires_at"] = r["expires_at"]
                entry["refreshed"] = r.get("refreshed", False)
                # SMTP 降级回显（INFRA-004）：仅 SMTP_HOST 未配置时
                if not getattr(settings, "SMTP_HOST", "") and token:
                    invite_links[r["email"]] = (
                        f"{getattr(settings, 'APP_BASE_URL', '')}/invite/{token}"
                    )
            results.append(entry)

        summary = {
            "added": sum(1 for r in results if r.get("status") == "added"),
            "invited": sum(1 for r in results if r.get("status") == "invited"),
            "skipped": sum(1 for r in results if r.get("status") == "skipped"),
            "failed": sum(1 for r in results if r.get("status") == "failed"),
        }
        meta = {"summary": summary,
                "invite_links": invite_links or None}
        return success_response(results, meta=meta)

    @require_permission("workspace.member.invite", scope="workspace")
    def get(self, request, slug):
        ws, _ = get_workspace_or_404(slug, request.user)
        invites = (
            WorkspaceMemberInvite.objects
            .filter(workspace=ws, status=WorkspaceMemberInvite.Status.PENDING)
            .select_related("invited_by")
            .order_by("-created_at")
        )
        return success_response(WorkspaceInviteLiteSerializer(invites, many=True).data)


# ────────── 撤销邀请 ──────────

class WorkspaceInvitationDetailView(APIView):
    """DELETE .../workspaces/{slug}/invitations/{invite_id}/ —— 撤销。"""

    permission_classes = [IsAuthenticatedAndActive]

    @require_permission("workspace.member.invite", scope="workspace")
    def delete(self, request, slug, invite_id):
        ws, _ = get_workspace_or_404(slug, request.user)
        ok = MemberService.revoke_invite(
            workspace=ws, invite_id=invite_id, actor=request.user,
        )
        if not ok:
            raise NotFound("RESOURCE_NOT_FOUND") from None
        return Response(status=status.HTTP_204_NO_CONTENT)


# ────────── 邀请预检 / 接受（全局端点，token 自带空间上下文） ──────────

class _InvitationTokenAPIView(APIView):
    """基类：按 token_hash 查找 + 实时有效性判定。"""

    permission_classes = [IsAuthenticatedAndActive]

    @staticmethod
    def _lookup(token: str):
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        invite = (
            WorkspaceMemberInvite.objects
            .select_related("workspace", "invited_by")
            .filter(token_hash=token_hash)
            .first()
        )
        if invite is None:
            raise AppException(
                "VALIDATION_ERROR",
                message="邀请无效",
                details=[{"field": "token", "code": "INVALID", "message": "邀请无效"}],
            )
        return invite


class InvitationPrecheckView(_InvitationTokenAPIView):
    """GET /api/v1/invitations/{token}/ —— 接受页预检（脱敏渲染）。"""

    def get(self, request, token):
        invite = self._lookup(token)

        # 失效态统一 400（§2.7 防枚举），包含预检失败的所有情况
        if invite.status != WorkspaceMemberInvite.Status.PENDING:
            message = {
                WorkspaceMemberInvite.Status.ACCEPTED: "邀请已被使用",
                WorkspaceMemberInvite.Status.REVOKED: "邀请已被撤销",
                WorkspaceMemberInvite.Status.EXPIRED: "邀请已过期",
            }.get(invite.status, "邀请无效")
            raise AppException(
                "VALIDATION_ERROR",
                message=message,
                details=[{"field": "token", "code": "INVALID", "message": message}],
            )
        if invite.expires_at <= timezone.now():
            raise AppException(
                "VALIDATION_ERROR",
                message="邀请已过期，请联系管理员重新发送",
                details=[{"field": "token", "code": "INVALID",
                          "message": "邀请已过期"}],
            )

        # 邮箱匹配校验：当前登录用户邮箱 == 邀请邮箱；不匹配给前端文案提示
        email_match = invite.email == request.user.email.lower()
        payload = {
            "workspace": {
                "id": str(invite.workspace.id),
                "name": invite.workspace.name,
                "slug": invite.workspace.slug,
            },
            "role": invite.role,
            "invited_by": (
                {
                    "id": str(invite.invited_by.id),
                    "display_name": invite.invited_by.display_name,
                    "email": invite.invited_by.email,
                } if invite.invited_by else None
            ),
            "expires_at": invite.expires_at,
            "masked_email": mask_email(invite.email),
            "email_match": email_match,
        }
        return success_response(payload)


class InvitationAcceptView(_InvitationTokenAPIView):
    """POST /api/v1/invitations/{token}/accept/ —— 接受邀请。"""

    def post(self, request, token):
        svc = MemberService()
        member = svc.accept_invite(token=token, actor=request.user)
        return success_response({
            "workspace": {
                "id": str(member.workspace.id),
                "name": member.workspace.name,
                "slug": member.workspace.slug,
            },
            "role": member.role,
            "current_user_role": member.role,
        })


# ── Sprint-5（AUTH-006 §4.4）：批量角色 + 账号启停 ──────────────────────
class WorkspaceMemberBulkRoleView(APIView):
    """POST .../workspaces/{slug}/members/bulk-role/ —— 批量改角色（§2.3 部分成功语义）。

    结构性校验整请求 400（缺字段 / >100 人 / 目标角色非法）；
    业务级分态 updated / skipped（附 reason）/ failed（附 reason+message）。
    """

    permission_classes = [IsAuthenticatedAndActive]

    @require_permission("workspace.member.manage", scope="workspace")
    def post(self, request, slug):
        from plane.db.models.roles import WorkspaceRole
        from plane.db.services import member_admin as svc

        ws, _ = get_workspace_or_404(slug, request.user)
        user_ids = request.data.get("user_ids")
        role = request.data.get("role")
        if not isinstance(user_ids, list) or not user_ids:
            raise AppException("VALIDATION_ERROR", message="user_ids 必须为非空数组",
                               details=[{"field": "user_ids", "code": "INVALID",
                                         "message": "user_ids 必须为非空数组"}])
        if len(user_ids) > svc.BULK_ROLE_MAX:  # BR-14
            raise AppException("VALIDATION_BULK_LIMIT_EXCEEDED",
                               message=f"单次批量上限 {svc.BULK_ROLE_MAX} 人")
        if role not in (WorkspaceRole.ADMIN, WorkspaceRole.MEMBER, WorkspaceRole.GUEST):
            raise AppException("VALIDATION_ERROR", message="角色非法",
                               details=[{"field": "role", "code": "NOT_A_CHOICE",
                                         "message": "目标角色必须为 ADMIN/MEMBER/GUEST"}])
        result = svc.bulk_role_workspace(
            workspace=ws, actor=request.user,
            user_ids=[str(u) for u in user_ids], role=int(role))
        return success_response(result)


class WorkspaceMemberDisableView(APIView):
    """POST .../workspaces/{slug}/members/{member_id}/disable/ —— 账号禁用（§4.4.2）。"""

    permission_classes = [IsAuthenticatedAndActive]

    @require_permission("workspace.member.manage", scope="workspace")
    def post(self, request, slug, member_id):
        from plane.db.services.member_admin import AccountService

        ws, _ = get_workspace_or_404(slug, request.user)
        member = self._resolve(ws, member_id)
        data = AccountService().disable(
            target=member.member, actor=request.user, workspace=ws)
        return success_response(data)

    @staticmethod
    def _resolve(ws, member_id):
        try:
            return WorkspaceMember.objects.select_related("member").get(
                id=member_id, workspace=ws, deleted_at__isnull=True)
        except WorkspaceMember.DoesNotExist:
            raise NotFound("RESOURCE_NOT_FOUND") from None


class WorkspaceMemberEnableView(APIView):
    """POST .../workspaces/{slug}/members/{member_id}/enable/ —— 账号启用（幂等）。"""

    permission_classes = [IsAuthenticatedAndActive]

    @require_permission("workspace.member.manage", scope="workspace")
    def post(self, request, slug, member_id):
        from plane.db.services.member_admin import AccountService

        ws, _ = get_workspace_or_404(slug, request.user)
        try:
            member = WorkspaceMember.objects.select_related("member").get(
                id=member_id, workspace=ws, deleted_at__isnull=True)
        except WorkspaceMember.DoesNotExist:
            raise NotFound("RESOURCE_NOT_FOUND") from None
        data = AccountService().enable(
            target=member.member, actor=request.user, workspace=ws)
        return success_response(data)
