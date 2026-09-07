"""公开分享端点（FILE-004 §4.2 公开 API 表 #5~#7，匿名）。

  GET   /api/v1/public/shares/{slug}/           链接元信息（密码门语义脱敏）
  POST  /api/v1/public/shares/{slug}/unlock/    密码校验 → 2h HMAC cookie
  GET   /api/v1/public/shares/{slug}/content/   预览调度（200/202）/ 下载（302）

安全底线（§1.2 / BR-10）：
- 读时四查四种失败统一 410 ``RESOURCE_GONE``「链接不存在或已失效」——无效
  slug 与失效三态同码同文案，防「从未存在 / 曾有效」被状态码区分（§2.4）；
- 未解锁 meta 仅 ``{requires_password}``，错误体不含文件名/项目名；
- 3xx 换发无响应体不套信封（api-conventions §4.1 仅约束 2xx）。

``unlock/`` 是公开分组的唯一匿名 POST（§4.2 豁免注：密码必须可提交——GET
携密码违反 api-conventions §9.3「禁止查询参数传凭证」）；豁免边界 = 不创建
不修改业务资源（仅 FileShareAccess 留痕行 + 2h 短时效 cookie），且受 BR-07
(IP, slug) 限流与 §7.2 匿名基线双重兜底。
"""
from __future__ import annotations

from django.conf import settings as dj_settings
from django.http import HttpResponseRedirect
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from plane.base.exception import AppException
from plane.base.response import success_response
from plane.base.throttling import BASE_THROTTLES, ShareUnlockRateThrottle
from plane.db.models import FileShareLink
from plane.db.services import file_share as svc
from plane.db.services.upload_session import preview_dispatch
from plane.storage import minio as storage

#: BR-07：5 次失败 / 10 分钟 / (IP, slug)——原 ``ShareUnlockThrottle`` 自带
#: 实现已收编入 ``plane.base.throttling.ShareUnlockRateThrottle``（配置单源
#: 化，语义不变：仅失败计数、成功清零、fail-open、XFF 首段取 IP）。


class PublicShareBaseView(APIView):
    """api-conventions §10.1 ``PublicAPIBaseView`` 匿名只读基线的 FILE-004 落地：
    AllowAny + 空认证类（物理隔离：不读会话、不 enforce CSRF）+ 脱敏序列化
    （``svc.public_meta``）+ 只读方法集。"""

    permission_classes = [AllowAny]
    authentication_classes: list = []
    http_method_names = ["get", "head", "options"]


class ShareMetaView(PublicShareBaseView):
    """GET /api/v1/public/shares/{slug}/ —— 元信息（§4.2.2 信息最小化）。"""

    def get(self, request, slug: str):
        link = svc.resolve_active_share(slug)  # 读时四查 → 统一 410
        unlocked = svc.is_unlocked(link, request)
        return success_response(svc.public_meta(link, unlocked=unlocked))


class ShareUnlockView(PublicShareBaseView):
    """POST /api/v1/public/shares/{slug}/unlock/ —— 密码门（§4.2.3）。

    成功签发 2h HMAC cookie（BR-08；HttpOnly / Secure（非 DEBUG 强制）/
    SameSite=Lax / Max-Age=7200——属性基线对齐 api-conventions §9.2）。
    """

    # 公开分组唯一匿名 POST（§4.2 豁免注——显式声明方法集）
    http_method_names = ["get", "post", "head", "options"]
    throttle_classes = [*BASE_THROTTLES, ShareUnlockRateThrottle]

    def post(self, request, slug: str):
        link = svc.resolve_active_share(slug)
        password = ""
        if isinstance(request.data, dict):
            password = request.data.get("password") or ""
        if not link.password_hash:
            return success_response({"unlocked": True})  # UT-03：无密码直通
        unlock_throttle = ShareUnlockRateThrottle()
        if not svc.check_password(link, str(password)):
            svc.record_access(link, request, action="unlock_failed", success=False)
            unlock_throttle.hit(request, self)  # 失败才累计（FILE-004 §4.6 原语义）
            raise AppException(
                "AUTH_INVALID_CREDENTIALS",
                message="密码错误",
                details=[{
                    "field": "password", "code": "INVALID",
                    "message": f"剩余 {unlock_throttle.remaining(request, self)} 次尝试",
                }],
            )
        token = svc.sign_share_token(slug)
        svc.record_access(link, request, action="unlock")
        unlock_throttle.clear(request, self)  # 成功清零（失败才累计）
        resp = success_response({"unlocked": True, "expires_in": svc.SHARE_TOKEN_TTL})
        resp.set_cookie(
            svc.SHARE_COOKIE_NAME,
            token,
            max_age=svc.SHARE_TOKEN_TTL,
            httponly=True,
            secure=not dj_settings.DEBUG,  # 生产强制（§4.2.3：Secure（生产强制））
            samesite="Lax",
        )
        return resp


class ShareContentView(PublicShareBaseView):
    """GET /api/v1/public/shares/{slug}/content/ —— 预览调度 / 下载（§4.3.3）。

    ``?download=1`` → 302 跳 5 分钟预签名（attachment；FILE-001 §4.3.4 换发
    范式）；view 态拒下载（BR-05 → 403）；预览复用 FILE-003 ``preview_dispatch``
    匿名直签变体（未转码 202 轮询，IT-06）。
    """

    def get(self, request, slug: str):
        link = svc.resolve_active_share(slug)
        if not svc.is_unlocked(link, request):
            # 密码链接必须持本 slug 的有效 token（UT-10：他链 token 亦拒）
            raise AppException("AUTH_REQUIRED", message="请先输入访问密码解锁")
        if request.query_params.get("download") == "1":
            if link.permission == FileShareLink.Permission.VIEW:
                raise AppException("PERM_DENIED", message="此链接不包含下载权限")
            try:
                url = svc.share_download_url(link)
            except storage.StorageUnavailable as exc:
                raise AppException(
                    "SERVER_STORAGE_ERROR", message="对象存储暂时不可用，请稍后重试"
                ) from exc
            svc.record_access(link, request, action="download")
            return HttpResponseRedirect(url)
        try:
            http_status, data = preview_dispatch(asset=link.asset, anonymous=True)
        except storage.StorageUnavailable as exc:
            raise AppException(
                "SERVER_STORAGE_ERROR", message="对象存储暂时不可用，请稍后重试"
            ) from exc
        svc.record_access(link, request, action="view")
        return success_response(data, status_code=http_status)
