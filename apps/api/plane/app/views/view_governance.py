"""视图治理端点（BOARD-005 §4.2，Sprint-8 R5）。

lock/（锁定/解锁/设默认，board.lock=PROJ_ADMIN）、duplicate/（任意成员）、
pin//unpin/（订阅，登录用户本人）+ 我的订阅列表。
"""
from __future__ import annotations

from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from plane.app.permissions import require_permission
from plane.app.views._access import get_project_or_404
from plane.base.exception import AppException
from plane.base.response import success_response
from plane.db.models import IssueView, ProjectRole, UserViewPreference
from plane.db.services import view_governance as vg


def _get_view(request, slug, project_id, view_id, *, need_lock=False):
    project, _, _ = get_project_or_404(slug, project_id, request.user)
    try:
        view = IssueView.objects.select_related("locked_by").get(
            id=view_id, project=project, deleted_at__isnull=True)
    except IssueView.DoesNotExist:
        raise NotFound("RESOURCE_NOT_FOUND") from None
    if need_lock:
        role = project.current_user_role
        if role is None or role < ProjectRole.ADMIN:  # board.lock（rbac §8.2）
            raise AppException("PERM_ROLE_INSUFFICIENT",
                               message="需要项目管理员权限")
    return project, view


def _view_payload(view) -> dict:
    from plane.db.services.view_governance import subscriber_count

    data = {
        "id": str(view.id), "name": view.name, "access": view.access,
        "layout": view.layout, "is_system": view.is_system,
        "is_locked": view.is_locked,
        "is_project_default": view.is_project_default,
        "locked_by": ({"id": str(view.locked_by_id),
                       "name": view.locked_by.display_name}
                      if view.locked_by_id else None),
        "locked_at": view.locked_at,
        "owner_id": str(view.owner_id) if view.owner_id else None,
        "subscriber_count": subscriber_count(view),
    }
    return data


class ViewLockView(APIView):
    """POST .../views/{view_id}/lock/ —— 锁定/解锁/设项目默认（board.lock）。"""

    permission_classes = [IsAuthenticated]

    @require_permission("board.lock", scope="project")
    def post(self, request, slug, project_id, view_id):
        project, view = _get_view(request, slug, project_id, view_id)
        is_locked = bool(request.data.get("is_locked"))
        is_project_default = request.data.get("is_project_default")
        if is_project_default is not None:
            is_project_default = bool(is_project_default)
        view = vg.lock_view(actor=request.user, view=view,
                            is_locked=is_locked,
                            is_project_default=is_project_default)
        return success_response(_view_payload(view))


class ViewDuplicateView(APIView):
    """POST .../views/{view_id}/duplicate/ —— 副本另存（任意成员，BR-06）。"""

    permission_classes = [IsAuthenticated]

    def post(self, request, slug, project_id, view_id):
        _, view = _get_view(request, slug, project_id, view_id)
        fork = vg.duplicate_view(actor=request.user, view=view)
        return Response({"status": "success",
                         "data": _view_payload(fork)},
                        status=status.HTTP_201_CREATED)


class ViewPinView(APIView):
    """POST .../views/{view_id}/pin/ —— 订阅（本人，BR-16 +1①）。"""

    permission_classes = [IsAuthenticated]

    def post(self, request, slug, project_id, view_id):
        _, view = _get_view(request, slug, project_id, view_id)
        vg.pin_view(actor=request.user, view=view)
        return success_response({"pinned": True})


class ViewUnpinView(APIView):
    """DELETE .../views/{view_id}/pin/ —— 取消订阅（BR-16 −1①）。"""

    permission_classes = [IsAuthenticated]

    def delete(self, request, slug, project_id, view_id):
        _, view = _get_view(request, slug, project_id, view_id)
        vg.unpin_view(actor=request.user, view=view)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MyViewPreferencesView(APIView):
    """GET /api/v1/workspaces/{slug}/projects/{pid}/views/preferences/ —— 我的订阅。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, slug, project_id):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        rows = (UserViewPreference.objects
                .filter(user=request.user, project=project,
                        pinned=True, deleted_at__isnull=True)
                .select_related("view").order_by("sort_order", "created_at"))
        data = [{
            "view_id": str(r.view_id), "name": r.view.name,
            "access": r.view.access, "is_locked": r.view.is_locked,
            "sort_order": r.sort_order,
        } for r in rows]
        return success_response(data, meta={"count": len(data),
                                            "total_count": len(data)})
