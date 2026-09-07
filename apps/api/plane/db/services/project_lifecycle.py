"""项目生命周期服务（PROJ-003 §4.3.1——状态迁移唯一入口，BR-01）。

四态状态机 + 转换守卫矩阵（§2.2）+ 模板实例化（§4.3.2）+ 副本重开（§2.5）。

- 生命周期事件经 ``record_project_activity`` 独立轨道落 project 域 Activity
  （T5-02 已建；verb ∈ {created, updated}、field=status、milestone 读取位）；
- ``project.*`` Webhook 扇出为 INTG-002（T5-05）挂点——本模块发 ``on_commit``
  钩子位 ``_dispatch_webhook_events``，INTG-002 落地前为 no-op（预留对齐）；
- closed 单向门（BR-05）；draft 静默（BR-03——draft 态转换前不通知不扇出）。
"""
from __future__ import annotations

import logging
import uuid

from django.db import transaction
from django.utils import timezone

from plane.base.exception import AppException
from plane.db.models import (
    CustomFieldDefinition,
    FileFolder,
    Issue,
    Label,
    Project,
    ProjectMember,
    ProjectRole,
    ProjectStatusLog,
    ProjectTemplate,
    State,
)

logger = logging.getLogger("plane.db.services.project_lifecycle")

#: 转换守卫矩阵（§2.2）——允许边的全集（closed 无出边，BR-05）
TRANSITION_GUARDS: dict[str, set[str]] = {
    "draft": {"active"},
    "active": {"archived", "closed"},
    "archived": {"active", "closed"},
    "closed": set(),
}

_OPEN_GROUPS = ("unstarted", "started")


