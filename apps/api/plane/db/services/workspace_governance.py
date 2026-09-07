"""工作空间治理服务（TEAM-003 §4.3——归档 / 全局标签 / 状态模板 / 活跃度）。

- 归档写保护中间件（§4.3.1）在 ``plane/base/middleware.py``（WorkspaceArchiveMiddleware）；
- 活跃度隐私红线（BR-09）：响应层无 per-user 行、键路径不含 user_id——
  per-member 维度一律 SQL 聚合内消化（登录命中表仅存 workspace×member×date 存在性）；
- 全局标签删除（BR-06）：软删 + 事务内为活跃 IssueLabel 写 name_snapshot；
  affected_issues = 直接引用 ∪ 覆盖链路所涉 Issue 的 distinct 数。
"""
from __future__ import annotations

import logging
import re
from datetime import timedelta

from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from plane.base.exception import AppException
from plane.db.models import (
    IssueActivity,
    IssueLabel,
    Label,
    Workspace,
    WorkspaceLabel,
    WorkspaceLoginDailyAggregate,
    WorkspaceMember,
    WorkspaceRole,
)

logger = logging.getLogger("plane.db.services.workspace_governance")

_LABEL_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_STATE_GROUPS = ("backlog", "unstarted", "started", "completed", "cancelled")
#: 内置默认状态模板（§2.3——default_states 快照为空时的兜底形态，version=0）
_BUILTIN_DEFAULT_STATES = {
    "name": "默认状态集",
    "version": 0,
    "groups": [
        {"group": "unstarted", "color": "#9CA3AF",
         "states": [{"name": "待办", "sequence": 1}]},
        {"group": "started", "color": "#3B82F6",
         "states": [{"name": "进行中", "sequence": 2}]},
        {"group": "completed", "color": "#10B981",
         "states": [{"name": "已完成", "sequence": 3}]},
        {"group": "cancelled", "color": "#6B7280",
         "states": [{"name": "已取消", "sequence": 4}]},
    ],
}
_LABEL_LIMIT = 100


def record_login_hits(user) -> None:
    """登录命中表写入（§4.3.3）：该用户全部活跃成员关系 × 今日，幂等。"""
    today = timezone.now().date()
    rows = WorkspaceMember.objects.filter(
        member=user, is_active=True, deleted_at__isnull=True).values_list(
        "workspace_id", "member_id")
    for ws_id, member_id in rows:
        WorkspaceLoginDailyAggregate.objects.get_or_create(
            workspace_id=ws_id, member_id=member_id, hit_date=today)


