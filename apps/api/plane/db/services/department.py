"""部门域服务（AUTH-007 §4.3，Sprint-8 R1）。

一棵树三类操作：
- 树管理：create / rename / reorder / move / delete（path 物化路径，子树 =
  前缀匹配，深度 = 实段数 ≤ 6）；
- 成员归属：挂部门经 TEAM-002 既有端点扩白名单字段（views 层），本模块提供
  bulk_move_members（批量调部门）；
- 按部门授权：expand_grant —— 「快照展开」（§1.3）把部门成员在授权时刻
  展开为逐人 ProjectMember 行（复用 PROJ-002 写入路径），批次行自含成员
  清单快照与 department_id_snapshot（幂等重同步 + 删除后溯源）。

audit 事件经 ``plane.bgtasks.audit.record_audit``（AUTH-010 占位，R4 兑现）。
"""
from __future__ import annotations

from django.db import connection, transaction
from django.db.models import Max

from plane.base.exception import AppException
from plane.bgtasks.audit import record_audit
from plane.db.models import (
    Department,
    DepartmentGrantBatch,
    Project,
    ProjectMember,
    WorkspaceMember,
)
from plane.db.models.roles import ProjectRole, WorkspaceRole
from plane.db.services.project_member import MAX_BATCH_MEMBERS, ProjectMemberService

MAX_DEPTH = 6
_SORT_STEP = 65536.0
#: 同级相邻 sort_order 间距小于该值时触发整级等差重排（UT-13）
_REBALANCE_EPSILON = 1e-6


# ── 内部工具 ──────────────────────────────────────────────

def _depth_of(department: Department) -> int:
    """深度 = path 实段数：根 ``/{id}/`` 为 1。不可 count("/")——首尾斜杠 +2 偏移。"""
    return department.path.strip("/").count("/") + 1


def _subtree(department: Department):
    """子树（含自身）：path 前缀匹配走 path 索引。"""
    return Department.objects.filter(
        workspace_id=department.workspace_id,
        path__startswith=department.path,
        deleted_at__isnull=True,
    )


def _subtree_height(department: Department) -> int:
    """子树最大深度差（自身为 0）。"""
    base = _depth_of(department)
    return max((_depth_of(d) - base for d in _subtree(department)), default=0)


def _siblings(workspace_id, parent_id):
    return Department.objects.filter(
        workspace_id=workspace_id, parent_id=parent_id, deleted_at__isnull=True,
    )


def _assert_sibling_name_free(workspace_id, parent_id, name: str, *, exclude_id=None) -> None:
    """同级重名显式预检（BR-02，不区分大小写）——唯一索引兜底防并发。"""
    qs = _siblings(workspace_id, parent_id).filter(name__iexact=name)
    if exclude_id is not None:
        qs = qs.exclude(id=exclude_id)
    if qs.exists():
        raise AppException(
            "RESOURCE_ALREADY_EXISTS",
            message="同级已存在同名部门",
            details=[{"field": "name", "code": "UNIQUE",
                      "message": "同级部门名不可重复（不区分大小写）"}],
        )


def _next_sort(workspace_id, parent_id) -> float:
    """新增部门排同级末尾：max + 65536（浮点插值基础步长）。"""
    top = (_siblings(workspace_id, parent_id)
           .aggregate(v=Max("sort_order"))["v"])
    return (top or 0.0) + _SORT_STEP


def _rebalance_siblings(workspace_id, parent_id) -> None:
    """整级等差重排（UT-13）：间距塌缩后从 65536 起按步长重写全部同级。"""
    ids = list(_siblings(workspace_id, parent_id)
               .order_by("sort_order").values_list("id", flat=True))
    Department.objects.bulk_update([
        Department(pk=i, sort_order=(n + 1) * _SORT_STEP) for n, i in enumerate(ids)
    ], ["sort_order"])


def _path_names(department: Department, other: Department) -> str:
    """环路径的可读描述（BR-03 details）：「A → B → C」。"""
    names = list(
        Department.objects.filter(
            workspace_id=department.workspace_id,
            id__in=[p for p in other.path.strip("/").split("/") if p],
        ).values_list("name", flat=True)
    )
    names.append(department.name)
    return " → ".join(names)


def _audit(event: str, *, actor_id: str, object_id, **extra) -> None:
    transaction.on_commit(
        lambda: record_audit.delay(event, actor_id=actor_id,
                                   object_id=str(object_id) if object_id else None, **extra)
    )


# ── 树管理 ────────────────────────────────────────────────

