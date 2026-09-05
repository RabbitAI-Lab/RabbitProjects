"""settings 入口 —— 按 DJANGO_SETTINGS_MODULE 分发（INFRA-004 §4.8）。

仅暴露 base 公共配置 + 防御性模块名校验；dev/prod 通过环境变量 DJANGO_SETTINGS_MODULE
切换（manage.py / wsgi.py / celery.py / asgi.py 全部用默认模块名 plane.settings.dev）。
"""
from __future__ import annotations

import os

from .base import *  # noqa: F401,F403

_mode = os.environ.get("DJANGO_SETTINGS_MODULE", "plane.settings.dev")
assert _mode.startswith("plane.settings."), f"非法 settings 模块：{_mode}"
