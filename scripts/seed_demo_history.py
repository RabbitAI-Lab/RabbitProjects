"""演示账号补「近 7 日完成事件」——让首页趋势图有起伏。

把 zhang 名下若干 active、未完成、未指派为 completed 的 task，
随机标 completed_at = 距今 0~7 天前的随机时刻 + state_id = 该项目 completed state。
"""
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "apps", "api"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "plane.settings.dev")

import django

django.setup()

from django.db import connection, transaction

random.seed(42)

with transaction.atomic():
    with connection.cursor() as cur:
        # 选目标：zhang 被指派、未归档、当前不是 completed state
        cur.execute(
            """
            WITH zhang AS (SELECT id FROM users WHERE email='zhangsan@rabbit.dev'),
                 sc AS (SELECT id, project_id FROM states WHERE "group"='completed' AND deleted_at IS NULL)
            SELECT i.id, sc.id
              FROM issues i
              JOIN zhang z ON TRUE
              JOIN issue_assignees ia ON ia.issue_id = i.id AND ia.assignee_id = z.id
              JOIN sc ON sc.project_id = i.project_id
             WHERE i.archived_at IS NULL
               AND i.state_id != sc.id
               AND random() < 0.4
        """
        )
        targets = cur.fetchall()
        print(f"targets = {len(targets)}")
        for i_id, sc_id in targets:
            cur.execute(
                """
                UPDATE issues
                   SET state_id = %s,
                       completed_at = NOW() - (random() * interval '7 days')
                 WHERE id = %s
            """,
                [str(sc_id), str(i_id)],
            )
    print(f"updated = {len(targets)}")
