"""目录同步 Celery 任务（AUTH-011 §4.4，P4 R2）。

ldap_sync：LDAPS paged 拉取 → DirectorySyncService.reconcile → 台账落库；
水位线批内取 max、跨批只进不退（UT-17）；重试 3 次仍败只告警不动作（BR-12）。
目录维护：待办 30 天过期 beat（§4.2 状态机）。

队列注：与治理域同因（dev worker 只消费默认队列）走默认队列，「独立
directory 队列」为部署期路由配置（ADR-0032#1 同款登记）。
"""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger("plane.directory")


class LdapConnectionError(Exception):
    """LDAP 连接/绑定失败（重试面；BR-12 只告警不动作）。"""


class LdapClient:
    """LDAP paged 拉取（ldap3；缺库/不可达抛 LdapConnectionError）。

    `fetch` 可注入替换（测试/干跑同构注入伪目录）——任务体只依赖
    normalize 后的 entries 契约 [{external_id, email, display_name, ...,
    usn_changed}]。
    """

    PAGE_SIZE = 500

    def __init__(self, config, fetch=None):
        self.config = config
        self._fetch = fetch

    def paged_search(self, *, full_sync: bool) -> list[dict]:
        if self._fetch is not None:
            return self._fetch(full_sync=full_sync)
        try:
            import ldap3  # noqa: F401, PLC0415 —— 可用性探测（成功才走真实路径）
        except ImportError as exc:
            raise LdapConnectionError("未安装 ldap3（uv add ldap3）") from exc
        from ldap3 import ALL, SUBTREE, Connection, Server, Tls

        server = Server(
            self.config.server_uri,
            get_info=ALL,
            use_ssl=True,
            tls=Tls(validate=True) if self.config.use_starttls else None,
        )
        entries: list[dict] = []
        with Connection(server, user=self.config.bind_dn, password=self._resolve_secret(), auto_bind=True) as conn:
            cookie = None
            while True:
                conn.search(
                    self.config.base_dn,
                    self.config.user_filter,
                    search_scope=SUBTREE,
                    attributes=["*"],
                    paged_size=self.PAGE_SIZE,
                    paged_cookie=cookie,
                )
                for e in conn.entries:
                    entries.append(self.normalize(e))
                cookie = (
                    conn.result.get("controls", {}).get("1.2.840.113556.1.4.319", {}).get("value", {}).get("cookie")
                )
                if not cookie:
                    return entries

    def _resolve_secret(self) -> str:
        """密保库句柄解析（BR-08：DB 只存句柄；dev 兜底 env LDAP_BIND_SECRET）。"""
        import os

        ref = self.config.bind_secret_ref
        if ref.startswith("env:"):
            return os.environ.get(ref[4:], "")
        return os.environ.get("LDAP_BIND_SECRET", "")

    @staticmethod
    def normalize(entry) -> dict:
        """ldap3 条目 → 规范化契约（external_id=entryUUID/objectGUID）。"""
        attrs = entry.entry_attributes_as_dict if hasattr(entry, "entry_attributes_as_dict") else dict(entry)
        external_id = (
            attrs.get("entryUUID") or attrs.get("objectGUID") or [entry.entry_dn if hasattr(entry, "entry_dn") else ""]
        )[0]
        mails = attrs.get("mail") or []
        return {
            "external_id": str(external_id),
            "email": (mails[0] if mails else "").strip().lower(),
            "display_name": str((attrs.get("displayName") or [""])[0]),
            "department": str((attrs.get("department") or [""])[0]),
            "title": str((attrs.get("title") or [""])[0]),
            "usn_changed": str((attrs.get("uSNChanged") or ["0"])[0]),
            "active": True,
        }


def _serialize_buckets(buckets) -> list:
    return buckets.serialize()


@shared_task(bind=True, autoretry_for=(LdapConnectionError,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def ldap_sync(
    self,
    config_id: str,
    *,
    full_sync: bool = False,
    dry_run: bool = False,
    triggered_by: str = "beat",
    client_fetch=None,
) -> str:
    from plane.db.models import DirectorySyncRun, LdapDirectoryConfig

    config = LdapDirectoryConfig.objects.select_related("workspace").get(id=config_id, is_enabled=True)
    run = DirectorySyncRun.objects.create(
        workspace=config.workspace,
        channel="ldap",
        is_dry_run=dry_run,
        full_sync=full_sync,
        triggered_by=triggered_by,
        expires_at=timezone.now() + timedelta(hours=24) if dry_run else None,
    )
    try:
        entries = LdapClient(config, fetch=client_fetch).paged_search(full_sync=full_sync)
        from plane.directory.services import DirectorySyncService

        svc = DirectorySyncService(config.workspace, "ldap", dry_run=dry_run, run_id=str(run.id))
        buckets = svc.reconcile(entries, full_sync=full_sync)
        run.status = "dry_run" if dry_run else "success"
        run.counts = buckets.counts()
        run.detail = _serialize_buckets(buckets)
        if not dry_run and entries:
            # 水位线：批内取 max（乱序防回退）+ 跨批只进不退（UT-17）
            batch_max = str(max(int(e.get("usn_changed", 0) or 0) for e in entries))
            if not config.sync_cursor or int(batch_max) > int(config.sync_cursor or 0):
                config.sync_cursor = batch_max
                config.save(update_fields=["sync_cursor", "updated_at"])
    except Exception as exc:  # noqa: BLE001 —— 台账必须落失败原因
        run.status, run.error = "failed", str(exc)[:2000]
        run.save()
        if self.request.retries >= self.max_retries:  # 重试 3 次仍败才告警（§2.3 时序）
            logger.error("directory.sync_failed ws=%s run=%s err=%s", config.workspace_id, run.id, run.error[:200])
        raise
    run.save()
    return str(run.id)


@shared_task
def directory_pending_expiry() -> dict:
    """待办 30 天过期（§4.2 状态机；幂等可重入）。"""
    from plane.db.models import DirectoryPendingAction

    cutoff = timezone.now() - timedelta(days=30)
    with transaction.atomic():
        updated = DirectoryPendingAction.objects.filter(status="pending", created_at__lt=cutoff).update(
            status="expired"
        )
    return {"expired": updated}


@shared_task
def directory_beat_dispatcher() -> dict:
    """beat 入口：对全部 enabled LDAP 配置按周期派发增量（全量 03:30 由
    独立 cron 派发 full_sync=True；重叠防护=同 config 无 running 增量 run
    则跳过）。"""
    from plane.db.models import DirectorySyncRun, LdapDirectoryConfig

    dispatched = skipped = 0
    for config in LdapDirectoryConfig.objects.filter(is_enabled=True):
        running = DirectorySyncRun.objects.filter(
            workspace=config.workspace, channel="ldap", status="running", full_sync=False, is_dry_run=False
        ).exists()
        if running:
            skipped += 1
            continue
        ldap_sync.delay(str(config.id), full_sync=False, triggered_by="beat")
        dispatched += 1
    return {"dispatched": dispatched, "skipped": skipped}
