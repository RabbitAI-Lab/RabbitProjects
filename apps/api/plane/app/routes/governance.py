"""governance 域路由片段（AUTH-012 租户治理与风控，P4 R1）——17 端点。

平台侧 13（instances/ 前缀，system.tenant.manage）+ 客户侧 4
（workspaces/{slug}/security/，WS_ADMIN 档）。全部经 BR-11 门控。
"""

from django.urls import path

from plane.app.views.governance import (
    BoundaryReportCreateView,
    BoundaryReportDetailView,
    FreezeApprovalView,
    L2RevocationView,
    L2TicketCreateView,
    L2WrittenConfirmView,
    ReleaseView,
    RiskAppealReviewView,
    RiskEventActionsView,
    RiskEventListView,
    TenantDetailView,
    TenantListView,
    TenantQuotaView,
)
from plane.app.views.security import (
    L2TicketApprovalView,
    SecurityAppealView,
    SecurityEventsView,
    SecurityRulesView,
)

urlpatterns = [
    # ── 平台侧：租户面 ──
    path("instances/tenants/", TenantListView.as_view(), name="gov-tenants"),
    path("instances/tenants/<uuid:tenant_id>/", TenantDetailView.as_view(), name="gov-tenant-detail"),
    path("instances/tenants/<uuid:tenant_id>/quota/", TenantQuotaView.as_view(), name="gov-tenant-quota"),
    # ── 平台侧：边界报告（202 异步）──
    path(
        "instances/tenants/<uuid:tenant_id>/boundary-reports/",
        BoundaryReportCreateView.as_view(),
        name="gov-boundary-create",
    ),
    path(
        "instances/tenants/<uuid:tenant_id>/boundary-reports/<uuid:report_id>/",
        BoundaryReportDetailView.as_view(),
        name="gov-boundary-detail",
    ),
    # ── 平台侧：风控事件面 ──
    path("instances/risk-events/", RiskEventListView.as_view(), name="gov-risk-events"),
    path("instances/risk-events/<uuid:event_id>/actions/", RiskEventActionsView.as_view(), name="gov-risk-actions"),
    path(
        "instances/risk-events/<uuid:event_id>/freeze-approval/",
        FreezeApprovalView.as_view(),
        name="gov-freeze-approval",
    ),
    path("instances/risk-events/<uuid:event_id>/releases/", ReleaseView.as_view(), name="gov-release"),
    # ── 平台侧：L2 工单面 ──
    path("instances/l2-tickets/", L2TicketCreateView.as_view(), name="gov-l2-create"),
    path(
        "instances/l2-tickets/<uuid:ticket_id>/written-confirm/", L2WrittenConfirmView.as_view(), name="gov-l2-written"
    ),
    path("instances/l2-tickets/<uuid:ticket_id>/revocation/", L2RevocationView.as_view(), name="gov-l2-revoke"),
    # ── 平台侧：申诉复核 ──
    path("instances/risk-appeals/<uuid:appeal_id>/review/", RiskAppealReviewView.as_view(), name="gov-appeal-review"),
    # ── 客户侧：我的租户安全 ──
    path("workspaces/<slug:slug>/security/events/", SecurityEventsView.as_view(), name="sec-events"),
    path("workspaces/<slug:slug>/security/rules/<str:code>/", SecurityRulesView.as_view(), name="sec-rules"),
    path(
        "workspaces/<slug:slug>/security/events/<uuid:event_id>/appeals/",
        SecurityAppealView.as_view(),
        name="sec-appeal",
    ),
    path(
        "workspaces/<slug:slug>/security/l2-tickets/<uuid:ticket_id>/approval/",
        L2TicketApprovalView.as_view(),
        name="sec-l2-approval",
    ),
]
