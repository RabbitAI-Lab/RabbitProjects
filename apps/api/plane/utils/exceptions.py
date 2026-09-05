"""业务级校验异常与字段错误助手（TASK-002 §4.4.1 落地）。

设计：
- AppValidationError 直接携带 ``error_code="VALIDATION_ERROR"`` 与 ``extra_details``，
  全局 handler（plane/base/handlers.py 第 1 步）按 AppException 同构处理：
  一次请求的多字段错误不再走 DRF ValidationError 的 flatten 分支，避免字典二次展开。
- field_error(field, code, message, **extra) → 标准条目：
    {"field": "type_id", "code": "REQUIRED", "message": "任务类型为必填项"}
"""
from __future__ import annotations

from rest_framework import status
from rest_framework.exceptions import APIException


def field_error(field: str, code: str, message: str, **extra) -> dict:
    """构造一条字段级错误条目。``extra`` 用于扩展（如 suggestion / limit）。"""
    out = {"field": field, "code": code, "message": message}
    out.update(extra)
    return out


class AppValidationError(APIException):
    """多字段一次性错误抛出 —— handler 第 1 步按 error_code+extra_details 直接装配。

    用法：
        raise AppValidationError([
            field_error("type_id", "REQUIRED", "任务类型为必填项"),
            field_error("label_ids", "TOO_LARGE", "单个任务最多 10 个标签"),
        ])

    实现：把自己伪装成 AppException —— handler 第 1 步判定 ``exc.error_code`` 立即命中，
    绕过第 2 步的 DRF ValidationError flatten 路径，list 内每个 dict 原样进入 details[]。
    """

    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "validation_error"

    def __init__(self, errors: list[dict] | dict):
        self.error_code = "VALIDATION_ERROR"
        self.http_status = self.status_code
        self.detail_message = "请求参数校验失败"
        self.extra_details = list(errors) if isinstance(errors, list) else [errors]
        self.doc_url = None
        super().__init__(self.detail_message)