@transaction.atomic
def create_department(*, actor, workspace, name: str, parent_id=None) -> Department:
    parent = None
    if parent_id:
        parent = (Department.objects
                  .select_for_update()
                  .get(pk=parent_id, workspace=workspace, deleted_at__isnull=True))  # 404 出域
        if _depth_of(parent) + 1 > MAX_DEPTH:  # BR-01
            raise AppException(
                "RESOURCE_LIMIT_EXCEEDED",
                message="部门层级最多 6 层",
                details=[{"field": "parent_id", "code": "TOO_LARGE",
                          "message": f"父部门已位于第 {_depth_of(parent)} 层，无法在其下新建（max={MAX_DEPTH}）"}],
            )
    name = name.strip()
    _assert_sibling_name_free(workspace.id, parent_id, name)  # BR-02（含根部门）
    dept = Department(workspace=workspace, parent=parent, name=name,
                      sort_order=_next_sort(workspace.id, parent_id),
                      created_by=actor, updated_by=actor)
    dept.path = (parent.path if parent else "/") + f"{dept.id}/"  # UUID 主键 init 即生成
    dept.full_clean()
    dept.save()
    _audit("department.created", actor_id=actor.id, object_id=dept.id)
    return dept


@transaction.atomic
def rename_department(*, actor, department: Department, name: str) -> Department:
    name = name.strip()
    _assert_sibling_name_free(department.workspace_id, department.parent_id,
                              name, exclude_id=department.id)  # BR-02
    department.name = name
    department.updated_by = actor
    department.save(update_fields=["name", "updated_by", "updated_at"])
    _audit("department.renamed", actor_id=actor.id, object_id=department.id)
    return department


@transaction.atomic
def reorder_department(*, actor, department: Department, sort_after_id) -> Department:
    """同级内移动到 ``sort_after_id`` 之后（None = 置顶）。间距 < 1e-6 触发整级重排。"""
    siblings = list(_siblings(department.workspace_id, department.parent_id)
                    .order_by("sort_order"))
    others = [d for d in siblings if d.id != department.id]
    if sort_after_id is None:
        lo, hi = 0.0, (others[0].sort_order if others else _SORT_STEP * 2)
        new_sort = (lo + hi) / 2 if others else _SORT_STEP
    else:
        anchor = next((d for d in others if str(d.id) == str(sort_after_id)), None)
        if anchor is None:
            raise AppException(
                "VALIDATION_ERROR",
                message="sort_after 指向的部门不存在或不为同级",
                details=[{"field": "sort_after", "code": "INVALID",
                          "message": "sort_after 必须指向同父级的部门"}],
            )
        idx = others.index(anchor)
        nxt = others[idx + 1] if idx + 1 < len(others) else None
        lo = anchor.sort_order
        hi = nxt.sort_order if nxt is not None else anchor.sort_order + _SORT_STEP
        new_sort = (lo + hi) / 2
        if nxt is not None and hi - lo < _REBALANCE_EPSILON:
            _rebalance_siblings(department.workspace_id, department.parent_id)
            # 重排后按 anchor 新位置取中点
            fresh = {str(d.id): d.sort_order for d in
                     _siblings(department.workspace_id, department.parent_id)}
            lo = fresh[str(sort_after_id)]
            hi = lo + _SORT_STEP
            new_sort = (lo + hi) / 2
    department.sort_order = new_sort
    department.updated_by = actor
    department.save(update_fields=["sort_order", "updated_by", "updated_at"])
    _audit("department.reordered", actor_id=actor.id, object_id=department.id)
    return department


@transaction.atomic
def move_department(*, actor, department: Department, new_parent_id) -> Department:
    new_parent = None
    if new_parent_id:
        new_parent = (Department.objects
                      .select_for_update()
                      .get(pk=new_parent_id, workspace_id=department.workspace_id,
                           deleted_at__isnull=True))
        # BR-03（含自身）：新父级 path 以自身 path 为前缀即成环
        if new_parent.path.startswith(department.path):
            raise AppException(
                "RESOURCE_CIRCULAR_DEPENDENCY",
                message="不能把部门移动到自身或其子部门下",
                details=[{"field": "parent_id", "code": "CIRCULAR",
                          "message": f"环路径：{_path_names(department, new_parent)}"}],
            )
        # BR-01：新父级深度 + 本子树高度 + 1 不得超限
        if _depth_of(new_parent) + _subtree_height(department) + 1 > MAX_DEPTH:
            raise AppException(
                "RESOURCE_LIMIT_EXCEEDED",
                message="部门层级最多 6 层",
                details=[{"field": "parent_id", "code": "TOO_LARGE",
                          "message": f"移动后子树最深处将超过 {MAX_DEPTH} 层"}],
            )
    _assert_sibling_name_free(department.workspace_id, new_parent_id,
                              department.name, exclude_id=department.id)  # BR-02
    old_prefix = department.path
    department.parent = new_parent
    department.path = (new_parent.path if new_parent else "/") + f"{department.id}/"
    department.sort_order = _next_sort(department.workspace_id, new_parent_id)
    department.updated_by = actor
    department.save(update_fields=["parent_id", "sort_order", "path", "updated_by", "updated_at"])
    # 整子树 path 前缀重写：单条 UPDATE（BR-03 校验后执行，前缀互斥保证无误伤）
    with connection.cursor() as cur:
        cur.execute(
            "UPDATE department SET path = %s || substring(path from %s) "
            "WHERE workspace_id = %s AND path LIKE %s AND deleted_at IS NULL",
            [department.path, len(old_prefix) + 1,
             department.workspace_id, old_prefix + "%"],
        )
    _audit("department.moved", actor_id=actor.id, object_id=department.id)
    return department


