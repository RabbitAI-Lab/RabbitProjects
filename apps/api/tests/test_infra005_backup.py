"""INFRA-005 §5.1 备份体系单元用例（UT-08~12，Sprint-6 T6-03）。

口径：pg_dump/pg_restore 与 MinIO 上传在 UT 层 mock（dev 宿主无本地 pg_dump
客户端；真实全链验证归 restore-drill 演练脚本——UT 与演练不双轨重复）。
"""
from __future__ import annotations

import subprocess
from datetime import UTC, timedelta
from unittest import mock

import pytest
from django.core.cache import cache
from django.utils import timezone

from plane.bgtasks import backup as bk
from plane.db.models import BackupRun, SystemAdmin, User

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clean():
    BackupRun.objects.all().delete()
    yield
    BackupRun.objects.all().delete()


def _fake_pg_dump(size_bytes: int, *, restorable: bool = True):
    """构造 subprocess.run 替身：pg_dump 写指定大小文件；pg_restore --list
    按 restorable 决定退出码。"""
    def runner(cmd, **kwargs):
        if cmd[0] == "pg_dump":
            path = cmd[cmd.index("-f") + 1]
            with open(path, "wb") as fh:
                fh.write(b"\x00" * size_bytes)
            return subprocess.CompletedProcess(cmd, 0)
        if cmd[0] == "pg_restore":
            return subprocess.CompletedProcess(
                cmd, 0 if restorable else 1,
                stderr=b"magic block mismatch" if not restorable else b"")
        raise AssertionError(f"unexpected cmd {cmd}")
    return runner


def _mock_storage():
    return mock.patch.multiple(
        "plane.storage.minio", upload_fileobj=mock.DEFAULT,
        put_object=mock.DEFAULT, remove_object=mock.DEFAULT,
        list_objects=mock.DEFAULT)


# ── UT-08：三校验失败路径（小 dump / 坏 dump）+ 留痕 ──────────────────

def test_ut08_small_dump_rejected(monkeypatch):
    """校验①：dump 异常偏小（< BACKUP_MIN_SIZE_BYTES）→ FAILED 留痕不伪造产物。"""
    monkeypatch.setattr(bk.dj_settings, "BACKUP_MIN_SIZE_BYTES", 1024)
    monkeypatch.setattr(bk.subprocess, "run", _fake_pg_dump(size_bytes=64))
    with _mock_storage() as st, pytest.raises(bk.BackupVerificationError):
        bk.run_backup(kind="daily")
        del st
    run = BackupRun.objects.latest("started_at")
    assert run.status == "failed"
    assert "异常偏小" in run.error
    assert run.object_key == ""                    # 未上传未记键


def test_ut08_corrupt_dump_rejected(monkeypatch):
    """校验②：pg_restore --list 不可读（损坏归档）→ FAILED 留痕。"""
    monkeypatch.setattr(bk.dj_settings, "BACKUP_MIN_SIZE_BYTES", 64)
    monkeypatch.setattr(bk.subprocess, "run", _fake_pg_dump(
        size_bytes=128, restorable=False))
    with _mock_storage(), pytest.raises(bk.BackupVerificationError) as ei:
        bk.run_backup(kind="daily")
    assert "不可读" in str(ei.value)
    run = BackupRun.objects.latest("started_at")
    assert run.status == "failed" and "pg_restore" in run.error


def test_ut08_success_path_records_all_three_checks(monkeypatch):
    """成功路径：大小/SHA-256/对象键三留痕 + 配置快照随行（BR-10）。"""
    monkeypatch.setattr(bk.dj_settings, "BACKUP_MIN_SIZE_BYTES", 64)
    monkeypatch.setattr(bk.subprocess, "run", _fake_pg_dump(size_bytes=128))
    with _mock_storage() as st:
        key = bk.run_backup(kind="daily")
        assert st["upload_fileobj"].called          # dump 产物上传
        assert st["put_object"].called              # 配置快照随行
    run = BackupRun.objects.latest("started_at")
    assert run.status == "success"
    assert run.dump_size_bytes == 128
    assert len(run.checksum_sha256) == 64
    assert run.object_key == key and key.startswith("backups/pg/")
    import os
    assert not os.path.exists("/tmp/rp-backup-") or not any(
        f.startswith("rp-backup-") for f in os.listdir("/tmp"))  # 临时文件清理


