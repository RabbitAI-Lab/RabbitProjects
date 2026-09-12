"""目录同步归并服务（AUTH-011 §4.3，P4 R2）。

LDAP 与 SCIM 共用的归并裁决核心；所有动作幂等、可干跑。规则要点：
  · 邮箱小写归一为身份主键（BR-02）；无邮箱进 skipped；
  · 缺席判定仅全量批（BR-04 门控——增量批只有变更集，差集=全员误判）；
  · 双确认禁用（absence_count 1→2 跃迁执行一次，BR-04）；
  · 复活守卫（disabled_at_source="manual" 永不自动复活，BR-06 延伸）；
  · 保护名单（WS_OWNER / is_sync_protected 不禁用不改角色，BR-06）；
  · 真实执行 1000 条分片事务（片异常已提交片保留）；干跑单事务回滚；
  · 审计经 AUTH-010 record()（event_key=sha256 五元，第五元 run_id 范式）；
  · 席位满进待开通队列不中断批次（BR-03）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from hashlib import sha256

from django.db import transaction
from django.utils import timezone

from plane.audit.recorder import record
from plane.db.models import User, WorkspaceMember

logger = logging.getLogger("plane.directory")

#: 系统主体快照（BR-09）：id="system" 对齐 AUTH-010 actor_id 枚举（BR-15）
SYSTEM_DIRECTORY_SYNC = {"id": "system", "type": "system", "display_name": "目录同步（directory_sync）"}

#: 台账 detail 上限（10 万字符，超出截断标记，§4.2 迁移要点）
DETAIL_CHAR_LIMIT = 100_000


@dataclass
class SyncBuckets:
    created: list = field(default_factory=list)
    updated: list = field(default_factory=list)
    disabled: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    failed: list = field(default_factory=list)

    def counts(self) -> dict:
        return {k: len(getattr(self, k)) for k in ("created", "updated", "disabled", "skipped", "failed")}

    def serialize(self) -> list:
        out: list = []
        for action in ("created", "updated", "disabled", "skipped", "failed"):
            for item in getattr(self, action):
                out.append(
                    {**item, "action": action} if isinstance(item, dict) else {"action": action, "ref": str(item)}
                )
        text = str(out)
        if len(text) > DETAIL_CHAR_LIMIT:  # 截断保护
            return out[:100] + [{"action": "truncated", "note": "超出上限截断"}]
        return out


def _audit(run_id, action: str, user, detail: dict) -> None:
    """同步动作留痕（AUTH-010 契约；注册表 member 域 directory_* 四事件）。

    obj 统一传 dict 快照（record 的 _enrich_object 只收 dict/None——
    User 模型实例会撞 `.get` 属性错，见 2026-09-12 UT 排障）。"""
    record(
        event_key=sha256(f"directory|{user.id}|{action}|system|{run_id}".encode()).hexdigest()[:80],
        category="member",
        action=f"directory_{action}",
        workspace_id=detail.get("workspace_id"),
        actor=SYSTEM_DIRECTORY_SYNC,
        obj={"type": "user", "id": str(user.id), "name": getattr(user, "display_name", "")},
        detail={k: v for k, v in detail.items() if k != "workspace_id"},
    )


class DirectorySyncService:
    """LDAP 与 SCIM 共用的归并裁决核心；所有动作幂等、可干跑。"""

    ABSENCE_THRESHOLD = 2  # BR-04 双确认
    SHARD_SIZE = 1000  # 真实执行分片事务粒度（§4.8）；干跑单事务 ≤ 5000

    def __init__(self, workspace, channel: str, dry_run: bool = False, run_id: str | None = None):
        self.workspace = workspace
        self.channel = channel
        self.dry_run = dry_run
        self.run_id = run_id
        self.buckets = SyncBuckets()

    # ── 主入口 ────────────────────────────────────────────────────────

    def reconcile(self, entries: list[dict], *, full_sync: bool) -> SyncBuckets:
        """entries: 规范化后的目录记录 [{external_id, email, attrs...}]。

        缺席判定门控（BR-04）：仅 full_sync=True 批进入（增量批只有变更集，
        差集=全员误判缺席→批量禁用事故）；水位窗口漏检由当日全量批兜底。
        """
        seen_ids = {e["external_id"] for e in entries}
        if self.dry_run:  # 干跑：单事务保证回滚原子性
            with transaction.atomic():
                for entry in entries:
                    self._reconcile_one(entry)
                if full_sync:  # BR-04 门控：仅全量批判定缺席
                    self._reconcile_absent(seen_ids)
                transaction.set_rollback(True)  # 干跑：全部回滚（桶计数已记录）
        else:  # 真实执行：每 1000 条独立事务分片
            for i in range(0, len(entries), self.SHARD_SIZE):
                with transaction.atomic():
                    for entry in entries[i : i + self.SHARD_SIZE]:
                        self._reconcile_one(entry)
            if full_sync:  # 缺席判定需全员视图，末尾独立片
                with transaction.atomic():
                    self._reconcile_absent(seen_ids)
        return self.buckets

    def _reconcile_one(self, entry: dict) -> None:
        from plane.db.models import DirectoryUserMapping

        email = (entry.get("email") or "").strip().lower()
        if not email:  # BR-02：无邮箱跳过
            self.buckets.skipped.append({"external_id": entry["external_id"], "reason": "missing_email"})
            return
        mapping = (
            DirectoryUserMapping.objects.filter(
                workspace=self.workspace, channel=self.channel, external_id=entry["external_id"]
            )
            .select_related("user")
            .first()
        )
        if mapping is None:
            self._create_or_merge(entry, email)
            return
        if not mapping.user.is_active and mapping.disabled_at_source != "manual":
            self._reactivate(mapping)  # 复活 + 快照回放（BR-05）
            return
        if not mapping.user.is_active:
            # 手工禁用不同步复活（UT-08 伪代码级锁定）：manual 优先级最高
            return
        # 邮箱变更疑义：老映射缺席 + 新邮箱出现 → 人工裁决队列（§2.6，不自动归并）
        if mapping.email_snapshot.lower() != email:
            self._queue_manual_review(mapping, email)
            return
        if self._attrs_changed(mapping, entry):
            self._apply_update(mapping, entry)

    def _reconcile_absent(self, seen_ids: set) -> None:
        """仅 full_sync 批调用（reconcile 门控）；SCIM 无缺席概念。"""
        from plane.db.models import DirectoryChannel, DirectoryUserMapping

        if self.channel == DirectoryChannel.SCIM:
            return
        qs = (
            DirectoryUserMapping.objects.filter(workspace=self.workspace, channel=self.channel, user__is_active=True)
            .exclude(external_id__in=seen_ids)
            .exclude(disabled_at_source="manual")
        )
        for mapping in qs.select_related("user"):
            mapping.absence_count += 1
            if mapping.absence_count >= self.ABSENCE_THRESHOLD:
                self._disable(mapping, source="ldap_absent")
            else:
                if not self.dry_run:
                    mapping.save(update_fields=["absence_count", "updated_at"])

    # ── 开通 / 归并 ──────────────────────────────────────────────────

    def _create_or_merge(self, entry: dict, email: str) -> None:
        from plane.db.models import DirectoryUserMapping

        # 身份映射唯一约束辖含软删行（uq_directory_identity 无条件式）——
        # 撞软删行时复活而非 500/400（身份重建即复活语义）
        stale = DirectoryUserMapping.all_objects.filter(
            workspace=self.workspace, channel=self.channel, external_id=entry["external_id"]
        ).first()
        if stale is not None:
            stale.deleted_at = None
            stale.email_snapshot = email
            stale.absence_count = 0
            stale.disabled_at_source = ""
            stale.updated_at = timezone.now()
            stale.save(
                update_fields=["deleted_at", "email_snapshot", "absence_count", "disabled_at_source", "updated_at"]
            )
            self._ensure_membership(stale.user)
            self.buckets.created.append({"email": email, "reason": "reactivated"})
            return
        user = User.objects.filter(email=email).first()
        if user is None:
            # 席位检查（BR-03）：租户/工作空间满员 → 待开通队列，不中断批次
            if not self._seats_available():
                self._queue_pending_provision(entry)
                return
            user = User.objects.create_user(
                email=email,
                password=None,  # 不可用口令：强制走 SSO/邀请设密
                display_name=entry.get("display_name") or email.split("@")[0],
            )
            DirectoryUserMapping.objects.create(
                workspace=self.workspace,
                user=user,
                channel=self.channel,
                external_id=entry["external_id"],
                email_snapshot=email,
                identity_source="directory",
            )
            self.buckets.created.append({"email": email})
            if not self.dry_run:
                _audit(
                    self.run_id,
                    "created",
                    user,
                    {
                        "channel": self.channel,
                        "workspace_id": str(self.workspace.id),
                        "external_id": entry["external_id"],
                    },
                )
            self._ensure_membership(user)
            return
        # 本地账号归并（BR-10）：绑定映射、identity_source=merged、台账高亮
        DirectoryUserMapping.objects.create(
            workspace=self.workspace,
            user=user,
            channel=self.channel,
            external_id=entry["external_id"],
            email_snapshot=email,
            identity_source="merged",
        )
        self._ensure_membership(user)
        self.buckets.created.append({"email": email, "reason": "merged_local"})
        if not self.dry_run:
            _audit(
                self.run_id,
                "created",
                user,
                {
                    "channel": self.channel,
                    "workspace_id": str(self.workspace.id),
                    "external_id": entry["external_id"],
                    "merged": True,
                },
            )

    def _ensure_membership(self, user) -> None:
        """目录账号开通即入工作空间成员（默认 MEMBER 角色；幂等）。"""
        if self.dry_run:
            return
        from plane.db.models.roles import WorkspaceRole

        if not WorkspaceMember.objects.filter(workspace=self.workspace, member=user, deleted_at__isnull=True).exists():
            WorkspaceMember.objects.create(workspace=self.workspace, member=user, role=WorkspaceRole.MEMBER)

    def _seats_available(self) -> bool:
        """席位检查：工作空间软限 MAX_WORKSPACE_MEMBERS + 治理租户层（若启用）。"""
        from plane.db.services.workspace_member import MAX_WORKSPACE_MEMBERS

        active = WorkspaceMember.objects.filter(
            workspace=self.workspace, is_active=True, deleted_at__isnull=True
        ).count()
        if active >= MAX_WORKSPACE_MEMBERS:
            return False
        from plane.governance.enforcement import _governed_tenant_of, check_member_quota

        if _governed_tenant_of(self.workspace) is not None:
            try:
                check_member_quota(self.workspace)  # 满员抛 AppException
            except Exception:  # noqa: BLE001
                return False
        return True

    def _queue_pending_provision(self, entry: dict) -> None:
        from plane.db.models import DirectoryPendingAction

        DirectoryPendingAction.objects.update_or_create(
            workspace=self.workspace,
            kind="pending_provision",
            dedup_key=entry["external_id"],
            status="pending",
            defaults={
                "payload": {
                    "external_id": entry["external_id"],
                    "email": (entry.get("email") or "").strip().lower(),
                    "display_name": entry.get("display_name", ""),
                }
            },
        )
        self.buckets.skipped.append({"external_id": entry["external_id"], "reason": "seats_full"})
        logger.warning("directory.seats_full ws=%s external_id=%s", self.workspace.id, entry["external_id"])

    def _queue_manual_review(self, mapping, new_email: str) -> None:
        from plane.db.models import DirectoryPendingAction

        old = mapping.email_snapshot.lower()
        DirectoryPendingAction.objects.update_or_create(
            workspace=self.workspace,
            kind="manual_review",
            dedup_key=f"{old}->{new_email}",
            status="pending",
            defaults={"payload": {"old_email": old, "new_email": new_email, "mapping_id": str(mapping.id)}},
        )
        self.buckets.skipped.append({"email": old, "reason": "email_changed_manual_review"})

    # ── 变更 / 禁用 / 复活 ───────────────────────────────────────────

    def _attrs_changed(self, mapping, entry: dict) -> bool:
        snap = mapping.restore_snapshot.get("attrs") or {}
        return any((entry.get(k) or "") != (snap.get(k) or "") for k in ("display_name", "department", "title"))

    def _apply_update(self, mapping, entry: dict) -> None:
        user = mapping.user
        new_name = entry.get("display_name") or user.display_name
        if new_name != user.display_name:
            if not self.dry_run:
                user.display_name = new_name
                user.save(update_fields=["display_name", "updated_at"])
        snapshot = dict(mapping.restore_snapshot or {})
        snapshot["attrs"] = {k: entry.get(k, "") for k in ("display_name", "department", "title")}
        mapping.restore_snapshot = snapshot
        if not self.dry_run:
            mapping.save(update_fields=["restore_snapshot", "updated_at"])
            _audit(self.run_id, "updated", user, {"channel": self.channel, "workspace_id": str(self.workspace.id)})
        self.buckets.updated.append({"email": user.email})

    def _disable(self, mapping, *, source: str) -> None:
        user = mapping.user
        if self._is_protected(mapping):  # BR-06
            self.buckets.skipped.append({"email": user.email, "reason": "sync_protected"})
            return
        mapping.restore_snapshot = self._snapshot_membership(user)
        mapping.disabled_at_source = source
        user.is_active = False
        if not self.dry_run:
            user.save(update_fields=["is_active"])
            mapping.save()
            _audit(
                self.run_id,
                "disabled",
                user,
                {
                    "channel": self.channel,
                    "workspace_id": str(self.workspace.id),
                    "source": source,
                    "run_id": str(self.run_id),
                },
            )
        self.buckets.disabled.append({"email": user.email, "source": source})

    def _reactivate(self, mapping) -> None:
        user = mapping.user
        if not self.dry_run:
            user.is_active = True
            user.save(update_fields=["is_active"])
            self._replay_snapshot(mapping)
            mapping.absence_count = 0
            mapping.disabled_at_source = ""
            mapping.save(update_fields=["absence_count", "disabled_at_source", "updated_at"])
            _audit(self.run_id, "restored", user, {"channel": self.channel, "workspace_id": str(self.workspace.id)})
        self.buckets.updated.append({"email": user.email, "reason": "restored"})

    def _is_protected(self, mapping) -> bool:
        """BR-06：映射保护标记 或 用户当前为 WS_OWNER（角色实时判定）。"""
        if mapping.is_sync_protected:
            return True
        from plane.db.models.roles import WorkspaceRole

        return WorkspaceMember.objects.filter(
            workspace=self.workspace,
            member=mapping.user,
            is_active=True,
            deleted_at__isnull=True,
            role=WorkspaceRole.OWNER,
        ).exists()

    def _snapshot_membership(self, user) -> dict:
        rows = WorkspaceMember.objects.filter(workspace=self.workspace, member=user).values("department_id", "role")
        return {"memberships": list(rows), "attrs": (user and {}) or {}}

    def _replay_snapshot(self, mapping) -> None:
        snap = mapping.restore_snapshot or {}
        for row in snap.get("memberships", []):
            WorkspaceMember.objects.filter(workspace=self.workspace, member=mapping.user).update(
                department_id=row.get("department_id"), role=row.get("role") or 10
            )
