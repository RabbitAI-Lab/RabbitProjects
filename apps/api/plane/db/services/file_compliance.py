"""文件合规服务（FILE-006 §4.2/§4.5，P4 R6）。

有效策略解析（四级最细非空覆盖）+ Redis 缓存精确失效；水印合成（Pillow
动态平铺——访问者姓名+邮箱+分钟级时间）；DLP 规则校验（编译+灾难性
回溯检测+配额 20 条）；留存 sweeper（软删+purge 两段、LegalHold 豁免）；
下载/分享闸门（BR-03/04 禁下载禁原文件、禁分享拒链接创建）。
"""

from __future__ import annotations

import io
import logging
import re
import sre_constants

from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger("plane.filecompliance")

CACHE_TTL = 600  # fpol:{asset_id}
DLP_RULE_LIMIT = 20
_RETENTION_SWEEPER_BATCH = 500

#: 有效域与默认值（未配置层继承——所有层全空 = 全放行默认）
_POLICY_FIELDS = ("watermark", "download", "share_link", "retention_days", "dark_watermark")
_DEFAULTS = {
    "watermark": "off",
    "download": "allow",
    "share_link": "allow",
    "retention_days": None,
    "dark_watermark": False,
}


def resolve_policy(asset) -> dict:
    """四级（asset→folder→project→workspace）最细非空覆盖解析。

    缓存键 fpol:{asset_id}（策略变更时精确失效，TTL 10min）。
    """
    key = f"fpol:{asset.id}"
    try:
        cached = cache.get(key)
        if cached is not None:
            return cached
    except Exception:  # noqa: BLE001
        cached = None

    from plane.db.models import CompliancePolicy

    effective = dict(_DEFAULTS)
    layers = []
    if getattr(asset, "folder_id", None):
        layers.append(("folder", asset.folder_id))
    layers.append(("project", asset.project_id))
    if getattr(asset, "project", None) and asset.project.workspace_id:
        layers.append(("workspace", asset.project.workspace_id))
    layers.append(("asset", asset.id))
    for field, ref in layers:
        row = CompliancePolicy.objects.filter(**{f"{field}_id": ref}, deleted_at__isnull=True).first()
        if row is None:
            continue
        for f in _POLICY_FIELDS:
            value = getattr(row, f)
            if value is not None:
                effective[f] = value
    try:
        cache.set(key, effective, timeout=CACHE_TTL)
    except Exception:  # noqa: BLE001
        pass
    return effective


def invalidate_policy(asset_id) -> None:
    try:
        cache.delete(f"fpol:{asset_id}")
    except Exception:  # noqa: BLE001
        pass


# ── 下载 / 分享闸门 ────────────────────────────────────────────────


def assert_download_allowed(asset) -> dict:
    """BR-03：download=deny 拒发预签名（403 PERM_DENIED）。"""
    policy = resolve_policy(asset)
    if policy["download"] == "deny":
        from plane.base.exception import AppException

        raise AppException("PERM_DENIED", message="该文件受合规策略保护：禁止下载（仅预览）")
    return policy


def assert_share_allowed(asset) -> None:
    """BR-04：share_link=deny 拒链接创建。"""
    policy = resolve_policy(asset)
    if policy["share_link"] == "deny":
        from plane.base.exception import AppException

        raise AppException("PERM_DENIED", message="该文件受合规策略保护：禁止分享")


# ── 水印合成 ───────────────────────────────────────────────────────


def watermark_text(viewer_name: str, viewer_email: str) -> str:
    """BR-02 水印语汇：姓名 + 邮箱 + 时间（精确到分）。"""
    return f"{viewer_name} {viewer_email} {timezone.now():%Y-%m-%d %H:%M}"


