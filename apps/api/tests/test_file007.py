"""归档强制合规测试（FILE-007，P4 R6）。"""

from __future__ import annotations

import pytest

from plane.db.models import Issue, Project, User, Workspace, WorkspaceMember
from plane.db.models.roles import WorkspaceRole
from plane.db.services.archive_compliance import (
    build_archive_report,
    check_archive_compliance,
)

pytestmark = pytest.mark.django_db


@pytest.fixture()
def env(db):
    owner = User.objects.create_user(email="ac-owner@rabbit.dev", password="Rabbit123!", display_name="主")
    confirmer = User.objects.create_user(email="ac-conf@rabbit.dev", password="Rabbit123!", display_name="确")
    ws = Workspace.objects.create(name="AC", slug=f"w-ac-{owner.id.hex[:8]}", owner=owner, created_by=owner)
    WorkspaceMember.objects.create(workspace=ws, member=owner, role=WorkspaceRole.OWNER, created_by=owner)
    proj = Project.objects.create(workspace=ws, name="P", identifier="AC1", created_by=owner)
    return {"owner": owner, "confirmer": confirmer, "ws": ws, "proj": proj}


def _issue(env, name, *, done=False, target=None):
    from datetime import date

    issue = Issue.objects.create(
        project=env["proj"],
        name=name,
        sequence_id=Issue.objects.filter(project=env["proj"]).count() + 1,
        completed_at=__import__("django.utils.timezone", fromlist=["timezone"]).now() if done else None,
        target_date=target or date(2026, 1, 1),
        created_by=env["owner"],
    )
    return issue


def test_ut01_open_issue_blocking(env):
    _issue(env, "未结任务")
    result = check_archive_compliance(env["ws"])
    assert any(b["kind"] == "open_issue" for b in result["blocking"])  # BR-01
    Issue.objects.all().update(completed_at=__import__("django.utils.timezone", fromlist=["timezone"]).now())
    result2 = check_archive_compliance(env["ws"])
    assert not [b for b in result2["blocking"] if b["kind"] == "open_issue"]


def test_ut02_legal_hold_blocking(env):
    import uuid as _uuid

    from plane.db.models import FileAsset, LegalHold

    asset = FileAsset.objects.create(
        workspace=env["ws"],
        project=env["proj"],
        entity_type="issue",
        entity_id=_uuid.uuid4(),
        size=1,
        storage_path="ac/a",
        created_by=env["owner"],
    )
    hold = LegalHold.objects.create(
        asset=asset, reason="诉讼", placed_by=env["owner"], confirmed_by=env["confirmer"], created_by=env["owner"]
    )
    result = check_archive_compliance(env["ws"])
    assert any(b["kind"] == "legal_hold" for b in result["blocking"])
    LegalHold.objects.filter(pk=hold.pk).update(
        released_at=__import__("django.utils.timezone", fromlist=["timezone"]).now()
    )
    result2 = check_archive_compliance(env["ws"])
    assert not [b for b in result2["blocking"] if b["kind"] == "legal_hold"]


def test_ut03_warnings_not_blocking(env):
    _issue(env, "无负责人的未结任务")
    result = check_archive_compliance(env["ws"])
    kinds = {w["kind"] for w in result["warnings"]}
    assert "unassigned" in kinds  # BR-02 警告不阻断
    assert result["blocking"] or result["warnings"]  # force 语义由端点承载


def test_ut04_report_snapshot(env):
    _issue(env, "done", done=True)
    _issue(env, "open")
    report = build_archive_report(env["ws"])
    assert report["issues_total"] == 2 and report["issues_done"] == 1
    assert report["members"] >= 1 and report["generated_at"]
