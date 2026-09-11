"""配额强制点辅助（AUTH-012 §4.5，P4 R1）。

五个写路径挂接的判定函数（阈值保存 BR-06 在 SecurityRulesView 内联）：
  · check_export_deny   第 5 强制点：R-03 当日行数 ≥ 配额 → 409 硬拒
  · check_member_quota  邀请接受：租户聚合成员数超限 → QUOTA_MEMBER_EXCEEDED
  · check_webhook_quota 订阅创建超限 / check_project_quota 项目创建超限
全部带 BR-11 门控（私有化直接返回）+ 未治理租户跳过（tenant=NULL）。
"""

from __future__ import annotations

import logging

from django.conf import settings as dj_settings
from django.core.cache import cache
from django.utils import timezone

from plane.base.exception import AppException

logger = logging.getLogger("plane.governance")


def _governed_tenant_of(workspace):
    """工作空间 → 治理租户；门控关闭、未治理或**降级宽限期内**（§2.3：
    只告警不硬拒，UT-04）返回 None——调用方统一跳过硬拒判定。"""
    if not getattr(dj_settings, "TENANT_GOVERNANCE_ENABLED", False):
        return None
    if workspace is None or workspace.tenant_id is None:
        return None
    tenant = workspace.tenant
    if _in_grace_period(tenant):
        return None
    return tenant


def _in_grace_period(tenant) -> bool:
    """降级宽限期内只告警不硬拒（§2.3：降级给 30 天宽限期，到期后硬拒——
    UT-04；告警面走引擎/日志，本判定只放行硬拒）。"""
    from plane.db.models import TenantQuota

    quota = TenantQuota.objects.filter(tenant=tenant).first()
    until = quota.downgrade_grace_until if quota else None
    return bool(until) and timezone.now().date() < until


def _export_day_key(tenant_id: str) -> str:
    return f"risk:R-03:tenant:{tenant_id}:1d:{timezone.now():%Y%m%d}:rows"


def check_storage_quota(workspace, incoming: int) -> None:
    """第 1 强制点（两层模型 L-T 租户层，硬上限）：Σ 下挂 WS 已用 + incoming
    > 租户配额 → 409 QUOTA_STORAGE_EXCEEDED（details 注明「租户层」，
    §2.3 扣减顺序：任一层拒绝整体拒绝、不落预留）。

    层序注：FILE-002 WS 层判定在 presign 服务事务内（QuotaExceededError
    先例）；本判定前置在服务调用前——租户层为 Σ 硬上限，先拒时 WS 层
    预留同样未落，语义等价（拒绝层归因按实际触发层标注）。"""
    tenant = _governed_tenant_of(workspace)
    if tenant is None:
        return
    from django.db.models import Sum

    from plane.db.models import FileAsset, Workspace
    from plane.governance.risk_engine import tier_quota

    quota = tier_quota(tenant)["storage_bytes"]
    if not quota or incoming <= 0:
        return
    ws_ids = list(Workspace.objects.filter(tenant=tenant).values_list("id", flat=True))
    used = FileAsset.objects.filter(workspace_id__in=ws_ids).aggregate(s=Sum("size"))["s"] or 0
    if used + incoming > quota:
        raise AppException(
            "QUOTA_STORAGE_EXCEEDED",
            message="租户存储配额不足",
            details=[
                {
                    "field": "file_size",
                    "code": "QUOTA",
                    "message": f"租户层：已用 {used} / {quota}，本次需 {incoming}（§2.3 两层模型硬上限）",
                }
            ],
        )


def check_export_deny(workspace, *, estimated_rows: int = 0) -> None:
    """第 5 强制点（R-03 deny 同步档）：当日已导出行数 ≥ 配额 → 409
    RESOURCE_LIMIT_EXCEEDED（details 注明规则与余量，§4.4 错误示例）。
    80%~100% 区间不拒（仅预警——预警事件由引擎 on_export 落）。"""
    tenant = _governed_tenant_of(workspace)
    if tenant is None:
        return
    from plane.governance.risk_engine import tier_quota

    quota = tier_quota(tenant)["export_rows_per_day"]
    if not quota:
        return
    try:
        used = int(cache.get(_export_day_key(str(tenant.id))) or 0)
    except Exception:  # noqa: BLE001 —— Redis 失联放行
        logger.warning("export.deny_check_degraded tenant=%s", tenant.id)
        return
    remaining = max(0, quota - used)
    if used >= quota:
        raise AppException(
            "RESOURCE_LIMIT_EXCEEDED",
            message="租户当日导出行数已达配额上限",
            details=[
                {
                    "field": "export",
                    "code": "LIMIT",
                    "message": f"规则 R-03：当日已导出 {used:,} 行 / 配额 "
                    f"{quota:,} 行，余量 {remaining:,}，"
                    f"次日窗口滚动后恢复",
                }
            ],
        )


def check_member_quota(workspace) -> None:
    """邀请接受前置判定：租户聚合成员数（去重）+1 > member_limit 拒绝。"""
    tenant = _governed_tenant_of(workspace)
    if tenant is None:
        return
    from plane.db.models import WorkspaceMember
    from plane.governance.risk_engine import tier_quota

    limit = tier_quota(tenant)["member_limit"]
    if not limit:  # enterprise 按合同 seats
        limit = tenant.seats or None
    if not limit:
        return
    count = (
        WorkspaceMember.objects.filter(workspace__tenant=tenant, is_active=True, workspace__deleted_at__isnull=True)
        .values("member")
        .distinct()
        .count()
    )
    if count + 1 > limit:
        raise AppException(
            "QUOTA_MEMBER_EXCEEDED",
            message="租户成员数已达配额上限",
            details=[{"field": "member", "code": "LIMIT", "message": f"当前 {count} / 上限 {limit}（租户层聚合）"}],
        )


def _count_cap_check(workspace, *, model_qs, limit: int | None, label: str) -> None:
    if limit is None:
        return
    if model_qs.count() >= limit:
        raise AppException(
            "RESOURCE_LIMIT_EXCEEDED",
            message=f"租户{label}数已达配额上限",
            details=[{"field": label, "code": "LIMIT", "message": f"当前已达上限 {limit}（租户层）"}],
        )


def check_webhook_quota(workspace) -> None:
    """INTG-002 订阅创建挂接：租户聚合订阅数 ≥ webhook_limit → 409（UT-25）。"""
    tenant = _governed_tenant_of(workspace)
    if tenant is None:
        return
    from plane.db.models import WebhookEndpoint
    from plane.governance.risk_engine import tier_quota

    limit = tier_quota(tenant)["webhook_limit"]
    _count_cap_check(
        workspace,
        model_qs=WebhookEndpoint.objects.filter(project__workspace__tenant=tenant, deleted_at__isnull=True),
        limit=limit,
        label="webhook 订阅",
    )


def check_project_quota(workspace) -> None:
    """项目创建挂接：租户聚合项目数 ≥ project_limit → 409（UT-26；
    standard/enterprise 档 project_limit=None 不限）。"""
    tenant = _governed_tenant_of(workspace)
    if tenant is None:
        return
    from plane.db.models import Project
    from plane.governance.risk_engine import tier_quota

    limit = tier_quota(tenant)["project_limit"]
    _count_cap_check(
        workspace,
        model_qs=Project.objects.filter(workspace__tenant=tenant, deleted_at__isnull=True),
        limit=limit,
        label="项目",
    )
