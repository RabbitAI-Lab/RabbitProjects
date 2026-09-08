"""模板库服务（WF-005 §4.2/§4.3）——实例化 + 两步下发 + 解锁申请闭环。

实例化 = 深拷贝（BR-03）：graph_snapshot → 项目三表实体；状态映射（BR-05）：
模板状态键 → 项目既有 State（复用）或新建（缺省）。审批流可选实例化
（同名后缀 §template，BR-10）。
"""
from __future__ import annotations

import logging

from django.db import transaction

from plane.db.models import (
    Project,
    State,
    TemplateDistribution,
    TemplateUnlockRequest,
    User,
    Workflow,
    WorkflowState,
    WorkflowTemplate,
    WorkflowTransition,
)
from plane.db.models.state import State as StateModel

logger = logging.getLogger(__name__)


class TemplateError(Exception):
    def __init__(self, code: str, status: int, message: str = "", sub: str | None = None):
        self.code, self.status, self.message, self.sub = code, status, message, sub
        super().__init__(message)


#: 预设四套模板（§2.4：研发需求 / 缺陷修复 / 测试上线 / 日常任务）——is_builtin。
BUILTIN_TEMPLATES: list[dict] = [
    {
        "name": "研发需求流程",
        "description": "需求从提出到上线的标准研发流（评审 → 开发 → 提测 → 上线）",
        "states": [
            {"key": "todo", "name": "待办", "group": "unstarted", "color": "#9CA3AF", "is_initial": True},
            {"key": "review", "name": "评审中", "group": "started", "color": "#3B82F6"},
            {"key": "dev", "name": "开发中", "group": "started", "color": "#3B82F6"},
            {"key": "test", "name": "测试中", "group": "started", "color": "#F59E0B"},
            {"key": "done", "name": "已完成", "group": "completed", "color": "#10B981"},
        ],
        "transitions": [
            {"from": "todo", "to": "review", "name": "提交评审"},
            {"from": "review", "to": "dev", "name": "评审通过"},
            {"from": "dev", "to": "test", "name": "提测"},
            {"from": "test", "to": "done", "name": "验收通过"},
        ],
    },
    {
        "name": "缺陷修复流程",
        "description": "缺陷提交 → 定位 → 修复 → 验证关闭",
        "states": [
            {"key": "todo", "name": "待办", "group": "unstarted", "color": "#9CA3AF", "is_initial": True},
            {"key": "locate", "name": "定位中", "group": "started", "color": "#3B82F6"},
            {"key": "fixing", "name": "修复中", "group": "started", "color": "#F59E0B"},
            {"key": "done", "name": "已修复", "group": "completed", "color": "#10B981"},
        ],
        "transitions": [
            {"from": "todo", "to": "locate", "name": "开始定位"},
            {"from": "locate", "to": "fixing", "name": "定位完成"},
            {"from": "fixing", "to": "done", "name": "修复验证"},
        ],
    },
    {
        "name": "测试上线流程",
        "description": "上线审批单：提单 → 审批 → 灰度 → 全量",
        "states": [
            {"key": "todo", "name": "待办", "group": "unstarted", "color": "#9CA3AF", "is_initial": True},
            {"key": "approved", "name": "已审批", "group": "started", "color": "#3B82F6"},
            {"key": "canary", "name": "灰度中", "group": "started", "color": "#F59E0B"},
            {"key": "done", "name": "已上线", "group": "completed", "color": "#10B981"},
        ],
        "transitions": [
            {"from": "todo", "to": "approved", "name": "审批通过"},
            {"from": "approved", "to": "canary", "name": "开始灰度"},
            {"from": "canary", "to": "done", "name": "全量上线"},
        ],
    },
    {
        "name": "日常任务流程",
        "description": "轻量三态：待办 → 进行中 → 完成",
        "states": [
            {"key": "todo", "name": "待办", "group": "unstarted", "color": "#9CA3AF", "is_initial": True},
            {"key": "doing", "name": "进行中", "group": "started", "color": "#3B82F6"},
            {"key": "done", "name": "已完成", "group": "completed", "color": "#10B981"},
        ],
        "transitions": [
            {"from": "todo", "to": "doing", "name": "开始"},
            {"from": "doing", "to": "done", "name": "完成"},
        ],
    },
]


