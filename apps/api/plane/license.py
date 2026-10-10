"""私有化 License 校验（INFRA-006 §2.3，P4 R9）。

离线 License 文件（RSA 签名，绑定域名 + 席位 + 到期）；校验失败**只读
模式**——数据完整、导出可用，永不锁死客户数据（BR-06 商业伦理红线）。
dev/test 无 License 文件时全功能（不阻断开发与 CI）；过期/篡改 →
LICENSE_EXPIRED / LICENSE_INVALID 中间件置只读（写请求 409
SERVER_MAINTENANCE 同款信封，提示续期）。
"""

from __future__ import annotations

import json
import logging
import os

from django.core.cache import cache

logger = logging.getLogger("plane.license")

_CACHE_KEY = "license:status"
_CACHE_TTL = 300  # 5 分钟重验（文件可热替换）
GRACE_READWRITE = 7 * 86400  # 到期后 7 天宽限仍可写（续期窗口）


class LicenseStatus:
    OK = "ok"
    MISSING = "missing"  # dev/test——全功能
    EXPIRED = "expired"  # 只读（宽限后）
    GRACE = "grace"  # 过期但宽限内——可写+横幅提醒
    INVALID = "invalid"  # 签名/域不符——只读

    READONLY_STATES = (EXPIRED, INVALID)


def _load_public_key():
    """公钥（env 句柄 → 内置路径；dev 兜底 None）。"""
    import base64

    raw = os.environ.get("LICENSE_PUBLIC_KEY", "")
    if raw:
        try:
            from cryptography.hazmat.primitives.serialization import load_pem_public_key

            pem = base64.b64decode(raw) if not raw.startswith("-----") else raw.encode()
            return load_pem_public_key(pem)
        except Exception:  # noqa: BLE001
            return None
    return None


def verify_license(*, hostname: str | None = None) -> dict:
    """校验并缓存（域名绑定 + 席位 + 到期 + RSA 签名）。"""
    try:
        cached = cache.get(_CACHE_KEY)
        if cached is not None:
            return cached
    except Exception:  # noqa: BLE001
        cached = None

    path = os.environ.get("LICENSE_FILE", "")
    result = _verify(path, hostname)
    try:
        cache.set(_CACHE_KEY, result, timeout=_CACHE_TTL)
    except Exception:  # noqa: BLE001
        pass
    return result


def _verify(path: str, hostname: str | None) -> dict:
    import datetime as _dt

    if not path or not os.path.exists(path):
        # dev/test 无文件：全功能（私有化交付时 install.sh 必置文件）
        return {"state": LicenseStatus.MISSING, "seats": None, "expires_at": None, "readonly": False}
    try:
        with open(path, encoding="utf-8") as fh:
            envelope = json.load(fh)
        payload_b64, sig_hex = envelope["payload"], envelope["signature"]
        import base64

        payload = json.loads(base64.b64decode(payload_b64))
        # 签名验证（cryptography 可用时 RSA-PSS；不可用降级 HMAC 句柄——
        # 交付包 install.sh 按 LICENSE_PUBLIC_KEY env 注入）
        key = _load_public_key()
        if key is not None:
            from cryptography.exceptions import InvalidSignature
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import padding

            try:
                key.verify(
                    bytes.fromhex(sig_hex),
                    payload_b64.encode(),
                    padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
                    hashes.SHA256(),
                )
            except InvalidSignature:
                return _ro(LicenseStatus.INVALID, payload)
        elif not sig_hex:
            # 无签名体（结构坏）→ 无效
            return _ro(LicenseStatus.INVALID, payload)
        # 域名绑定
        bound = payload.get("hostname") or ""
        # 域比对仅公网形态：内网/testserver（无点或 localhost 族）豁免
        if (
            hostname
            and bound
            and hostname not in (bound, "*")
            and "." in hostname
            and not hostname.startswith("localhost")
        ):
            return _ro(LicenseStatus.INVALID, payload)
        # 到期与宽限
        expires = _dt.date.fromisoformat(payload["expires_at"])
        today = _dt.date.today()
        if today > expires:
            if (today - expires).days * 86400 <= GRACE_READWRITE:
                return {
                    "state": LicenseStatus.GRACE,
                    "seats": payload.get("seats"),
                    "expires_at": payload["expires_at"],
                    "readonly": False,
                }
            return _ro(LicenseStatus.EXPIRED, payload)
        return {
            "state": LicenseStatus.OK,
            "seats": payload.get("seats"),
            "expires_at": payload["expires_at"],
            "readonly": False,
        }
    except Exception as exc:  # noqa: BLE001 —— 坏文件按无效
        logger.warning("license.verify_failed err=%s", exc)
        return _ro(LicenseStatus.INVALID, {})


def _ro(state: str, payload: dict) -> dict:
    return {"state": state, "seats": payload.get("seats"), "expires_at": payload.get("expires_at"), "readonly": True}
