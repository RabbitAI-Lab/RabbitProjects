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


SECRET_KEY = env("SECRET_KEY", "dev-insecure-key")  # prod 强制覆盖（§ prod.py BR-13）
#: 集成层独立对称密钥（INTG-002 交接项 5：Fernet 加密 webhook secret）。
#: 缺省空 → 由 SECRET_KEY SHA-256 派生（dev/CI 零配置）；**生产必须注入独立
#: 值**（compose prod env + 发布 checklist 项——禁止 dev 派生口径进生产）。
INTEGRATION_SECRET_KEY = env("INTEGRATION_SECRET_KEY", "")
DEBUG = env_bool("DEBUG", False)
ALLOWED_HOSTS = [h.strip() for h in env("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]

# ── AUTH-009（Sprint-8 R3）：SSO 逃生通道与密钥加密 ──
#: 逃生名单（BR-06：仅环境变量，不入库不可 API 改）——强制 SSO 下仍可密码登录
SSO_BREAK_GLASS_EMAILS = [e.strip().lower() for e in env("SSO_BREAK_GLASS_EMAILS", "").split(",") if e.strip()]
#: client_secret / SP 私钥 Fernet 主密钥（BR-14；缺省时 SSO 配置写入将报错提示设置）
SSO_FERNET_KEY = env("SSO_FERNET_KEY", "")

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
    "plane.audit.middleware.AuditContextMiddleware",  # AUTH-010：审计 ip/ua threadlocal
    "plane.base.middleware.RequestIDMiddleware",  # ①
    "plane.base.middleware.StructuredLoggingMiddleware",  # ②
    "plane.base.middleware.RateLimitHeaderMiddleware",  # ③
    "plane.base.middleware.AuditContextMiddleware",  # ④
    "plane.base.middleware.ResponseEnvelopeMiddleware",  # ⑤
    "plane.base.middleware.MaintenanceModeMiddleware",  # ⑥
    "plane.base.middleware.WorkspaceArchiveMiddleware",  # ⑦ Sprint-5 TEAM-003 归档写保护
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
    "ATOMIC_REQUESTS": True,  # §10.5：单资源写操作默认事务包裹
    # INFRA-005 §4.3.2 L2 全局四类（BR-04 判定顺序）。注意：与规格「settings/
    # production.py 增量」不同，这里放 base 全量 + RATE_LIMIT_ENABLED 运行时
    # 门控（类内 gated 位）——settings 级 prod-only 会与 ViewSet 级
    # [*BASE_THROTTLES, X] 展开模式冲突（dev 被带起 L2 打爆 flow/pytest，或
    # prod 静默摘除视图级 L2），偏差见 plane/base/throttling.py 模块 docstring。
    "DEFAULT_THROTTLE_CLASSES": [
        "plane.base.throttling.ApiKeyRateThrottle",    # ① Key
        "plane.base.throttling.OAuthAppRateThrottle",  # ② OAuth（复合键）
        "plane.base.throttling.UserRateThrottle",      # ③ Session 用户
        "plane.base.throttling.AnonRateThrottle",      # ④ 匿名 IP
    ],
    "DEFAULT_THROTTLE_RATES": {
        "user": "60/min", "apikey": "60/min", "oauth": "60/min", "anon": "30/min",
        "auth": "10/min", "report": "10/min", "search": "30/min",
        "presign": "30/min", "bulk": "10/min", "share_unlock": "5/10m",
    },
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

#: L2 全局限流 + AuthBurst 门控开关（INFRA-005）：dev/test 缺省关（jMeter
#: flow 六套与 pytest 会被全局 60/min 打爆——收编类 Report/Bulk/ShareUnlock
#: 不受此开关控制，保持 sprint-4/5 既有全环境生效面）；prod 置 True；验收
#: 演示经 env ``RATE_LIMIT_ENABLED=1`` 临时开启（§7.2.1 限流矩阵压测入口）。
RATE_LIMIT_ENABLED = env_bool("RATE_LIMIT_ENABLED", False)

#: 备份产物三校验①的大小阈值（INFRA-005 BR-07）：低于即判「异常偏小」失败
#:（空库/错库/半途截断）。dev 空库调试可 env 调小。
BACKUP_MIN_SIZE_BYTES = int(env("BACKUP_MIN_SIZE_BYTES", str(1024 * 1024)))

SPECTACULAR_SETTINGS = {"TITLE": "RabbitProjects API", "VERSION": "0.1.0"}

# ── CORS：精确白名单，禁止 "*"（§13.4）────────────────────
CORS_ALLOWED_ORIGINS = [o.strip() for o in env("CORS_ALLOWED_ORIGINS", "http://localhost:3000").split(",") if o.strip()]
CORS_ALLOW_CREDENTIALS = True  # Session 认证需要
CORS_ALLOW_METHODS = ["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"]
CORS_ALLOW_HEADERS = ["Content-Type", "X-CSRFToken", "X-API-Key", "Authorization", "If-Match", "Idempotency-Key"]
CORS_EXPOSE_HEADERS = [
    "X-Request-Id",
    "X-RateLimit-Limit",
    "X-RateLimit-Remaining",
    "X-RateLimit-Reset",
    "ETag",
    "Location",
    "Retry-After",
]

# ── 数据层 / 队列 / 对象存储（变量名与 INFRA-002 compose 对齐）──
REDIS_URL = env("REDIS_URL", "redis://localhost:6379/0")
CELERY_BROKER_URL = env("CELERY_BROKER_URL", "amqp://rp:rp@localhost:5672//")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", REDIS_URL.replace("/0", "/1"))

# ── Sprint-3 COLLAB-004 实时票据（api-conventions.md §9.5/§9.7）──────────
# LIVE_JWT_PRIVATE_KEY：RS256 私钥 PEM（仅 api 持有，签发票据）。支持两种形态：
# 真实多行（.env 引号包裹）或单行 ``\n`` 转义（消费方 normalize_pem 归一）。
LIVE_JWT_PRIVATE_KEY = env("LIVE_JWT_PRIVATE_KEY", "")
# LIVE_JWT_PUBLIC_KEY：RS256 公钥 PEM（live 容器注入，验签 only——被攻破也无法伪造）。
LIVE_JWT_PUBLIC_KEY = env("LIVE_JWT_PUBLIC_KEY", "")
# 业务事件票据有效期（秒，COLLAB-004 BR-02；90s 静默续签留 30s 轮换余量）。
LIVE_TICKET_TTL = int(env("LIVE_TICKET_TTL", "120") or 120)
# 心跳间隔（秒，BR-04；live 侧同名变量消费，60s 无 pong 断开）。
LIVE_HEARTBEAT = int(env("LIVE_HEARTBEAT", "25") or 25)
# INTERNAL_KEY：live→api 服务间共享密钥（X-Internal-Key，§9.7；verify-rooms 复核用）。
INTERNAL_KEY = env("INTERNAL_KEY", "")
AWS_S3_ENDPOINT_URL = env("AWS_S3_ENDPOINT_URL", "http://localhost:9000")
AWS_ACCESS_KEY_ID = env("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = env("AWS_SECRET_ACCESS_KEY", "")
AWS_S3_BUCKET_NAME = env("AWS_S3_BUCKET_NAME", "rp-uploads")

# ── FILE-002 工作空间存储配额（BR-11「存 WS 设置，默认 10GB，可配置」──
# 仓库无 Workspace 设置模型，以环境级配置承载（规格 §4.1.3 DDL 清单亦无 WS 列；
# 偏差登记见 FILE-002 任务报告 / ADR-0022）。
WS_STORAGE_QUOTA_BYTES = int(env("WS_STORAGE_QUOTA_BYTES", str(10 * 1024 ** 3)))

# ── 功能常量（Sprint-2 TASK-004 §4.1：层级三层防线 + 子树上限）──
from plane.settings.features import (  # noqa: E402,F401
    CTE_GUARD_DEPTH,
    MAX_FOLDER_DEPTH,
    MAX_ISSUE_DEPTH,
    MAX_SUB_ISSUES_PER_PARENT,
    SUBTREE_NODE_LIMIT,
)

# ── SMTP：P1 可空 = 邮件降级为日志投递（BR-14，IT-05）──────
SMTP_HOST = env("SMTP_HOST", "")
EMAIL_FROM = env("EMAIL_FROM", "noreply@example.com")

# ── 维护模式开关（⑥ 号中间件消费）────────────────────────
MAINTENANCE_MODE = env_bool("MAINTENANCE_MODE", False)

# ── 日志：structlog 在进程入口（wsgi/worker/beat）统一初始化 ──
from plane.logging import configure_logging  # noqa: E402

configure_logging(debug=DEBUG)
