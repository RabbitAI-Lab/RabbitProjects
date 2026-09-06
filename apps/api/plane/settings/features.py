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

#: ── Sprint-2 TASK-008 自定义字段（BR-10 / §4.3.1）──
#: 单 Workspace 启用字段上限（超出 → 409 RESOURCE_LIMIT_EXCEEDED / LIMIT）
MAX_CUSTOM_FIELDS_PER_WORKSPACE: int = 50
#: 单 Workspace is_indexed 表达式索引上限（写入放大锁死在 10-15%，架构 §6.5）
MAX_INDEXED_CUSTOM_FIELDS_PER_WORKSPACE: int = 10
#: Schema Redis 缓存 TTL（秒）—— 读极多写极少，1h TTL + 写时失效双保险
FIELD_SCHEMA_CACHE_TTL_SECONDS: int = 3600

#: ── Sprint-3 BOARD-003 视图（BR-02：含内置五视图，超出 → 409 LIMIT）──
MAX_VIEWS_PER_PROJECT: int = 20

#: ── Sprint-4 FILE-002 项目文件库（§2.6 边界条件）──
#: 目录深度上限（根=1，BR-01；复用 TASK-004 层级治理经验，环防护走同款 CTE）
MAX_FOLDER_DEPTH: int = 5
