"""统一响应信封（api-conventions.md §4）。"""
from importlib import import_module

from django.db import connection
from django.urls import path
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from plane.app.views.activity_dlq_admin import (
    ActivityDeadLetterBulkReplayView,
    ActivityDeadLetterDiscardView,
    ActivityDeadLetterListView,
    ActivityDeadLetterReplayView,
)
from plane.app.views.auth import MeView, SignInView, SignOutView, SignUpView, csrf_token
from plane.app.views.issues import IssueDetailView, IssueListCreateView
from plane.app.views.ops_admin import (
    BackupRunListView,
    BackupRunTriggerView,
    RateLimitSummaryView,
)
from plane.app.views.project_templates import (
    ProjectTemplateDetailView,
    ProjectTemplateListCreateView,
)
from plane.app.views.projects import (
    ProjectDetailView,
    ProjectDuplicateView,
    ProjectListCreateView,
    ProjectStateListView,
    ProjectStatusLogView,
    ProjectTransitionView,
)
from plane.app.views.release_gates import (
    ReleaseGateCreateView,
    ReleaseGateDetailView,
    ReleaseGateEventView,
    ReleaseGateListView,
    ReleaseGateSignView,
    ReleaseGateVerdictView,
)
from plane.app.views.workspaces import WorkspaceDetailView, WorkspaceListCreateView
from plane.base.exception import AppException
from plane.base.response import success_response


class HealthView(APIView):
    """INFRA-002 §4.10 健康检查端点（db 连接探针）。

    注意：celery/redis 连接故意不在此检查——服务组件健康由 compose depends_on 链表达，
    health 端点仅作为容器级可用性的真实探针（避免假就绪）。

    响应同样走 C1 信封（INFRA-004 §4.6 强制：不存在第三种结构）。
    compose ``curl -f`` 只看 HTTP 状态码，所以 ``data.checks.db`` 改名为
    ``data.checks.db==ok`` 不影响探针判定。
    """

    permission_classes = [AllowAny]
    # BR-05（INFRA-005 §2.3）：健康检查不消耗配额也不被限——L2 全局四类经
    # DEFAULT_THROTTLE_CLASSES 生效后，显式空声明维持 INFRA-002 §4.10 口径
    #（DRF 的 throttle_classes 是整体替换，空列表即豁免）
    throttle_classes: list = []

    def get(self, request):
        try:
            with connection.cursor() as cur:
                cur.execute("SELECT 1")
        except Exception as exc:
            raise AppException(
                "SERVER_DATABASE_ERROR",
                message=str(exc)[:200] or "数据库探针失败",
            ) from exc
        return success_response({"checks": {"db": "ok"}})


