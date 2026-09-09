"""项目集视图（PROJ-004 §4.6，Sprint-9）——10 端点。

权限（BR-03/BR-14）：读 WS_MEMBER+（WS_GUEST 403）；写 WS_ADMIN+ 或项目集
manager（含祖先链 ≤2 跳）；挂载/卸载双端权限（项目集管理权 + 项目侧
PROJ_ADMIN+）。聚合读面经 PortfolioService.descendant_projects 收窄（rbac
行级 Manager 覆盖不到多表聚合，BR-14 服务层显式收口）。
"""
from __future__ import annotations

from rest_framework.exceptions import NotFound
from rest_framework.views import APIView

from plane.app.permissions import IsAuthenticated
from plane.app.views._access import get_project_or_404, get_workspace_or_404
from plane.base.exception import AppException
from plane.base.response import created_response, success_response
from plane.db.models import (
    MilestoneItem,
    Portfolio,
    PortfolioMilestone,
    PortfolioProject,
    ProjectRole,
    WorkspaceRole,
)
from plane.db.services.portfolio import (
    DepthLimitError,
    MilestoneItemScopeError,
    MountConflictError,
    MountTargetError,
    ParentCycleError,
    PortfolioError,
    PortfolioNotEmptyError,
    PortfolioService,
)

ORDERING_WHITELIST = {"name", "target_date", "created_at"}


def _require_member(ws, member) -> None:
    """BR-14 读门槛：WS_MEMBER+（GUEST 403）。"""
    if member.role < WorkspaceRole.MEMBER:
        raise AppException("PERM_ROLE_INSUFFICIENT", message="访客无权访问项目集")


def _assert_manager(ws, member, portfolio: Portfolio) -> None:
    """BR-03 写门槛：WS_ADMIN+ 或项目集 manager（含祖先链 ≤2 跳）。"""
    if member.role >= WorkspaceRole.ADMIN:
        return
    node: Portfolio | None = portfolio
    hops = 0
    while node is not None and hops <= 2:
        if node.manager_id == member.member_id:
            return
        node = node.parent
        hops += 1
    raise AppException("PERM_ROLE_INSUFFICIENT", message="仅空间管理员或项目集负责人可操作")


def _get_portfolio(ws, portfolio_id) -> Portfolio:
    pf = Portfolio.objects.select_related("parent", "manager").filter(
        id=portfolio_id, workspace=ws).first()
    if pf is None:
        raise NotFound("RESOURCE_NOT_FOUND") from None
    return pf


def _serialize_tree(nodes_qs, counts: dict) -> list[dict]:
    """整树嵌套序列化（游标分页显式豁免：深度 ≤3、节点有界，§4.6 注）。"""
    nodes = {n.id: {"id": str(n.id), "name": n.name, "parent_id": str(n.parent_id) if n.parent_id else None,
                    "depth": n.depth, "manager_id": str(n.manager_id) if n.manager_id else None,
                    "project_count": counts.get(n.id, 0), "children": []}
             for n in nodes_qs}
    roots = []
    for n in nodes_qs:
        node = nodes[n.id]
        if n.parent_id and n.parent_id in nodes:
            nodes[n.parent_id]["children"].append(node)
        else:
            roots.append(node)
    return roots


