"""功能开关与业务常量（迭代递增；与 architecture 文档对齐）。

Sprint-2 TASK-004 §4.1：层级模型三层防线的两个常量——
  MAX_ISSUE_DEPTH   业务层级上限（根=第 1 层），仅由写入层校验保证（BR-02）；
  CTE_GUARD_DEPTH   递归 CTE 保险丝：防环上行扫描深度硬上限，仅为查询侧脏数据
                    告警线（触达即 500 + logger.error），**不是业务深度限制**，
                    两层机制不得互相替代（sprint-overview 风险 #1）。
"""

from __future__ import annotations

MAX_ISSUE_DEPTH: int = 5
CTE_GUARD_DEPTH: int = 100

#: 单父直接子任务上限（沿用 TASK-002 §2.7，TASK-004 不放开宽度）
MAX_SUB_ISSUES_PER_PARENT: int = 100

#: subtree/ 展示截断阈值（TASK-004 BR-11：truncated=true 时不装配 stats）
SUBTREE_NODE_LIMIT: int = 500