class WorkspaceGovernanceService:

    # ── 归档 / 恢复（§2.1/§4.2.1；OWNER 专属，ADR-0023 收窄）──
    @transaction.atomic
    def archive(self, ws: Workspace, *, actor) -> dict:
        self._require_owner(ws, actor)
        if ws.archived_at:  # 幂等（BR-01）
            return {"slug": ws.slug, "archived_at": ws.archived_at.isoformat(),
                    "affected_projects": ws.projects.filter(deleted_at__isnull=True).count(),
                    "idempotent": True}
        ws.archived_at = timezone.now()
        ws.archived_by = actor
        ws.save(update_fields=["archived_at", "archived_by", "updated_at"])
        affected = ws.projects.filter(deleted_at__isnull=True).count()
        transaction.on_commit(lambda: self._notify_all(ws, "workspace.archived"))
        return {"slug": ws.slug, "archived_at": ws.archived_at.isoformat(),
                "affected_projects": affected, "idempotent": False}

    @transaction.atomic
    def restore(self, ws: Workspace, *, actor) -> dict:
        self._require_owner(ws, actor)
        if not ws.archived_at:  # 幂等
            return {"slug": ws.slug, "archived_at": None, "idempotent": True}
        ws.archived_at = None
        ws.archived_by = None
        ws.save(update_fields=["archived_at", "archived_by", "updated_at"])
        transaction.on_commit(lambda: self._notify_all(ws, "workspace.restored"))
        return {"slug": ws.slug, "archived_at": None, "idempotent": False}

    @staticmethod
    def _require_owner(ws: Workspace, actor) -> None:
        row = WorkspaceMember.objects.filter(
            workspace=ws, member=actor, is_active=True, deleted_at__isnull=True).first()
        if row is None or row.role != WorkspaceRole.OWNER:
            raise AppException("PERM_WORKSPACE_OWNER_REQUIRED", message="仅工作空间所有者可操作")

    @staticmethod
    def _notify_all(ws: Workspace, event: str) -> None:
        from plane.bgtasks.notifications import send_workspace_notification

        for uid in WorkspaceMember.objects.filter(
                workspace=ws, is_active=True, deleted_at__isnull=True,
        ).values_list("member_id", flat=True):
            send_workspace_notification.delay(
                receiver_id=str(uid), event=event,
                context={"workspace_slug": ws.slug, "workspace_name": ws.name},
                title="工作空间已归档，进入只读" if event.endswith("archived") else "工作空间已恢复",
            )

    # ── 全局标签（§2.2/§4.2.3）──
    @staticmethod
    def list_labels(ws: Workspace) -> tuple[list[dict], dict]:
        rows = (WorkspaceLabel.objects
                .filter(workspace=ws, deleted_at__isnull=True)
                .order_by("created_at", "id"))
        data = [{
            "id": str(r.id), "name": r.name, "color": r.color,
            "description": r.description,
            "created_at": r.created_at.isoformat(),
        } for r in rows[:_LABEL_LIMIT]]
        total = rows.count()
        return data, {"count": total, "limit": _LABEL_LIMIT,
                      "remaining": max(0, _LABEL_LIMIT - total)}

    @transaction.atomic
    def create_label(self, ws: Workspace, *, payload: dict, actor) -> dict:
        name = str(payload.get("name") or "").strip()
        color = str(payload.get("color") or "").strip()
        if not name or len(name) > 50:
            raise AppException("VALIDATION_ERROR", message="标签名必填且 ≤50 字符",
                               details=[{"field": "name", "code": "INVALID"}])
        if not _LABEL_COLOR_RE.fullmatch(color):
            raise AppException("VALIDATION_ERROR", message="颜色必须为 #RRGGBB",
                               details=[{"field": "color", "code": "INVALID"}])
        if WorkspaceLabel.objects.filter(workspace=ws, name=name,
                                         deleted_at__isnull=True).exists():
            raise AppException("RESOURCE_ALREADY_EXISTS", message="同名全局标签已存在",
                               details=[{"field": "name", "code": "UNIQUE"}])
        row = WorkspaceLabel.objects.create(
            workspace=ws, name=name, color=color,
            description=str(payload.get("description") or "")[:255], created_by=actor)
        return {"id": str(row.id), "name": row.name, "color": row.color,
                "description": row.description,
                "created_at": row.created_at.isoformat()}

    @transaction.atomic
    def update_label(self, ws: Workspace, label: WorkspaceLabel, *, payload: dict) -> dict:
        fields = ["updated_at"]
        if "name" in payload:
            name = str(payload["name"]).strip()
            if not name or len(name) > 50:
                raise AppException("VALIDATION_ERROR", message="标签名必填且 ≤50 字符")
            if WorkspaceLabel.objects.filter(workspace=ws, name=name,
                                             deleted_at__isnull=True).exclude(
                    pk=label.pk).exists():
                raise AppException("RESOURCE_ALREADY_EXISTS", message="同名全局标签已存在")
            label.name = name
            fields.append("name")
        if "color" in payload:
            color = str(payload["color"]).strip()
            if not _LABEL_COLOR_RE.fullmatch(color):
                raise AppException("VALIDATION_ERROR", message="颜色必须为 #RRGGBB")
            label.color = color
            fields.append("color")
        if "description" in payload:
            label.description = str(payload.get("description") or "")[:255]
            fields.append("description")
        label.save(update_fields=fields)
        return {"id": str(label.id), "name": label.name, "color": label.color,
                "description": label.description}

    @transaction.atomic
    def delete_label(self, ws: Workspace, label: WorkspaceLabel, *, actor) -> dict:
        """软删 + IssueLabel 快照写入（BR-06/§4.3.4，事务内）。"""
        affected = self.affected_issue_ids(label)
        label.deleted_at = timezone.now()
        label.updated_by = actor
        label.save(update_fields=["deleted_at", "updated_by", "updated_at"])
        # 快照：活跃 IssueLabel 行（经覆盖链路 Label）写入删除时刻的名字
        override_ids = list(Label.objects.filter(
            overrides_global_id=label.id).values_list("id", flat=True))
        IssueLabel.objects.filter(
            label_id__in=override_ids, deleted_at__isnull=True,
        ).update(name_snapshot=label.name)
        return {"id": str(label.id),
                "deleted_at": label.deleted_at.isoformat(),
                "affected_issues": len(affected)}

    @staticmethod
    def affected_issue_ids(label: WorkspaceLabel) -> set:
        """直接引用 ∪ 覆盖链路（Label.overrides_global_id）所涉 Issue 的并集——
        同一 Issue 双行计 1（§4.2.3 口径；软删行不计）。"""
        override_ids = list(Label.objects.filter(
            overrides_global_id=label.id).values_list("id", flat=True))
        direct = IssueLabel.objects.filter(
            label__origin="global", deleted_at__isnull=True,
        )  # 直接引用经下发创建的项目级行（见 merged_labels 的创建路径）
        # 直接引用口径：项目 Label 行 origin=global 且名字与全局一致（下发投影）
        projected = Label.objects.filter(
            project__workspace_id=label.workspace_id, origin="global",
            name=label.name, deleted_at__isnull=True).values_list("id", flat=True)
        ids = set(
            IssueLabel.objects.filter(
                label_id__in=list(projected) + override_ids,
                deleted_at__isnull=True,
                issue__deleted_at__isnull=True,
            ).values_list("issue_id", flat=True))
        del direct
        return ids

    # ── 基础状态模板（§2.3/§4.2.4）──
    @staticmethod
    def get_default_states(ws: Workspace) -> dict:
        snap = ws.default_states if isinstance(ws.default_states, dict) else {}
        if snap.get("groups"):
            return {"name": snap.get("name", "默认状态集"),
                    "version": int(snap.get("version", 1)),
                    "groups": snap["groups"]}
        return dict(_BUILTIN_DEFAULT_STATES)

    @transaction.atomic
    def put_default_states(self, ws: Workspace, *, payload: dict, actor) -> dict:
        groups = self._validate_state_groups(payload)
        snap = ws.default_states if isinstance(ws.default_states, dict) else {}
        version = int(snap.get("version", 0)) + 1
        ws.default_states = {"name": str(payload.get("name") or "默认状态集"),
                             "version": version, "groups": groups}
        ws.updated_by = actor
        ws.save(update_fields=["default_states", "updated_by", "updated_at"])
        return {"name": ws.default_states["name"], "version": version, "groups": groups}

    @staticmethod
    def _validate_state_groups(payload: dict) -> list[dict]:
        groups = payload.get("groups")
        if not isinstance(groups, list):
            raise AppException("VALIDATION_ERROR", message="groups 必须为数组")
        seen, seq_expect = set(), 1
        for g in groups:
            if not isinstance(g, dict) or g.get("group") not in _STATE_GROUPS:
                raise AppException("VALIDATION_ERROR", message="group 必须为五组之一",
                                   details=[{"field": "groups", "code": "INVALID"}])
            if g["group"] in seen:
                raise AppException("VALIDATION_ERROR", message=f"group {g['group']} 重复")
            seen.add(g["group"])
            states = g.get("states")
            if not isinstance(states, list) or not states:
                raise AppException("VALIDATION_ERROR", message="每组至少 1 个状态",
                                   details=[{"field": f"groups.{g['group']}.states",
                                             "code": "INVALID"}])
            for s in states:
                name = str(s.get("name") or "").strip()
                if not name or len(name) > 50:
                    raise AppException(
                        "VALIDATION_ERROR", message="状态名非空且 ≤50 字符",
                        details=[{"field": "states.name", "code": "INVALID"}])
                if s.get("sequence") != seq_expect:
                    raise AppException(
                        "VALIDATION_INVALID_PARAM", message="sequence 必须从 1 连续递增",
                        details=[{"field": "states.sequence", "code": "INVALID",
                                  "message": f"期望 {seq_expect}"}])
                seq_expect += 1
        if seen != set(_STATE_GROUPS):
            raise AppException("VALIDATION_ERROR",
                               message="五组（backlog/unstarted/started/completed/cancelled）各需 ≥1")
        return groups

    # ── 活跃度（§2.4/§4.2.5——BR-09 只聚合）──
    @staticmethod
    def activity_stats(ws: Workspace, *, days: int) -> dict:
        today = timezone.now().date()
        since7, since30 = today - timedelta(days=6), today - timedelta(days=29)
        total = WorkspaceMember.objects.filter(
            workspace=ws, is_active=True, deleted_at__isnull=True).count()
        member_ids7 = set(IssueActivity.objects.filter(
            issue__project__workspace_id=ws.id, created_at__date__gte=since7,
        ).values_list("actor_id", flat=True))
        member_ids7 |= set(WorkspaceLoginDailyAggregate.objects.filter(
            workspace=ws, hit_date__gte=since7).values_list("member_id", flat=True))
        member_ids30 = set(IssueActivity.objects.filter(
            issue__project__workspace_id=ws.id, created_at__date__gte=since30,
        ).values_list("actor_id", flat=True))
        member_ids30 |= set(WorkspaceLoginDailyAggregate.objects.filter(
            workspace=ws, hit_date__gte=since30).values_list("member_id", flat=True))
        member_ids30 |= member_ids7  # 7d ⊆ 30d 口径

        # 周分桶（近 N 天按 ISO 周聚合贡献次数 → 桶人数）
        week_rows = list(IssueActivity.objects.filter(
            issue__project__workspace_id=ws.id,
            created_at__date__gte=today - timedelta(days=days - 1),
        ).values("actor_id").annotate(c=Count("id")))
        hits = dict(WorkspaceLoginDailyAggregate.objects.filter(
            workspace=ws, hit_date__gte=today - timedelta(days=days - 1),
        ).values_list("member_id").annotate(c=Count("id")))
        per_member: dict = {}
        for r in week_rows:
            if r["actor_id"]:
                per_member[r["actor_id"]] = per_member.get(r["actor_id"], 0) + r["c"]
        for mid, c in hits.items():
            per_member[mid] = per_member.get(mid, 0) + c * 0  # 命中只补活跃人集，不叠计数
        # 当前 ISO 周（只出当周一行——跨周分布归前端按 days 请求逐周拉取）
        def _bucket(c: int) -> str:
            if c == 0:
                return "0"
            if c <= 5:
                return "1_5"
            if c <= 20:
                return "6_20"
            return "gt_20"
        week_key = f"{today.isocalendar().year}-W{today.isocalendar().week:02d}"
        buckets = {"0": 0, "1_5": 0, "6_20": 0, "gt_20": 0}
        all_member_ids = set(WorkspaceMember.objects.filter(
            workspace=ws, is_active=True, deleted_at__isnull=True,
        ).values_list("member_id", flat=True))
        for mid in all_member_ids:
            buckets[_bucket(per_member.get(mid, 0))] += 1

        # 登录天数直方图（30d 窗口）
        login_days = {
            r["member_id"]: r["d"]
            for r in WorkspaceLoginDailyAggregate.objects.filter(
                workspace=ws, hit_date__gte=since30,
            ).values("member_id").annotate(d=Count("id"))
        }
        hist = {"1": 0, "2_3": 0, "4_5": 0, "ge_6": 0}
        for mid in all_member_ids:
            d = login_days.get(mid, 0)
            if 1 <= d <= 1:
                hist["1"] += 1
            elif d <= 3:
                hist["2_3"] += 1
            elif d <= 5:
                hist["4_5"] += 1
            elif d >= 6:
                hist["ge_6"] += 1

        # top_actions：issue vs comment 双源占比（30d）
        issue_c = IssueActivity.objects.filter(
            issue__project__workspace_id=ws.id, created_at__date__gte=since30).count()
        from plane.db.models import IssueComment
        comment_c = IssueComment.objects.filter(
            issue__project__workspace_id=ws.id, created_at__date__gte=since30).count()
        denom = issue_c + comment_c
        top = {"issue": round(issue_c / denom, 2), "comment": round(comment_c / denom, 2)} \
            if denom else {"issue": None, "comment": None}
        return {
            "active_members_7d": len({str(m) for m in member_ids7 & all_member_ids}),
            "active_members_30d": len({str(m) for m in member_ids30 & all_member_ids}),
            "total_members": total,
            "contribution_distribution": [{"week": week_key, "buckets": buckets}],
            "login_days_histogram": hist,
            "top_actions": top,
        }