class PortfolioListCreateView(APIView):
    """GET/POST portfolios/ —— 组合树整树返回 / 新建节点（BR-01 深度校验）。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, slug):
        ws, member = get_workspace_or_404(slug, request.user)
        _require_member(ws, member)
        qs = (Portfolio.objects.filter(workspace=ws)
              .select_related("parent", "manager").order_by("depth", "sort_order", "created_at"))
        counts = _project_counts(ws)
        roots = _serialize_tree(qs, counts)
        return success_response(roots, meta={"count": sum(1 for n in qs if n.parent_id is None)})

    def post(self, request, slug):
        ws, member = get_workspace_or_404(slug, request.user)
        _assert_manager_scope(ws, member, request)
        name = (request.data.get("name") or "").strip()
        if not name:
            raise AppException("VALIDATION_ERROR",
                               details=[{"field": "name", "code": "REQUIRED", "message": "名称必填"}])
        parent_id = request.data.get("parent_id") or None
        svc = PortfolioService()
        try:
            svc.validate_parent(workspace=ws, parent_id=parent_id)
        except DepthLimitError as e:
            raise AppException("RESOURCE_LIMIT_EXCEEDED",
                               details=[{"field": "parent_id", "code": "DEPTH", "message": str(e)}],
                               message="组合树深度上限 3") from None
        except PortfolioError:
            raise AppException("VALIDATION_ERROR",
                               details=[{"field": "parent_id", "code": "DOES_NOT_EXIST",
                                         "message": "父节点不存在"}]) from None
        pf = Portfolio.objects.create(
            workspace=ws, name=name, description=request.data.get("description") or "",
            parent_id=parent_id,
            depth=1 if parent_id is None else Portfolio.objects.get(pk=parent_id).depth + 1,
            manager_id=request.data.get("manager_id") or None,
            created_by=request.user)
        return created_response(
            {"id": str(pf.id), "name": pf.name, "depth": pf.depth, "parent_id": parent_id},
            location=f"/api/v1/workspaces/{slug}/portfolios/{pf.id}/")


def _project_counts(ws) -> dict:
    from django.db.models import Count
    return {r["portfolio"]: r["n"] for r in
            PortfolioProject.objects.filter(portfolio__workspace=ws)
            .values("portfolio").annotate(n=Count("id"))}


def _assert_manager_scope(ws, member, request) -> None:
    """新建根/子节点的管理权：WS_ADMIN+（manager 尚不存在，无对象可判）。"""
    if member.role < WorkspaceRole.ADMIN:
        raise AppException("PERM_ROLE_INSUFFICIENT", message="仅空间管理员可创建项目集节点")


class PortfolioDetailView(APIView):
    """GET/PATCH/DELETE portfolios/{id}/ —— 详情 / 改 / 删（BR-11 非空阻断）。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, slug, portfolio_id):
        ws, member = get_workspace_or_404(slug, request.user)
        _require_member(ws, member)
        pf = _get_portfolio(ws, portfolio_id)
        return success_response({
            "id": str(pf.id), "name": pf.name, "description": pf.description,
            "parent_id": str(pf.parent_id) if pf.parent_id else None,
            "depth": pf.depth, "manager_id": str(pf.manager_id) if pf.manager_id else None,
        })

    def patch(self, request, slug, portfolio_id):
        ws, member = get_workspace_or_404(slug, request.user)
        pf = _get_portfolio(ws, portfolio_id)
        _assert_manager(ws, member, pf)
        if "name" in request.data:
            name = (request.data["name"] or "").strip()
            if not name:
                raise AppException("VALIDATION_ERROR",
                                   details=[{"field": "name", "code": "REQUIRED", "message": "名称必填"}])
            pf.name = name
        if "description" in request.data:
            pf.description = request.data["description"] or ""
        if "manager_id" in request.data:
            pf.manager_id = request.data["manager_id"] or None
        if "parent_id" in request.data:
            new_parent = request.data["parent_id"] or None
            svc = PortfolioService()
            try:
                svc.validate_parent(workspace=ws, parent_id=new_parent)
                svc.assert_cycle_free(node_id=pf.id, new_parent_id=new_parent)
            except DepthLimitError as e:
                raise AppException("RESOURCE_LIMIT_EXCEEDED",
                                   details=[{"field": "parent_id", "code": "DEPTH", "message": str(e)}],
                                   message="组合树深度上限 3") from None
            except ParentCycleError:
                raise AppException("RESOURCE_CIRCULAR_DEPENDENCY",
                                   details=[{"field": "parent_id", "code": "CYCLE", "message": "父链成环"}],
                                   message="父链成环") from None
            except PortfolioError:
                raise AppException("VALIDATION_ERROR",
                                   details=[{"field": "parent_id", "code": "DOES_NOT_EXIST",
                                             "message": "父节点不存在"}]) from None
            pf.parent_id = new_parent
            pf.depth = 1 if new_parent is None else Portfolio.objects.get(pk=new_parent).depth + 1
        pf.updated_by = request.user
        pf.save()
        return success_response({"id": str(pf.id)})

    def delete(self, request, slug, portfolio_id):
        ws, member = get_workspace_or_404(slug, request.user)
        pf = _get_portfolio(ws, portfolio_id)
        _assert_manager(ws, member, pf)
        try:
            PortfolioService().assert_deletable(portfolio=pf)
        except PortfolioNotEmptyError as e:
            details = []
            if e.children:
                details.append({"field": "children", "code": "IN_USE", "message": f"子节点 {e.children} 个"})
            if e.projects:
                details.append({"field": "projects", "code": "IN_USE", "message": f"挂载项目 {e.projects} 个"})
            raise AppException("RESOURCE_IN_USE", details=details,
                               message="项目集下仍有挂载内容，请先迁移后再删除") from None
        pf.soft_delete(actor_id=request.user.id)
        return success_response({"id": str(pf.id)})