@transaction.atomic
def delete_department(*, actor, department: Department) -> None:
    """BR-04：仅空部门可删（无直属成员且无子部门）；曾授权部门可删——批次保留。"""
    member_count = WorkspaceMember.objects.filter(
        department=department, deleted_at__isnull=True,
        is_active=True,
    ).count()
    child_count = _siblings(department.workspace_id, department.id).count()
    if member_count or child_count:
        raise AppException(
            "RESOURCE_IN_USE",
            message="部门非空，无法删除",
            details=[{"field": "id", "code": "IN_USE",
                      "message": f"直属成员 {member_count} 人 / 子部门 {child_count} 个，请先迁移"}],
        )
    dept_id = department.id
    department.delete()  # 软删（BaseModel）
    _audit("department.deleted", actor_id=actor.id, object_id=dept_id)


# ── 成员归属 ──────────────────────────────────────────────

@transaction.atomic
def bulk_move_members(*, actor, workspace, member_ids: list[str], department_id) -> int:
    """批量调部门（BR-05 一人一部门；department_id=None 移入未分配）。"""
    if department_id is not None:
        Department.objects.get(pk=department_id, workspace=workspace,
                               deleted_at__isnull=True)  # 404 出域
    qs = WorkspaceMember.objects.filter(
        workspace=workspace, id__in=member_ids, deleted_at__isnull=True,
    )
    count = 0
    for wm in qs:
        wm.department_id = department_id
        wm.updated_by = actor
        wm.save(update_fields=["department_id", "updated_by", "updated_at"])
        count += 1
    _audit("department.members_moved", actor_id=actor.id, object_id=department_id,
           moved=count)
    return count


# ── 按部门授权（快照展开）──────────────────────────────────

def _expand_targets(department: Department, with_descendants: bool) -> list[dict]:
    """圈定目标成员集（BR-08）：部门下未软删成员全集；停用成员计入 skipped。"""
    dept_ids = ([department.id] if not with_descendants
                else list(_subtree(department).values_list("id", flat=True)))
    return list(
        WorkspaceMember.objects
        .filter(workspace_id=department.workspace_id,
                department_id__in=dept_ids, deleted_at__isnull=True)
        .values("member_id", "is_active", "role")
    )


def _grant_guard(project: Project, department: Department, role: int, actor) -> None:
    """BR-10 / 归档守卫（正式与预览同逻辑）。"""
    if role not in ProjectRole.values:
        raise AppException(
            "VALIDATION_ERROR",
            message="非法的项目角色",
            details=[{"field": "role", "code": "NOT_A_CHOICE", "message": "非法的项目角色"}],
        )
    if project.status == Project.Status.ARCHIVED:
        raise AppException("PERM_PROJECT_ARCHIVED", message="项目已归档")
    if role == ProjectRole.ADMIN:
        actor_ws_role = WorkspaceMember.objects.filter(
            workspace_id=department.workspace_id, member=actor,
            is_active=True, deleted_at__isnull=True,
        ).values_list("role", flat=True).first() or 0
        actor_proj_role = ProjectMember.objects.filter(
            project=project, member=actor, is_active=True, deleted_at__isnull=True,
        ).values_list("role", flat=True).first() or 0
        # rbac §7.4：WS_ADMIN 对空间项目隐式 PROJ_ADMIN
        if not (actor_proj_role >= ProjectRole.ADMIN
                or actor_ws_role >= WorkspaceRole.ADMIN):
            raise AppException("PERM_PROJECT_ADMIN_REQUIRED",
                               message="分配项目管理员需要本人具备项目或空间管理员身份")


