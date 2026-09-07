"""备份体系 Celery 任务（INFRA-005 §4.4.1/4.4.3——Sprint-6 T6-03）。

链路：beat 03:07 → worker daily_backup() → pg_dump -Fc（-Fc 自定义格式支持
pg_restore 并行恢复）→ 三校验（大小阈值 + pg_restore --list 目录可读 + SHA-256
记录，BR-07）→ MinIO rp-backups 桶（SSE-AES256，MinIO 无 KMS 自动降级明文）
→ 配置随行快照（BR-10：迁移版本头 + 环境变量键清单**只记键名与是否设置，
绝不明文入库**）→ BackupRun 留痕；连续 2 次失败告警 WS Admin（BR-07）。

与规格的偏差：任务走既有默认 ``celery`` 队列（worker -Q 白名单已含），未另建
``backup`` 专用队列——避免 rabbitmq 队列拓扑变更（坑 15：同名队列参数不可变），
时序影响为零（beat 投递与队列归属无关）。
"""
from __future__ import annotations

import hashlib
import logging
import os
import subprocess
from pathlib import Path

from celery import shared_task
from django.conf import settings as dj_settings
from django.utils import timezone

from plane.db.models import BackupRun

logger = logging.getLogger("plane.bgtasks.backup")

BACKUP_BUCKET = "rp-backups"
BACKUP_PREFIX = "backups/pg"          # BR-08：独立于用户配额的前缀
#: 30 天保留（BR-08；MinIO ilm 为第一道，本任务为 beat 双保险第二道）
RETENTION_DAYS = 30
#: dump 校验和分块读取大小
_CHUNK = 1024 * 1024


def _pg_conn() -> dict[str, str]:
    """DATABASES default → pg_dump 连接参数（PGPASSWORD 走进程 env，不落命令行）。"""
    db = dj_settings.DATABASES["default"]
    return {
        "host": db.get("HOST") or "localhost",
        "port": str(db.get("PORT") or 5432),
        "user": db.get("USER") or "rp",
        "dbname": db.get("NAME") or "rabbit_projects",
        "password": db.get("PASSWORD") or "",
    }


def pg_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PGPASSWORD"] = _pg_conn()["password"]
    return env


def sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _migration_head() -> str:
    """迁移版本头（BR-10 随行——恢复目标防跳版，preflight 校验口径同源）。"""
    from django.db import connection
    from django.db.migrations.loader import MigrationLoader

    loader = MigrationLoader(connection, replace_migrations=False)
    return ",".join(f"{app_label}.{name}"
                    for app_label, name in sorted(loader.graph.leaf_nodes()))


def config_snapshot_body() -> dict:
    """配置快照（BR-10）：环境变量只记键名与是否设置，绝不明文入库。"""
    from django.db import connection

    seeds: list[str] = []
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT count(*) FROM projects WHERE identifier = 'DEMO'")
            seeds = ["demo-history"] if cur.fetchone()[0] else []
    except Exception:  # noqa: BLE001 —— 快照尽力而为，不阻断备份主链
        pass
    return {
        "migration_head": _migration_head(),
        "django_settings_module": os.environ.get("DJANGO_SETTINGS_MODULE", ""),
        "env_keys": sorted(
            k for k in os.environ
            if k.startswith(("POSTGRES_", "REDIS_", "RABBITMQ", "MINIO_",
                             "AWS_", "CELERY_", "SECRET_", "INTEGRATION_"))
        ),
        # 凭证值一律不出现在快照——只有键名清单（评审红线 BR-10）
        "env_values_leaked": False,
        "seed_markers": seeds,
        "taken_at": timezone.now().isoformat(),
        "tool": "infra-005/config-snapshot/1",
    }


