"""SSO 密钥列 Fernet 加解密（AUTH-009 BR-14）。

主密钥走 SSO_FERNET_KEY 环境变量（INFRA-005 生产配置统一）；未配置时
写入密钥报错（提示设置），读取返回 None。
"""
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings as dj_settings


def _fernet() -> Fernet | None:
    key = getattr(dj_settings, "SSO_FERNET_KEY", "")
    if not key:
        return None
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_secret(plain: str) -> bytes:
    f = _fernet()
    if f is None:
        from plane.base.exception import AppException

        raise AppException(
            "SERVER_STORAGE_ERROR",
            message="未配置 SSO_FERNET_KEY，无法加密存储密钥",
        )
    return f.encrypt(plain.encode())


def decrypt_secret(blob: bytes | None) -> str | None:
    if not blob:
        return None
    f = _fernet()
    if f is None:
        return None
    try:
        return f.decrypt(bytes(blob)).decode()
    except InvalidToken:
        return None
