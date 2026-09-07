"""本地开发配置（INFRA-004 §4.8 / INFRA-003 §2.2 命名收口）。"""
from __future__ import annotations

from .base import *  # noqa: F401,F403
from .base import env

DEBUG = True
CORS_ALLOWED_ORIGINS = ["http://localhost:3000", "http://localhost:3001",
                        "http://localhost:3002", "http://localhost:3003"]
# 开发态：Envelope 中间件对漏包装直接抛错（⑤ 号 read：settings_debug()）
ENVELOPE_STRICT = True

# INTG-001 真联调注入点（§4.3.2）：三项全空 = 无 App 凭据，GitHubClient 走
# dev token 直通（mock/单测口径不变）；GITHUB_WEBHOOK_BASE 设为公网穿透域名
# 时，绑仓会在真实仓库注册 repo webhook（{base}/api/v1/integrations/github/webhook/）
GITHUB_APP_ID = env("GITHUB_APP_ID", "")
GITHUB_APP_PRIVATE_KEY = env("GITHUB_APP_PRIVATE_KEY", "")
GITHUB_WEBHOOK_BASE = env("GITHUB_WEBHOOK_BASE", "")
