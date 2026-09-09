"""audit_logs 域路由片段（AUTH-010 — 全站审计，Sprint-8 R4）。"""
from django.urls import path

from plane.app.views.audit_logs import (
    AuditCatalogView,
    AuditExportView,
    AuditLogListView,
    InstanceAuditLogListView,
)

urlpatterns = [
    path("workspaces/<slug:slug>/audit-logs/",
         AuditLogListView.as_view(), name="audit-logs-list"),
    path("workspaces/<slug:slug>/audit-logs/catalog/",
         AuditCatalogView.as_view(), name="audit-logs-catalog"),
    path("workspaces/<slug:slug>/audit-logs/exports/",
         AuditExportView.as_view(), name="audit-logs-export"),
    path("instances/audit-logs/",
         InstanceAuditLogListView.as_view(), name="instance-audit-logs"),
]