class PortfolioProjectMountView(APIView):
    """POST portfolios/{id}/projects/ —— 挂载（BR-02 双端权限）。"""

    permission_classes = [IsAuthenticated]

    def post(self, request, slug, portfolio_id):
        ws, member = get_workspace_or_404(slug, request.user)
        pf = _get_portfolio(ws, portfolio_id)
        _assert_manager(ws, member, pf)
        project, _, _ = get_project_or_404(slug, request.data.get("project_id"), request.user)
        if project.current_user_role < ProjectRole.ADMIN:
            raise AppException("PERM_ROLE_INSUFFICIENT", message="需项目管理员权限挂载")
        try:
            PortfolioService().mount_project(portfolio=pf, project=project)
        except MountTargetError as e:
            raise AppException("VALIDATION_INVALID_PARAM",
                               details=[{"field": "portfolio_id", "code": "INVALID", "message": str(e)}],
                               message="项目仅可挂载叶子节点") from None
        except MountConflictError as e:
            raise AppException("RESOURCE_ALREADY_EXISTS",
                               details=[{"field": "project_id", "code": "UNIQUE", "message": str(e)}],
                               message="项目已挂载在其他项目集") from None
        return created_response(
            {"portfolio_id": str(pf.id), "project_id": str(project.id)},
            location=f"/api/v1/workspaces/{slug}/portfolios/{pf.id}/projects/{project.id}/")


class PortfolioProjectUnmountView(APIView):
    """DELETE portfolios/{id}/projects/{project_id}/ —— 卸载（无请求体，路径承载目标）。"""

    permission_classes = [IsAuthenticated]

    def delete(self, request, slug, portfolio_id, project_id):
        ws, member = get_workspace_or_404(slug, request.user)
        pf = _get_portfolio(ws, portfolio_id)
        _assert_manager(ws, member, pf)
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        if project.current_user_role < ProjectRole.ADMIN:
            raise AppException("PERM_ROLE_INSUFFICIENT", message="需项目管理员权限卸载")
        row = PortfolioProject.objects.filter(portfolio=pf, project=project).first()
        if row is None:
            raise NotFound("RESOURCE_NOT_FOUND") from None
        row.soft_delete(actor_id=request.user.id)
        return success_response({"portfolio_id": str(pf.id), "project_id": str(project.id)})