urlpatterns = [
    path("health/", HealthView.as_view(), name="health"),
    # admin 死信补偿（TASK-010 §4.2.2：系统级顶层资源，不嵌套 workspace）
    path("activity-dead-letters/", ActivityDeadLetterListView.as_view(), name="activity-dead-letters"),
    path(
        "activity-dead-letters/bulk/",
        ActivityDeadLetterBulkReplayView.as_view(),
        name="activity-dead-letters-bulk",
    ),
    path(
        "activity-dead-letters/<uuid:message_id>/replay/",
        ActivityDeadLetterReplayView.as_view(),
        name="activity-dead-letter-replay",
    ),
    path(
        "activity-dead-letters/<uuid:message_id>/",
        ActivityDeadLetterDiscardView.as_view(),
        name="activity-dead-letter-discard",
    ),
    # 发布门禁（QA-001 §4.4——系统管理员面 instances/ 前缀，Sprint-6 T6-06）
    path("instances/release-gates/", ReleaseGateListView.as_view(),
         name="release-gates-list"),
    path("instances/release-gates/create/", ReleaseGateCreateView.as_view(),
         name="release-gates-create"),
    path("instances/release-gates/<uuid:gate_id>/", ReleaseGateDetailView.as_view(),
         name="release-gates-detail"),
    path("instances/release-gates/<uuid:gate_id>/gate-events/",
         ReleaseGateEventView.as_view(), name="release-gates-event"),
    path("instances/release-gates/<uuid:gate_id>/checklist/<str:key>/sign/",
         ReleaseGateSignView.as_view(), name="release-gates-sign"),
    path("instances/release-gates/<uuid:gate_id>/verdict/",
         ReleaseGateVerdictView.as_view(), name="release-gates-verdict"),
    # admin 运维面（INFRA-005 §4.2——备份/限流，T6-05）
    path("instances/backups/", BackupRunListView.as_view(), name="ops-backups-list"),
    path("instances/backups/trigger/", BackupRunTriggerView.as_view(),
         name="ops-backups-trigger"),
    path("instances/rate-limit/summary/", RateLimitSummaryView.as_view(),
         name="ops-ratelimit-summary"),
    path("auth/sign-up/", SignUpView.as_view(), name="auth-signup"),
    path("auth/sign-in/", SignInView.as_view(), name="auth-signin"),
    path("auth/sign-out/", SignOutView.as_view(), name="auth-signout"),
    path("auth/csrf-token/", csrf_token, name="auth-csrf"),
    path("users/me/", MeView.as_view(), name="users-me"),
    path("workspaces/", WorkspaceListCreateView.as_view(), name="workspaces-list-create"),
    path("workspaces/<slug:slug>/", WorkspaceDetailView.as_view(), name="workspaces-detail"),
    path("workspaces/<slug:slug>/projects/", ProjectListCreateView.as_view(), name="projects-list-create"),
    # ── Sprint-5（PROJ-003 §4.2）──
    path("workspaces/<slug:slug>/projects/<uuid:project_id>/transitions/",
         ProjectTransitionView.as_view(), name="project-transitions"),
    path("workspaces/<slug:slug>/projects/<uuid:project_id>/status-logs/",
         ProjectStatusLogView.as_view(), name="project-status-logs"),
    path("workspaces/<slug:slug>/projects/<uuid:project_id>/duplicate/",
         ProjectDuplicateView.as_view(), name="project-duplicate"),
    path("workspaces/<slug:slug>/project-templates/",
         ProjectTemplateListCreateView.as_view(), name="project-templates-list-create"),
    path("workspaces/<slug:slug>/project-templates/<uuid:template_id>/",
         ProjectTemplateDetailView.as_view(), name="project-templates-detail"),
    path("workspaces/<slug:slug>/projects/<uuid:project_id>/", ProjectDetailView.as_view(), name="projects-detail"),
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/states/",
        ProjectStateListView.as_view(),
        name="project-states",
    ),
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/issues/",
        IssueListCreateView.as_view(),
        name="issues-list-create",
    ),
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/",
        IssueDetailView.as_view(),
        name="issues-detail",
    ),
]

#: sprint-1 按功能域拆分的路由片段（plane/app/routes/*.py 各自导出 urlpatterns）。
#: 拆分原因见 plane/app/routes/__init__.py：避免并行开发在单一 urls.py 上互相踩踏。
FEATURE_MODULES = (
    "permissions",      # AUTH-005 权限下发
    "users",            # AUTH-004 资料 / 密码 / 重置
    "members",          # TEAM-002 团队成员与邀请
    "project_members",  # PROJ-002 项目成员 / 收藏
    "labels",           # TASK-002 项目标签
    "attachments",      # FILE-001 任务附件
    "file_library",     # FILE-002 项目文件库与多层级目录
    "file_versions",    # FILE-003 分片会话 / 版本 / 预览调度
    "file_shares",      # FILE-004 文件分享链接与权限管控
    "comments",         # COLLAB-001 评论
    "notifications",    # COLLAB-001 通知中心
    "stats",            # RPT-001 个人统计
    "custom_fields",    # TASK-008 自定义字段（Schema API + 管理 CRUD）
    "issue_views",      # BOARD-003 保存的视图（views/ CRUD 五端点）
    "activity_stream",  # COLLAB-003 项目动态流（合流 + 折叠 + 组感知游标）
    "issue_bulk",       # BOARD-004 任务批量操作（bulk/ + bulk/archive/ + bulk/preview/）
    "realtime",         # COLLAB-004 live 实时票据（换票 / 续签 / verify-rooms 内部复核）
    "gantt",            # GANTT-001 甘特取数地基（视窗行 / 连线批量 / 未排期）
    "integrations",     # INTG-001 GitHub 集成（安装 / 绑定 / 入站 Webhook / 日志）
    "webhooks",         # INTG-002 出站 Webhook（端点 / 投递 / 重放）
    "workflow",         # M11-WF 工作流引擎与审批（WF-001/WF-002，Sprint-7）
)

for _name in FEATURE_MODULES:
    _module = import_module(f"plane.app.routes.{_name}")
    urlpatterns += _module.urlpatterns