def upload_config_snapshot(stamp: str) -> str:
    """配置快照落 rp-backups（backups/config/{stamp}/snapshot.json）。"""
    import json

    from plane.storage import minio as storage

    key = f"backups/config/{stamp}/snapshot.json"
    body = json.dumps(config_snapshot_body(), ensure_ascii=False,
                      indent=2, default=str).encode()
    storage.put_object(bucket=BACKUP_BUCKET, key=key, body=body,
                       content_type="application/json")
    return key


def notify_admins_if_streak(threshold: int = 2) -> bool:
    """连续 ≥threshold 次失败 → 通知 WS Admin（BR-07；返回是否触发）。"""
    from plane.bgtasks.notifications import send_workspace_notification
    from plane.db.models import SystemAdmin

    recent = list(BackupRun.objects.order_by("-started_at")
                  .values_list("status", flat=True)[:threshold])
    if len(recent) < threshold or any(s != "failed" for s in recent):
        return False
    admins = list(SystemAdmin.objects.filter(
        is_active=True, deleted_at__isnull=True
    ).values_list("user_id", flat=True))
    for admin_id in admins:
        if admin_id:
            send_workspace_notification.delay(
                receiver_id=str(admin_id),
                event="backup.failed_streak",
                context={"streak": threshold,
                         "last_started_at": timezone.now().isoformat()},
                title=f"备份连续 {threshold} 次失败，请检查 worker 日志")
    return bool(admins)


class BackupVerificationError(Exception):
    """三校验失败（BR-07）——dump 异常偏小 / 目录不可读。"""


def run_backup(kind: str = "daily") -> str:
    """执行一次备份全链（daily beat 与 manual 端点共用；返回对象键）。

    供 ``daily_backup`` 任务与 admin 端点（T6 后续）调用；UT-08 断言两次失败
    路径（小 dump / 坏 dump）与 BackupRun 留痕字段。
    """
    from plane.storage import minio as storage

    run = BackupRun.objects.create(kind=kind, started_at=timezone.now())
    local = ""
    try:
        stamp = timezone.now().strftime("%Y%m%d-%H%M%S")
        local = f"/tmp/rp-backup-{stamp}.dump"
        conn = _pg_conn()
        dockerized = bool(os.environ.get("BACKUP_PG_CMD"))
        # docker exec 模式：连接参数与 -f 输出路径都在容器内坐标系——host 用
        # 容器名、dump 落容器内 /tmp 再 docker cp 出来（宿主无客户端也能同链）
        if dockerized:
            container = os.environ["BACKUP_PG_CMD"].split()[-1]
            inner = f"/tmp/{os.path.basename(local)}"
            subprocess.run(
                [*os.environ["BACKUP_PG_CMD"].split(), "pg_dump", "-Fc",
                 "-U", conn["user"], "-f", inner, conn["dbname"]],
                check=True, timeout=1500)
            subprocess.run(["docker", "cp", f"{container}:{inner}", local],
                           check=True, timeout=300)
            subprocess.run(
                [*os.environ["BACKUP_PG_CMD"].split(), "rm", "-f", inner],
                check=False)
        else:
            subprocess.run(
                ["pg_dump", "-Fc",
                 "-h", conn["host"], "-p", conn["port"],
                 "-U", conn["user"], "-f", local, conn["dbname"]],
                check=True, env=pg_env(), timeout=1500)
        size = os.path.getsize(local)
        if size < getattr(dj_settings, "BACKUP_MIN_SIZE_BYTES", 1_048_576):
            # 三校验①：dump 异常偏小（几乎必然是空库/错库/半途截断）
            raise BackupVerificationError(f"dump 异常偏小 {size}B")
        # 三校验②：目录可读（pg_restore --list 真实解析归档头）
        if dockerized:
            inner = f"/tmp/{os.path.basename(local)}"
            container = os.environ["BACKUP_PG_CMD"].split()[-1]
            subprocess.run(["docker", "cp", local, f"{container}:{inner}"],
                           check=True, timeout=300)
            verify = subprocess.run(
                [*os.environ["BACKUP_PG_CMD"].split(), "pg_restore", "--list", inner],
                capture_output=True, timeout=120)
            subprocess.run(
                [*os.environ["BACKUP_PG_CMD"].split(), "rm", "-f", inner],
                check=False)
        else:
            verify = subprocess.run(["pg_restore", "--list", local],
                                    capture_output=True, timeout=120,
                                    env=pg_env())
        if verify.returncode != 0:
            raise BackupVerificationError(
                f"pg_restore --list 不可读: {verify.stderr.decode()[:300]}")
        sha = sha256_of(local)                       # 三校验③：校验和记录
        key = f"{BACKUP_PREFIX}/{stamp}/db.dump"
        storage.upload_fileobj(bucket=BACKUP_BUCKET, key=key, path=local)
        upload_config_snapshot(stamp)                # BR-10 配置随行
        run.finish_success(size, sha, key)
        logger.info("backup_ok kind=%s key=%s size=%s sha=%s",
                    kind, key, size, sha[:12])
        return key
    except Exception as exc:  # noqa: BLE001 —— 失败留痕 + 告警 + 重抛（beat 可见）
        run.finish_failure(str(exc))
        logger.error("event=backup_failed kind=%s err=%s", kind, exc)
        notify_admins_if_streak(2)
        raise
    finally:
        if local:
            Path(local).unlink(missing_ok=True)