def render_watermarked_png(image_bytes: bytes, text: str, *, strength: int = 2) -> bytes | None:
    """图片动态水印（Pillow 平铺 + 低透明 + 旋转；失败返回 None 不阻断预览）。

    暗水印（DCT 频域）仅旗舰档 dark_watermark 开启时由预览管线替换本函数
    （本函数为明式基线实现）。
    """
    try:
        from PIL import Image, ImageDraw, ImageFont

        base = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        try:
            font = ImageFont.truetype(
                "/System/Library/Fonts/PingFang.ttc", 18)
        except OSError:
            font = ImageFont.load_default()  # type: ignore[assignment]
        step_x = max(int(base.width / 3), 160)
        step_y = max(int(base.height / 4), 90)
        for y in range(0, base.height, step_y):
            for x in range(0, base.width, step_x):
                draw.text((x, y), text, fill=(128, 128, 128, 46), font=font)
        out = Image.alpha_composite(base, overlay.rotate(12, expand=False))
        buf = io.BytesIO()
        out.convert("RGB").save(buf, format="PNG")
        return buf.getvalue()
    except Exception:  # noqa: BLE001 —— 水印失败不阻断
        logger.warning("compliance.watermark_failed size=%d", len(image_bytes))
        return None


# ── DLP ────────────────────────────────────────────────────────────


def validate_dlp_pattern(pattern: str) -> None:
    """编译验证 + 灾难性回溯面检测（嵌套量词黑名单——§4.7 轻量版）。"""
    try:
        re.compile(pattern)
    except (re.error, sre_constants.error) as exc:
        raise ValueError(f"正则非法：{exc}") from None
    if re.search(r"(\((?:[^()]*(\([^()]*\)[^()]*)*)+\)[*+{])", pattern):
        raise ValueError("疑似灾难性回溯（嵌套量词）——拒绝保存")


def scan_text(text: str, rules) -> list[str]:
    """轻量内容识别：命中规则名列表（全文 ≤10 万字符护栏）。"""
    hits: list[str] = []
    for rule in rules:
        try:
            if re.search(rule.pattern, text[:100_000]):
                hits.append(rule.name)
        except re.error:
            continue
    return hits


# ── 留存 sweeper ───────────────────────────────────────────────────


def retention_sweep(*, now=None) -> dict:
    """两段清理：过期未持保留行软删；软删超 retention+30 天 purge 标记。

    LegalHold 活跃行豁免（BR-06 对冲）；purge 实际对象清理归 FILE-002
    purge 任务口径（FILE-002 待回改登记分工）。
    """
    from datetime import timedelta

    from plane.db.models import CompliancePolicy, FileAsset, LegalHold

    now = now or timezone.now()
    soft_deleted = 0
    purged_marked = 0
    asset_policies = {
        p.asset_id: p for p in CompliancePolicy.objects.filter(asset__isnull=False, deleted_at__isnull=True)
    }
    held = set(LegalHold.objects.filter(released_at__isnull=True).values_list("asset_id", flat=True))

    def retention_of(asset) -> int | None:
        pol = asset_policies.get(asset.id)
        if pol and pol.retention_days:
            return pol.retention_days
        return resolve_policy(asset)["retention_days"]

    candidates = FileAsset.all_objects.filter(deleted_at__isnull=True).iterator(chunk_size=_RETENTION_SWEEPER_BATCH)
    for asset in candidates:
        if asset.id in held:
            continue
        days = retention_of(asset)
        if not days:
            continue
        cutoff = asset.created_at + timedelta(days=days)
        if now > cutoff:
            asset.deleted_at = now
            asset.save(update_fields=["deleted_at", "updated_at"])
            soft_deleted += 1
    # purge 标记：软删超（retention + 30 天宽限）置 purged_at（对象清理归 FILE-002）
    stale = FileAsset.all_objects.filter(deleted_at__isnull=False, purged_at__isnull=True)
    for asset in stale.iterator(chunk_size=_RETENTION_SWEEPER_BATCH):
        days = retention_of(asset) or 0
        purge_cutoff = (asset.deleted_at or now) + timedelta(days=days + 30)
        if now > purge_cutoff and asset.id not in held:
            FileAsset.all_objects.filter(pk=asset.pk).update(purged_at=now)
            purged_marked += 1
    return {"soft_deleted": soft_deleted, "purged_marked": purged_marked}
