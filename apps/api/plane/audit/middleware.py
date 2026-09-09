"""审计请求上下文 threadlocal（AUTH-010 §2.3 ip/user_agent 采集）。

服务层 `_audit` helper 无法拿到视图层 request——中间件把当前请求存
threadlocal，recorder 组装 payload（web 进程、on_commit 同线程）时读取
ip/ua 后注入。worker 进程无此 threadlocal（payload 已带值，不依赖）。
"""
from threading import local

_local = local()


def current_request():
    return getattr(_local, "request", None)


class AuditContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        _local.request = request
        try:
            return self.get_response(request)
        finally:
            _local.request = None