@transaction.atomic
def expand_grant(*, actor, department: Department, project: Project,
                 role: int, with_descendants: bool = True,
                 dry_run: bool = False):
    """按部门授权：快照展开为逐人 ProjectMember（§1.3）。

    dry_run=True 为授权预览（§4.2 grants/preview/）——只圈定与分桶，零写入。
    幂等重同步：对同 (department, project) 重复 POST 不产生重复成员行；
    响应计数恒等式 added + role_changed + skipped + unchanged = 目标集总数。
    """
    _grant_guard(project, department, role, actor)
    members = _expand_targets(department, with_descendants)
    added: list[str] = []
    changed: list[str] = []
    skipped: list[dict] = []
    unchanged: list[str] = []
    to_add: list[str] = []
    for m in members:
        mid = str(m["member_id"])  # values() 回 UUID——统一 str，防与 existing 键类型错配
        if not m["is_active"]:  # BR-08：停用成员不静默排除
            skipped.append({"member_id": mid, "reason": "member_inactive"})
        elif (m["role"] == WorkspaceRole.GUEST
              and role > ProjectRole.COMMENTER):
            # 与 PROJ-002 BR-05「整单拒绝」的差异声明（§4.3）：展开集由系统圈定、
            # 用户不可预选，单个 GUEST 不阻塞整批 → 逐人跳过
            skipped.append({"member_id": mid, "reason": "guest_role_cap"})
        else:
            to_add.append(mid)

    existing = {str(pm.member_id): pm for pm in ProjectMember.objects
                .filter(project=project, member_id__in=to_add,
                        is_active=True, deleted_at__isnull=True)}

    if dry_run:
        for mid in to_add:
            pm = existing.get(mid)
            if pm is None:
                added.append(mid)
            elif pm.role != role:  # BR-09
                changed.append(mid)
            else:
                unchanged.append(mid)
        return {"added": [str(m) for m in added],
                "role_changed": [str(m) for m in changed],
                "skipped": [{"member_id": str(s["member_id"]), "reason": s["reason"]}
                            for s in skipped],
                "unchanged": [str(m) for m in unchanged], "dry_run": True}

    svc = ProjectMemberService()
    # PROJ-002 add_members 限单批 ≤ MAX_BATCH_MEMBERS=20：按批循环，各批明细
    # 汇总到同一批次聚合记录（嵌套事务 = savepoint，§2.2）
    new_ids = [t for t in to_add if t not in existing]
    for i in range(0, len(new_ids), MAX_BATCH_MEMBERS):
        for r in svc.add_members(project=project, actor=actor,
                                 member_ids=new_ids[i:i + MAX_BATCH_MEMBERS],
                                 role=role):
            if r["status"] == "added":
                added.append(r["member_id"])
            else:  # failed（not_workspace_member 等）逐条留痕不中断
                skipped.append({"member_id": str(r["member_id"]), "reason": r["reason"]})
    for mid in [t for t in to_add if t in existing]:
        pm = existing[mid]
        if pm.role != role:  # BR-09
            svc.change_role(project=project, member=pm, new_role=role, actor=actor)
            changed.append(mid)
        else:
            unchanged.append(mid)

    batch = DepartmentGrantBatch.objects.create(
        workspace_id=department.workspace_id, department=department,
        department_id_snapshot=department.id,  # 自含溯源（BR-04：曾授权部门可删）
        project=project,
        role=role, with_descendants=with_descendants,
        added_count=len(added), role_changed_count=len(changed),
        skipped_count=len(skipped), unchanged_count=len(unchanged),
        member_snapshot=([{"member_id": str(m), "action": "added"} for m in added]
                         + [{"member_id": str(m), "action": "role_changed"} for m in changed]
                         + [{"member_id": str(s["member_id"]),
                             "action": f"skipped:{s['reason']}"} for s in skipped]
                         + [{"member_id": str(m), "action": "unchanged"} for m in unchanged]),
        created_by=actor, updated_by=actor,
    )
    _audit("department.granted", actor_id=actor.id, object_id=batch.id,
           project_id=str(project.id), role=role)
    return batch


# ── 统计（§4.2 stats/）────────────────────────────────────

def department_stats(department: Department) -> dict:
    """部门统计：成员数（直属 / 含子级）+ 任务量（按 assignee 归属聚合）。"""
    subtree_ids = list(_subtree(department).values_list("id", flat=True))
    direct_count = WorkspaceMember.objects.filter(
        department=department, is_active=True, deleted_at__isnull=True,
    ).count()
    with_descendants_count = WorkspaceMember.objects.filter(
        department_id__in=subtree_ids, is_active=True, deleted_at__isnull=True,
    ).count()
    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(DISTINCT i.id)
            FROM issues i
            JOIN issue_assignees ia ON ia.issue_id = i.id
            JOIN workspace_members wm ON wm.member_id = ia.assignee_id
                 AND wm.deleted_at IS NULL AND wm.is_active = TRUE
                 AND wm.workspace_id = %s
            WHERE wm.department_id = ANY(%s::uuid[])
              AND i.deleted_at IS NULL
            """,
            [department.workspace_id, subtree_ids],
        )
        issue_count = cur.fetchone()[0]
    return {
        "direct_member_count": direct_count,
        "with_descendants_member_count": with_descendants_count,
        "issue_count": issue_count,
    }
