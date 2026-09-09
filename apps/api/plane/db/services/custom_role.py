"""自定义角色域服务（AUTH-008 §4.3，Sprint-8 R2）。

角色 CRUD（码校验/重名/上限/删除保护）+ 挂接（幂等/GUEST 天花板预检/
缓存主动失效）+ 按部门批量挂（复用 AUTH-007 快照展开范式，批次表
target_type="role"）+ WS 降级级联卸除（BR-16 后半）。
"""
from __future__ import annotations

from django.db import transaction

from plane.base.exception import AppException
from plane.bgtasks.audit import record_audit
from plane.constants.custom_role_catalog import (
    guest_ceiling_violations,
    validate_codes,
)
from plane.db.models import (
    CustomRole,
    Department,
    DepartmentGrantBatch,
    Project,
    ProjectRoleAssignment,
    WorkspaceMember,
)
from plane.db.models.roles import WorkspaceRole

MAX_ROLES_PER_PROJECT = 20

#: BR-14 内置三模板（稳定 template_key → 预置码集；采用时可改可删）
BUILTIN_TEMPLATES: dict[str, dict] = {
    "qa_engineer": {
        "name": "测试工程师",
        "description": "任务读写 + 流转 + 评论 + 文件（不含删除/归档）",
        "permissions": [
            "issue.read", "issue.create", "issue.update",
            "issue.state.transition", "comment.create", "comment.update.own",
            "comment.delete.own", "file.read", "file.upload",
        ],
    },
    "external_collab": {
        "name": "外包协作",
        "description": "任务只读 + 评论",
        "permissions": ["issue.read", "comment.read", "comment.create",
                        "comment.update.own"],
    },
    "stakeholder_readonly": {
        "name": "只读干系人",
        "description": "全量只读（报表可见）",
        "permissions": ["issue.read", "comment.read", "file.read", "board.read",
                        "gantt.read", "report.read", "notification.read"],
    },
}


def _audit(event: str, *, actor_id, object_id, **extra) -> None:
    transaction.on_commit(
        lambda: record_audit.delay(event, actor_id=str(actor_id),
                                   object_id=str(object_id) if object_id else None,
                                   **extra)
    )


def find_guard_references(role: CustomRole) -> list[str]:
    """BR-10：扫描流转守卫/审批节点对 custom:<role_id> 的引用（WF-001 §4.7 格式）。

    返回引用点描述清单（如 "transition:开始"、"approval-flow:发布审批"）。
    """
    from plane.db.models import ApprovalNode, WorkflowTransition

    marker = f"custom:{role.id}"
    refs: list[str] = []
    for t in (WorkflowTransition.objects
              .filter(workflow__project_id=role.project_id,
                      guards__icontains=marker)
              .select_related("workflow")):
        if any(marker in str(g) for g in (t.guards or [])):
            refs.append(f"transition:{t.name}")
    for n in (ApprovalNode.objects
              .filter(flow__project_id=role.project_id,
                      approver_config__icontains=marker)
              .select_related("flow")):
        if marker in str(n.approver_config or {}):
            refs.append(f"approval-flow:{n.flow.name}")
    return refs