class PortfolioSummaryView(APIView):
    """GET portfolios/{id}/summary/ —— 三卡一次载荷（进度+资源+风险，BR-14 收窄）。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, slug, portfolio_id):
        ws, member = get_workspace_or_404(slug, request.user)
        _require_member(ws, member)
        pf = _get_portfolio(ws, portfolio_id)
        svc = PortfolioService()
        from datetime import timedelta
        today = timezone_localdate()
        progress = svc.progress(request.user, pf.id)
        weeks = [(today - timedelta(days=today.weekday()) - timedelta(weeks=k)).isoformat()
                 for k in (3, 2, 1, 0)]
        matrix = [
            {"actor": r["actor__display_name"], "actor_id": str(r["actor_id"]),
             "cells": {r["identifier"]: r["minutes"]}}
            for r in svc.resource_matrix(request.user, pf.id, weeks[0], weeks[-1])]
        risks = svc.risk_list(request.user, pf.id)
        return success_response({
            "portfolio": {"id": str(pf.id), "name": pf.name,
                          "manager_id": str(pf.manager_id) if pf.manager_id else None},
            "progress": {"overall": round(progress.overall, 4),
                         "by_project": [{**p, "ratio": round(p["ratio"], 4)} for p in progress.by_project]},
            "resource": {"weeks": weeks, "matrix": matrix},
            "risks": risks,
        })


class PortfolioMilestoneListCreateView(APIView):
    """GET/POST portfolios/{id}/milestones/ —— 列表（含完成度）+ 新建。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, slug, portfolio_id):
        ws, member = get_workspace_or_404(slug, request.user)
        _require_member(ws, member)
        pf = _get_portfolio(ws, portfolio_id)
        ordering = request.query_params.get("ordering", "-created_at")
        if ordering.lstrip("-") not in ORDERING_WHITELIST:
            ordering = "-created_at"
        svc = PortfolioService()
        rows = (pf.milestones.order_by(ordering)
                .prefetch_related("items", "items__issue", "items__issue__state", "items__issue__project"))
        data = [{
            "id": str(ms.id), "name": ms.name, "description": ms.description,
            "target_date": ms.target_date, "completed_at": ms.completed_at,
            "progress": round(svc.milestone_progress(ms.id, actor=request.user), 4),
            "item_count": len(ms.items.all()),
        } for ms in rows]
        return success_response(data, meta={"count": len(data)})

    def post(self, request, slug, portfolio_id):
        ws, member = get_workspace_or_404(slug, request.user)
        pf = _get_portfolio(ws, portfolio_id)
        _assert_manager(ws, member, pf)
        name = (request.data.get("name") or "").strip()
        target = request.data.get("target_date")
        if not name or not target:
            raise AppException("VALIDATION_ERROR", details=[
                {"field": "name", "code": "REQUIRED", "message": "名称必填"} if not name else
                {"field": "target_date", "code": "REQUIRED", "message": "截止日期必填"}])
        ms = PortfolioMilestone.objects.create(
            portfolio=pf, name=name, description=request.data.get("description") or "",
            target_date=target, created_by=request.user)
        return created_response({"id": str(ms.id), "name": ms.name, "target_date": ms.target_date},
                                location=f"/api/v1/workspaces/{slug}/portfolios/{portfolio_id}/milestones/{ms.id}/")


class PortfolioMilestoneDetailView(APIView):
    """GET/PATCH/DELETE milestones/{milestone_id}/ —— completed_at 显式置位/清除唯一路径。"""

    permission_classes = [IsAuthenticated]

    def _get_ms(self, ws, portfolio_id, milestone_id) -> PortfolioMilestone:
        ms = PortfolioMilestone.objects.select_related("portfolio").filter(
            id=milestone_id, portfolio_id=portfolio_id, portfolio__workspace=ws).first()
        if ms is None:
            raise NotFound("RESOURCE_NOT_FOUND") from None
        return ms

    def get(self, request, slug, portfolio_id, milestone_id):
        ws, member = get_workspace_or_404(slug, request.user)
        _require_member(ws, member)
        ms = self._get_ms(ws, portfolio_id, milestone_id)
        svc = PortfolioService()
        return success_response({
            "id": str(ms.id), "name": ms.name, "target_date": ms.target_date,
            "completed_at": ms.completed_at,
            "progress": round(svc.milestone_progress(ms.id, actor=request.user), 4),
            "items": [{"id": str(it.id), "issue_id": str(it.issue_id),
                       "issue_key": f"{it.issue.project.identifier}-{it.issue.sequence_id}",
                       "name": it.issue.name, "weight": it.weight,
                       "state_group": (it.issue.state.group if it.issue.state else None) or "unstarted"}
                      for it in ms.items.select_related("issue", "issue__project", "issue__state")],
        })

    def patch(self, request, slug, portfolio_id, milestone_id):
        ws, member = get_workspace_or_404(slug, request.user)
        ms = self._get_ms(ws, portfolio_id, milestone_id)
        _assert_manager(ws, member, ms.portfolio)
        for f in ("name", "description", "target_date"):
            if f in request.data:
                setattr(ms, f, request.data[f])
        if "completed_at" in request.data:  # 显式置位/清除（§3.2 唯一路径）
            ms.completed_at = request.data["completed_at"]
        ms.updated_by = request.user
        ms.save()
        return success_response({"id": str(ms.id)})

    def delete(self, request, slug, portfolio_id, milestone_id):
        ws, member = get_workspace_or_404(slug, request.user)
        ms = self._get_ms(ws, portfolio_id, milestone_id)
        _assert_manager(ws, member, ms.portfolio)
        ms.soft_delete(actor_id=request.user.id)
        return success_response({"id": str(ms.id)})


