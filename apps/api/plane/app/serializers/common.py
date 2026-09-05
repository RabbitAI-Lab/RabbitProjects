"""Sprint-1 INFRA-004 收口后，业务代码统一从 ``plane.base.response`` 装配成功信封；
本模块仅保留 ``success_response`` / ``created_response`` 的轻量 re-export 以便
逐步迁移（历史代码 ``from plane.app.serializers.common import envelope`` 已
全部改用 ``plane.base.response``；envelope 函数本身已彻底删除）。"""
from plane.base.response import created_response, success_response

__all__ = ["success_response", "created_response"]
