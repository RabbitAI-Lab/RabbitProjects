"""流转守卫矩阵（WF-004 §4.1/§4.2）——四类守卫执行器与拦截响应构造。

固定执行序 required_fields → estimate_required → blocker_completed →
role_allowed（字段类先于权限类）；全量收集失败项，主码按 §2.5 分流
（403 > 400 必填 > 409 阻塞），details[] 与主码无关地全量携带（§4.5 冻结契约）。

BR-10：守卫求值只读——执行器禁止写库。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from django.db import connection

from plane.db.models import CustomFieldDefinition, Issue, ProjectMember, State, SystemAdmin, User
from plane.db.models.roles import ProjectRole, WorkspaceRole
from plane.db.services.issue_transition_guard import BLOCKER_SQL
from plane.workflow.services import TransitionError

#: 固定执行序（§2.2：字段类先于权限类——补齐表单优先暴露业务缺口）
EXECUTION_ORDER = ("required_fields", "estimate_required", "blocker_completed", "role_allowed")

#: 系统字段 → 空值判定与元数据（§4.9 矩阵的内置字段行；引擎字段不可守卫不可锁）
SYSTEM_FIELD_META: dict[str, dict[str, Any]] = {
    "assignees": {"label": "负责人", "type": "members"},
    "target_date": {"label": "截止日期", "type": "date"},
    "start_date": {"label": "开始日期", "type": "date"},
    "estimate_minutes": {"label": "预估工时", "type": "estimate"},
    "priority": {"label": "优先级", "type": "priority"},
    "labels": {"label": "标签", "type": "labels"},
}

#: 引擎字段（BR-09：不可被锁定配置；亦不可进 required_fields）
ENGINE_FIELDS = frozenset({"state", "parent", "sequence_id", "project", "issue_type"})


@dataclass
class GuardFailure:
    """守卫失败 → 逐项映射为拦截响应 error.details[] 的条目（§4.5）。"""

    type: str
    items: list[dict] = field(default_factory=list)


def failures_payload(failures: list[GuardFailure]) -> list[dict]:
    return [item for f in failures for item in f.items]


def guard_error(failures: list[GuardFailure]) -> TransitionError:
    """主码分流（§2.5）：role 403 > required 400 > estimate 400 > blocked 409。

    details[] 全量与主码无关地携带——前端按 guard 键分区渲染。
    """
    details = failures_payload(failures)
    if any(i.get("guard") == "role_allowed" for i in details):
        return TransitionError("PERM_TRANSITION_NOT_ALLOWED", 403, details=details,
                               message="当前角色不可执行此流转")
    if any(i.get("guard") == "required_fields" for i in details):
        return TransitionError("VALIDATION_REQUIRED_FIELD_MISSING", 400, details=details,
                               message="流转被守卫拦截：必填字段缺失")
    if any(i.get("guard") == "estimate_required" for i in details):
        return TransitionError("VALIDATION_ESTIMATE_REQUIRED", 400, details=details,
                               message="流转被守卫拦截：预估工时未填")
    return TransitionError("RESOURCE_TRANSITION_BLOCKED", 409, details=details,
                           message="流转被守卫拦截：前置任务未完成")


# ── 有效角色判定（服务层；与 permissions.py 视图层同口径）────────────

def effective_project_role(actor: User, project_id) -> int | None:
    """rbac §7.4：SYSTEM_ADMIN / WS_ADMIN+ → 隐式 PROJ_ADMIN；否则显式成员角色。"""
    if SystemAdmin.objects.filter(user=actor, is_active=True).exists():
        return ProjectRole.ADMIN
    pm = ProjectMember.objects.filter(
        project_id=project_id, member=actor, is_active=True).values_list("role", flat=True).first()
    if pm is not None:
        return pm
    from plane.db.models import Project, WorkspaceMember

    ws_id = Project.objects.filter(pk=project_id).values_list("workspace_id", flat=True).first()
    if ws_id is None:
        return None
    role = WorkspaceMember.objects.filter(
        workspace_id=ws_id, member=actor, is_active=True).values_list("role", flat=True).first()
    if role is not None and role >= WorkspaceRole.ADMIN:
        return ProjectRole.ADMIN
    return None


def has_any_role(actor: User, project_id, roles: list[str]) -> bool:
    """role_allowed 判定：角色码（PROJ_* 大写形）或 custom:<role_id>（Sprint 8 数据源，
    现恒不命中）。走有效角色判定（§4.2 注：不比较原始角色字符串）。"""
    level = effective_project_role(actor, project_id)
    if level is None:
        return False
    code_by_level: dict[int, str] = {
        int(ProjectRole.ADMIN): "PROJ_ADMIN",
        int(ProjectRole.CONTRIBUTOR): "PROJ_CONTRIBUTOR",
        int(ProjectRole.COMMENTER): "PROJ_COMMENTER",
        int(ProjectRole.VIEWER): "PROJ_VIEWER",
    }
    my = code_by_level.get(int(level))
    for r in roles:
        if r == my:
            return True
        if r.startswith("custom:"):
            continue  # Sprint 8 AUTH-008 自定义角色数据源落地前恒不命中
    return False


# ── 字段取值与空值判定（§4.9 矩阵）──────────────────────────────────

def resolve_field(issue: Issue, field_key: str,
                  definitions: dict[str, CustomFieldDefinition] | None = None) -> Any:
    """内置字段直取 / cf_* 走 custom_fields（值层不触校验——守卫只读）。"""
    if field_key == "assignees":
        return list(issue.assignees.values_list("id", flat=True)) if issue.pk else []
    if field_key == "labels":
        return list(issue.labels.values_list("id", flat=True)) if issue.pk else []
    if hasattr(issue, field_key):
        return getattr(issue, field_key)
    if field_key.startswith("cf_"):
        return (issue.custom_fields or {}).get(field_key)
    return None


def is_empty(field_key: str, value: Any,
             definitions: dict[str, CustomFieldDefinition] | None = None) -> bool:
    """空值判定矩阵（§4.9）：文本空白=空；数值 null=空（0 合法）；多选/成员空数组=空。"""
    if value is None:
        return True
    d = (definitions or {}).get(field_key)
    if d is not None:  # cf_*：按 field_type 映射
        if d.field_type in (CustomFieldDefinition.FieldType.TEXT,
                            CustomFieldDefinition.FieldType.TEXTAREA,
                            CustomFieldDefinition.FieldType.URL):
            return not str(value).strip()
        if d.field_type in (CustomFieldDefinition.FieldType.MULTI_SELECT,
                            CustomFieldDefinition.FieldType.MEMBER_MULTI,
                            CustomFieldDefinition.FieldType.CASCADE,
                            CustomFieldDefinition.FieldType.RELATION,
                            CustomFieldDefinition.FieldType.ATTACHMENT):
            return not isinstance(value, (list, tuple)) or len(value) == 0
        if d.field_type == CustomFieldDefinition.FieldType.CHECKBOX:
            return False  # 布尔不设必填（false 是值）
        return False
    # 内置字段
    if field_key in ("assignees", "labels"):
        return not value
    if field_key in ("name",):
        return not str(value).strip()
    return False  # 数值/日期/单选：null 已在首行返回


def field_meta(field_key: str, project,
               definitions: dict[str, CustomFieldDefinition] | None = None) -> dict:
    """补录控件元数据（§4.9 注册表）：label/type/options——前端零特判渲染。"""
    d = (definitions or {}).get(field_key)
    if d is not None:
        meta = {"label": d.name, "type": d.field_type}
        if d.field_type in (CustomFieldDefinition.FieldType.SELECT,
                            CustomFieldDefinition.FieldType.MULTI_SELECT):
            meta["options"] = d.options
        return meta
    base = SYSTEM_FIELD_META.get(field_key, {"label": field_key, "type": "text"})
    return dict(base)


# ── 四类守卫执行器（§4.2）───────────────────────────────────────────

def check_required_fields(config: dict, *, issue: Issue, actor: User,
                          to_state: State | None, payload: dict | None,
                          definitions) -> GuardFailure | None:
    fields = config.get("fields") or []

    def value_of(f: str):
        if payload and f in payload:  # guard_payload 优先（BR-05 单请求补齐）
            return payload[f]
        return resolve_field(issue, f, definitions)

    missing = [f for f in fields if is_empty(f, value_of(f), definitions)]
    if not missing:
        return None
    return GuardFailure("required_fields", [
        {"field": f, "code": "REQUIRED", "guard": "required_fields",
         "message": f"缺少必填字段：{field_meta(f, issue.project, definitions)['label']}",
         "meta": field_meta(f, issue.project, definitions)} for f in missing])


def check_estimate_required(config: dict, *, issue: Issue, actor: User,
                            to_state: State | None, payload: dict | None,
                            definitions) -> GuardFailure | None:
    threshold = int(config.get("min_minutes") or 1)
    minutes = issue.estimate_minutes
    if payload and "estimate_minutes" in payload:
        minutes = payload["estimate_minutes"]
    if (minutes or 0) >= threshold:
        return None
    return GuardFailure("estimate_required", [{
        "field": "estimate_minutes", "code": "REQUIRED", "guard": "estimate_required",
        "message": "需填写预估工时后方可流转",
        "meta": {"label": "预估工时", "type": "estimate", "min_minutes": threshold}}])


def check_blocker_completed(config: dict, *, issue: Issue, actor: User,
                            to_state: State | None, payload: dict | None,
                            definitions) -> GuardFailure | None:
    """TASK-005 §4.3.3 读时判定的守卫化封装（同一 SQL、同一判定域）。"""
    if to_state is None or issue.state is None:
        return None
    if to_state.group != "completed" or issue.state.group == "completed":
        return None  # 判定域与 assert_completable 完全一致：仅「迁入 completed」拦截
    with connection.cursor() as cursor:
        cursor.execute(BLOCKER_SQL, {"me": issue.id})
        rows = cursor.fetchall()
    if not rows:
        return None
    return GuardFailure("blocker_completed", [{
        "field": "blockers", "code": "BLOCKED_BY", "guard": "blocker_completed",
        "message": f"{len(rows)} 个前置任务未完成",
        "blockers": [{"id": str(r[0]),
                      "issue_key": f"{issue.project.identifier}-{r[1]}",
                      "name": r[2], "state_group": r[3] or "unstarted"} for r in rows]}])


def check_role_allowed(config: dict, *, issue: Issue, actor: User,
                       to_state: State | None, payload: dict | None,
                       definitions) -> GuardFailure | None:
    roles = config.get("roles") or []
    if has_any_role(actor, issue.project_id, roles):
        return None
    return GuardFailure("role_allowed", [{
        "field": "transition", "code": "ROLE_REQUIRED", "guard": "role_allowed",
        "message": "当前角色不可执行此流转", "required_roles": roles}])


#: type → 执行器（注册表形态；BR-10 只读约束由实现保证——四执行器零写库）
GUARD_EXECUTORS = {
    "required_fields": check_required_fields,
    "estimate_required": check_estimate_required,
    "blocker_completed": check_blocker_completed,
    "role_allowed": check_role_allowed,
}


def run_guards(guards: list[dict], *, issue: Issue, actor: User, to_state: State | None,
               payload: dict | None = None, definitions=None) -> list[GuardFailure]:
    """全量求值（§4.1）：隐式 blocker 仅注入迁入 completed 组的边；固定序；全量收集。"""
    effective = list(guards or [])
    if (to_state is not None and to_state.group == "completed"
            and not any(g.get("type") == "blocker_completed" for g in effective)):
        effective.append({"type": "blocker_completed", "config": {}})
    failures: list[GuardFailure] = []
    for g in sorted(effective, key=lambda x: EXECUTION_ORDER.index(x["type"])):
        if (g.get("config") or {}).get("enabled") is False:
            continue  # 显式关闭（需 workflow.manage，保存侧 BR-14 校验）
        fn = GUARD_EXECUTORS[g["type"]]
        failure = fn(g.get("config") or {}, issue=issue, actor=actor,
                     to_state=to_state, payload=payload, definitions=definitions)
        if failure is not None:
            failures.append(failure)
    return failures


def guard_config_issues(guards: list[dict], project) -> list[dict]:
    """保存侧校验（PUT graph/ 内调用；BR-02/03/09 + §2.6 上限）。返回 details[] 项。"""
    from plane.db.services.field_schema import resolve_fields

    issues: list[dict] = []
    if len(guards) > 8:
        issues.append({"field": "guards", "code": "LIMIT",
                       "message": "单边守卫数上限 8"})
    definitions = {d.field_key: d for d in resolve_fields(project, None)}
    for i, g in enumerate(guards):
        type_ = g.get("type")
        if type_ not in GUARD_EXECUTORS:
            issues.append({"field": f"guards[{i}].type", "code": "NOT_A_CHOICE",
                           "message": f"未知守卫类型 {type_!r}，合法枚举 {sorted(GUARD_EXECUTORS)}"})
            continue
        cfg = g.get("config") or {}
        if type_ == "required_fields":
            fields = cfg.get("fields")
            if not isinstance(fields, list) or not fields:
                issues.append({"field": f"guards[{i}].config.fields", "code": "REQUIRED",
                               "message": "required_fields 须配置 fields 数组"})
                continue
            if len(fields) > 20:
                issues.append({"field": f"guards[{i}].config.fields", "code": "LIMIT",
                               "message": "required_fields 字段数上限 20"})
            for f in fields:
                if f in ENGINE_FIELDS:
                    issues.append({"field": f"guards[{i}].config.fields", "code": "INVALID",
                                   "message": f"引擎字段 {f} 不可配置守卫（BR-09）"})
                elif f not in SYSTEM_FIELD_META and not (
                        f.startswith("cf_") and f in definitions):
                    issues.append({"field": f"guards[{i}].config.fields", "code": "DOES_NOT_EXIST",
                                   "message": f"字段 {f} 不存在于项目字段集（BR-03）"})
        if type_ == "role_allowed":
            roles = cfg.get("roles")
            if not isinstance(roles, list) or not roles:
                issues.append({"field": f"guards[{i}].config.roles", "code": "REQUIRED",
                               "message": "role_allowed 须配置 roles 数组"})
    return issues