class PortfolioMilestoneItemView(APIView):
    """POST milestones/{milestone_id}/items/ —— 增贡献项（BR-04/BR-05 权重快照）。"""

    permission_classes = [IsAuthenticated]

    def post(self, request, slug, portfolio_id, milestone_id):
        ws, member = get_workspace_or_404(slug, request.user)
        ms = PortfolioMilestone.objects.select_related("portfolio").filter(
            id=milestone_id, portfolio_id=portfolio_id, portfolio__workspace=ws).first()
        if ms is None:
            raise NotFound("RESOURCE_NOT_FOUND") from None
        _assert_manager(ws, member, ms.portfolio)
        from plane.db.models import Issue
        issue = Issue.objects.select_related("project", "project__workspace").filter(
            id=request.data.get("issue_id"), deleted_at__isnull=True).first()
        svc = PortfolioService()
        try:
            # BR-04：贡献项必须属于项目集（任一子树节点挂载的项目）内任务
            subtree_projects = svc.descendant_projects(request.user, portfolio_id)
            if issue is None or issue.project_id not in subtree_projects:
                raise MilestoneItemScopeError("贡献项必须属于项目集内任务")
        except MilestoneItemScopeError:
            raise AppException("VALIDATION_ERROR",
                               details=[{"field": "issue_id", "code": "DOES_NOT_EXIST",
                                         "message": "贡献项必须属于项目集内任务"}]) from None
        item = MilestoneItem.objects.create(
            milestone=ms, issue=issue,
            weight=(issue.estimate_minutes or 0) or 1,  # BR-05 权重快照
            created_by=request.user)
        return created_response(
            {"id": str(item.id), "issue_id": str(issue.id), "weight": item.weight},
            location=f"/api/v1/workspaces/{slug}/portfolios/{portfolio_id}/milestones/{milestone_id}/items/{item.id}/")


class PortfolioMilestoneItemDeleteView(APIView):
    """DELETE milestones/{milestone_id}/items/{item_id}/ —— 删贡献项（软删，同名重建放行）。"""

    permission_classes = [IsAuthenticated]

    def delete(self, request, slug, portfolio_id, milestone_id, item_id):
        ws, member = get_workspace_or_404(slug, request.user)
        ms = PortfolioMilestone.objects.select_related("portfolio").filter(
            id=milestone_id, portfolio_id=portfolio_id, portfolio__workspace=ws).first()
        if ms is None:
            raise NotFound("RESOURCE_NOT_FOUND") from None
        _assert_manager(ws, member, ms.portfolio)
        item = MilestoneItem.objects.filter(id=item_id, milestone=ms).first()
        if item is None:
            raise NotFound("RESOURCE_NOT_FOUND") from None
        item.soft_delete(actor_id=request.user.id)
        return success_response({"id": str(item.id)})


class PortfolioDependencyGraphView(APIView):
    """GET portfolios/{id}/dependency-graph/ —— 依赖图载荷（节点+边，跨项目标记）。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, slug, portfolio_id):
        ws, member = get_workspace_or_404(slug, request.user)
        _require_member(ws, member)
        pf = _get_portfolio(ws, portfolio_id)
        payload = PortfolioService().dependency_graph(request.user, pf.id)
        return success_response(payload, meta={"nodes": len(payload["nodes"]),
                                               "edges": len(payload["edges"]),
                                               "truncated": payload["truncated"]})


def timezone_localdate():
    from django.utils import timezone
    return timezone.localdate()
