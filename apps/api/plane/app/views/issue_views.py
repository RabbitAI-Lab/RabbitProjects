"""保存的视图端点（BOARD-003 §4.2 —— views/ CRUD 五端点）。

权限码按 rbac §8 矩阵拆行（§4.2 注）：
  - board.read（全员）：GET 列表 / 详情（内置 + 本人；他人视图需 board.manage——审计）；
  - view.create.own（全员，含 VIEWER）：创建个人视图——视图是读配置偏好，不涉写语义；
  - 本人（owner）或 board.manage（PROJ_CONTRIBUTOR+，审计）：PATCH / DELETE；
  - board.update 是「拖拽卡片」语义，不用于视图 CRUD。

「设为默认」零新端点（BR-10）：PATCH /users/me/settings/ 偏好键
board.default_view_id（见 views/users.py UserSettingsView）。
"""
from __future__ import annotations

from django.db.models import Max, Q
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.generics import ListCreateAPIView, RetrieveUpdateDestroyAPIView
from rest_framework.response import Response

from plane.app.filters.compiler import CompileContext, resolved_applied
from plane.app.permissions import IsAuthenticated
from plane.app.serializers.view import IssueViewSerializer, IssueViewWriteSerializer
from plane.app.views._access import get_project_or_404
from plane.base.exception import AppException
from plane.base.response import created_response, success_response
from plane.db.models import IssueView
from plane.db.models.roles import ProjectRole
from plane.db.services.view_service import validate_view_payload

WRITABLE_FIELDS = ("name", "description", "access", "layout", "filters", "display_props", "sort_order")


