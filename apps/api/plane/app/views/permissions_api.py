"""``GET /api/v1/users/me/permissions/`` —— AUTH-005 §4.2 权限快照下发。

四查询聚合（WorkspaceMember / SystemAdmin / ProjectMember / Project 索引扫描），
与用户加入的资源数无关（``assertNumQueries(4)`` 写入 IT-02）。

设计取舍：
  * 响应**实时计算**（BR-07：``Cache-Control: no-store``），不缓存 —— 角色变更
    即时可感知，403 触发重拉的「被动路径」只是 UX 兜底。
  * ``?workspace_slug=<slug>`` 过滤参数：仅返回该工作空间的角色行（其它工作
    空间忽略；缺省 → 全量）。slug 不存在或不归属当前用户 → 静默忽略、不报错
    （权限数据严格遵循「仅本人可见」原则）。
  * 截断阈值 200：超过则 ``meta.truncated=true``（EC-02，P2 升级分页）。
"""
from __future__ import annotations

from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from plane.app.permissions import is_system_admin
from plane.base.response import success_response
from plane.db.models import Project, ProjectMember, WorkspaceMember
from plane.db.models.roles import ProjectRole, WorkspaceRole

#: EC-02 截断阈值（AUTH-005 §2.8 / §4.2 字段说明）
PROJECT_TRUNCATE_THRESHOLD = 200


class UserPermissionsView(APIView):
    """GET /api/v1/users/me/permissions/

    认证：Session + CSRF（GET 免 CSRF 但保持同源调用）。
    限流：60/min（已认证用户档，api-conventions.md §7.2）。
    缓存：``Cache-Control: no-store``（BR-07：实时性优先）。
    权限：``IsAuthenticated``（L0）—— 本端点不设资源门槛。
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        # ── 查询 ① WorkspaceMember（索引 idx_wm_member_ws_role）──
        ws_rows = list(
            WorkspaceMember.objects
            .filter(member=user, is_active=True)
            .values("workspace_id", "workspace__slug", "role")
        )

        # ── 查询 ② SystemAdmin 存在性（索引 idx_sa_user_active）──
        is_admin = is_system_admin(user)

        # ── 查询 ③ ProjectMember 显式行（索引 idx_pm_member_project_role）──
        proj_rows = list(
            ProjectMember.objects
            .filter(member=user, is_active=True)
            .values("project_id", "workspace_id", "role")
        )
        explicit_project_ids = {r["project_id"] for r in proj_rows}
        explicit_role_by_pid = {r["project_id"]: r["role"] for r in proj_rows}

        # ── 可选过滤：?workspace_slug=<slug>（§4.2 查询参数）──
        slug_filter = request.query_params.get("workspace_slug")
        if slug_filter:
            # slug 不归属当前用户 → 静默忽略（只保留 ws_rows 中匹配行）
            filtered_ws_ids = {r["workspace_id"] for r in ws_rows if r["workspace__slug"] == slug_filter}
            ws_rows = [r for r in ws_rows if r["workspace_id"] in filtered_ws_ids]
            ws_role_by_id = {r["workspace_id"]: r["role"] for r in ws_rows}
        else:
            ws_role_by_id = {r["workspace_id"]: r["role"] for r in ws_rows}

        # ── 查询 ④ Project 单表扫描（索引 idx_project_ws_status）──
        # WS_ADMIN+ 在该 workspace 下对所有项目隐式 PROJ_ADMIN（rbac §7.4），
        # 此查询让「隐式成员项目」也进入 projects 映射并标 inherited=true。
        candidate_projects = (
            Project.objects
            .filter(workspace_id__in=ws_role_by_id.keys())
            .values_list("id", "workspace_id")
        )

        projects: dict[str, dict] = {}
        truncated = False
        total = 0
        for project_id, workspace_id in candidate_projects:
            is_explicit = project_id in explicit_project_ids
            ws_r = ws_role_by_id.get(workspace_id, 0)
            # ★ inherited 仅在「无显式 ProjectMember 行 ∧ workspace 角色 ≥ 15」时为 true
            inherited = (not is_explicit) and (ws_r >= WorkspaceRole.ADMIN)
            if not (is_explicit or inherited):
                continue                                          # 既无显式成员、ws 角色也 < 15
            total += 1
            if total > PROJECT_TRUNCATE_THRESHOLD:                 # EC-02 截断
                truncated = True
                continue
            role = (
                explicit_role_by_pid[project_id] if is_explicit else ProjectRole.ADMIN
            )
            projects[str(project_id)] = {
                "workspace_id": str(workspace_id),
                "role": role,
                "inherited": inherited,
            }

        # ── 组装响应（序列化器只承担 shape 文档化，逻辑已在上方聚合完毕）──
        payload = {
            "is_system_admin": is_admin,
            "workspaces": {
                str(r["workspace_id"]): {"slug": r["workspace__slug"], "role": r["role"]}
                for r in ws_rows
            },
            "projects": projects,
        }
        meta = {"generated_at": timezone.now().isoformat(), "truncated": truncated}

        resp = success_response(payload, meta=meta)
        # BR-07：实时性优先，禁止任何缓存层缓存权限快照
        resp["Cache-Control"] = "no-store"
        return resp
