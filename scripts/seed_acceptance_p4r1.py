"""P4 R1（AUTH-012）验收演示种子——幂等，录制器启动时重跑。

造数口径（与冻结原型数值自洽）：
  · 平台运营二人（张运营=发起 / 李运营=二签，均 SystemAdmin+tenant_ops）
  · 演示租户（WS workspace 归集）→ standard 档 + 配额（100GB/3000rpm/100k 导出）
  · 存储 78.2GB（8×9.775GB FileAsset 数字行——size 仅计量列）
  · 风控事件三枚：R-03 open high（102,340/100,000=102.3% 硬拒线越界）
    R-06 open medium（55,100 次 92% GET）+ R-02 done low（异地登录已处置）
  · R-03 当日 Redis 计数对齐 102,340（导出硬拒演示）
输出 JSON（stdout 末行）：tenant/event/ops 清单。

用法（仓库根）：PATH=apps/api/.venv/bin:$PATH python3 scripts/seed_acceptance_p4r1.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import timedelta
from pathlib import Path

import django

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "plane.settings.dev")
django.setup()  # noqa: E402

from django.utils import timezone  # noqa: E402

from plane.db.models import (  # noqa: E402
    FileAsset,
    GovernanceTicket,
    RiskEvent,
    RiskRule,
    SystemAdmin,
    Tenant,
    TenantQuota,
    User,
    Workspace,
)

OPS1 = {"email": "gov-ops1@rabbit.dev", "name": "张运营", "password": "Rabbit123!"}
OPS2 = {"email": "gov-ops2@rabbit.dev", "name": "李运营", "password": "Rabbit123!"}


def upsert_ops(spec: dict) -> User:
    from plane.db.models import Workspace, WorkspaceMember
    from plane.db.models.roles import WorkspaceRole
    from plane.db.seeds.issue_types import seed_issue_types

    u = User.objects.filter(email=spec["email"]).first()
    if u is None:
        u = User.objects.create_user(email=spec["email"], password=spec["password"],
                                     display_name=spec["name"])
    sa, _ = SystemAdmin.objects.get_or_create(
        user=u, defaults={"is_active": True, "is_tenant_ops": True, "created_by": u})
    SystemAdmin.objects.filter(pk=sa.pk).update(is_active=True, is_tenant_ops=True)
    # 个人工作空间（注册同款默认）——否则 web 登录流程对无工作空间用户无法
    # 落到 /projects，运营进不了 admin 台（cookie 需经 web 登录建立）
    if not WorkspaceMember.objects.filter(member=u).exists():
        ws = Workspace.objects.create(
            name=f"{spec['name']} 的工作空间", slug=f"gov-ops-{u.id.hex[:8]}",
            owner=u, created_by=u)
        WorkspaceMember.objects.create(workspace=ws, member=u,
                                       role=WorkspaceRole.OWNER, created_by=u)
        seed_issue_types(ws, actor_id=u.id)
    return u


def main() -> None:
    ops1 = upsert_ops(OPS1)
    ops2 = upsert_ops(OPS2)

    ws = Workspace.objects.filter(slug="workspace", deleted_at__isnull=True).first()
    assert ws is not None, "演示工作空间不存在（先启动 dev 栈）"
    tenant = ws.tenant
    assert tenant is not None, "演示工作空间未归集租户（迁移回填应为 SaaS 形态）"
    tenant.name = "Beta 演示租户"
    tenant.tier = "standard"
    tenant.save()

    quota, _ = TenantQuota.objects.get_or_create(
        tenant=tenant, defaults={"created_by": ops1})
    TenantQuota.objects.filter(pk=quota.pk).update(
        storage_bytes=100 * 2**30, member_limit=100,
        api_rate_per_minute=3000, export_rows_per_day=100_000,
        webhook_limit=50, project_limit=None)

    # 存储水位 78.2GB（数字行，无真实对象）
    FileAsset.objects.filter(workspace=ws, entity_type="issue",
                             storage_path__startswith="gov-demo/").delete()
    import uuid as _uuid

    for i in range(8):
        FileAsset.objects.create(
            workspace=ws, entity_type="issue", entity_id=_uuid.uuid4(),
            size=9_775_000_000, attributes={"size": 9_775_000_000},
            storage_path=f"gov-demo/{i}", status="completed", created_by=ops1)

    # 风控事件三枚（聚合键带日期，重跑幂等）
    today_hour = timezone.now().strftime("%Y%m%d%H")
    r03, _ = RiskEvent.objects.update_or_create(
        aggregate_key=f"R-03:{tenant.id}:tenant:{tenant.id}:{today_hour}",
        defaults=dict(
            tenant=tenant, rule_code="R-03", severity="high", status="open",
            evidence={"rows_used": 102_340, "rows_quota": 100_000,
                      "ratio": 1.023, "actor_id": "u_01J6AB8C", "tier": "deny"},
            actions=[{"action": "deny", "by": "system",
                      "at": timezone.now().isoformat(), "note": "auto"}]))
    r06, _ = RiskEvent.objects.update_or_create(
        aggregate_key=f"R-06:{tenant.id}:token:tk_9F02:{today_hour}",
        defaults=dict(
            tenant=tenant, rule_code="R-06", severity="medium", status="open",
            evidence={"token_id": "tk_9F02", "total_calls": 55_100,
                      "getlist_calls": 50_692, "getlist_ratio": 0.92},
            actions=[]))
    RiskEvent.objects.update_or_create(
        aggregate_key=f"R-02:{tenant.id}:user:u_demo:2026090809",
        defaults=dict(
            tenant=tenant, rule_code="R-02", severity="low", status="actioned",
            evidence={"distance_km": 1860.4, "actor_id": "u_demo"},
            actions=[{"action": "alert", "by": str(ops1.id),
                      "at": (timezone.now() - timedelta(days=3)).isoformat(),
                      "note": "本人确认为出差"}]))

    # 重置租户阈值覆盖行（幕 06 的 300→100 演示每次从平台默认起步）与冻结态
    RiskRule.objects.filter(tenant=tenant).delete()
    Tenant.objects.filter(pk=tenant.pk).update(is_frozen=False, frozen_at=None,
                                               frozen_reason="")
    from django.core.cache import cache as _cache
    _cache.delete(f"frozen:{tenant.id}")
    _cache.delete(f"risk:throttle:{tenant.id}")

    # R-03 当日 Redis 计数对齐（硬拒演示）
    from django.core.cache import cache

    cache.set(f"risk:R-03:tenant:{tenant.id}:1d:"
              f"{timezone.now():%Y%m%d}:rows", 102_340, timeout=86400)

    # L2 待批工单（幕 04 客户批准演示；scoped 字段与对象 ID）
    ticket, _ = GovernanceTicket.objects.update_or_create(
        tenant=tenant, risk_event=r03, requested_by=ops1, approve_channel="online",
        status="pending",
        defaults=dict(
            scope={"fields": ["issue.title", "file.name"],
                   "ids": ["u_01J6AB8C", "u_01J7D2E3"]},
            note="R-03 事件复核需确认导出对象清单", created_by=ops1))

    ws_admin = (Workspace.objects.filter(pk=ws.pk)
                .values_list("workspace_member__member__email", flat=True))
    print(json.dumps({
        "tenant": str(tenant.id), "tenant_name": tenant.name,
        "r03": str(r03.id), "r06": str(r06.id), "ticket": str(ticket.id),
        "ops1": OPS1["email"], "ops2": OPS2["email"],
        "ops_password": OPS1["password"],
        "ws_slug": ws.slug,
    }))


if __name__ == "__main__":
    main()
