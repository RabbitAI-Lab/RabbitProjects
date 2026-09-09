#!/usr/bin/env python3
"""Sprint-8 验收种子（幂等）：项目/任务 + 4 层部门树 + 挂接 + 角色模板 + 锁定默认视图。

用法：DATABASE_URL=… SECRET_KEY=dev uv run --project apps/api python scripts/seed_acceptance_s8.py
前置：API 8000 已起（SSO FERNET env 不需要——视图/部门不涉密钥列）。
幂等：重跑先软删 S8AC 旧项目与 S8A* 部门再重建（跨 run 撞名/撞标识防御）。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "apps", "api"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "plane.settings.dev")

import django  # noqa: E402

django.setup()

from plane.db.models import (  # noqa: E402
    Department,
    Issue,
    IssueView,
    Project,
    ProjectMember,
    ProjectRole,
    User,
    Workspace,
    WorkspaceMember,
)
from plane.db.models.roles import WorkspaceRole
from plane.db.seeds.project_states import seed_project_states
from plane.db.services import department as dept_svc  # noqa: E402
from plane.db.services import view_governance as vg  # noqa: E402

WS_SLUG = os.environ.get("S8_WS", "workspace")
IDENT = "S8AC"

ws = Workspace.objects.get(slug=WS_SLUG)
owner = ws.owner

# ── 清旧（幂等）──
old = Project.objects.filter(workspace=ws, identifier=IDENT, deleted_at__isnull=True).first()
if old:
    old.delete()  # 软删
for d in Department.objects.filter(workspace=ws, name__startswith="S8A", deleted_at__isnull=True):
    d.delete()

# ── 项目 + 任务 ──
proj = Project.objects.create(workspace=ws, name="S8 验收演示", identifier=IDENT,
                              created_by=owner)
seed_project_states(proj)
states = list(proj.states.all())
ProjectMember.objects.create(project=proj, member=owner, role=ProjectRole.ADMIN,
                             created_by=owner)
titles = ["SSO 配置向导", "JIT 建号链路", "认领页文案", "强制门拦截",
          "证书临期横幅", "SAML 元数据", "会话时长口径", "逃生名单说明"]
for i, t in enumerate(titles):
    Issue.objects.create(project=proj, name=t, state=states[i % 3],
                         created_by=owner, sequence_id=i + 1, sort_order=(i + 1) * 100)
# 负责人两分（泳道矩阵非平凡）
issues = list(proj.issues.order_by("sequence_id"))
member2 = WorkspaceMember.objects.filter(workspace=ws, role=WorkspaceRole.MEMBER,
                                         is_active=True).first()
if member2:
    for it in issues[:4]:
        it.assignees.add(owner)
    for it in issues[4:6]:
        it.assignees.add(member2.member)

# ── 4 层部门树（S8A 前缀，恒等式成员挂第 3 层）──
l1 = dept_svc.create_department(actor=owner, workspace=ws, name="S8A 研发中心")
l2 = dept_svc.create_department(actor=owner, workspace=ws, name="S8A 平台组", parent_id=str(l1.id))
l3 = dept_svc.create_department(actor=owner, workspace=ws, name="S8A 后端组", parent_id=str(l2.id))
dept_svc.create_department(actor=owner, workspace=ws, name="S8A 存储小组", parent_id=str(l3.id))
wm_owner = WorkspaceMember.objects.get(workspace=ws, member=owner)
wm_owner.department = l3
wm_owner.save(update_fields=["department_id"])
if member2:
    member2.department = l2
    member2.save(update_fields=["department_id"])

# ── 共享 + 锁定 + 项目默认视图（锁定横幅/标识/订阅幕）──
view = IssueView.objects.create(
    workspace=ws, project=proj, owner=owner, name="S8 迭代评审",
    filters={"op": "AND", "conditions": []},
    display_props={"group_by": "state_id"},
    created_by=owner, updated_by=owner)
vg.share_view(actor=owner, view=view, access="shared")
vg.lock_view(actor=owner, view=view, is_locked=True, is_project_default=True)

print(f"OK project={proj.id} view={view.id} dept={l1.id}")
