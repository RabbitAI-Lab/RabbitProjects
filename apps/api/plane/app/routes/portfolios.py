"""portfolios 域路由片段（PROJ-004 — 项目集组合树，Sprint-9）。

10 端点：组合树 CRUD / 挂载 / 卸载 / summary / 里程碑 CRUD / 贡献项增删 / 依赖图。
"""
from django.urls import path

from plane.app.views.portfolios import (
    PortfolioDependencyGraphView,
    PortfolioDetailView,
    PortfolioListCreateView,
    PortfolioMilestoneDetailView,
    PortfolioMilestoneItemDeleteView,
    PortfolioMilestoneItemView,
    PortfolioMilestoneListCreateView,
    PortfolioProjectMountView,
    PortfolioProjectUnmountView,
    PortfolioSummaryView,
)

urlpatterns = [
    # 组合树整树返回（GET）/ 新建节点（POST）
    path(
        "workspaces/<slug:slug>/portfolios/",
        PortfolioListCreateView.as_view(),
        name="portfolios-list-create",
    ),
    # 详情 / 改（含移动换父）/ 删（BR-11 非空阻断）
    path(
        "workspaces/<slug:slug>/portfolios/<uuid:portfolio_id>/",
        PortfolioDetailView.as_view(),
        name="portfolios-detail",
    ),
    # 挂载项目（BR-02 双端权限）
    path(
        "workspaces/<slug:slug>/portfolios/<uuid:portfolio_id>/projects/",
        PortfolioProjectMountView.as_view(),
        name="portfolios-mount-project",
    ),
    # 卸载项目（无请求体，删除目标由路径承载）
    path(
        "workspaces/<slug:slug>/portfolios/<uuid:portfolio_id>/projects/<uuid:project_id>/",
        PortfolioProjectUnmountView.as_view(),
        name="portfolios-unmount-project",
    ),
    # 汇总面板（三卡一次载荷，BR-14 收窄）
    path(
        "workspaces/<slug:slug>/portfolios/<uuid:portfolio_id>/summary/",
        PortfolioSummaryView.as_view(),
        name="portfolios-summary",
    ),
    # 里程碑列表（含完成度）/ 新建
    path(
        "workspaces/<slug:slug>/portfolios/<uuid:portfolio_id>/milestones/",
        PortfolioMilestoneListCreateView.as_view(),
        name="portfolios-milestones-list-create",
    ),
    # 里程碑详情 / 编辑（completed_at 显式置位·清除唯一路径）/ 删（软删）
    path(
        "workspaces/<slug:slug>/portfolios/<uuid:portfolio_id>/milestones/<uuid:milestone_id>/",
        PortfolioMilestoneDetailView.as_view(),
        name="portfolios-milestones-detail",
    ),
    # 增贡献项（BR-04 归属校验 + BR-05 权重快照）
    path(
        "workspaces/<slug:slug>/portfolios/<uuid:portfolio_id>/milestones/<uuid:milestone_id>/items/",
        PortfolioMilestoneItemView.as_view(),
        name="portfolios-milestones-items-create",
    ),
    # 删贡献项（软删——condition 唯一约束同步放行同名重建）
    path(
        "workspaces/<slug:slug>/portfolios/<uuid:portfolio_id>/milestones/<uuid:milestone_id>/items/<uuid:item_id>/",
        PortfolioMilestoneItemDeleteView.as_view(),
        name="portfolios-milestones-items-delete",
    ),
    # 依赖图载荷（节点+边，跨项目边标记）
    path(
        "workspaces/<slug:slug>/portfolios/<uuid:portfolio_id>/dependency-graph/",
        PortfolioDependencyGraphView.as_view(),
        name="portfolios-dependency-graph",
    ),
]