class IssueViewListCreateView(ListCreateAPIView):
    """GET / POST .../projects/{pid}/views/ —— 列表（内置+本人，全量无分页）+ 创建。"""

    permission_classes = [IsAuthenticated]
    serializer_class = IssueViewSerializer

    def list(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        views = (
            IssueView.objects.filter(project=project, deleted_at__isnull=True)
            .filter(Q(is_system=True) | Q(owner=request.user))  # 可见性 = 内置 + 本人（BR-11）
            .order_by("sort_order", "created_at")
        )
        data = IssueViewSerializer(views, many=True).data
        return success_response(data, meta={"count": len(data), "total_count": len(data)})

    def create(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        # view.create.own：全员（含 VIEWER）——无角色闸门，仅项目可见性（get_project_or_404）
        s = IssueViewWriteSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        # PATCH 安全同款：只取请求中实际出现的字段，serializer 默认值不覆盖用户意图
        payload = {k: v for k, v in s.validated_data.items() if k in request.data}
        validate_view_payload(project=project, payload=payload, instance=None, user=request.user)
        max_order = (
            IssueView.objects.filter(project=project, deleted_at__isnull=True).aggregate(m=Max("sort_order"))["m"]
            or 0.0
        )
        view = IssueView.objects.create(
            workspace_id=project.workspace_id,
            project=project,
            owner=request.user,
            name=payload["name"],
            description=payload.get("description", ""),
            access=payload.get("access", "personal"),
            layout=payload.get("layout", IssueView.Layout.LIST),
            filters=payload.get("filters") or {},
            display_props=payload.get("display_props") or {},
            sort_order=payload.get("sort_order") if payload.get("sort_order") is not None else max_order + 65535.0,
            created_by=request.user,
            updated_by=request.user,
        )
        # BR-17：保存即回显「筛选生效了什么」——视图标识 + 占位符解析值 + 条件统计
        echo = resolved_applied(view.filters or {}, CompileContext.build(project=project, user=request.user))
        return created_response(
            IssueViewSerializer(view).data,
            meta={
                "applied": {
                    "view": {"id": str(view.id), "name": view.name, "access": view.access},
                    "resolved_placeholders": echo["resolved_placeholders"],
                    "conditions_count": echo["conditions_count"],
                    "groups_count": echo["groups_count"],
                }
            },
            location=request.build_absolute_uri(
                f"/api/v1/workspaces/{kwargs['slug']}/projects/{project.id}/views/{view.id}/"
            ),
        )


class IssueViewDetailView(RetrieveUpdateDestroyAPIView):
    """GET / PATCH / DELETE .../views/{view_id}/ —— 详情 / 更新 / 软删。"""

    permission_classes = [IsAuthenticated]
    serializer_class = IssueViewSerializer

    def _get_view(self, *, for_write: bool = False):
        project, _, _ = get_project_or_404(self.kwargs["slug"], self.kwargs["project_id"], self.request.user)
        try:
            view = IssueView.objects.get(
                id=self.kwargs["view_id"], project=project, deleted_at__isnull=True
            )
        except IssueView.DoesNotExist:
            raise NotFound("RESOURCE_NOT_FOUND") from None
        own = view.owner_id == self.request.user.id
        managed = project.current_user_role >= ProjectRole.CONTRIBUTOR  # board.manage（审计）
        if for_write:
            if not (own or managed):
                raise NotFound("RESOURCE_NOT_FOUND") from None  # 存在性隐藏（§6）
        elif not (view.is_system or own or managed):
            raise NotFound("RESOURCE_NOT_FOUND") from None
        self._project = project
        return view

    def retrieve(self, request, *args, **kwargs):
        return success_response(IssueViewSerializer(self._get_view()).data)

    def update(self, request, *args, **kwargs):
        view = self._get_view(for_write=True)
        # BOARD-005 BR-02：锁定态拦截优先于权限码判定（含 access 收回——
        # 锁定视图仅 board.lock 可动；UI 引导副本路径）
        if view.is_locked:
            locker = view.locked_by.display_name if view.locked_by else "管理员"
            raise AppException(
                "RESOURCE_LOCKED",
                message=f"此视图已被管理员锁定（{locker} · "
                        f"{view.locked_at:%Y-%m-%d %H:%M}），请另存为副本",
                details=[{"field": "view", "code": "LOCKED",
                          "message": f"locked_by={locker}, locked_at={view.locked_at}"}],
            )
        s = IssueViewWriteSerializer(data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        payload = {k: v for k, v in s.validated_data.items() if k in request.data}
        if view.is_system and "access" in payload and \
                payload["access"] != view.access:  # BR-14
            raise AppException("PERM_DENIED", message="内置视图不可共享")
        if "access" in payload and payload["access"] != view.access:
            # BOARD-005 §2.1：共享/收回走治理服务（收回级联软删订阅）
            from plane.db.services import view_governance as vg
            view = vg.share_view(actor=request.user, view=view,
                                 access=payload["access"])
            payload.pop("access")
            if not payload:
                return success_response(IssueViewSerializer(view).data)
        if view.is_system and "filters" in payload:
            # BR-03：内置视图 filters 锁定（display_props 可保存）
            raise AppException(
                "PERM_DENIED",
                message="内置视图的筛选条件不可修改（显示配置可保存）",
            )
        merged = {
            "access": payload.get("access", view.access),
            "layout": payload.get("layout", view.layout),
            "filters": payload.get("filters", view.filters or {}),
            "display_props": payload.get("display_props", view.display_props or {}),
        }
        validate_view_payload(project=self._project, payload=merged, instance=view, user=request.user)
        update_fields = ["updated_at", "updated_by"]
        for field in WRITABLE_FIELDS:
            if field in payload:
                setattr(view, field, payload[field])
                update_fields.append(field)
        view.updated_by = request.user
        view.save(update_fields=update_fields)
        return success_response(IssueViewSerializer(view).data)

    def destroy(self, request, *args, **kwargs):
        view = self._get_view(for_write=True)
        if view.is_system:
            raise AppException("PERM_DENIED", message="内置视图不可删除")
        if view.is_locked:  # BR-03：锁定视图不可删（先解锁）
            raise AppException("RESOURCE_LOCKED", message="锁定视图不可删除，请先解锁")
        from plane.db.models import UserViewPreference
        UserViewPreference.objects.filter(
            view=view, deleted_at__isnull=True).delete()  # BR-16 −1③ 级联软删
        view.is_project_default = False  # 删除事务内清默认（§2.5）
        view.save(update_fields=["is_project_default"])
        view.soft_delete(actor_id=request.user.id)
        return Response(status=status.HTTP_204_NO_CONTENT)
