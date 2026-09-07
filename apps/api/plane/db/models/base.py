import uuid

from django.db import models
from django.utils import timezone


class SoftDeleteQuerySet(models.QuerySet):
    def delete(self, soft: bool = True) -> tuple[int, dict[str, int]]:
        if soft:
            return self.update(deleted_at=timezone.now()), {}
        return super().delete()

    # ── 行级可见性通道（AUTH-006 §4.2；全体模型经 SoftDeleteManager 继承）──
    # 语义：accessible_by/accessible_in 起步即过滤（BR-01）；unsafe_all 为唯一
    # 例外出口（BR-08：必须带 reason 且进例外登记）。
    def accessible_by(self, user, *, workspace_id=None):
        from plane.access.matrix import MATRIX, matrix_family_for

        family = matrix_family_for(self.model)
        if workspace_id is None and family != "workspaces":
            raise ValueError("accessible_by 需显式 workspace_id（工作空间族除外）")
        return self.filter(MATRIX[family](user, workspace_id))

    def accessible_in(self, workspace, user):
        return self.accessible_by(user, workspace_id=workspace.id)

    def unsafe_all(self, *, reason: str):
        import logging as _logging

        from plane.access import matrix as _matrix

        table = self.model._meta.db_table  # noqa: SLF001
        allowed = _matrix.UNSAFE_EXCEPTIONS.get(__file__, set())
        _logging.getLogger("plane.access").warning(
            "unsafe_all model=%s reason=%s registered=%s", table, reason,
            reason in allowed or __file__ in _matrix.UNSAFE_EXCEPTIONS,
        )
        _matrix._unsafe_counters[f"{table}:{reason}"] = (
            _matrix._unsafe_counters.get(f"{table}:{reason}", 0) + 1)
        return self.all()


class SoftDeleteManager(models.Manager.from_queryset(SoftDeleteQuerySet)):
    """from_queryset 派生：QuerySet 方法（accessible_by/unsafe_all 等）经管理器转发。"""

    def get_queryset(self) -> SoftDeleteQuerySet:
        return SoftDeleteQuerySet(self.model, using=self._db).filter(deleted_at__isnull=True)


class BaseModel(models.Model):
    id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True, primary_key=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")
    created_by = models.ForeignKey(
        "db.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="%(class)s_created_by",
        verbose_name="创建人",
    )
    updated_by = models.ForeignKey(
        "db.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="%(class)s_updated_by",
        verbose_name="最后修改人",
    )
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True, verbose_name="删除时间")
    objects = SoftDeleteManager()
    all_objects = models.Manager()

    class Meta:
        abstract = True
        ordering = ("-created_at",)

    def soft_delete(self, *, actor_id=None) -> None:
        self.deleted_at = timezone.now()
        if actor_id is not None:
            self.updated_by_id = actor_id
        self.save(update_fields=["deleted_at", "updated_by", "updated_at"])
