#!/usr/bin/env python
"""Django 管理入口（apps/api · INFRA-001 骨架，模型由 INFRA-003 交付）。"""

import os
import sys


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "plane.settings.dev")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError("无法导入 Django——请先 `uv sync --project apps/api` 安装依赖") from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