@shared_task(bind=True, soft_time_limit=1800, max_retries=1)
def daily_backup(self) -> str:
    """每日全量（beat 03:07 错峰——BR-07；soft_time_limit 1800 对齐 §2.5
    「备份单次 > 30 分钟告警」的硬上限，超时即失败留痕）。"""
    return run_backup(kind="daily")


@shared_task
def cleanup_old_backups() -> int:
    """BR-08 双保险第二道：>30 天的 backups/ 前缀对象删除（第一道是 MinIO
    ilm 规则）。幂等——ilm 已删过的对象此处列表为空自然零删除。"""
    from datetime import timedelta

    from plane.storage import minio as storage

    cutoff = timezone.now() - timedelta(days=RETENTION_DAYS)
    removed = 0
    for obj in storage.list_objects(bucket=BACKUP_BUCKET, prefix="backups/"):
        if obj["last_modified"] < cutoff:
            storage.remove_object(bucket=BACKUP_BUCKET, key=obj["key"])
            removed += 1
    if removed:
        logger.info("backup_cleanup_removed=%s", removed)
    return removed


def record_drill(backup_run_id: str, *, rto_seconds: int,
                 smoke_passed: int, smoke_total: int, notes: str) -> bool:
    """演练报告回写（BR-09 留痕；restore-drill.sh 经 admin 端点调用——
    UT-12 断言 drill_report 四字段齐）。返回是否命中成功备份行。"""
    from plane.db.models import BackupRun

    run = (BackupRun.objects
           .filter(pk=backup_run_id, status=BackupRun.Status.SUCCESS)
           .first())
    if run is None:
        return False
    run.drill_report = {
        "rto_seconds": int(rto_seconds),
        "smoke_passed": int(smoke_passed),
        "smoke_total": int(smoke_total),
        "notes": notes[:500],
        "recorded_at": timezone.now().isoformat(),
    }
    run.save(update_fields=["drill_report", "updated_at"])
    # 演练也入账（BR-09 可追溯）：kind=drill 的留痕行
    BackupRun.objects.create(
        kind=BackupRun.Kind.DRILL, status=BackupRun.Status.SUCCESS,
        started_at=timezone.now(), finished_at=timezone.now(),
        object_key=run.object_key,
        drill_report={"source_run": str(run.id),
                      "rto_seconds": int(rto_seconds),
                      "smoke_passed": int(smoke_passed),
                      "smoke_total": int(smoke_total),
                      "notes": notes[:500]})
    return True
