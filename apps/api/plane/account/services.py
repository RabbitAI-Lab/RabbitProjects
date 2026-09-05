"""Account 域服务（AUTH-004 §4.3）。

分三块：
- ``ProfileService`` —— 资料 PATCH（白名单局部更新 + 安全字段显式拒绝）
- ``PasswordService`` —— 改密 + 忘记密码 + 重置密码 + 会话吊销
- ``AvatarService`` —— 头像 presign / complete / 恢复默认

跨切关注点：
- 写动作的副作用（邮件 / Activity / 清理）置于 ``transaction.on_commit``，
  事务回滚不留幽灵日志（api-conventions.md §10.5）。
- 防枚举三件套（forgot-password）：同响应 + 恒定时序 + 限流（§2.3）。
- 令牌消费用 ``select_for_update(skip_locked=True)``，并发双请求恰一成功（§4.3.4）。
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from collections.abc import Iterable
from datetime import timedelta

from django.contrib.auth import password_validation
from django.contrib.auth.hashers import check_password
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from plane.account.sessions import (
    delete_all_sessions as _delete_all_sessions,
)
from plane.account.sessions import (
    revoke_other_sessions as _revoke_other_sessions,
)
from plane.base.exception import AppException
from plane.db.models import FileAsset, PasswordResetToken, User
from plane.storage import minio as storage

logger = logging.getLogger("plane.account.services")

# 资料可写字段白名单（§4.3.1 BR-02）
PROFILE_WRITABLE_FIELDS: frozenset[str] = frozenset(
    {"display_name", "first_name", "last_name", "intro"}
)
# 永不可通过资料端点写入的字段（§1.2）
FORBIDDEN_ON_PROFILE: frozenset[str] = frozenset(
    {"password", "email", "is_active", "is_staff", "last_workspace_id"}
)

# 头像 presign 直传约束（BR-03）
AVATAR_MAX_BYTES = 2 * 1024 * 1024  # 2MB
AVATAR_ALLOWED_MIME: frozenset[str] = frozenset({"image/png", "image/jpeg", "image/webp"})
# 头像存储前缀（与 FILE-001 §4.1.3 对齐）
AVATAR_BUCKET = "rp-uploads"


# ──────────────────────────────────────────────────────────────────────
# Profile
# ──────────────────────────────────────────────────────────────────────
class ProfileService:
    @staticmethod
    def update_profile(*, user: User, payload: dict) -> User:
        """白名单局部更新 —— §4.3.1 BR-02 / S5 双保险。

        返回更新后的 user（save 前 ``refresh_from_db`` 拿最新服务端真值）。
        """
        if not isinstance(payload, dict):
            raise ValidationError({"__all__": "请求体必须是对象"})

        forbidden = set(payload) & FORBIDDEN_ON_PROFILE
        if forbidden:
            raise ValidationError(
                {
                    f: ["该字段不可通过资料端点修改"]
                    for f in sorted(forbidden)
                }
            )
        unknown = set(payload) - PROFILE_WRITABLE_FIELDS
        if unknown:
            raise ValidationError(
                {f: ["未知字段"] for f in sorted(unknown)}
            )

        with transaction.atomic():
            for field in PROFILE_WRITABLE_FIELDS & set(payload):
                value = payload[field]
                if isinstance(value, str):
                    value = value.strip()  # BR-13
                setattr(user, field, value)
            # User 模型未继承 BaseModel（自管理 created_by/updated_by）；
            # 仅刷新 updated_at 触发 ORM auto_now
            update_fields = sorted(PROFILE_WRITABLE_FIELDS & set(payload)) + [
                "updated_at",
            ]
            user.save(update_fields=update_fields)
        return user

    @staticmethod
    def serialize(user: User) -> dict:
        """GET / PATCH 响应 —— §4.2.1 / §4.5.1 ProfileStore 唯一写入点。"""
        return {
            "id": str(user.id),
            "email": user.email,
            "display_name": user.display_name,
            "first_name": user.first_name or "",
            "last_name": user.last_name or "",
            "intro": user.intro or "",
            "avatar_url": user.avatar_url or None,
            "is_default_avatar": not (user.avatar_url or "").strip(),
            "is_active": user.is_active,
            "last_workspace_id": str(user.last_workspace_id) if user.last_workspace_id else None,
            "updated_at": user.updated_at.isoformat(timespec="milliseconds").replace(
                "+00:00", "Z"
            ),
        }


# ──────────────────────────────────────────────────────────────────────
# Password
# ──────────────────────────────────────────────────────────────────────
class PasswordService:
    """改密 / 忘记 / 重置 + 会话吊销。"""

    # 复用 AUTH-001 BR-03 / 04 校验器链 —— 当前 settings 未配 AUTH_PASSWORD_VALIDATORS，
    # 实际由 Django 默认 + 显式长度 8~128 兜底（与 SignUpSerializer 同款）。
    @staticmethod
    def _validate_new_password(new_password: str, *, user: User) -> None:
        try:
            password_validation.validate_password(new_password, user=user)
        except ValidationError:
            raise
        if not (8 <= len(new_password) <= 128):
            raise ValidationError(
                {"new_password": ["密码长度需在 8~128 之间"]}
            )

    @staticmethod
    @transaction.atomic
    def change_password(
        *,
        user: User,
        old_password: str,
        new_password: str,
        new_password_confirm: str,
        request_session_key: str | None,
    ) -> dict:
        if new_password != new_password_confirm:
            raise ValidationError(
                {"new_password_confirm": ["两次输入的新密码不一致"]}
            )
        if not check_password(old_password, user.password):
            raise ValidationError({"old_password": ["旧密码不正确"]})
        if check_password(new_password, user.password):
            raise ValidationError(
                {"new_password": ["新密码不能与当前密码相同"]}
            )

        PasswordService._validate_new_password(new_password, user=user)

        user.set_password(new_password)
        user.save(update_fields=["password", "updated_at"])
        revoked = _revoke_other_sessions(user.id, keep_session_key=request_session_key)
        # 安全审计消费方：AUTH-010（S8/P3 全站审计）；当前异步不可用时降级记本地日志
        transaction.on_commit(
            lambda: logger.info(
                "account.password_changed user=%s revoked=%s",
                user.id,
                revoked,
            )
        )
        return {"revoked_sessions": revoked}

    @staticmethod
    def forgot_password(*, email: str, ip: str | None = None) -> None:
        """申请重置 —— 防枚举三件套（§2.3 / D4）。"""
        email = (email or "").strip().lower()
        user = User.objects.filter(email=email, is_active=True).first()

        if user is None:
            # 时序抹平：执行等价 token_urlsafe + sha256 算力（不落库、不发任务）
            # —— 与真实签发路径 IO 形态一致即可
            secrets.token_urlsafe(PasswordResetToken.TOKEN_BYTES)
            hashlib.sha256(secrets.token_urlsafe(8).encode()).hexdigest()
            return

        with transaction.atomic():
            token = PasswordResetToken.issue(user=user, ip=ip)
            # on_commit 投递邮件（事务回滚不发幽灵邮件，§4.3.3 注释）
            transaction.on_commit(
                lambda: _dispatch_reset_email(user_id=str(user.id), token=token)
            )

    @staticmethod
    @transaction.atomic
    def reset_password(
        *, token: str, new_password: str, new_password_confirm: str
    ) -> dict:
        if new_password != new_password_confirm:
            raise ValidationError(
                {"new_password_confirm": ["两次输入的新密码不一致"]}
            )

        token_hash = hashlib.sha256((token or "").encode()).hexdigest()
        prt = (
            PasswordResetToken.objects
            .select_for_update(skip_locked=True)
            .filter(token_hash=token_hash, used_at__isnull=True)
            .first()
        )
        now = timezone.now()
        if prt is None:
            raise AppException("AUTH_PASSWORD_RESET_INVALID")
        if prt.expires_at <= now:
            raise AppException("AUTH_PASSWORD_RESET_EXPIRED")

        PasswordService._validate_new_password(new_password, user=prt.user)
        # 校验通过后再写——前面抛错全靠 raise 终止，前面的 __exit__ 不做事
        prt.user.set_password(new_password)
        prt.user.save(update_fields=["password", "updated_at"])
        prt.used_at = now
        prt.save(update_fields=["used_at", "updated_at"])
        # 兄弟令牌一并作废（BR-07）
        PasswordResetToken.objects.filter(user=prt.user).exclude(pk=prt.pk).update(
            used_at=now
        )
        revoked = _delete_all_sessions(prt.user_id)
        transaction.on_commit(
            lambda: logger.info(
                "account.password_reset user=%s revoked=%s", prt.user_id, revoked
            )
        )
        return {"revoked_sessions": revoked}


# ──────────────────────────────────────────────────────────────────────
# Avatar
# ──────────────────────────────────────────────────────────────────────
class AvatarService:
    """头像 presign / complete / 恢复默认（§4.2.2 / §4.2.3 / §4.2.4）。"""

    @staticmethod
    def presign(*, user: User, file_name: str, file_size: int, content_type: str) -> dict:
        """申请头像直传凭证 —— 协议字段以架构 §13.2 为准。

        字段对齐说明：FILE-001 §4.3.1 早期草案用 ``name/size/mime`` + 响应 ``method/expires_in``，
        与架构 §13.2 不一致；AUTH-004 §4.2.2 / 架构 §13.2 统一为：
          - 请求 ``file_name / file_size / content_type``
          - 响应 ``asset_id / upload_url / fields / expires_at``
        FILE-001 §4.3.1 已按架构回改。本实现输出架构版本。
        """
        if content_type not in AVATAR_ALLOWED_MIME:
            raise AppException(
                "VALIDATION_FILE_TYPE_NOT_ALLOWED",
                message="仅支持 png / jpeg / webp 图片",
                details=[
                    {
                        "field": "content_type",
                        "code": "INVALID",
                        "message": f"{content_type} 不在允许列表",
                    }
                ],
            )
        if file_size <= 0:
            raise AppException(
                "VALIDATION_FILE_SIZE_EXCEEDED",
                message="不接受空文件",
                details=[
                    {"field": "file_size", "code": "TOO_SMALL", "message": "空文件"}
                ],
            )
        if file_size > AVATAR_MAX_BYTES:
            raise AppException(
                "VALIDATION_FILE_SIZE_EXCEEDED",
                message="头像文件超过 2MB 上限",
                details=[
                    {
                        "field": "file_size",
                        "code": "TOO_LARGE",
                        "message": "头像最大 2MB",
                    }
                ],
            )

        key = f"avatar/{user.id}/{secrets.token_urlsafe(16)}.webp"
        asset = FileAsset.objects.create(
            workspace_id=None,  # type: ignore[misc]  # FK null=True：头像无工作空间归属
            project_id=None,
            entity_type=FileAsset.EntityType.AVATAR,
            entity_id=user.id,
            attributes={
                "name": file_name,
                "size": file_size,
                "mime": content_type,
                "ext": ".webp",
            },
            size=file_size,
            storage_path=key,
            uploaded_by=user,
            created_by=user,
            updated_by=user,
        )

        try:
            upload_url = storage.presigned_put_url(
                bucket=AVATAR_BUCKET,
                key=key,
                content_type=content_type,
                content_length_range=(1, AVATAR_MAX_BYTES),
            )
        except storage.StorageUnavailable as exc:
            # 状态保留 uploading，30 分钟后 beat 兜底回收（§4.4 cleanup_avatar_orphan_assets）
            logger.warning("avatar.presign_failed user=%s err=%s", user.id, exc)
            raise AppException(
                "SERVER_STORAGE_ERROR",
                message="对象存储暂时不可用，请稍后重试",
            ) from exc
        # 把 MinIO 端点 host 改写为同源 /uploads/ 前缀（Nginx 反代，FILE-001 §4.7）
        upload_url = _rewrite_to_uploads_prefix(upload_url)
        expires_at = timezone.now() + timedelta(seconds=storage.DEFAULT_PRESIGN_EXPIRES)
        return {
            "asset_id": str(asset.id),
            "upload_url": upload_url,
            "fields": {"Content-Type": content_type},
            "expires_at": expires_at.isoformat(timespec="milliseconds").replace(
                "+00:00", "Z"
            ),
        }

    @staticmethod
    def complete(*, user: User, asset_id: str) -> dict:
        """complete 后翻转状态 + 写入 ``User.avatar_url``。"""
        try:
            asset = FileAsset.objects.get(
                pk=asset_id,
                entity_type=FileAsset.EntityType.AVATAR,
                entity_id=user.id,
                uploaded_by=user,
            )
        except FileAsset.DoesNotExist as exc:
            raise AppException(
                "RESOURCE_NOT_FOUND",
                message="头像资源不存在或无权操作",
            ) from exc

        if asset.status == FileAsset.Status.UPLOADED:
            # 幂等快路径：直接以现值返回
            return _avatar_url_payload(user, asset.storage_path)

        try:
            real_size = storage.head_object_size(
                bucket=AVATAR_BUCKET, key=asset.storage_path
            )
        except storage.StorageObjectNotFound as exc:
            raise AppException(
                "VALIDATION_FILE_UPLOAD_MISMATCH",
                message="头像对象校验失败",
                details=[
                    {
                        "field": "asset",
                        "code": "DOES_NOT_EXIST",
                        "message": "存储中未找到该文件，请重新上传",
                    }
                ],
            ) from exc
        except storage.StorageUnavailable as exc:
            logger.warning("avatar.head_failed user=%s err=%s", user.id, exc)
            raise AppException(
                "SERVER_STORAGE_ERROR",
                message="对象存储暂时不可用，请稍后重试",
            ) from exc

        if real_size != asset.size:
            raise AppException(
                "VALIDATION_FILE_UPLOAD_MISMATCH",
                message="对象大小与声明不一致，请重新上传",
                details=[
                    {
                        "field": "size",
                        "code": "INVALID",
                        "message": "对象大小与声明不一致，请重新上传",
                    }
                ],
            )

        avatar_url = _public_avatar_url(asset.storage_path)
        with transaction.atomic():
            FileAsset.objects.filter(pk=asset.pk, status=FileAsset.Status.UPLOADING).update(
                status=FileAsset.Status.UPLOADED,
                is_uploaded=True,
            )
            User.objects.filter(pk=user.pk).update(
                avatar_url=avatar_url,
                updated_at=timezone.now(),  # User 不继承 BaseModel，无 updated_by 字段
            )
        user.refresh_from_db()
        return _avatar_url_payload(user, asset.storage_path, avatar_url=avatar_url)

    @staticmethod
    def delete(*, user: User) -> dict:
        """恢复默认 —— §4.2.4（对象延迟回收由 cleanup_avatar_orphan_assets 兜底，
        故此处只清 avatar_url，不在同步路径里删对象）。"""
        with transaction.atomic():
            User.objects.filter(pk=user.pk).update(
                avatar_url="",
            )
        user.refresh_from_db()
        return {
            "avatar_url": None,
            "is_default_avatar": True,
            "default_avatar_url": _public_avatar_url(user_id=str(user.id), name=user.display_name),
            "updated_at": user.updated_at.isoformat(timespec="milliseconds").replace(
                "+00:00", "Z"
            ),
        }


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────
def _public_avatar_url(storage_path: str | None = None, *, user_id: str = "", name: str = "") -> str:
    """构造头像最终可访问 URL。

    storage_path 给定 → 对象存储公开 URL（直传通道）；
    否则 → 默认头像服务端 SVG 端点（AUTH-004 §4.2.5）。
    """
    if storage_path:
        # 与 presign 同源形态：/uploads/<bucket>/<key>；Nginx 反代到 MinIO
        return f"/uploads/{AVATAR_BUCKET}/{storage_path}"
    # 默认头像端点：让浏览器 / 邮件 / space 公开页直接 GET
    from urllib.parse import quote
    return f"/api/v1/public/users/{user_id}/avatar/?seed={user_id}&name={quote((name or '?')[:1])}"


def _avatar_url_payload(user: User, storage_path: str | None = None, *, avatar_url: str | None = None) -> dict:
    return {
        "avatar_url": avatar_url if avatar_url is not None else (user.avatar_url or None),
        "is_default_avatar": not (user.avatar_url or "").strip(),
        "updated_at": user.updated_at.isoformat(timespec="milliseconds").replace(
            "+00:00", "Z"
        ),
    }


def _rewrite_to_uploads_prefix(url: str) -> str:
    """把 MinIO 端点 host 改写为相对路径 /uploads/<bucket>/<key> —— Nginx 反代，
    浏览器零跨域预检成本（FILE-001 §4.7 / AUTH-004 §4.2.2 对齐说明）。

    boto3 生成的 URL 形如 ``http://localhost:9000/rp-uploads/<key>?X-Amz-…`` →
    ``/uploads/rp-uploads/<key>?X-Amz-…``。桶名保留在路径中以便 Nginx 路由。
    """
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(url)
    # 路径已是 /<bucket>/<key> 形态，直接拼前缀
    path = parts.path
    if not path.startswith("/"):
        path = "/" + path
    return urlunsplit(("", "", f"/uploads{path}", parts.query, ""))


def _dispatch_reset_email(*, user_id: str, token: str) -> None:
    """同步投递（on_commit 内）—— 通过 Celery 异步任务发邮件；broker 不可达时降级为同步投递并写日志。"""
    try:
        from plane.account.tasks import send_reset_email

        send_reset_email.delay(user_id, token)
    except Exception as exc:  # broker 不可达 / 任务路由失败
        # broker down 时降级为同步邮件投递（SMTP 同样降级到日志），保证 forgot
        # 端到端可验证（IT-01）。生产由运维保证 broker 在线，hits 此分支即告警。
        logger.warning(
            "account.reset_email_broker_down user=%s err=%s falling_back_to_sync",
            user_id, exc,
        )
        try:
            from plane.app.mail import deliver_reset_email
            deliver_reset_email(user_id=user_id, token=token)
        except Exception as inner_exc:
            logger.error(
                "account.reset_email_sync_fallback_failed user=%s err=%s",
                user_id, inner_exc,
            )


def active_password_reset_tokens(user) -> Iterable[PasswordResetToken]:
    return PasswordResetToken.objects.filter(user=user, used_at__isnull=True)
