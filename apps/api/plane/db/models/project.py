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
    # Sprint-5（AUTH-006 §4.1 注 B）：可见性枚举先行定义、默认 private——
    # 「公开项目对 WS_ONLY 只读可见」通道待架构回改（unified-issue-model §2.4
    # 加列登记 + rbac §6.2 _scoped_for 公开分支）后解锁；落地前 public 行为=private。
    visibility = models.CharField(
        max_length=8,
        choices=[("private", "私有"), ("public", "公开")],
        default="private",
        db_index=True,
        verbose_name="可见性",
    )

    class Meta(BaseModel.Meta):
        db_table = "projects"
        verbose_name = "项目"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "identifier"],
                # BR-14：identifier 占用只看 active；draft 同号是合法中间态
                # （激活时 _guard_activate 应用层复检挡 409），见 0037 迁移
                condition=models.Q(deleted_at__isnull=True, status="active"),
                name="uniq_project_identifier_per_workspace",
            ),
        ]
        indexes = [models.Index(fields=["workspace", "status"], name="idx_project_ws_status")]

    def save(self, *args, **kwargs):
        self.identifier = self.identifier.strip().upper()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"[{self.identifier}] {self.name}"


class ProjectStatusLog(BaseModel):
    """生命周期只增日志（PROJ-003 BR-13）。任何 transition 写一行，永不更新。"""

    project = models.ForeignKey(
        "db.Project", on_delete=models.CASCADE, related_name="status_logs",
        verbose_name="项目",
    )
    from_status = models.CharField(max_length=16, verbose_name="源状态")   # '' 表示创建
    to_status = models.CharField(max_length=16, verbose_name="目标状态")
    operator = models.ForeignKey(
        "db.User", on_delete=models.SET_NULL, null=True, related_name="+",
        verbose_name="操作人",
    )
    reason = models.CharField(max_length=512, blank=True, default="", verbose_name="原因")
    meta = models.JSONField(default=dict, verbose_name="扩展")  # {open_count, forced, affected_issue_ids}

    class Meta(BaseModel.Meta):
        db_table = "project_status_logs"
        verbose_name = "项目状态日志"
        indexes = [models.Index(fields=["project", "-created_at"], name="idx_projstatuslog_time")]


class ProjectTemplate(BaseModel):
    """项目模板：配置四件套快照（非引用，PROJ-003 边界 #7）。内置 3 套 + 空间自定义。"""

    workspace = models.ForeignKey(
        "db.Workspace", on_delete=models.CASCADE,
        related_name="project_templates", null=True, blank=True, verbose_name="工作空间",
    )  # null = 内置
    name = models.CharField(max_length=128, verbose_name="模板名")
    description = models.CharField(max_length=512, blank=True, default="", verbose_name="描述")
    is_builtin = models.BooleanField(default=False, verbose_name="内置模板")
    states_snapshot = models.JSONField(verbose_name="状态快照")       # [{name, group, color, sort_order}]
    labels_snapshot = models.JSONField(default=list, verbose_name="标签快照")     # [{name, color}]
    fields_snapshot = models.JSONField(default=list, verbose_name="字段快照")
    folders_snapshot = models.JSONField(default=list, verbose_name="目录快照")    # [{name, parent_path}]
    created_by = models.ForeignKey(
        "db.User", on_delete=models.SET_NULL, null=True, related_name="+", verbose_name="创建人",
    )

    class Meta(BaseModel.Meta):
        db_table = "project_templates"
        verbose_name = "项目模板"
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "name"], name="uniq_projtpl_ws_name",
                condition=models.Q(is_builtin=False),
            ),
        ]


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

    def save(self, *args, **kwargs):
        # BR-13：workspace 冗余列由 save() 从 project.workspace_id 自动填充，
        # 业务代码禁止手工赋值（rbac §3.2）。`project_id` 已 set 时才触发；
        # 完整对象（已含 .workspace）走 ORM cache 不发 SQL。
        if self.project_id and not self.workspace_id:
            self.workspace_id = self.project.workspace_id
        super().save(*args, **kwargs)


class ProjectFavorite(BaseModel):
    """项目收藏 —— 独立表（对标 Plane UserProjectFavorite），为 P2 视图收藏与排序扩展预留（PROJ-002 §4.1.1）。

    设计取舍（独立表 vs ProjectMember.view_props 内嵌）：
    - 独立表可用 (user, project) 数据库级唯一约束表达幂等（BR-08），
      内嵌 JSONB 做不到数据库级防重；
    - 「按收藏时间排序的收藏列表」是索引查询（idx_pf_user_time），
      内嵌方案需要读出整个 view_props 再内存过滤；
    - P2 视图收藏 / 自定义首页卡片沿袭本表结构扩展 resource_type 列即可。
    """

    user = models.ForeignKey(
        "db.User",
        on_delete=models.CASCADE,
        related_name="project_favorites",
        verbose_name="用户",
    )
    project = models.ForeignKey(
        "db.Project",
        on_delete=models.CASCADE,
        related_name="favorited_by",
        verbose_name="项目",
    )

    class Meta(BaseModel.Meta):
        db_table = "project_favorites"
        verbose_name = "项目收藏"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["user", "project"],
                condition=models.Q(deleted_at__isnull=True),
                name="uniq_user_project_favorite",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "created_at"], name="idx_pf_user_time"),
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
