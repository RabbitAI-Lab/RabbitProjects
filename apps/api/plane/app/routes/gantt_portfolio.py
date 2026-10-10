"""gantt_portfolio 域路由片段（GANTT-004/005/006，P4 R15）。"""

from django.urls import path

from plane.app.views.gantt_portfolio import (
    CpLockView,
    PortfolioGanttView,
    ResourceLoadView,
)

urlpatterns = [
    path("workspaces/<slug:slug>/portfolios/<uuid:portfolio_id>/gantt/", PortfolioGanttView.as_view(), name="pf-gantt"),
    path("workspaces/<slug:slug>/resource-load/", ResourceLoadView.as_view(), name="ws-resource-load"),
    path("workspaces/<slug:slug>/projects/<uuid:project_id>/gantt/cp-lock/", CpLockView.as_view(), name="cp-lock"),
]
