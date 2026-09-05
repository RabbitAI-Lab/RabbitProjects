import uuid

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models


class UserManager(BaseUserManager):
    def create_user(self, email: str, password: str | None = None, **extra):
        if not email:
            raise ValueError("邮箱不能为空")
        email = self.normalize_email(email).strip().lower()
        user = self.model(email=email, **extra)
        user.set_password(password)  # type: ignore[attr-defined]  # stub 对自引用泛型 _T 不收敛
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
    username = None  # type: ignore[assignment]  # 邮箱登录（AUTH-001）
    display_name = models.CharField(max_length=150, verbose_name="显示名")
    avatar_url = models.URLField(max_length=800, blank=True, verbose_name="头像地址")
    is_active = models.BooleanField(default=True, db_index=True, verbose_name="是否启用")
    last_login_at = models.DateTimeField(null=True, blank=True, verbose_name="最近登录时间")
    last_workspace_id = models.UUIDField(null=True, blank=True, verbose_name="最近访问的工作空间")
    # P1 新增（AUTH-004 §4.1.2）：≤500 字符；纯文本（无富文本），列表/提及浮层可截断展示
    intro = models.CharField(
        max_length=500,
        blank=True,
        default="",
        verbose_name="个人简介",
        help_text="≤500 字符；纯文本（无富文本），列表/提及浮层可截断展示",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []
    objects = UserManager()  # type: ignore[misc,assignment]  # 自定义 Manager 覆盖 AbstractUser 默认

    class Meta:
        db_table = "users"
        verbose_name = "用户"
        swappable = "AUTH_USER_MODEL"
        indexes = [models.Index(fields=["email", "is_active"])]

    def __str__(self) -> str:
        return f"{self.display_name} <{self.email}>"
