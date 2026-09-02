from django.db import models

from plane.db.models.base import BaseModel
from plane.db.models.roles import ProjectRole


class Project(BaseModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "草稿"
        ACTIVE = "active", "进行中"
        ARCHIVED = "archived", "已归档"
        CLOSED = "closed", "已关闭"

    workspace = models.ForeignKey(
        "db.Workspace", on_delete=models.CASCADE, related_name="projects", verbose_name="所属工作空间"
    )
    name = models.CharField(max_length=255, verbose_name="项目名称")
    description = models.TextField(blank=True, verbose_name="项目描述")
    identifier = models.CharField(max_length=12, verbose_name="项目标识", help_text="工作项编号前缀")
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACTIVE, db_index=True, verbose_name="项目状态"
    )

    class Meta(BaseModel.Meta):
        db_table = "projects"
        verbose_name = "项目"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "identifier"],
                condition=models.Q(deleted_at__isnull=True),
                name="uniq_project_identifier_per_workspace",
            ),
        ]
        indexes = [models.Index(fields=["workspace", "status"], name="idx_project_ws_status")]

    def save(self, *args, **kwargs):
        self.identifier = self.identifier.strip().upper()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"[{self.identifier}] {self.name}"


class ProjectMember(BaseModel):
    project = models.ForeignKey("db.Project", on_delete=models.CASCADE, related_name="project_projectmember")
    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE, related_name="project_member")
    member = models.ForeignKey("db.User", on_delete=models.CASCADE, related_name="member_project")
    role = models.IntegerField(choices=ProjectRole.choices, default=ProjectRole.CONTRIBUTOR)
    is_active = models.BooleanField(default=True)
    view_props = models.JSONField(default=dict, blank=True)

    class Meta(BaseModel.Meta):
        db_table = "project_members"
        verbose_name = "项目成员"
        unique_together = ("project", "member")
        indexes = [
            models.Index(fields=["member", "project", "role"]),
            models.Index(fields=["member", "workspace"]),
        ]


class SystemAdmin(BaseModel):
    user = models.OneToOneField("db.User", on_delete=models.CASCADE, related_name="system_admin")
    is_active = models.BooleanField(default=True)
    granted_by = models.ForeignKey(
        "db.User", on_delete=models.SET_NULL, null=True, related_name="granted_system_admins"
    )
    allowed_ip_cidrs = models.JSONField(default=list, blank=True)

    class Meta(BaseModel.Meta):
        db_table = "system_admins"
        verbose_name = "系统管理员"
        indexes = [models.Index(fields=["user", "is_active"])]
