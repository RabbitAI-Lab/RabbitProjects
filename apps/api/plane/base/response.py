"""成功信封唯一装配点 —— 业务 ViewSet 禁止手写 JsonResponse（BR-01/BR-04）。"""
from __future__ import annotations

from rest_framework.response import Response
from rest_framework.status import HTTP_200_OK, HTTP_201_CREATED

from plane.base.request_context import current_request_id


def success_response(data=None, *, meta: dict | None = None,
                     status_code: int = HTTP_200_OK, headers: dict | None = None) -> Response:
    body: dict = {"status": "success", "data": data}
    if meta is not None:
        body["meta"] = meta
    return Response(body, status=status_code, headers=headers)


def created_response(data, *, location: str, meta: dict | None = None) -> Response:
    """201 专用：必须携带 Location 头（§4.3 状态码表）。"""
    return success_response(data, meta=meta, status_code=HTTP_201_CREATED,
                            headers={"Location": location})


def paginated_response(results: list, *, paginator, request) -> Response:
    """游标分页 meta 装配 —— 九个必填字段一次到位（§6.3 表）。

    paginator 为 plane/base/paginator.py 的 CursorPagination 实例
    （INFRA-003 交付，格式 "{value}:{offset}:{is_prev}" Base64）。
    """
    return success_response(
        results,
        meta={
            "next_cursor": paginator.next_cursor,
            "prev_cursor": paginator.prev_cursor,
            "next_page_results": paginator.has_next,
            "prev_page_results": paginator.has_prev,
            "count": len(results),
            "total_count": paginator.total_count,
            "total_pages": paginator.total_pages,
            "page": paginator.page_number,
            "per_page": paginator.per_page,
        },
        headers={"X-Request-Id": current_request_id() or ""},
    )
