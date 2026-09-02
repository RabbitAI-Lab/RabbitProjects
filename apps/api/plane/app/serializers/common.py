"""统一响应信封（api-conventions.md §4）。"""

from rest_framework.response import Response


def envelope(status: bool = True, data=None, meta=None, http_status: int = 200):
    return Response({"status": status, "data": data, "meta": meta or {}}, status=http_status)
