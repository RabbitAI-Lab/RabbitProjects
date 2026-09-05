"""测试配置（pytest-django 使用，见 pyproject [tool.pytest.ini_options]）。

INFRA-004 §4.8 显式声明 test.py 不在 P1 范围：测试态通过
DJANGO_SETTINGS_MODULE 切到 dev + 环境变量覆盖实现。本文件保留为 dev 的
别名以兼容既有 pytest.ini_options 入口。
"""
from __future__ import annotations

from .dev import *  # noqa: F401,F403
