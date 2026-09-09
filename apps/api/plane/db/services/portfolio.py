"""项目集聚合服务（PROJ-004 §4.3，Sprint-9）。

BR-14 强制层落点：summary/资源矩阵/风险列表/依赖图跨 Project/Issue/WorkLogSummary
多表聚合，QuerySet 注入覆盖不到——行级收口在服务层显式完成：统一经
descendant_projects(actor, …) 与 Project.objects.accessible_by(actor) 求交。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import timedelta

from django.db import connection
from django.db.models import Count, F, Q, Sum
from django.utils import timezone

from plane.db.models import (
    Issue,
    IssueLink,
    MilestoneItem,
    Portfolio,
    PortfolioMilestone,
    PortfolioProject,
    Project,
    WorkLogSummary,
)
from plane.db.services.issue_link import DirtyDependencyGraphError  # noqa: F401  # re-export 供视图层转译

# 子树展开 CTE：parent 链 + 挂载表一次拿全（深度 ≤3 有界）
SUBTREE_SQL = """
    WITH RECURSIVE sub(id) AS (
        SELECT %(root)s::uuid
        UNION ALL
        SELECT c.id FROM portfolios c JOIN sub s ON c.parent_id = s.id
         WHERE c.deleted_at IS NULL
    )
    SELECT pp.project_id
      FROM portfolio_projects pp JOIN sub ON pp.portfolio_id = sub.id
     WHERE pp.deleted_at IS NULL
