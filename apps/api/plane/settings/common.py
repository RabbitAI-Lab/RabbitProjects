"""公共配置基线 —— 敏感与环境的差异项由 local/production 覆盖（monorepo-structure.md §9）。"""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY", default="dev-only-not-for-production")
DEBUG = env.bool("DEBUG", default=False)
ALLOWED_HOSTS: list[str] = ["*"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # 三方
    "rest_framework",
    "django_filters",
    "drf_spectacular",
    # 本地 app（模型由 INFRA-003 交付后注册 plane.db）
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "plane.urls"
WSGI_APPLICATION = "plane.wsgi.application"
ASGI_APPLICATION = "plane.asgi.application"

# AUTH_USER_MODEL 在 INFRA-003 引入自定义 User 前保持 Django 默认；
# 该决定必须在首个 migration 前落定（sprint-overview 风险 #4）

DATABASES = {
    "default": env.db_url(
        "DATABASE_URL", default="postgresql://rp:rp@localhost:5432/rabbit_projects"
    ),
}

# Argon2 密码哈希（AUTH-001 §4 要求，优先于 PBKDF2）
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

REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "RabbitProjects API",
    "VERSION": "0.1.0",
    # 统一响应信封 {status,data,meta} 由 api-conventions.md §4 定义，INFRA-003 起接入
}
