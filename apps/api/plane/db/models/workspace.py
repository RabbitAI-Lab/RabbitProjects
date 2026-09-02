from django.db import models

from plane.db.models.base import BaseModel
from plane.db.models.roles import WorkspaceRole


class Workspace(BaseModel):
    name = models.CharField(max_length=255, verbose_name="名称")
    slug = models.SlugField(max_length=48, db_index=True, verbose_name="URL 标识")
    description = models.TextField(blank=True, verbose_name="描述")
    logo = models.URLField(max_length=800, blank=True, null=True, verbose_name="Logo 地址")
    owner = models.ForeignKey(
        "db.User", on_delete=models.CASCADE, related_name="owner_workspaces", verbose_name="所有者"
    )

    class Meta(BaseModel.Meta):
        db_table = "workspaces"
        verbose_name = "工作空间"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["slug"], condition=models.Q(deleted_at__isnull=True), name="uniq_workspace_slug_alive"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.slug})"


class WorkspaceMember(BaseModel):
    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE, related_name="workspace_member")
    member = models.ForeignKey("db.User", on_delete=models.CASCADE, related_name="member_workspace")
    role = models.IntegerField(choices=WorkspaceRole.choices, default=WorkspaceRole.MEMBER)
    is_active = models.BooleanField(default=True)
    company_role = models.TextField(null=True, blank=True, verbose_name="公司内职务")

    class Meta(BaseModel.Meta):
        db_table = "workspace_members"
        verbose_name = "工作空间成员"
        unique_together = ("workspace", "member")
        indexes = [
            models.Index(fields=["member", "workspace", "role"]),
            models.Index(fields=["workspace", "role"]),
        ]