class RoleService:

    # ── 角色 CRUD ─────────────────────────────────────────

    @transaction.atomic
    def create_role(self, *, actor, project: Project, name: str,
                    description: str = "", permissions: list[str] | None = None,
                    template_key: str | None = None) -> CustomRole:
        if template_key:
            tpl = BUILTIN_TEMPLATES.get(template_key)
            if tpl is None:
                raise AppException(
                    "VALIDATION_ERROR",
                    message="未知的内置模板",
                    details=[{"field": "template_key", "code": "NOT_A_CHOICE",
                              "message": f"template_key 仅支持 {sorted(BUILTIN_TEMPLATES)}"}],
                )
            name = name or tpl["name"]
            description = description or tpl["description"]
            permissions = permissions or list(tpl["permissions"])
        if CustomRole.objects.filter(project=project, deleted_at__isnull=True) \
                .count() >= MAX_ROLES_PER_PROJECT:  # BR-04
            raise AppException(
                "RESOURCE_LIMIT_EXCEEDED",
                message=f"每项目自定义角色最多 {MAX_ROLES_PER_PROJECT} 个",
                details=[{"field": "id", "code": "TOO_LARGE",
                          "message": f"每项目自定义角色最多 {MAX_ROLES_PER_PROJECT} 个"}],
            )
        name = (name or "").strip()
        if not name:
            raise AppException(
                "VALIDATION_ERROR",
                message="角色名不可为空",
                details=[{"field": "name", "code": "INVALID", "message": "角色名不可为空"}],
            )
        if CustomRole.objects.filter(project=project, name__iexact=name,
                                     deleted_at__isnull=True).exists():  # BR-03
            raise AppException(
                "RESOURCE_ALREADY_EXISTS",
                message="项目内已存在同名角色",
                details=[{"field": "name", "code": "UNIQUE",
                          "message": "项目内角色名不可重复（不区分大小写）"}],
            )
        role = CustomRole.objects.create(
            project=project, name=name, description=description,
            permissions=validate_codes(permissions or []),
            is_builtin_template=bool(template_key),
            template_key=template_key,
            created_by=actor, updated_by=actor,
        )
        _audit("role.create", actor_id=actor.id, object_id=role.id,
               role_name=name)
        return role

    @transaction.atomic
    def update_role(self, *, actor, role: CustomRole,
                    name: str | None = None,
                    description: str | None = None,
                    permissions: list[str] | None = None) -> CustomRole:
        if name is not None:
            name = name.strip()
            if not name:
                raise AppException(
                    "VALIDATION_ERROR",
                    message="角色名不可为空",
                    details=[{"field": "name", "code": "INVALID",
                              "message": "角色名不可为空"}],
                )
            if (CustomRole.objects
                    .filter(project_id=role.project_id, name__iexact=name,
                            deleted_at__isnull=True)
                    .exclude(pk=role.pk).exists()):  # BR-03
                raise AppException(
                    "RESOURCE_ALREADY_EXISTS",
                    message="项目内已存在同名角色",
                    details=[{"field": "name", "code": "UNIQUE",
                              "message": "项目内角色名不可重复（不区分大小写）"}],
                )
            role.name = name
        if description is not None:
            role.description = description
        if permissions is not None:
            role.permissions = validate_codes(permissions)
        role.updated_by = actor
        role.save()
        # BR-12：码收紧对全部挂接者即时生效——按挂接清单批量失效
        from plane.app.effective_perms import invalidate

        affected = list(role.assignments.filter(deleted_at__isnull=True)
                        .values_list("user_id", flat=True))
        transaction.on_commit(lambda: invalidate(affected, role.project_id))
        _audit("role.update", actor_id=actor.id, object_id=role.id,
               role_name=role.name)
        return role

    @transaction.atomic
    def delete_role(self, *, actor, role: CustomRole) -> None:
        refs = find_guard_references(role)  # BR-10
        assigned = role.assignments.filter(deleted_at__isnull=True).count()  # BR-05
        if assigned or refs:
            details = [{"field": "id", "code": "IN_USE",
                        "message": f"assigned_count={assigned};"
                                   f" referenced_by={','.join(refs) or '无'}"}]
            raise AppException(
                "RESOURCE_IN_USE",
                message="角色存在挂接或引用，无法删除",
                details=details,
            )
        role.delete()
        _audit("role.delete", actor_id=actor.id, object_id=role.id,
               role_name=role.name)

    # ── 挂接 ──────────────────────────────────────────────

    def _assert_attachable(self, role: CustomRole, target_user,
                           workspace) -> None:
        """BR-16（方案 B）：WS_GUEST 只能挂天花板内角色——拒绝而非静默交集。"""
        ws_role = WorkspaceMember.objects.filter(
            workspace=workspace, member=target_user,
            is_active=True, deleted_at__isnull=True,
        ).values_list("role", flat=True).first()
        if ws_role != WorkspaceRole.GUEST:
            return
        over = guest_ceiling_violations(role.permissions or [])
        if over:
            raise AppException(
                "RESOURCE_STATE_INVALID",
                message="访客成员不能挂接超出访客天花板的角色",
                details=[{"field": "role_id", "code": "GUEST_CEILING",
                          "message": f"越界权限码：{', '.join(over)}"}],
            )

    @transaction.atomic
    def assign(self, *, actor, role: CustomRole, target_user) -> bool:
        """挂接（BR-06 幂等；BR-16 预检；BR-07 缓存失效）。返回是否新建行。"""
        self._assert_attachable(role, target_user, role.project.workspace)
        _, created = ProjectRoleAssignment.objects.get_or_create(
            project=role.project, user=target_user, role=role,
            defaults={"created_by": actor, "updated_by": actor},
        )
        if created:
            from plane.app.effective_perms import invalidate

            transaction.on_commit(
                lambda: invalidate([target_user.id], role.project_id))
            _audit("role.assign", actor_id=actor.id, object_id=role.id,
                   target_user=str(target_user.id), role_name=role.name)
        return created

    @transaction.atomic
    def revoke(self, *, actor, role: CustomRole, target_user) -> None:
        rows = ProjectRoleAssignment.objects.filter(
            project=role.project, user=target_user, role=role,
            deleted_at__isnull=True,
        )
        if rows.exists():
            rows.delete()  # 实例级软删语义（QuerySet 软删 update）
            from plane.app.effective_perms import invalidate

            transaction.on_commit(
                lambda: invalidate([target_user.id], role.project_id))
            _audit("role.revoke", actor_id=actor.id, object_id=role.id,
                   target_user=str(target_user.id), role_name=role.name)

    @transaction.atomic
    def bulk_assign(self, *, actor, role: CustomRole,
                    user_ids: list[str] | None = None,
                    department_id=None) -> dict:
        """批量挂接（BR-09 复用 AUTH-007 快照展开范式，整事务）。

        user_ids 或 department_id 二选一；按部门时圈定子树（含停用者过滤）
        成员 User id 集。任一目标违反 BR-16 → 整批拒绝（无部分挂接）。
        """
        if bool(user_ids) == (department_id is not None):
            raise AppException(
                "VALIDATION_ERROR",
                message="user_ids 与 department_id 必须二选一",
                details=[{"field": "user_ids", "code": "INVALID",
                          "message": "user_ids 与 department_id 必须二选一"}],
            )
        ws = role.project.workspace
        if department_id is not None:
            dept = Department.objects.get(pk=department_id, workspace=ws,
                                          deleted_at__isnull=True)
            subtree_ids = list(Department.objects.filter(
                workspace=ws, deleted_at__isnull=True,
                path__startswith=dept.path).values_list("id", flat=True))
            members = list(WorkspaceMember.objects.filter(
                workspace=ws, department_id__in=subtree_ids,
                is_active=True, deleted_at__isnull=True,
            ).select_related("member"))
        else:
            members = list(WorkspaceMember.objects.filter(
                workspace=ws, member_id__in=user_ids,
                is_active=True, deleted_at__isnull=True,
            ).select_related("member"))
        # BR-16 整批预检（任一 GUEST 越界 → 整批 409，不产生部分挂接）
        for wm in members:
            self._assert_attachable(role, wm.member, ws)
        added: list[str] = []
        skipped: list[str] = []
        for wm in members:
            _, created = ProjectRoleAssignment.objects.get_or_create(
                project=role.project, user=wm.member, role=role,
                defaults={"created_by": actor, "updated_by": actor},
            )
            (added if created else skipped).append(str(wm.member_id))
        # 批次行（target_type="role"，AUTH-007 溯源复用）
        batch = DepartmentGrantBatch.objects.create(
            workspace=ws,
            department_id=department_id,
            department_id_snapshot=department_id,
            project=role.project,
            role=0,
            target_type="role",
            with_descendants=True,
            added_count=len(added), skipped_count=len(skipped),
            unchanged_count=0,
            member_snapshot=([{"member_id": m, "action": "added"} for m in added]
                             + [{"member_id": m, "action": "skipped:already"} for m in skipped]),
            created_by=actor, updated_by=actor,
        )
        from plane.app.effective_perms import invalidate

        transaction.on_commit(
            lambda: invalidate([m.member_id for m in members], role.project_id))
        _audit("role.granted", actor_id=actor.id, object_id=batch.id,
               role_name=role.name, added=len(added))
        return {"batch_id": str(batch.id), "added": added,
                "skipped": skipped}

    def cascade_revoke_on_ws_downgrade(self, *, workspace, target_user) -> list[str]:
        """BR-16 后半：WS 成员降级为 GUEST 时，同事务级联卸除越界挂接。

        返回被卸除的角色名清单（供通知文案）。调用方须在同一事务内。
        """
        qs = ProjectRoleAssignment.objects.filter(
            user=target_user, deleted_at__isnull=True,
            project__workspace=workspace).select_related("role")
        pairs = [(target_user.id, pid) for pid in
                 qs.values_list("project_id", flat=True).distinct()]
        revoked: list[str] = []
        for pa in qs:
            over = guest_ceiling_violations(pa.role.permissions or [])
            if over:
                pa.delete()
                revoked.append(pa.role.name)
        if revoked:
            from plane.app.effective_perms import invalidate_many

            transaction.on_commit(lambda: invalidate_many(pairs))
        return revoked
