"""一次性回填脚本：给所有现存项目补上 cancelled state（sprint-1 验收发现 50/50 项目缺第 4 列）。

用法：
  DATABASE_URL=... python3 scripts/backfill_cancelled_state.py
"""
import os
import sys

import django

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "apps", "api"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "plane.settings.dev")
django.setup()

from django.db import transaction
from plane.db.models import Project, State

DEFAULT = {
    "name": "已取消",
    "color": "#9CA3AF",
    "group": State.Group.CANCELLED,
    "sort_order": 262140,
}


def main() -> None:
    total = 0
    inserted = 0
    skipped = 0
    for p in Project.objects.filter(deleted_at__isnull=True).only("id", "name"):
        total += 1
        if State.objects.filter(project=p, group=State.Group.CANCELLED, deleted_at__isnull=True).exists():
            skipped += 1
            continue
        with transaction.atomic():
            State.objects.create(
                project=p,
                name=DEFAULT["name"],
                color=DEFAULT["color"],
                group=DEFAULT["group"],
                sort_order=DEFAULT["sort_order"],
                created_by=p.created_by,
            )
        inserted += 1
    print(f"扫描项目 {total} | 新建 cancelled {inserted} | 已有 {skipped}")


if __name__ == "__main__":
    main()
