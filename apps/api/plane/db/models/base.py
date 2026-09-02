import uuid

from django.db import models
from django.utils import timezone


class SoftDeleteQuerySet(models.QuerySet):
    def delete(self, soft: bool = True) -> tuple[int, dict[str, int]]:
        if soft:
            return self.update(deleted_at=timezone.now()), {}
        return super().delete()


class SoftDeleteManager(models.Manager):
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
