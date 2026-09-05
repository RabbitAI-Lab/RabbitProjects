"""项目列表查询服务（PROJ-002 §4.3.1）—— 搜索 + 收藏注水 + 置顶排序。

rbac §6.2 权限过滤在最外层；搜索 / 状态 / 收藏均在可见集内完成。
"""
from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Case, Count, Q, QuerySet, Value, When

from plane.db.models import Project, ProjectFavorite


@dataclass
class ProjectListResult:
    queryset: QuerySet
    favorite_ids: set[str]
    visible_favorite_count: int  # 可见集中被收藏的数量


def list_for_user(
    *,
    user,
    workspace,
    q: str | None = None,
    status: str | None = None,
    favorite_only: bool = False,
    favorite_first: bool = False,
):
    """返回 (qs, favorite_ids)。

    字段：
      q: 前缀匹配（name / identifier），大小写不敏感
      status: active / archived / all；未传 → 默认排除 archived（BR-11）
      favorite_only: 仅收藏项
      favorite_first: 收藏段置顶（组内按收藏时间倒序）
    """
    # ① 权限最外层（rbac §6.2 accessible_by：WS_ADMIN+ 全可见 / 其余显式成员）
    qs = _accessible_projects(user=user, workspace=workspace)

    # ② 状态筛选：未传默认排除归档（BR-11）
    if status == "all":
        pass
    elif status in ("active", "archived"):
        qs = qs.filter(status=status)
    else:
        qs = qs.exclude(status=Project.Status.ARCHIVED)

    # ③ 搜索：前缀匹配（istartswith）。identifier 用大写（Model.save 自动大写化）
    if q:
        qs = qs.filter(
            Q(name__istartswith=q) | Q(identifier__istartswith=q.upper())
        )

    # ④ 收藏集一次取全（≤ 50 行），供过滤 / 注水 / 计数共用
    favorites_qs = ProjectFavorite.objects.filter(
        user=user, project__deleted_at__isnull=True, deleted_at__isnull=True
    ).values("project_id", "created_at")
    favorite_rows = list(favorites_qs)
    favorite_ids = {str(r["project_id"]) for r in favorite_rows}

    if favorite_only:
        qs = qs.filter(id__in=favorite_ids)

    # ⑤ 置顶排序：is_fav(0/1) → -fav_time → -id（尾部唯一键保游标稳定，BR-10）
    if favorite_first and favorite_ids:
        # 收藏组内按收藏时间倒序（PROJ-002 §4.2.1 BR-10）。
        # 收藏时间由 Subquery 注水到 fav_time 列，直接参与 SQL 端排序。
        from django.db.models import OuterRef, Subquery

        fav_time_subq = Subquery(
            ProjectFavorite.objects.filter(
                user=user, project=OuterRef("pk"), deleted_at__isnull=True
            ).values("created_at")[:1]
        )
        qs = qs.annotate(
            is_fav=Case(
                When(id__in=favorite_ids, then=Value(0)),
                default=Value(1),
            ),
            fav_time=fav_time_subq,
        ).order_by("is_fav", "-fav_time", "-id")
    else:
        qs = qs.order_by("-updated_at", "-id")

    # ⑥ 聚合（成员计数只数显式 ProjectMember —— 隐式管理员不占位，rbac §7.4）
    qs = qs.annotate(
        total_members=Count(
            "project_projectmember",
            filter=Q(project_projectmember__is_active=True,
                     project_projectmember__deleted_at__isnull=True),
            distinct=True,
        ),
        total_issues=Count(
            "issues",
            filter=Q(issues__deleted_at__isnull=True),
            distinct=True,
        ),
    )

    # 可见集中被收藏数量：count 走同一条 SQL（BR-10 favorite_count 字段）
    visible_favorite_count = qs.filter(id__in=favorite_ids).count() if favorite_ids else 0

    return qs, favorite_ids, visible_favorite_count


def _accessible_projects(*, user, workspace) -> QuerySet:
    """权限最外层：WS_ADMIN+ 全可见 / 其余显式成员（rbac §6.2）。

    实现：通过 WorkspaceMember.role 取当前用户空间角色；
    ADMIN+ 直接给全空间 active 非软删项目；否则取其作为 active ProjectMember 的项目集。
    """
    from plane.db.models import WorkspaceMember
    from plane.db.models.roles import WorkspaceRole

    ws_member = WorkspaceMember.objects.filter(
        workspace=workspace, member=user, is_active=True, deleted_at__isnull=True
    ).first()
    base = Project.objects.filter(workspace_id=workspace.id, deleted_at__isnull=True)
    if ws_member is not None and ws_member.role >= WorkspaceRole.ADMIN:
        return base
    return base.filter(
        project_projectmember__member=user,
        project_projectmember__is_active=True,
        project_projectmember__deleted_at__isnull=True,
    ).distinct()