"""


class PortfolioError(Exception):
    """业务校验失败基类（视图层转 AppException，语义码由子类声明）。"""


class DepthLimitError(PortfolioError):
    """树深度 >3 → 409 RESOURCE_LIMIT_EXCEEDED（子码 DEPTH）。"""


class ParentCycleError(PortfolioError):
    """父链成环 → 409 RESOURCE_CIRCULAR_DEPENDENCY（子码 CYCLE）。"""


class MountTargetError(PortfolioError):
    """挂载目标非叶子 → 400 VALIDATION_INVALID_PARAM（子码 INVALID）。"""


class MountConflictError(PortfolioError):
    """项目重复挂载 → 409 RESOURCE_ALREADY_EXISTS（子码 UNIQUE）。"""


class PortfolioNotEmptyError(PortfolioError):
    """删除非空项目集 → 409 RESOURCE_IN_USE（子码 IN_USE，携带计数）。"""

    def __init__(self, children: int, projects: int):
        self.children = children
        self.projects = projects
        super().__init__(f"children={children} projects={projects}")


class MilestoneItemScopeError(PortfolioError):
    """贡献项非项目集内任务 → 400 VALIDATION_ERROR（子码 DOES_NOT_EXIST）。"""


@dataclass
class PortfolioProgress:
    overall: float
    by_project: list[dict] = field(default_factory=list)


class PortfolioService:
    # ── 组合树写路径（BR-01/BR-02/BR-11）────────────────────────────

    def validate_parent(self, *, workspace, parent_id: uuid.UUID | None) -> None:
        """新建节点的父链校验：深度 ≤3（BR-01）。新建节点自身不可能已在祖先链上，
        环禁止仅移动语义需要（assert_cycle_free）。"""
        if parent_id is None:
            return
        parent = Portfolio.objects.filter(
            id=parent_id, workspace=workspace, deleted_at__isnull=True).first()
        if parent is None:
            raise PortfolioError("父节点不存在")
        if parent.depth >= Portfolio.MAX_DEPTH:
            raise DepthLimitError("组合树深度上限 3")

    def assert_cycle_free(self, *, node_id: uuid.UUID, new_parent_id: uuid.UUID | None) -> None:
        """移动语义的环校验：new_parent 的祖先链不得含 node 自身（A→B→C→A）。"""
        if new_parent_id is None:
            return
        hop, cur = 0, Portfolio.objects.filter(id=new_parent_id).first()
        while cur is not None and hop <= Portfolio.MAX_DEPTH + 1:
            if cur.id == node_id:
                raise ParentCycleError("父链成环")
            cur = cur.parent
            hop += 1

    def mount_project(self, *, portfolio: Portfolio, project: Project) -> None:
        """BR-02：项目至多挂一个项目集 + 目标必须是叶子节点（无子节点）。"""
        if Portfolio.objects.filter(parent=portfolio).exists():
            raise MountTargetError("项目仅可挂载叶子节点")
        if PortfolioProject.objects.filter(project=project).exists():
            existing = PortfolioProject.objects.select_related("portfolio").filter(
                project=project).first()
            raise MountConflictError(f"当前挂载点 {existing.portfolio.name}")
        PortfolioProject.objects.create(portfolio=portfolio, project=project)

    def assert_deletable(self, *, portfolio: Portfolio) -> None:
        """BR-11：非空（子节点/挂载项目，含子树内挂载）阻断删除。"""
        children = Portfolio.objects.filter(parent=portfolio).count()
        projects = PortfolioProject.objects.filter(
            portfolio_id__in=self._subtree_ids(portfolio.id)).count()
        if children or projects:
            raise PortfolioNotEmptyError(children, projects)

    # ── 读面聚合（BR-13/BR-12/BR-06 判定源）─────────────────────────

    def _subtree_ids(self, portfolio_id) -> set[uuid.UUID]:
        ids = {portfolio_id}
        frontier = {portfolio_id}
        for _ in range(Portfolio.MAX_DEPTH):
            children = set(
                Portfolio.objects.filter(parent_id__in=frontier)
                .values_list("id", flat=True))
            if not children:
                break
            ids |= children
            frontier = children
        return ids

    def descendant_projects(self, actor, portfolio_id) -> list[uuid.UUID]:
        """项目集下 actor 可见项目（子树展开 + 挂载表，再与 rbac §6 project_q
        求交）——面板/里程碑读面共用入口（唯一定义，BR-14）。"""
        with connection.cursor() as cursor:
            cursor.execute(SUBTREE_SQL, {"root": str(portfolio_id)})
            subtree_projects = [r[0] for r in cursor.fetchall()]
        if not subtree_projects:
            return []
        workspace_id = Portfolio.objects.values_list(
            "workspace_id", flat=True).get(pk=portfolio_id)
        from plane.access.matrix import project_q
        return list(
            Project.objects.filter(id__in=subtree_projects)
            .filter(project_q(actor, workspace_id))
            .values_list("id", flat=True))

    def progress(self, actor, portfolio_id) -> PortfolioProgress:
        """BR-13：项目进度 = completed/(total - cancelled)；项目集 = 按未取消任务数加权
        （经 descendant_projects 收窄至可见项目，BR-14）。"""
        rows = (
            Issue.objects.filter(project_id__in=self.descendant_projects(actor, portfolio_id),
                                 deleted_at__isnull=True)
            .values("project_id", "project__identifier")
            .annotate(total=Count("id", filter=~Q(state__group="cancelled")),
                      done=Count("id", filter=Q(state__group="completed")))
        )
        total = sum(r["total"] for r in rows)
        overall = (sum(r["done"] for r in rows) / total) if total else 0.0
        return PortfolioProgress(
            overall=overall,
            by_project=[{"project_id": r["project_id"], "identifier": r["project__identifier"],
                         "ratio": (r["done"] / r["total"]) if r["total"] else 0.0}
                        for r in rows])

    def milestone_progress(self, milestone_id, *, for_system: bool = False, actor=None) -> float:
        """BR-05：Σ(已完成贡献项 weight) / Σ(weight)。
        读面必须显式传 actor（BR-14 收窄）；for_system=True 仅限 §4.5 预警 beat
        （通知 manager 的完成度不随读者视角波动，全集口径）。"""
        qs = MilestoneItem.objects.filter(milestone_id=milestone_id)
        if not for_system and actor is not None:
            portfolio_id = PortfolioMilestone.objects.values_list(
                "portfolio_id", flat=True).get(pk=milestone_id)
            qs = qs.filter(issue__project_id__in=self.descendant_projects(actor, portfolio_id))
        agg = qs.aggregate(
            total=Sum("weight"),
            done=Sum("weight", filter=Q(issue__state__group="completed")))
        total, done = agg["total"] or 0, agg["done"] or 0
        return (done / total) if total else 0.0

    def resource_matrix(self, actor, portfolio_id, from_week, to_week):
        """BR-12：消费 TASK-013 快照，人×项目矩阵——不扫明细表（BR-14 收窄）。"""
        return (
            WorkLogSummary.objects.filter(
                project_id__in=self.descendant_projects(actor, portfolio_id),
                week_start__range=(from_week, to_week))
            .annotate(identifier=F("project__identifier"),
                      actor_name=F("actor__display_name"))
            .values("actor_id", "actor_name", "project_id", "identifier")
            .annotate(minutes=Sum("total_minutes"))
            .order_by("actor__display_name")
        )

    def risk_list(self, actor, portfolio_id) -> list[dict]:
        """风险三源：逾期任务 / 被外部依赖阻塞 / 里程碑偏差（BR-06 判定同源）。"""
        projects = self.descendant_projects(actor, portfolio_id)
        risks: list[dict] = []
        today = timezone.localdate()
        overdue = (
            Issue.objects.filter(
                project_id__in=projects, target_date__lt=today,
                deleted_at__isnull=True)
            .exclude(state__group__in=["completed", "cancelled"])
            .select_related("project", "state")
            .order_by("target_date")[:20])
        for it in overdue:
            risks.append({
                "type": "overdue_issue", "issue": f"{it.project.identifier}-{it.sequence_id}",
                "title": it.name, "days": (today - it.target_date).days})
        # 被外部项目任务阻塞（BR-07 统计源；镜像行语义：issue=被阻塞方 related_issue=源）
        blocked = (
            IssueLink.objects
            .filter(relation_type="is_blocked_by",
                    issue__project_id__in=projects, issue__deleted_at__isnull=True)
            .exclude(related_issue__project_id=F("issue__project_id"))
            .exclude(related_issue__state__group__in=["completed", "cancelled"])
            .exclude(issue__state__group__in=["completed", "cancelled"])
            .select_related("issue", "issue__project", "related_issue", "related_issue__project")[:20])
        by_issue: dict[uuid.UUID, dict] = {}
        for link in blocked:
            key = link.issue_id
            row = by_issue.setdefault(key, {
                "type": "external_blocked",
                "issue": f"{link.issue.project.identifier}-{link.issue.sequence_id}",
                "title": link.issue.name, "blocked_by": []})
            row["blocked_by"].append(
                f"{link.related_issue.project.identifier}-{link.related_issue.sequence_id}")
        risks.extend(by_issue.values())
        # 里程碑偏差：未完成 + 剩 ≤7 天（与 §4.5 预警同窗口；读面带 actor 口径）
        subtree = self._subtree_ids(portfolio_id)
        for ms in (PortfolioMilestone.objects
                   .filter(portfolio_id__in=subtree, completed_at__isnull=True,
                           target_date__gte=today - timedelta(days=30),
                           target_date__lte=today + timedelta(days=7))
                   .order_by("target_date")):
            progress = self.milestone_progress(ms.id, actor=actor)
            if progress < 1.0:
                risks.append({
                    "type": "milestone_slip", "milestone": ms.name,
                    "progress": round(progress, 4),
                    "days_left": (ms.target_date - today).days})
        return risks

    def dependency_graph(self, actor, portfolio_id, *, node_limit: int = 100,
                         edge_limit: int = 500) -> dict:
        """依赖图载荷（§3.3）：节点 = 有关联边的任务卡片；边 = IssueLink（跨项目标记）。
        仅跨项目边 + 关键同项目边；超 node_limit 提示过滤（前端降级，IT-02）。"""
        projects = set(self.descendant_projects(actor, portfolio_id))
        links = (
            IssueLink.objects.filter(
                deleted_at__isnull=True, relation_type="blocks",   # 成对存储：仅正向行
                issue__deleted_at__isnull=True, related_issue__deleted_at__isnull=True)
            .filter(Q(issue__project_id__in=projects) | Q(related_issue__project_id__in=projects))
            .select_related("issue", "issue__project", "issue__state",
                            "related_issue", "related_issue__project", "related_issue__state")
            .order_by("-created_at")[: edge_limit * 2])
        nodes: dict[uuid.UUID, dict] = {}
        edges: list[dict] = []
        for link in links:
            src, dst = link.issue, link.related_issue
            if src.project_id not in projects and dst.project_id not in projects:
                continue
            cross = src.project_id != dst.project_id
            if not cross and link.relation_type != "blocks":
                continue  # 同项目仅保留 blocks（关键边）；relates_to 噪音剔除
            if len(edges) >= edge_limit:
                break
            for it in (src, dst):
                if it.id not in nodes and len(nodes) < node_limit:
                    nodes[it.id] = {
                        "id": str(it.id),
                        "issue_key": f"{it.project.identifier}-{it.sequence_id}",
                        "name": it.name,
                        "project_id": str(it.project_id),
                        "identifier": it.project.identifier,
                        "state_group": (it.state.group if it.state else None) or "unstarted",
                    }
            if src.id in nodes and dst.id in nodes:
                edges.append({
                    "source": str(src.id), "target": str(dst.id),
                    "relation_type": link.relation_type, "cross_project": cross})
        return {"nodes": list(nodes.values()), "edges": edges,
                "truncated": len(edges) >= edge_limit or len(nodes) >= node_limit}
