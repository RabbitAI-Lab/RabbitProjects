"""最小冒烟集 —— 让 pytest 有可收集用例（CI 第 4 步），并锁定框架层不变量。

真正的接口/行为验证在 tests/jmeter/sprint-{0,1}-flow.py（CI gate），此处只守
「包能导入 + 关键常量不漂移」。
"""
import django

django.setup()

from plane.base.error_codes import ErrorCodes  # noqa: E402
from plane.constants.permissions import (  # noqa: E402
    PERMISSION_LABELS,
    all_permission_keys,
)


def test_error_registry_has_75_codes():
    assert len(ErrorCodes.all()) == 75


def test_permission_labels_cover_all_keys():
    assert all_permission_keys() == set(PERMISSION_LABELS)


def test_envelope_shape_constants():
    from plane.base.response import success_response

    resp = success_response({"ok": 1})
    assert resp.data["status"] == "success"
    assert resp.data["data"] == {"ok": 1}
    assert "meta" not in resp.data  # meta 键可缺省（详情端点）