# ── UT-09：连续 2 次失败告警 WS Admin ─────────────────────────────────

def test_ut09_streak_alerts_admins(monkeypatch):
    monkeypatch.setattr(bk.dj_settings, "BACKUP_MIN_SIZE_BYTES", 1024)
    monkeypatch.setattr(bk.subprocess, "run", _fake_pg_dump(size_bytes=8))
    admin = User.objects.create_user(email="bk-admin@rabbit.dev",
                                     password="Rabbit123!")
    SystemAdmin.objects.create(user=admin)
    with _mock_storage(), mock.patch(
            "plane.bgtasks.notifications.send_workspace_notification"
    ) as notify, pytest.raises(bk.BackupVerificationError):
        bk.run_backup(kind="daily")
    assert not notify.delay.called                  # 第 1 次失败不告警
    with _mock_storage(), mock.patch(
            "plane.bgtasks.notifications.send_workspace_notification"
    ) as notify, pytest.raises(bk.BackupVerificationError):
        bk.run_backup(kind="daily")
    # dev 库 system_admins 有共享行（坑 18 不清别人数据）——断言口径收窄到
    # 「本测试新建的 admin 收到 backup.failed_streak」而非全量次数
    mine = [c for c in notify.delay.call_args_list
            if c.kwargs.get("receiver_id") == str(admin.id)]
    assert len(mine) == 1
    assert mine[0].kwargs["event"] == "backup.failed_streak"


def test_ut09_success_breaks_streak(monkeypatch):
    """失败→成功→失败：streak 断开，第二次失败不告警。"""
    monkeypatch.setattr(bk.dj_settings, "BACKUP_MIN_SIZE_BYTES", 64)
    admin = User.objects.create_user(email="bk-admin2@rabbit.dev",
                                     password="Rabbit123!")
    SystemAdmin.objects.create(user=admin)
    bad, good = _fake_pg_dump(8), _fake_pg_dump(128)
    with _mock_storage(), pytest.raises(bk.BackupVerificationError):
        monkeypatch.setattr(bk.subprocess, "run", bad)
        bk.run_backup(kind="daily")
    with _mock_storage():
        monkeypatch.setattr(bk.subprocess, "run", good)
        bk.run_backup(kind="daily")
    with _mock_storage(), mock.patch(
            "plane.bgtasks.notifications.send_workspace_notification"
    ) as notify, pytest.raises(bk.BackupVerificationError):
        monkeypatch.setattr(bk.subprocess, "run", bad)
        bk.run_backup(kind="daily")
    assert not notify.delay.called


# ── UT-10：保留清理（31 天删 / 30 天内留）────────────────────────────

def test_ut10_cleanup_removes_only_expired():
    from datetime import datetime

    old = datetime(2026, 1, 1, tzinfo=UTC)
    fresh = timezone.now()
    with mock.patch(
        "plane.storage.minio.list_objects",
        return_value=[
            {"key": "backups/pg/old/db.dump", "size": 1, "last_modified": old},
            {"key": "backups/pg/new/db.dump", "size": 1, "last_modified": fresh},
            {"key": "backups/uploads/x", "size": 1, "last_modified": old},
        ]), mock.patch("plane.storage.minio.remove_object") as rm:
        removed = bk.cleanup_old_backups()
    # BR-08 为桶级 backups/ 前缀策略：过期 pg 与 uploads 镜像同删、新对象保留
    assert removed == 2
    assert {c.kwargs["key"] for c in rm.call_args_list} == {
        "backups/pg/old/db.dump", "backups/uploads/x"}


# ── UT-11：配置快照脱敏（BR-10 红线）─────────────────────────────────

