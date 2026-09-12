"""目录同步子包（AUTH-011，P4 R2）。

services 归并裁决核心 / tasks Celery 面（ldap_sync+beat）/ signals manual
盖章钩子。信号经路由模块导入保证注册（urls 装载链）。
"""

# manual 盖章钩子包级注册（ORM-only 使用面也生效；dispatch_uid 防重复连接）
from plane.directory import signals  # noqa: E402,F401
