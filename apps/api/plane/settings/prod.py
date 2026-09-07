"""生产配置 —— 安全收紧 + 9 项必填启动校验（INFRA-004 §4.8 BR-13）。"""
from __future__ import annotations

import os

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403
from .base import REDIS_URL

DEBUG = False

# ── 9 项必填：import 即校验（manage.py 任何命令快速失败，BR-13）──
# 注意：DATABASE_URL / CELERY_BROKER_URL / AWS_SECRET_ACCESS_KEY 在 base.py 设有默认值，
# 仅靠 POSTGRES_PASSWORD / RABBITMQ_DEFAULT_PASS / MINIO_ROOT_PASSWORD 校验会让
# 用户覆盖连接串时绕过 BR-13（base.py 默认值会静默接管，落到本地 sqlite / 内存 broker），
# 因此三者必须同时列入 REQUIRED_ENV。
REQUIRED_ENV = [
    "SECRET_KEY",             # 会话签名
    "POSTGRES_PASSWORD",      # 数据库密码（compose 同名变量，§2.5 of INFRA-002）
    "DATABASE_URL",           # 数据库连接串（必须显式给出，禁止默认 sqlite 回退）
    "RABBITMQ_DEFAULT_PASS",  # 消息队列密码
    "CELERY_BROKER_URL",      # Celery broker 连接串（必须显式给出，禁止默认 localhost 回退）
    "MINIO_ROOT_PASSWORD",    # 对象存储密码
    "AWS_SECRET_ACCESS_KEY",  # S3 签名密钥（必须显式给出，禁止空串回退导致上传 403）
    "CORS_ALLOWED_ORIGINS",   # 精确白名单（禁止回退 * ）
    "APP_BASE_URL",           # Cookie Domain / 绝对链接推导
]
_missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
if _missing:
    raise ImproperlyConfigured(
        f"prod 配置缺少必填环境变量：{_missing}。请对照 .env.example 补齐后重启（BR-13）。")

# ── 安全头（§13.4；proxy 层另有 HSTS 等注入，此处为应用层兜底）──
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SAMESITE = "Lax"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
X_FRAME_OPTIONS = "DENY"

# ── INFRA-005 §4.3.2：L2 全局限流生效（api ×2 副本共享 Redis 计数）──
RATE_LIMIT_ENABLED = True

# ── INFRA-005：限流计数缓存走 Redis（base 缺省 LocMem 仅单进程可见）──
# 兑现 INTG-002 交接风险 #2「生产必走 Redis」；DB 1 与 AUTH-004 会话索引
# （account/sessions.py 期望 Valkey DB 1）分库避让。
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_URL.replace("/0", "/2"),
        "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
    }
}
