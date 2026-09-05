"""测试配置（pytest-django 使用，见 pyproject [tool.pytest.ini_options]）。

Sprint-2 起 pytest 专项需要真实 PG（递归 CTE / 行锁 / 并发用例）。Django 5.1 +
自定义 User 在 PG 上 migrate 建测试库有已知问题（CLAUDE.md 坑 #1），故测试库
**直接复用 dev 库**（已按 tests/e2e/PG_README.md 完成迁移，含 pg_trgm/btree_gin
扩展），由 pytest-django 的 TestCase 事务回滚保证零残留——**禁止在本配置下写
TransactionTestCase（其 flush 会清空 dev 库）**；需要真提交语义的用例走
tests/jmeter/sprint-2-flow.py（HTTP 侧，自建自清）。
"""
from __future__ import annotations

from .dev import *  # noqa: F401,F403

DATABASES["default"]["TEST"] = {"NAME": DATABASES["default"]["NAME"]}