def seed_builtin_templates(workspace) -> int:
    """预设四套（幂等）：不存在则建 is_builtin=True。返回新建数。"""
    created = 0
    for spec in BUILTIN_TEMPLATES:
        _, was_created = WorkflowTemplate.objects.get_or_create(
            workspace=workspace, is_builtin=True, name=spec["name"],
            defaults={"description": spec["description"], "status": "published",
                      "version": 1, "graph_snapshot": spec})
        created += int(was_created)
    return created


class TemplateInstantiator:
    """graph_snapshot → 项目三表实体（深拷贝 BR-03）+ 状态映射（BR-05）。"""

    @transaction.atomic
    def instantiate(self, *, template: WorkflowTemplate, project: Project, actor: User,
                    issue_type=None, state_mapping: dict | None = None) -> Workflow:
        snap = template.graph_snapshot or {}
        state_mapping = state_mapping or {}
        wf = Workflow.objects.create(
            project=project, issue_type=issue_type,
            name=f"{template.name} v{template.version}",
            source_template=template, status=Workflow.Status.DRAFT,
            created_by=actor)
        key_map: dict[str, WorkflowState] = {}
        for s in snap.get("states") or []:
            state = self._reuse_or_create_state(project, s, state_mapping, issue_type, actor)
            node = WorkflowState.objects.create(
                workflow=wf, state=state, is_initial=bool(s.get("is_initial")),
                layout_x=0.0, layout_y=0.0)
            key_map[s["key"]] = node
        for t in snap.get("transitions") or []:
            if t["from"] not in key_map or t["to"] not in key_map:
                continue  # 快照残缺防御——保存侧校验为主
            WorkflowTransition.objects.create(
                workflow=wf, from_state=key_map[t["from"]], to_state=key_map[t["to"]],
                name=t.get("name") or "未命名", created_by=actor)
        return wf

    @staticmethod
    def _reuse_or_create_state(project, spec: dict, mapping: dict, issue_type, actor) -> State:
        """BR-05 状态映射：模板键 → 项目既有 State（同名优先）或新建。"""
        target_id = mapping.get(spec["key"])
        if target_id:
            existing = State.objects.filter(pk=target_id, project=project).first()
            if existing is not None:
                return existing
        existing = State.objects.filter(
            project=project, name=spec["name"], issue_type=issue_type,
            deleted_at__isnull=True).first()
        if existing is not None:
            return existing
        return StateModel.objects.create(
            project=project, name=spec["name"], color=spec.get("color", "#9CA3AF"),
            group=spec.get("group", "unstarted"),
            is_default=bool(spec.get("is_initial")),
            issue_type=issue_type, created_by=actor)


def distribute(*, template: WorkflowTemplate, project: Project, actor) -> TemplateDistribution:
    """两步下发（§4.3）：受理 pending_confirm（锁定引用落账）——确认后 active。"""
    dist, _ = TemplateDistribution.objects.get_or_create(
        template=template, project=project,
        defaults={"template_version": template.version, "locked": True,
                  "status": "pending_confirm"})
    return dist


def confirm_distribution(*, dist: TemplateDistribution, project: Project, actor,
                         issue_type=None, state_mapping: dict | None = None) -> Workflow:
    """确认实例化（第二步）：建 Workflow 草稿 + 状态 active + applied_workflow 溯源。"""
    wf = TemplateInstantiator().instantiate(
        template=dist.template, project=project, actor=actor,
        issue_type=issue_type, state_mapping=state_mapping)
    dist.status = "active"
    dist.applied_workflow = wf
    dist.state_mapping = state_mapping or {}
    dist.save(update_fields=["status", "applied_workflow", "state_mapping", "updated_at"])
    return wf


def request_unlock(*, dist: TemplateDistribution, project: Project, actor,
                   kind: str, reason: str) -> TemplateUnlockRequest:
    """项目侧申请（§4.4）：每项目至多一条待审（部分唯一约束兜底 409）。"""
    if not (reason or "").strip():
        raise TemplateError("VALIDATION_ERROR", 400, sub="REQUIRED", message="申请理由必填")
    if TemplateUnlockRequest.objects.filter(
            project=project, status="pending", deleted_at__isnull=True).exists():
        raise TemplateError("RESOURCE_ALREADY_EXISTS", 409, message="已有待审申请")
    return TemplateUnlockRequest.objects.create(
        distribution=dist, project=project, kind=kind, reason=reason[:255],
        created_by=actor)