class ProjectLifecycleService:
    """状态迁移唯一入口——View 直改 ``project.status`` 即 CI 失败（BR-01，AC-06）。"""

    @transaction.atomic
    def transition(self, project: Project, *, to_status: str, actor,
                   force: bool = False, reason: str = "") -> dict:
        """幂等转换（BR-02：当前态=目标态短路返回快照）。"""
        if to_status not in TRANSITION_GUARDS:
            raise AppException(
                "VALIDATION_ERROR", message="目标状态非法",
                details=[{"field": "to_status", "code": "NOT_A_CHOICE",
                          "message": "must be one of: draft, active, archived, closed"}])
        current = project.status
        if current == to_status:  # 幂等短路
            return {"id": str(project.id), "status": current,
                    "transitioned_at": None, "affected_issues": 0, "idempotent": True}
        allowed = TRANSITION_GUARDS.get(current, set())
        if to_status not in allowed:
            raise AppException(
                "RESOURCE_TRANSITION_INVALID", message="Cannot transition project status",
                details=[{"field": "to_status", "code": "INVALID_TRANSITION",
                          "message": f"cannot transition from {current} to {to_status}; "
                                     f"allowed: [{', '.join(sorted(allowed))}]",
                          "current_status": current,
                          "allowed_targets": sorted(allowed)}])

        meta: dict = {}
        affected = 0
        if current == "draft" and to_status == "active":
            self._guard_activate(project)  # BR-14 identifier 复检 + 至少 1 状态
        if to_status == "closed":
            affected = self._guard_close(project, force=force, actor=actor)
            meta = {"open_count": affected, "forced": force}

        project.status = to_status
        project.updated_by = actor
        project.save(update_fields=["status", "updated_by", "updated_at"])
        ProjectStatusLog.objects.create(
            project=project, from_status=current, to_status=to_status,
            operator=actor, reason=reason, meta=meta)
        self._emit_lifecycle_event(project, current, to_status, actor)
        return {"id": str(project.id), "status": to_status,
                "transitioned_at": timezone.now().isoformat(),
                "affected_issues": affected, "idempotent": False}

    # ── 守卫（§2.2）──
    @staticmethod
    def _guard_activate(project: Project) -> None:
        if not State.objects.filter(project=project).exists():
            raise AppException(
                "RESOURCE_STATE_INVALID", message="项目缺少状态配置",
                details=[{"field": "to_status", "code": "NO_STATES",
                          "message": "draft → active 需要至少 1 个状态（State）"}])
        clash = (Project.objects
                 .filter(workspace_id=project.workspace_id,
                         identifier=project.identifier, status="active",
                         deleted_at__isnull=True)
                 .exclude(pk=project.pk).exists())  # BR-14 复检（draft 期间他人占用）
        if clash:
            raise AppException(
                "RESOURCE_ALREADY_EXISTS", message="项目标识已被占用",
                details=[{"field": "identifier", "code": "UNIQUE",
                          "message": "draft 期间该 identifier 已被其他 active 项目占用"}])

    @staticmethod
    def _guard_close(project: Project, *, force: bool, actor=None) -> int:
        """开放任务计数=0 或 force（BR-06）；force 批量迁「已取消」默认态（BR-07）。"""
        open_qs = Issue.objects.filter(
            project=project, deleted_at__isnull=True, archived_at__isnull=True,
            state__group__in=_OPEN_GROUPS)
        open_count = open_qs.count()
        if open_count == 0:
            return 0
        if not force:
            raise AppException(
                "RESOURCE_STATE_INVALID", message="Project has open issues",
                details=[{"field": "to_status", "code": "OPEN_ISSUES",
                          "message": f"{open_count} open issues; pass force=true "
                                     "to cancel them in bulk",
                          "open_count": open_count,
                          "list_url": f"/issues?state_group={','.join(_OPEN_GROUPS)}"}])
        cancelled = State.objects.filter(
            project=project, group="cancelled").order_by("sort_order").first()
        if cancelled is None:
            raise AppException(
                "RESOURCE_STATE_INVALID", message="项目缺少「已取消」状态",
                details=[{"field": "to_status", "code": "NO_CANCELLED_STATE",
                          "message": "force 关闭需要「已取消」状态承载开放任务"}])
        from plane.bgtasks.issue_activity import enqueue_activity

        epoch = timezone.now().timestamp() * 1000.0
        ids = list(open_qs.values_list("id", flat=True))
        Issue.objects.filter(id__in=ids).update(state=cancelled)
        force_actor_id = getattr(actor, "id", None) or project.created_by_id
        assert force_actor_id is not None
        for iid in ids:  # BR-07：逐条 Activity（actor=操作者，单事务）
            enqueue_activity(
                issue_id=iid, actor_id=force_actor_id,
                verb="updated", epoch=epoch,
                before={"state": "open"}, after={"state": "cancelled"},
                comment="项目关闭时批量取消")
        logger.info("lifecycle.force_close project=%s cancelled=%d", project.id, len(ids))
        return len(ids)

    # ── 生命周期事件（project 域 Activity + 里程碑；Webhook 挂点预留）──
    @staticmethod
    def _emit_lifecycle_event(project: Project, from_status: str, to_status: str, actor) -> None:
        from plane.bgtasks.project_activity import enqueue_project_activity

        # §2.3 菱形节点全集：created/activated/restored/archived/closed（迁移面 = 三目标态）
        milestone = to_status in ("active", "archived", "closed")
        enqueue_project_activity(
            project_id=project.id, actor_id=actor.id if actor else None,
            verb="updated", field="status",
            old_value=from_status or None, new_value=to_status,
            comment="milestone" if milestone else "",
        )
        transaction.on_commit(
            lambda: _dispatch_webhook_events(project, from_status, to_status))

    # ── 状态历史（BR-13 只增）──
    @staticmethod
    def status_logs(project: Project) -> list[dict]:
        rows = (ProjectStatusLog.objects.filter(project=project)
                .select_related("operator").order_by("-created_at"))
        return [{
            "id": str(r.id), "from_status": r.from_status, "to_status": r.to_status,
            "operator": ({"id": str(r.operator_id), "display_name": r.operator.display_name}
                         if r.operator_id else None),
            "reason": r.reason, "meta": r.meta,
            "transitioned_at": r.created_at.isoformat(),
        } for r in rows]

    # ── 模板实例化（§4.3.2 四件套，BR-11 单事务）──
    @staticmethod
    def apply_template(project: Project, tpl: ProjectTemplate, *, actor) -> dict:
        from plane.db.models import Label as LabelModel

        with transaction.atomic():
            state_ids = []
            for s in tpl.states_snapshot:
                state_ids.append(State.objects.create(
                    project=project,
                    name=s["name"], group=s["group"], color=s.get("color", "#9CA3AF"),
                    sort_order=s.get("sort_order", 65535.0),
                    is_default=bool(s.get("is_default")), created_by=actor))
            # Project 无 default_state 列——默认态由 State.is_default 承载（快照直传）
            LabelModel.objects.bulk_create([
                LabelModel(project=project, name=lb["name"], color=lb.get("color", "#9CA3AF"),
                           created_by=actor)
                for lb in tpl.labels_snapshot])
            CustomFieldDefinition.objects.bulk_create([
                CustomFieldDefinition(project=project, workspace=project.workspace,
                                      created_by=actor, **f)
                for f in tpl.fields_snapshot])
            by_path: dict[str, FileFolder] = {}
            for f in tpl.folders_snapshot:
                parent = by_path.get(f.get("parent_path") or "")
                by_path[f["name"] if not f.get("parent_path")
                        else f"{f.get('parent_path')}/{f['name']}"] = (
                    FileFolder.objects.create(
                        project=project, name=f["name"],
                        parent=parent, created_by=actor))
        return {"states": len(state_ids), "labels": len(tpl.labels_snapshot),
                "fields": len(tpl.fields_snapshot), "folders": len(tpl.folders_snapshot)}

    # ── 副本重开（§2.5）──
    @transaction.atomic
    def duplicate(self, source: Project, *, actor) -> Project:
        """closed 源 → draft 副本（四件套 + 成员 + 描述附注；任务不复制——
        「可选勾选任务」为 UI 层后续调 TASK-009 复制服务的编排，端点最小闭环）。"""
        if source.status != "closed":
            raise AppException(
                "RESOURCE_STATE_INVALID", message="仅已关闭项目可副本重开",
                details=[{"field": "status", "code": "INVALID",
                          "message": f"duplicate 要求源项目为 closed（当前 {source.status}）"}])
        base = source.identifier
        identifier, n = f"{base[:9]}-C", 1
        while Project.objects.filter(workspace_id=source.workspace_id,
                                     identifier=identifier,
                                     deleted_at__isnull=True).exists():
            n += 1
            identifier = f"{base[:9]}-C{n}"
        copy = Project.objects.create(
            workspace=source.workspace, name=f"{source.name}（副本）",
            identifier=identifier, status="draft",
            description=f"本项目为 {source.name} 的副本（原项目已关闭）\n{source.description}",
            created_by=actor)
        for s in State.objects.filter(project=source).order_by("sort_order"):
            State.objects.create(project=copy, name=s.name,
                                 group=s.group, color=s.color, sort_order=s.sort_order,
                                 is_default=s.is_default, created_by=actor)
        for lb in Label.objects.filter(project=source):
            Label.objects.create(project=copy, name=lb.name, color=lb.color, created_by=actor)
        ProjectMember.objects.create(
            project=copy, member=actor, role=ProjectRole.ADMIN, created_by=actor)
        ProjectStatusLog.objects.create(
            project=copy, from_status="", to_status="draft", operator=actor)
        return copy


def actor_id_of(project: Project) -> uuid.UUID | None:
    """project.current_user_actor 由视图注入（无则回退 created_by）。"""
    return getattr(project, "current_user_actor", None) or project.created_by_id


def _dispatch_webhook_events(project: Project, from_status: str, to_status: str) -> None:
    """project.* Webhook 扇出挂点——INTG-002（T5-05）落地后接线；先 no-op 预留。"""
    try:
        from plane.bgtasks.event_publisher import dispatch_event  # noqa: F401
        # INTG-002 落地后：dispatch_event(f"project.{…}", payload, rooms)
    except Exception:  # noqa: BLE001
        pass
