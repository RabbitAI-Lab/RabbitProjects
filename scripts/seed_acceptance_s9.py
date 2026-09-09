#!/usr/bin/env python3
"""Sprint-9 验收演示数据种子（项目集 + 迭代燃尽 + Wiki + 关键路径）。

幂等：以标识符 S9AC 前缀识别旧数据先清后建（软删行经 _base_manager 兜底）。
用法：uv run --project apps/api python scripts/seed_acceptance_s9.py
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "apps", "api"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "plane.settings.dev")
os.environ.setdefault("DATABASE_URL", "postgresql://rp:rp@localhost:5432/rabbit_projects")
os.environ.setdefault("SECRET_KEY", "dev")

import django  # noqa: E402

django.setup()

from plane.db.models import (  # noqa: E402
    Cycle,
    CycleSnapshot,
    Issue,
    Portfolio,
    PortfolioMilestone,
    PortfolioProject,
    Project,
    State,
    User,
    WikiPage,
    WikiSpace,
    Workspace,
    WorkspaceMember,
    WorkspaceRole,
)
from plane.db.seeds.project_states import seed_project_states  # noqa: E402
from plane.db.services.issue_link import create_relation  # noqa: E402
from plane.db.services.wiki import WikiService, render_tiptap  # noqa: E402

WS_SLUG = "workspace"
PROJ_IDS = ["S9A1", "S9G2", "S9D3"]


def main() -> None:
    ws = Workspace.objects.get(slug=WS_SLUG)
    owner = ws.owner

    # ── 幂等清理 ──
    old_projects = list(Project.all_objects.filter(identifier__in=PROJ_IDS))
    for p in old_projects:
        Issue.all_objects.filter(project=p).delete()
        State.all_objects.filter(project=p).delete()
        p.delete()
    for pf in Portfolio.all_objects.filter(workspace=ws, name__startswith="S9 演示"):
        PortfolioProject.all_objects.filter(portfolio=pf).delete()
        PortfolioMilestone.all_objects.filter(portfolio=pf).delete()
        pf.delete()
    for sp in WikiSpace.all_objects.filter(name__startswith="S9 "):
        WikiPage.all_objects.filter(space=sp).delete()
        sp.delete()

    # ── 三项目 + 任务 ──
    today = date.today()
    projects = {}
    for ident, name in (("S9A1", "App 重构"), ("S9G2", "中台网关"), ("S9D3", "数据迁移")):
        p = Project.objects.create(name=name, identifier=ident, workspace=ws, created_by=owner)
        seed_project_states(p)
        projects[ident] = p

    def mk_issue(ident: str, name: str, group: str, start: date, target: date) -> Issue:
        st = State.objects.filter(project=projects[ident], group=group).first()
        return Issue.objects.create(
            project=projects[ident], name=name, state=st,
            start_date=start, target_date=target, estimate_minutes=480, created_by=owner)

    a1 = mk_issue("S9A1", "订单中心重构", "started", today - timedelta(days=8), today + timedelta(days=2))
    a2 = mk_issue("S9A1", "联调用例通过", "started", today - timedelta(days=5), today + timedelta(days=4))
    g1 = mk_issue("S9G2", "网关验收", "started", today - timedelta(days=6), today - timedelta(days=1))
    g2 = mk_issue("S9G2", "限流配置", "unstarted", today + timedelta(days=1), today + timedelta(days=5))
    d1 = mk_issue("S9D3", "双写校验报告", "completed", today - timedelta(days=10), today - timedelta(days=2))
    _ = mk_issue("S9D3", "存量数据切流", "unstarted", today + timedelta(days=3), today + timedelta(days=10))
    create_relation(issue_id=a1.id, related_issue_id=g1.id, relation_type="blocks", actor_id=owner.id)
    create_relation(issue_id=a2.id, related_issue_id=g2.id, relation_type="blocks", actor_id=owner.id)
    create_relation(issue_id=g1.id, related_issue_id=d1.id, relation_type="blocks", actor_id=owner.id)

    # ── 项目集 + 里程碑 ──
    root = Portfolio.objects.create(workspace=ws, name="S9 演示组合", depth=1, created_by=owner)
    leaf = Portfolio.objects.create(workspace=ws, name="S9 电商平台 2.0", parent=root, depth=2,
                                    manager=owner, created_by=owner)
    for p in projects.values():
        PortfolioProject.objects.create(portfolio=leaf, project=p, created_by=owner)
    PortfolioMilestone.objects.create(
        portfolio=leaf, name="全量联调", target_date=today + timedelta(days=7), created_by=owner)
    PortfolioMilestone.objects.create(
        portfolio=leaf, name="灰度上线", target_date=today + timedelta(days=53), created_by=owner)
    done = PortfolioMilestone.objects.create(
        portfolio=leaf, name="接口冻结", target_date=today - timedelta(days=9),
        completed_at=timezone_now(), created_by=owner)

    # ── 迭代 + 燃尽快照（S24 进行中 + S22/S23 已归档）──
    s24 = Cycle.objects.create(project=projects["S9A1"], name="Sprint 24",
                               start_date=today - timedelta(days=11), end_date=today + timedelta(days=2),
                               status=Cycle.Status.ACTIVE, created_by=owner)
    a1.cycle = s24; a1.save(update_fields=["cycle"])
    a2.cycle = s24; a2.save(update_fields=["cycle"])
    days = []
    d = s24.start_date
    remaining = 960
    while d <= today:
        rem = max(remaining, 480)
        CycleSnapshot.objects.update_or_create(
            cycle=s24, snapshot_date=d,
            defaults={
                "measure": "estimate_minutes",
                "remaining_by_group": {"backlog": 0, "unstarted": rem - 480, "started": 480, "completed": 960 - rem, "cancelled": 0},
                "remaining_total": rem, "completed_delta": 960 - rem,
                "scope_total": 960, "created_by": owner})
        remaining -= 60
        d += timedelta(days=1)
        days.append(d)
    for idx, (name, done_h, plan_h) in enumerate((("Sprint 22", 82, 88), ("Sprint 23", 74, 80))):
        c = Cycle.objects.create(project=projects["S9A1"], name=name,
                                 start_date=today - timedelta(days=45 - idx * 14),
                                 end_date=today - timedelta(days=31 - idx * 14),
                                 status=Cycle.Status.COMPLETED, created_by=owner)
        CycleSnapshot.objects.update_or_create(
            cycle=c, snapshot_date=c.end_date,
            defaults={
                "measure": "estimate_minutes",
                "remaining_by_group": {"backlog": 0, "unstarted": 0, "started": 0, "completed": done_h * 60, "cancelled": 0},
                "remaining_total": 0, "completed_delta": done_h * 60, "scope_total": plan_h * 60,
                "frozen": True, "is_final": True, "created_by": owner})

    # ── Wiki 空间 + 两页（含一页已发布 v2）──
    space = WikiSpace.objects.create(project=projects["S9A1"], name="S9 研发规范", created_by=owner)
    svc = WikiService()
    page1 = WikiPage.objects.create(space=space, title="API 设计规范", created_by=owner,
                                    draft_json={"type": "doc", "content": [
                                        {"type": "heading", "content": [{"type": "text", "text": "错误码约定"}]},
                                        {"type": "paragraph", "content": [
                                            {"type": "text", "text": "所有接口错误码必须从注册表选取，禁止自创。完整注册表见任务 S9A1-2。"}]}]})
    v1 = svc.publish(page_id=page1.id, actor=owner, base_version_id=None, change_summary="初版")
    page1.refresh_from_db()
    page1.draft_json = {"type": "doc", "content": [
        {"type": "heading", "content": [{"type": "text", "text": "错误码约定"}]},
        {"type": "paragraph", "content": [
            {"type": "text", "text": "所有接口错误码必须从注册表选取，禁止自创；新增需架构评审并登记 ADR。"}]}]}
    page1.base_version = v1
    page1.save(update_fields=["draft_json", "base_version", "updated_at"])
    svc.publish(page_id=page1.id, actor=owner, base_version_id=v1.id, change_summary="补充 ADR 要求")
    WikiPage.objects.create(space=space, title="上线 checklist", created_by=owner)
    page3 = WikiPage.objects.create(space=space, title="数据库约定", parent=None, created_by=owner,
                                    draft_json={"type": "doc", "content": [
                                        {"type": "paragraph", "content": [
                                            {"type": "text", "text": "迁移一律先 sqlmigrate 导 DDL 审阅，再手工灌 + fake。错误码与迁移守则互引。"}]}]})
    svc.publish(page_id=page3.id, actor=owner, base_version_id=None, change_summary="初版")
    _ = done
    print("seeded: 3 projects / portfolio+3 milestones / cycle S24 active + 2 archived / wiki 3 pages")


def timezone_now():
    from django.utils import timezone
    return timezone.now()


if __name__ == "__main__":
    main()
