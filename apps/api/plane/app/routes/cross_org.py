"""cross_org 域路由片段（RPT-006，P4 R7）。"""

from django.urls import path

from plane.app.views.cross_org import CrossOrgCompareView, CrossOrgSummaryView

urlpatterns = [
    path("instances/cross-org/summary/", CrossOrgSummaryView.as_view(), name="xorg-summary"),
    path("instances/cross-org/compare/", CrossOrgCompareView.as_view(), name="xorg-compare"),
]
