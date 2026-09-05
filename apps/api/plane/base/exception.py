"""业务异常 —— 业务代码唯一允许抛出的异常类型（BR-04）。"""
from __future__ import annotations

from rest_framework import exceptions

from plane.base.error_codes import DEFAULT_MESSAGES, ErrorCodes

MAX_MESSAGE_LENGTH = 200


class AppException(exceptions.APIException):
    """携带注册表错误码的业务异常。

    用法：
        raise AppException("RESOURCE_ALREADY_EXISTS",
                           message="项目缩写 RBT 已被使用",
                           details=[{"field": "identifier", "code": "UNIQUE",
                                     "message": "该缩写已存在"}])

    约束：
        - code 必须已注册（未注册码在此 KeyError，测试期即暴露，UT-02）
        - message ≤ 200 字符（BR-07，超长断言失败）
        - status_code 由注册表决定，调用方不可覆盖
    """

    status_code = 400  # 占位；实际由注册表覆盖

    def __init__(
        self,
        code: str,
        message: str | None = None,
        details: list[dict] | None = None,
        doc_url: str | None = None,
    ):
        registry = ErrorCodes.all()
        if code not in registry:
            raise KeyError(
                f"未注册的错误码 {code!r}：请先在 api-conventions.md §8 与 "
                f"error_codes.py 登记（同一 PR 完成前后端与 OpenAPI 同步，BR-16）"
            )
        self.error_code = code
        self.http_status = registry[code]
        self.detail_message = message or DEFAULT_MESSAGES.get(code, "请求失败")
        assert len(self.detail_message) <= MAX_MESSAGE_LENGTH, "message 超过 200 字符（BR-07）"
        self.extra_details = details or []
        self.doc_url = doc_url
        super().__init__(self.detail_message)


class BusinessError(AppException):
    """别名 —— 对齐 api-conventions.md §10.4 第 1 步的规范用名。

    规范文档称 BusinessError，代码主名 AppException（沿用 Plane 基类名，
    便于对照阅读）。二者是同一类，新代码建议统一用 AppException。
    """
