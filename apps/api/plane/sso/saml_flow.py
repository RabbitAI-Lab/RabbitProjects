"""SAML 2.0 SP 流程（AUTH-009 §2.2/§4.3，Sprint-8 R3）。

python3-saml（OneLogin SDK）封装，strict=True 全量校验：双签名（消息 +
断言）、NotOnOrAfter（±300s 时钟偏移）、InResponseTo（SP-initiated）、
Audience = SP EntityID、destination 严格匹配、拒绝弃用算法。
IdP-initiated（无 InResponseTo）允许，审计标记 initiated_by=idp。
"""
from __future__ import annotations

import logging

from django.conf import settings as dj_settings

logger = logging.getLogger("plane.api.sso")

CLOCK_SKEW = 300  # §2.2：±5min


class SAMLError(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _base_url() -> str:
    return getattr(dj_settings, "SSO_BASE_URL", "") or "http://localhost:8000"


def saml_settings_dict(idp) -> dict:
    """OneLogin Settings JSON（从 IdP 模型生成；SP 私钥 Fernet 解密运行时）。"""
    from plane.sso.crypto import decrypt_secret

    sp_private = decrypt_secret(idp.sp_private_key_enc) or ""
    return {
        "strict": True,
        "debug": False,
        "sp": {
            "entityId": idp.sp_entity_id or f"{_base_url()}/api/v1/auth/sso/saml/metadata/",
            "assertionConsumerService": {
                "url": f"{_base_url()}/api/v1/auth/sso/saml/acs/",
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
            },
            "singleLogoutService": {
                "url": f"{_base_url()}/api/v1/auth/sso/saml/slo/",
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
            },
            "NameIDFormat": "urn:oasis:names:tc:SAML:2.0:nameid-format:persistent",
            "x509cert": idp.sp_x509_cert or "",
            "privateKey": sp_private,
        },
        "idp": {
            "entityId": idp.idp_entity_id,
            "singleSignOnService": {
                "url": idp.idp_sso_url,
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
            },
            "singleLogoutService": {
                "url": idp.idp_slo_url or "",
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
            },
            "x509cert": (idp.idp_x509_cert or "").strip(),
        },
        "security": {
            "nameIdEncrypted": False,
            "authnRequestsSigned": bool(sp_private),
            "logoutRequestSigned": bool(sp_private),
            "logoutResponseSigned": bool(sp_private),
            "signMetadata": bool(sp_private),
            "wantMessagesSigned": True,
            "wantAssertionsSigned": True,
            "wantNameId": True,
            "wantNameIdEncrypted": False,
            "wantAssertionsEncrypted": False,
            "allowSingleLabelDomains": False,
            "signatureAlgorithm": "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256",
            "digestAlgorithm": "http://www.w3.org/2001/04/xmlenc#sha256",
            "rejectDeprecatedAlgorithm": True,
        },
    }


def _build_auth(request_data: dict, idp):
    from onelogin.saml2.auth import OneLogin_Saml2_Auth
    from onelogin.saml2.settings import OneLogin_Saml2_Settings

    try:
        return OneLogin_Saml2_Auth(
            request_data, OneLogin_Saml2_Settings(saml_settings_dict(idp)))
    except Exception as exc:  # noqa: BLE001
        raise SAMLError(f"settings_invalid: {exc}") from exc


def begin_sso(request_data: dict, idp, next_url: str) -> str:
    """SP-initiated：生成 AuthnRequest，返回重定向 URL（RelayState=next）。"""
    auth = _build_auth(request_data, idp)
    return auth.login(return_to=next_url)


def consume_assertion(request_data: dict, idp) -> dict:
    """ACS：校验并解析断言 → claims（email/name/NameID）。

    返回 {sub, email, name, initiated_by, attributes}；任一校验失败抛
    SAMLError（视图统一 401 AUTH_INVALID_CREDENTIALS，细节进日志）。
    """
    auth = _build_auth(request_data, idp)
    try:
        if not auth.process_response():
            raise SAMLError(f"process_failed: {auth.get_last_error_reason()}")
        errors = auth.get_errors()
        if errors:
            raise SAMLError(f"validate_failed: {errors}")
        if not auth.is_authenticated():
            raise SAMLError("not_authenticated")
    except SAMLError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise SAMLError(f"assertion_invalid: {exc}") from exc
    in_response_to = auth.get_last_response_in_response_to()
    attrs = {k: (v[0] if isinstance(v, list) and v else v)
             for k, v in (auth.get_attributes() or {}).items()}
    name_id = auth.get_nameid()
    email = (attrs.get("email") or attrs.get("emailAddress")
             or (name_id if "@" in str(name_id or "") else None))
    if not name_id or not email:
        raise SAMLError("missing_nameid_or_email")
    return {
        "sub": str(name_id),
        "email": str(email),
        "name": attrs.get("name") or attrs.get("displayName"),
        "initiated_by": "sp" if in_response_to else "idp",
        "attributes": attrs,
    }


def build_metadata(idp) -> str:
    """SP 元数据 XML（公开端点）。"""
    from onelogin.saml2.settings import OneLogin_Saml2_Settings

    try:
        return OneLogin_Saml2_Settings(saml_settings_dict(idp)).get_sp_metadata()
    except Exception as exc:  # noqa: BLE001
        raise SAMLError(f"metadata_invalid: {exc}") from exc