def test_ut11_config_snapshot_no_plaintext_secrets(monkeypatch, settings):
    monkeypatch.setenv("SECRET_KEY", "super-secret-value-xyz")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "plain-pw-should-not-leak")
    body = bk.config_snapshot_body()
    blob = repr(body)
    assert "super-secret-value-xyz" not in blob
    assert "plain-pw-should-not-leak" not in blob
    assert "SECRET_KEY" in body["env_keys"]              # 键名在
    assert body["env_values_leaked"] is False
    # 迁移版本头随行（leaf 节点含 db.* 即可——具体编号随迭代推进）
    assert any(m.startswith("db.") for m in body["migration_head"].split(","))
    del settings


# ── UT-12：演练报告回写 ───────────────────────────────────────────────

def test_ut12_record_drill_writes_report():
    ok = BackupRun.objects.create(
        kind="daily", status="success",
        started_at=timezone.now() - timedelta(minutes=5),
        finished_at=timezone.now(), object_key="backups/pg/x/db.dump")
    assert bk.record_drill(str(ok.id), rto_seconds=412, smoke_passed=18,
                           smoke_total=18, notes="演练栈即弃")
    ok.refresh_from_db()
    r = ok.drill_report
    assert (r["rto_seconds"], r["smoke_passed"], r["smoke_total"]) == (412, 18, 18)
    assert r["notes"] == "演练栈即弃"
    # 演练也入账（BR-09）：kind=drill 留痕行指向源备份
    drill_row = BackupRun.objects.filter(kind="drill").get()
    assert drill_row.drill_report["source_run"] == str(ok.id)
    # 非 success 行不可回写
    failed = BackupRun.objects.create(kind="daily", status="failed",
                                      started_at=timezone.now())
    assert bk.record_drill(str(failed.id), rto_seconds=1,
                           smoke_passed=0, smoke_total=18, notes="") is False
    cache.clear()


# ── T6-05 运维端点：列表/触发限频/概览快照（SystemAdmin 鉴权）──────────

def test_ops_endpoints_permission_and_shape(db):
    from rest_framework.test import APIClient

    plain = User.objects.create_user(email="ops-plain@rabbit.dev",
                                     password="Rabbit123!")
    admin = User.objects.create_user(email="ops-admin@rabbit.dev",
                                     password="Rabbit123!")
    SystemAdmin.objects.create(user=admin)
    c = APIClient()
    c.force_authenticate(plain)
    assert c.get("/api/v1/instances/backups/").status_code == 403   # 非 SystemAdmin
    c.force_authenticate(admin)
    r = c.get("/api/v1/instances/backups/")
    assert r.status_code == 200 and r.json()["data"] == []
    # 概览快照：冻结配额单源镜像
    r = c.get("/api/v1/instances/rate-limit/summary/")
    assert r.status_code == 200
    snap = r.json()["data"]["config_snapshot"]
    assert snap["user_per_min"] == "60/min" and snap["bulk_per_min"] == "10/min"
    assert snap["share_unlock"] == "5/10m" and snap["edge"]["auth_per_min"] == 10
    assert r.json()["data"]["degraded"] is False


def test_ops_backup_trigger_throttled_within_10min(db, monkeypatch):
    from django.utils import timezone as tz
    from rest_framework.test import APIClient

    admin = User.objects.create_user(email="ops-trg@rabbit.dev",
                                     password="Rabbit123!")
    SystemAdmin.objects.create(user=admin)
    BackupRun.objects.create(kind="manual", status="success",
                             started_at=tz.now() - tz.timedelta(minutes=3))
    c = APIClient()
    c.force_authenticate(admin)
    r = c.post("/api/v1/instances/backups/trigger/", {}, format="json")
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"
    assert r.json()["error"]["details"][0]["field"] == "retry_after"
    # 10 分钟外的成功备份不拦截（429→202 快回包：任务入队 mock）
    BackupRun.objects.update(started_at=tz.now() - tz.timedelta(minutes=11))
    with mock.patch("plane.bgtasks.backup.daily_backup") as task:
        r = c.post("/api/v1/instances/backups/trigger/", {}, format="json")
    assert r.status_code == 202 and task.delay.called
