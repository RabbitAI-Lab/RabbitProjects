"""departments 域路由片段（AUTH-007 — 部门组织架构，Sprint-8 R1）。

11 端点：部门 CRUD / move / 批量调部门 / 授权预览 / 执行 / 批次详情 / 统计。
挂部门/岗位复用 members 域既有端点（PATCH members/{id} 白名单扩展）。
"""
from django.urls import path

from plane.app.views.departments import (
    DepartmentBulkMoveMembersView,
    DepartmentDetailView,
    DepartmentGrantBatchDetailView,
    DepartmentGrantPreviewView,
    DepartmentGrantView,
    DepartmentListCreateView,
    DepartmentMoveView,
    DepartmentStatsView,
)

urlpatterns = [
    # 部门平铺列表（GET）/ 新建（POST）
    path(
        "workspaces/<slug:slug>/departments/",
        DepartmentListCreateView.as_view(),
        name="departments-list-create",
    ),
    # 改名 / 排序（PATCH）/ 删除（DELETE，204）
    path(
        "workspaces/<slug:slug>/departments/<uuid:department_id>/",
        DepartmentDetailView.as_view(),
        name="departments-detail",
    ),
    # 移动（换父级）
    path(
        "workspaces/<slug:slug>/departments/<uuid:department_id>/move/",
        DepartmentMoveView.as_view(),
        name="departments-move",
    ),
    # 批量调部门
    path(
        "workspaces/<slug:slug>/departments/<uuid:department_id>/members/bulk-move/",
        DepartmentBulkMoveMembersView.as_view(),
        name="departments-bulk-move",
    ),
    # 按部门授权：预览 / 执行 / 批次详情
    path(
        "workspaces/<slug:slug>/departments/<uuid:department_id>/grants/preview/",
        DepartmentGrantPreviewView.as_view(),
        name="departments-grant-preview",
    ),
    path(
        "workspaces/<slug:slug>/departments/<uuid:department_id>/grants/",
        DepartmentGrantView.as_view(),
        name="departments-grant",
    ),
    path(
        "workspaces/<slug:slug>/departments/<uuid:department_id>/grants/<uuid:batch_id>/",
        DepartmentGrantBatchDetailView.as_view(),
        name="departments-grant-batch",
    ),
    # 部门统计（成员直属/含子级 + 任务量）
    path(
        "workspaces/<slug:slug>/departments/<uuid:department_id>/stats/",
        DepartmentStatsView.as_view(),
        name="departments-stats",
    ),
]
