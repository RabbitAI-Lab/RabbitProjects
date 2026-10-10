"""P4 R2（AUTH-011 目录同步）验收演示种子——幂等。

造数：演示工作空间启用 LDAP 通道 + 两类待办（席位满待开通 / 邮箱变更
人工裁决）+ 三条台账（全量成功 / 增量成功 / 干跑待确认——明细含部门
变更行）。输出 JSON（stdout 末行）。
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
django.setup()

from django.utils import timezone  # noqa: E402

from plane.db.models import (  # noqa: E402
    DirectoryPendingAction,
    DirectorySyncRun,
    LdapDirectoryConfig,
    Workspace,
)


def main() -> None:
    ws = Workspace.objects.filter(slug="workspace", deleted_at__isnull=True).first()
    assert ws is not None, "演示工作空间不存在"
    # 幂等清理：前轮演示映射（外部键撞 uq_directory_identity）；用户行保留
    # （User 删除会级联 django_admin_log——该表本库不存在，坑 1 同款）
    from plane.db.models import DirectoryUserMapping

    DirectoryUserMapping.all_objects.filter(workspace=ws, channel="ldap").delete()
    # 通道：LDAP 启用（幕三将 PATCH 停用后切 SCIM）
    cfg, _ = LdapDirectoryConfig.objects.update_or_create(
        workspace=ws, defaults={
            "name": "Acme AD 域", "server_uri": "ldaps://ad.corp.cn:636",
            "bind_dn": "CN=syncsvc,OU=Service,DC=corp,DC=cn",
            "bind_secret_ref": "env:LDAP_BIND_SECRET",
            "base_dn": "OU=Staff,DC=corp,DC=cn", "is_enabled": True,
            "sync_interval_minutes": 15})
    # 待办 ×2
    DirectoryPendingAction.objects.update_or_create(
        workspace=ws, kind="pending_provision", dedup_key="guid-wangfang",
        status="pending", defaults={"payload": {
            "external_id": "guid-wangfang",
            "email": "wang.fang@corp.cn", "display_name": "王芳"}})
    DirectoryPendingAction.objects.update_or_create(
        workspace=ws, kind="manual_review",
        dedup_key="li.wei@corp.cn->li.wei2@corp.cn", status="pending",
        defaults={"payload": {
            "old_email": "li.wei@corp.cn", "new_email": "li.wei2@corp.cn"}})
    # 台账 ×3
    DirectorySyncRun.objects.filter(workspace=ws).delete()
    run_ok = DirectorySyncRun.objects.create(
        workspace=ws, channel="ldap", status="success", full_sync=True,
        triggered_by="beat", counts={"created": 3, "updated": 12,
                                      "disabled": 0, "skipped": 1, "failed": 0},
        detail=[{"email": "wang.fang@corp.cn", "action": "created"},
                {"email": "li.wei@corp.cn", "action": "updated"}])
    DirectorySyncRun.objects.create(
        workspace=ws, channel="ldap", status="success", full_sync=False,
        triggered_by="beat", counts={"created": 0, "updated": 2,
                                      "disabled": 0, "skipped": 0, "failed": 0},
        detail=[])
    run_dry = DirectorySyncRun.objects.create(
        workspace=ws, channel="ldap", status="dry_run", is_dry_run=True,
        full_sync=True, triggered_by="dry_run",
        counts={"created": 0, "updated": 23, "disabled": 0, "skipped": 0,
                "failed": 0},
        detail=[{"email": "wang.fang@corp.cn", "action": "updated",
                 "reason": "质量部 → 测试中心"},
                {"email": "li.wei@corp.cn", "action": "updated",
                 "reason": "质量部 → 测试中心"},
                {"email": "zhao.liu@corp.cn", "action": "updated",
                 "reason": "质量部 → 测试中心"}],
        expires_at=timezone.now() + timedelta(hours=24))
    print(json.dumps({"config": str(cfg.id), "run_ok": str(run_ok.id),
                      "run_dry": str(run_dry.id), "ws_slug": ws.slug}))


if __name__ == "__main__":
    main()
