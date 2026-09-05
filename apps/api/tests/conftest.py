"""pytest 共享夹具。

Sprint-2 起：pytest 专项直连 dev PG（settings/test.py 已把 TEST.NAME 指向本体）。
覆盖 ``django_db_setup`` 为 no-op —— 跳过 pytest-django 的建库 + migrate
（Django 5.1 自定义 User 在 PG 上建测试库有已知问题，CLAUDE.md 坑 #1），
由 ``django_db``（TestCase 事务回滚）保证零残留。
禁止 TransactionTestCase：其 flush 会清空 dev 库（真提交语义走 sprint-2-flow.py）。
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="session")
def django_db_setup():
    """不建测试库、不迁移：直接复用已迁移的 dev 库。"""
    return None
