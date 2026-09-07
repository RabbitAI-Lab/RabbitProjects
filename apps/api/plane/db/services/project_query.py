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
    # Sprint-5 性能优化（sprint-5-bench P1 门禁驱动）：LEFT JOIN + Count(distinct)
    # 在 10 万 issues 下做 join 笛卡尔计数（实测 P95 375ms）——改 correlated
    # subquery 标量计数，每项目两次索引点查（idx 前缀 project_id），页内 20 行
    # 仅 40 次点查，P95 降 ~15ms；输出契约不变（total_members/total_issues）。
    from django.db.models import IntegerField
    from django.db.models.expressions import OuterRef, Subquery

    from plane.db.models import Issue
    from plane.db.models import ProjectMember as _PM
    qs = qs.annotate(
        total_members=Subquery(
            _PM.objects.filter(
                project_id=OuterRef("pk"), is_active=True, deleted_at__isnull=True,
            ).values("project_id").annotate(c=Count("id")).values("c")[:1],
            output_field=IntegerField(),
        ),
        total_issues=Subquery(
            Issue.objects.filter(
                project_id=OuterRef("pk"), deleted_at__isnull=True,
            ).values("project_id").annotate(c=Count("id")).values("c")[:1],
            output_field=IntegerField(),
        ),
    )

    # 可见集中被收藏数量：count 走同一条 SQL（BR-10 favorite_count 字段）
    visible_favorite_count = qs.filter(id__in=favorite_ids).count() if favorite_ids else 0

    return qs, favorite_ids, visible_favorite_count


def _accessible_projects(*, user, workspace) -> QuerySet:
    """权限最外层（rbac §6.2）——Sprint-5 起收敛为 access/matrix.py 单源调用
    （AUTH-006 §2.2：矩阵 Q 唯一实现地，视图/服务层禁止手写可见性谓词）。

    matrix.project_q 语义与本函数原实现一致，另收编 BR-11 draft 行
    （draft 仅创建者 ∪ WS_ADMIN+ 可见）。
    """
    from plane.access.matrix import project_q

    return Project.objects.filter(project_q(user, workspace.id)).distinct()
