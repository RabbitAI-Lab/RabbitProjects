"""本地开发配置（INFRA-004 §4.8 / INFRA-003 §2.2 命名收口）。"""
from __future__ import annotations

from .base import *  # noqa: F401,F403

DEBUG = True
CORS_ALLOWED_ORIGINS = ["http://localhost:3000", "http://localhost:3001",
                        "http://localhost:3002", "http://localhost:3003"]
# 开发态：Envelope 中间件对漏包装直接抛错（⑤ 号 read：settings_debug()）
ENVELOPE_STRICT = True
