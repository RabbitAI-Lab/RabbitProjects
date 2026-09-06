"""space 公开 API 分组（api-conventions.md §2.1 第三套 API）。

匿名只读面（FILE-004 首个落地：文件分享）：与内部 API 物理隔离——独立
序列化器（脱敏）、无会话认证（``authentication_classes = []``，不 enforce
CSRF）、不共享 Permission 类；共享的只有 Model 与领域服务层。
"""
