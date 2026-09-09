"""自定义角色组与挂接（AUTH-008，Sprint-8 R2）。

- CustomRole：项目级角色组，权限码存 JSONB 字符串数组（码权威在注册表
  代码，CI 校验一致，不入库码表 FK）。
- ProjectRoleAssignment：(project, user, role) 挂接行——权限判定的
  「自定义来源」；grant_batch 复用 AUTH-007 批次表（批量挂角色溯源）。
"""
from django.db import models
from django.db.models import functions

from plane.db.models.base import BaseModel


class CustomRole(BaseModel):
    """自定义角色组（P3）。"""

    project = models.ForeignKey("db.Project", on_delete=models.CASCADE,
                                related_name="custom_roles")
    name = models.CharField(max_length=40)
    description = models.CharField(max_length=200, blank=True, default="")
    permissions = models.JSONField(default=list)  # ["issue.read", ...] 有序去重
    is_builtin_template = models.BooleanField(default=False)
    template_key = models.CharField(max_length=40, null=True, blank=True)  # BR-14 稳定键

    class Meta(BaseModel.Meta):
        db_table = "custom_role"
        constraints = [
            models.UniqueConstraint(
                "project", functions.Lower("name"),
                condition=models.Q(deleted_at__isnull=True),
                name="uq_custom_role_name",
            ),
            models.CheckConstraint(
                # type: ignore[call-arg]  # stub 声明过窄（同 workflow chk_transition_no_self_loop）
                check=models.Q(permissions__isnull=False),
                name="ck_custom_role_perms_shape",
            ),
        ]
        indexes = [
            models.Index("project", "deleted_at", name="idx_custom_role_project"),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.project_id})"


class ProjectRoleAssignment(BaseModel):
    """成员-自定义角色挂接行：(project, user, role) 唯一（BR-06 幂等）。"""

    project = models.ForeignKey("db.Project", on_delete=models.CASCADE)
    user = models.ForeignKey("db.User", on_delete=models.CASCADE)
    role = models.ForeignKey(CustomRole, on_delete=models.CASCADE,
                             related_name="assignments")
    # AUTH-007 批量溯源（按部门挂角色时记批次；逐人挂接为 NULL）
    grant_batch = models.ForeignKey("db.DepartmentGrantBatch", null=True,
                                    on_delete=models.SET_NULL)

    class Meta(BaseModel.Meta):
        db_table = "project_role_assignment"
        constraints = [
            models.UniqueConstraint("project", "user", "role",
                                    condition=models.Q(deleted_at__isnull=True),
                                    name="uq_role_assignment"),
        ]
        indexes = [
            models.Index("project", "user", name="idx_role_assign_lookup"),
        ]
