"""公共配置基线 —— 全量直出（INFRA-004 §4.8 / monorepo-structure.md §9）。

敏感与环境差异项由 dev / prod 覆盖；本文件禁止出现 `if DEBUG` 类的环境判断。
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def env(key: str, default=None):
    return os.environ.get(key, default)


def env_bool(key: str, default: bool = False) -> bool:
    return str(env(key, default)).lower() in ("1", "true", "yes")


def _parse_db_url(url: str) -> dict:
    """最小化的 DATABASE_URL 解析（避免引入 django-environ 与基线冲突）。

    格式：postgresql://user:pass@host:port/dbname
    """
    parsed = urlparse(url)
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": parsed.path.lstrip("/") or "rabbit_projects",
        "USER": parsed.username or "",
        "PASSWORD": parsed.password or "",
        "HOST": parsed.hostname or "localhost",
        "PORT": str(parsed.port or 5432),
        "CONN_MAX_AGE": 60,
    }


SECRET_KEY = env("SECRET_KEY", "dev-insecure-key")     # prod 强制覆盖（§ prod.py BR-13）
DEBUG = env_bool("DEBUG", False)
ALLOWED_HOSTS = [h.strip() for h in env("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "django_filters",
    "drf_spectacular",
    "plane.db",
]

# ── 中间件：六件套顺序即 §4.6 编号（顺序敏感，禁止重排）────
MIDDLEWARE = [
    "plane.base.middleware.RequestIDMiddleware",             # ①
    "plane.base.middleware.StructuredLoggingMiddleware",     # ②
    "plane.base.middleware.RateLimitHeaderMiddleware",       # ③
    "plane.base.middleware.AuditContextMiddleware",          # ④
    "plane.base.middleware.ResponseEnvelopeMiddleware",      # ⑤
    "plane.base.middleware.MaintenanceModeMiddleware",       # ⑥
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "plane.urls"
WSGI_APPLICATION = "plane.wsgi.application"
ASGI_APPLICATION = "plane.asgi.application"
AUTH_USER_MODEL = "db.User"

DATABASES = {
    "default": _parse_db_url(env("DATABASE_URL", "postgresql://rp:rp@localhost:5432/rabbit_projects")),
}

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]

# ── DRF：异常处理器 / 事务 / 默认认证 ────────────────────
# 注意：不设置 DEFAULT_PAGINATION_CLASS ——sprint-0 的端点全部手工分页；
# 一旦挂全局 LimitOffsetPagination 既会静默改变所有列表响应的结构（多出
# count/next/previous 字段），又会让 INFRA-004 计划中的
# plane.base.paginator.CursorPagination 上线时无从替换（BR-04：业务视图
# 不得假设全局分页存在）。需要分页的视图显式声明 paginator。
REST_FRAMEWORK = {
    "EXCEPTION_HANDLER": "plane.base.handlers.envelope_exception_handler",
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "ATOMIC_REQUESTS": True,                       # §10.5：单资源写操作默认事务包裹
    "DEFAULT_THROTTLE_CLASSES": [],                 # INFRA-005 填充
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

SPECTACULAR_SETTINGS = {"TITLE": "RabbitProjects API", "VERSION": "0.1.0"}

# ── CORS：精确白名单，禁止 "*"（§13.4）────────────────────
CORS_ALLOWED_ORIGINS = [
    o.strip()
    for o in env("CORS_ALLOWED_ORIGINS", "http://localhost:3000").split(",")
    if o.strip()
]
CORS_ALLOW_CREDENTIALS = True                     # Session 认证需要
CORS_ALLOW_METHODS = ["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"]
CORS_ALLOW_HEADERS = ["Content-Type", "X-CSRFToken", "X-API-Key",
                      "Authorization", "If-Match", "Idempotency-Key"]
CORS_EXPOSE_HEADERS = ["X-Request-Id", "X-RateLimit-Limit", "X-RateLimit-Remaining",
                       "X-RateLimit-Reset", "ETag", "Location", "Retry-After"]

# ── 数据层 / 队列 / 对象存储（变量名与 INFRA-002 compose 对齐）──
REDIS_URL = env("REDIS_URL", "redis://localhost:6379/0")
CELERY_BROKER_URL = env("CELERY_BROKER_URL", "amqp://guest:guest@localhost:5672//")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", REDIS_URL.replace("/0", "/1"))
AWS_S3_ENDPOINT_URL = env("AWS_S3_ENDPOINT_URL", "http://localhost:9000")
AWS_ACCESS_KEY_ID = env("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = env("AWS_SECRET_ACCESS_KEY", "")
AWS_S3_BUCKET_NAME = env("AWS_S3_BUCKET_NAME", "rp-uploads")

# ── SMTP：P1 可空 = 邮件降级为日志投递（BR-14，IT-05）──────
SMTP_HOST = env("SMTP_HOST", "")
EMAIL_FROM = env("EMAIL_FROM", "noreply@example.com")

# ── 维护模式开关（⑥ 号中间件消费）────────────────────────
MAINTENANCE_MODE = env_bool("MAINTENANCE_MODE", False)

# ── 日志：structlog 在进程入口（wsgi/worker/beat）统一初始化 ──
from plane.logging import configure_logging  # noqa: E402

configure_logging(debug=DEBUG)
