"""部门层级组织架构（AUTH-007，Sprint-8）。

实体对齐 rbac-permission-model.md §3.4 预留模型：
- Department：物化路径树（``/{dept_id}/…/{dept_id}/``，段为 UUID v4，含自身）；
  子树查询 = path 前缀匹配，深度 = 实段数（根 1、上限 6，BR-01）。
- DepartmentGrantBatch：按部门授权的「快照展开」审计锚点（§1.3）——
  授权时刻把部门成员展开为逐人 ProjectMember 行，批次行自含成员清单
  快照与 department_id_snapshot（曾授权部门可删，溯源不依赖 FK 存活）。

注：AUTH-008 将经独立迁移为 DepartmentGrantBatch 增补 target_type
判别列（"project_membership" | "role"），本文不预建（规格 §4.1）。
"""
from django.db import models
from django.db.models import functions

from plane.db.models.base import BaseModel
from plane.db.models.project import Project
from plane.db.models.roles import ProjectRole


class Department(BaseModel):
    """部门层级（P3）。"""

    workspace = models.ForeignKey(
        "db.Workspace",
        on_delete=models.CASCADE,
        related_name="departments",
    )
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,  # 删除受限走 Service 层 BR-04 校验，PROTECT 兜底防误删子树失根
        related_name="children",
    )
    name = models.CharField(max_length=255)
    # 物化路径："/{dept_id}/{dept_id}/…/"，含自身 id。
    # 深度 = strip 首尾斜杠后剩余分隔符计数 + 1（§4.3 _depth_of——不可 count("/")，
    # 首尾斜杠会 +2 偏移）；子树 = path 前缀匹配走本列索引。
    path = models.TextField(db_index=True, editable=False)
    sort_order = models.FloatField(default=65536.0)

    class Meta(BaseModel.Meta):
        db_table = "department"
        constraints = [
            models.UniqueConstraint(
                "workspace",
                "parent",
                functions.Lower("name"),
                condition=models.Q(deleted_at__isnull=True),
                name="uq_department_sibling_name",
            ),
            # 根部门（parent IS NULL）：PG 复合唯一中 NULL 不参与比较，上面约束
            # 对根部门失效——按规格 §4.1 以部分唯一索引兜底（BR-02）
            models.UniqueConstraint(
                "workspace",
                functions.Lower("name"),
                condition=models.Q(parent__isnull=True, deleted_at__isnull=True),
                name="uq_department_root_name",
            ),
            models.CheckConstraint(
                check=models.Q(sort_order__gt=0),  # type: ignore[call-arg]  # stub 声明过窄（同 workflow chk_transition_no_self_loop）
                name="ck_department_sort_positive",
            ),
        ]
        indexes = [
            models.Index("workspace", "parent", "sort_order", name="idx_department_tree_read"),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.workspace.slug})"


class DepartmentGrantBatch(BaseModel):
    """授权批次：快照展开的审计锚点（BR-09 / 幂等重同步）。"""

    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE)
    # BR-04：曾授权部门可删（只校验成员/子部门，不校验批次）——SET_NULL 保留
    # 批次审计行，杜绝 ProtectedError → 500（api-conventions §4.3 已知业务失败
    # 不得 500）；溯源靠下方自含快照列
    department = models.ForeignKey(
        Department,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="grant_batches",
    )
    department_id_snapshot = models.UUIDField(editable=False)  # 授权时刻的部门 id
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    role = models.IntegerField(
        choices=ProjectRole.choices,
        default=ProjectRole.CONTRIBUTOR,
    )
    with_descendants = models.BooleanField(default=True)
    added_count = models.IntegerField(default=0)
    role_changed_count = models.IntegerField(default=0)
    skipped_count = models.IntegerField(default=0)
    unchanged_count = models.IntegerField(default=0)
    member_snapshot = models.JSONField(default=list)  # [{member_id, action}]

    class Meta(BaseModel.Meta):
        db_table = "department_grant_batch"
        indexes = [
            models.Index("project", "created_at", name="idx_grant_batch_project"),
        ]

    def __str__(self) -> str:
        return f"grant-batch {self.id} project={self.project_id}"
