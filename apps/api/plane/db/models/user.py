import uuid

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models


class UserManager(BaseUserManager):
    def create_user(self, email: str, password: str | None = None, **extra):
        if not email:
            raise ValueError("邮箱不能为空")
        email = self.normalize_email(email).strip().lower()
        user = self.model(email=email, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email: str, password: str, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        return self.create_user(email, password, **extra)


class User(AbstractUser):
    """系统用户。手工对齐 BaseModel 三项约定，不继承（避免自引用外键）。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(max_length=254, unique=True, db_index=True, verbose_name="邮箱")
    username = None
    display_name = models.CharField(max_length=150, verbose_name="显示名")
    avatar_url = models.URLField(max_length=800, blank=True, verbose_name="头像地址")
    is_active = models.BooleanField(default=True, db_index=True, verbose_name="是否启用")
    last_login_at = models.DateTimeField(null=True, blank=True, verbose_name="最近登录时间")
    last_workspace_id = models.UUIDField(null=True, blank=True, verbose_name="最近访问的工作空间")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []
    objects = UserManager()

    class Meta:
        db_table = "users"
        verbose_name = "用户"
        swappable = "AUTH_USER_MODEL"
        indexes = [models.Index(fields=["email", "is_active"])]

    def __str__(self) -> str:
        return f"{self.display_name} <{self.email}>"
