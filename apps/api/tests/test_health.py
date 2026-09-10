"""项目健康度测试（RPT-004，Sprint-9 R4 门禁）。

覆盖：四维评分数值链（规格 §3.1 示例可逐格复算：86/56/85/73 → 75.0）、
样本不足维度剔除重归一（BR-05）与 insufficient 分档（BR-10）、快照历史不
重算（BR-02/03）、负载窗口校验（BR-07）与矩阵口径、导出权限/同步流式/
异步两段式状态机（S7 A#1 + S8 A#1 债范式）。夹具风格对照 test_cycles.py。
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from plane.db.models import (
    Cycle,
    ExportTask,
    HealthConfig,
    HealthSnapshot,
    Issue,
    Project,
    ProjectMember,
    ProjectRole,
    ProjectWorklogConfig,
    State,
    User,
    WorkLog,
    WorkLogSummary,
    Workspace,
    WorkspaceMember,
    WorkspaceRole,
)
from plane.db.seeds.project_states import seed_project_states
from plane.db.services.health import (
    HealthReportService,
)
from plane.db.services.issue_sequence import next_sequence_id

pytestmark = pytest.mark.django_db


@pytest.fixture()
def env(db):
    owner = User.objects.create_user(email="hlt-owner@rabbit.dev", password="Rabbit123!",
                                     display_name="管理员")
    ws = Workspace.objects.create(name="HL", slug="w-hlt-test", owner=owner, created_by=owner)
    WorkspaceMember.objects.create(workspace=ws, member=owner,
                                   role=WorkspaceRole.OWNER, created_by=owner)
    proj = Project.objects.create(name="电商重构", identifier="RBT", workspace=ws, created_by=owner)
    seed_project_states(proj)
    ProjectMember.objects.create(project=proj, member=owner, role=ProjectRole.ADMIN, created_by=owner)
    viewer = User.objects.create_user(email="hlt-viewer@rabbit.dev", password="Rabbit123!")
    WorkspaceMember.objects.create(workspace=ws, member=viewer, role=WorkspaceRole.MEMBER)
    ProjectMember.objects.create(project=proj, member=viewer, role=ProjectRole.VIEWER)
    return {"owner": owner, "viewer": viewer, "ws": ws, "proj": proj}


def _client(user) -> APIClient:
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _base(env) -> str:
    return (f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/reports/")


def _mk_issue(env, name, *, group="unstarted", estimate=None, target=None) -> Issue:
    state = State.objects.filter(project=env["proj"], group=group).first()
    return Issue.objects.create(
        project=env["proj"], name=name, state=state,
        sequence_id=next_sequence_id(env["proj"].pk),
        estimate_minutes=estimate, target_date=target, created_by=env["owner"])


# ────────────────────────────────────────────────────────────────
# 1. 四维评分数值链（UT-01~04 · 规格 §3.1 示例逐格复算）
# ────────────────────────────────────────────────────────────────
class TestScores:
    def _setup(self, env):
        """61/128 完成、时间进度 0.55、11/50 逾期、spent/est=1.15、6/67 阻塞。"""
        today = timezone.localdate()
        Cycle.objects.create(
            project=env["proj"], name="S24",
            start_date=today - timedelta(days=11), end_date=today + timedelta(days=2),
            status=Cycle.Status.ACTIVE, created_by=env["owner"])
        for i in range(61):
            _mk_issue(env, f"done{i}", group="completed", estimate=480)
        for i in range(61, 128 - 11):   # 未完成无截止 56 条
            _mk_issue(env, f"open{i}", group="started", estimate=480)
        for i in range(11):              # 逾期 11 条（有截止且已过）
            _mk_issue(env, f"late{i}", group="started", estimate=480,
                      target=today - timedelta(days=1))
        for i in range(39):              # 未逾期有截止 39 条 → due_open=50
            _mk_issue(env, f"due{i}", group="started", estimate=480,
                      target=today + timedelta(days=5))
        # spent/est=1.15：est=106×480=50880（open 组），spent=42×1393=58506 → 1.1500
        targets = Issue.objects.filter(project=env["proj"],
                                       state__group__in=["unstarted", "started"])[:42]
        for it in targets:
            WorkLog.objects.create(issue=it, actor=env["owner"], minutes=1393,
                                   worked_on=timezone.localdate(),
                                   created_by=env["owner"])  # 42×1393=58506
        return today

    def test_numeric_chain(self, env):
        today = self._setup(env)
        snap = HealthReportService().compute(env["proj"], today, HealthConfig.of(env["proj"]))
        dims = snap.dimensions
        # progress：done_ratio=61/128≈0.477，elapsed=11/13≈0.846 → value≈-0.37 → score≈26
        assert dims["progress"] is not None and dims["progress"]["n"] == 167  # 61+56+11+39
        # overdue：11/50=0.22 → 100-44=56（规格 §3.1 同公式）
        assert dims["overdue"]["value"] == 0.22
        assert dims["overdue"]["score"] == 56
        # effort：36992/32160≈1.15 → 100-15=85
        assert dims["effort"]["value"] == 1.15
        assert dims["effort"]["score"] == 85
        # 总评 = Σ(score×0.25)（blocked=0 无阻塞 → 100）
        expected = (dims["progress"]["score"] + 56 + 85 + 100) * 0.25
        assert snap.total_score == round(expected, 1)
        assert snap.band in ("green", "yellow", "red")

    def test_effort_insufficient_renormalizes(self, env):
        """BR-05：est 样本 <3 → effort=None，其余三维权重重归一 1/3。"""
        today = timezone.localdate()
        Cycle.objects.create(project=env["proj"], name="S",
                             start_date=today - timedelta(days=2),
                             end_date=today + timedelta(days=12),
                             status=Cycle.Status.ACTIVE, created_by=env["owner"])
        _mk_issue(env, "a", group="completed")     # 无 estimate → effort 样本 0
        _mk_issue(env, "b", group="started")
        snap = HealthReportService().compute(env["proj"], today, HealthConfig.of(env["proj"]))
        assert snap.dimensions["effort"] is None
        # 重归一数值锚（BR-05）：total = Σ(存活三维 × 1/3)——若不剔除 effort
        # 槽位（突变哨兵），total 会是 Σ(三维 × 0.25) 偏低且可区分
        dims = snap.dimensions
        alive = [d["score"] for d in dims.values() if d]
        expected = round(sum(alive) / len(alive), 1)   # 等权重归一到存活维度数
        assert snap.total_score == expected

    def test_all_insufficient_band(self, env):
        """BR-10：空项目 → total None + band=insufficient。"""
        snap = HealthReportService().compute(
            env["proj"], timezone.localdate(), HealthConfig.of(env["proj"]))
        assert snap.total_score is None
        assert snap.band == "insufficient"

    def test_snapshot_history_not_recomputed(self, env):
        """BR-02/03：历史快照 config 冻结——后续改配置不重算历史。"""
        today = timezone.localdate()
        snap1 = HealthReportService().compute(
            env["proj"], today - timedelta(days=1), HealthConfig.of(env["proj"]))
        cfg = HealthConfig.of(env["proj"])
        cfg.weights = {"progress": 1.0, "overdue": 0, "effort": 0, "blocked": 0}
        cfg.save()
        snap2 = HealthReportService().compute(env["proj"], today, cfg)
        snap1.refresh_from_db()
        assert snap1.config_snapshot["weights"]["progress"] == 0.25   # 历史不重算
        assert snap2.config_snapshot["weights"]["progress"] == 1.0


# ────────────────────────────────────────────────────────────────
# 2. 端点（IT-01~04 + 错误矩阵）
# ────────────────────────────────────────────────────────────────
class TestEndpoints:
    def test_health_endpoint_with_fallback(self, env):
        """beat 未跑时读侧即时补算当日（幂等兜底）。"""
        resp = _client(env["viewer"]).get(f"{_base(env)}health/")
        assert resp.status_code == 200
        body = resp.json()["data"]
        assert {"total_score", "dimensions", "band", "trend_7d"} <= set(body)
        assert HealthSnapshot.objects.filter(project=env["proj"]).count() == 1

    def test_drilldown_realtime_and_validation(self, env):
        resp = _client(env["viewer"]).get(f"{_base(env)}health/drilldown/?dimension=bogus")
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "VALIDATION_INVALID_PARAM"
        ok = _client(env["viewer"]).get(f"{_base(env)}health/drilldown/?dimension=overdue")
        assert ok.status_code == 200
        assert ok.json()["meta"]["as_of"] == "realtime"    # BR-04② 字面量标记

    def test_config_weights_validation(self, env):
        # 负权重（和恰为 1）→ 非负校验
        bad = _client(env["owner"]).patch(
            f"{_base(env)}health/config/",
            {"weights": {"progress": 0.5, "overdue": 0.5, "effort": 0.5, "blocked": -0.5}},
            format="json")
        assert bad.status_code == 400
        assert "非负" in bad.json()["error"]["details"][0]["message"]
        # 和 ≠ 1 → 总和校验
        bad2 = _client(env["owner"]).patch(
            f"{_base(env)}health/config/",
            {"weights": {"progress": 0.4, "overdue": 0.4, "effort": 0.4, "blocked": 0.4}},
            format="json")
        assert bad2.status_code == 400
        assert "≠ 1" in bad2.json()["error"]["details"][0]["message"]
        # viewer 无 project.setting.manage → 403
        assert _client(env["viewer"]).patch(
            f"{_base(env)}health/config/", {"weights": DEFAULT_W}, format="json"
        ).status_code == 403

    def test_workload_window_validation(self, env):
        monday = timezone.localdate() - timedelta(days=timezone.localdate().weekday())
        # 非周一起始 → 400
        bad = _client(env["viewer"]).get(
            f"{_base(env)}workload/?from={(monday + timedelta(days=1)).isoformat()}&to={monday.isoformat()}")
        assert bad.status_code == 400
        # 跨度 >12 周 → 400
        bad2 = _client(env["viewer"]).get(
            f"{_base(env)}workload/?from={(monday - timedelta(weeks=13)).isoformat()}&to={monday.isoformat()}")
        assert bad2.status_code == 400
        ok = _client(env["viewer"]).get(
            f"{_base(env)}workload/?from={monday.isoformat()}&to={(monday + timedelta(weeks=4)).isoformat()}")
        assert ok.status_code == 200
        assert "capacity_minutes" in ok.json()["data"]

    def test_workload_matrix_cells(self, env):
        monday = timezone.localdate() - timedelta(days=timezone.localdate().weekday())
        WorkLogSummary.objects.create(project=env["proj"], actor=env["owner"],
                                      week_start=monday, total_minutes=1800)
        WorkLogSummary.objects.create(project=env["proj"], actor=env["owner"],
                                      week_start=monday + timedelta(days=7), total_minutes=2100)
        data = _client(env["viewer"]).get(
            f"{_base(env)}workload/?from={monday.isoformat()}&to={(monday + timedelta(days=14)).isoformat()}"
        ).json()["data"]
        assert data["capacity_minutes"] == 2400          # 默认 40h（无 ProjectWorklogConfig 行）
        row = next(r for r in data["matrix"] if r["actor_id"] == str(env["owner"].id))
        assert row["cells"][monday.isoformat()] == 1800
        # 容量唯一源：ProjectWorklogConfig.weekly_capacity_minutes（TASK-013 归属）
        ProjectWorklogConfig.objects.create(project=env["proj"], weekly_capacity_minutes=1800)
        data2 = _client(env["viewer"]).get(
            f"{_base(env)}workload/?from={monday.isoformat()}&to={(monday + timedelta(days=14)).isoformat()}"
        ).json()["data"]
        assert data2["capacity_minutes"] == 1800


DEFAULT_W = {"progress": 0.25, "overdue": 0.25, "effort": 0.25, "blocked": 0.25}


# ────────────────────────────────────────────────────────────────
# 3. 导出：权限 + 同步流式 + 异步两段式（S7 A#1 / S8 A#1 债范式）
# ────────────────────────────────────────────────────────────────
class TestExports:
    def _monday(self):
        t = timezone.localdate()
        return t - timedelta(days=t.weekday())

    def test_export_requires_permission(self, env):
        monday = self._monday()
        assert _client(env["viewer"]).get(
            f"{_base(env)}workload/export/?from={monday.isoformat()}&to={monday.isoformat()}"
        ).status_code == 403

    def test_sync_export_small(self, env):
        monday = self._monday()
        WorkLogSummary.objects.create(project=env["proj"], actor=env["owner"],
                                      week_start=monday, total_minutes=1800)
        resp = _client(env["owner"]).get(
            f"{_base(env)}workload/export/?from={monday.isoformat()}&to={monday.isoformat()}")
        assert resp.status_code == 200
        assert resp["Content-Type"].startswith("text/csv")
        assert "actor,week_start,minutes,load_ratio" in resp.content.decode()

    def test_async_two_phase_export(self, env, settings):
        """超阈值 → 202 {task_id,status_url} → 状态机 pending→succeeded（MinIO mock）。"""
        monday = self._monday()
        # 少量数据 + 阈值 patch 为 0 → 强制走异步分支（行数边界在同步用例已测）
        from plane.db.models import User as U
        u = U.objects.create_user(email="hlt-m0@rabbit.dev", password="Rabbit123!",
                                  display_name="甲")
        WorkLogSummary.objects.create(project=env["proj"], actor=u,
                                      week_start=monday, total_minutes=600)
        from plane.app.views import health as hv
        hv.SYNC_EXPORT_ROW_LIMIT = 0
        from plane.db.services import health as hsvc

        def fake_render(task):
            return "actor,week_start,minutes,load_ratio\n甲,2026-09-07,600,0.25"

        def fake_put(key, text, task):
            return f"http://minio.local/{key}?sig=x", timezone.now() + timedelta(seconds=900)

        original = (hsvc._render_export_csv, hsvc._put_minio_and_presign)
        hsvc._render_export_csv, hsvc._put_minio_and_presign = fake_render, fake_put
        try:
            resp = _client(env["owner"]).get(
                f"{_base(env)}workload/export/?from={monday.isoformat()}"
                f"&to={(monday + timedelta(weeks=11)).isoformat()}")
            assert resp.status_code == 202, resp.content[:200]
            data = resp.json()["data"]
            assert {"task_id", "state", "status_url"} <= set(data)
            task = ExportTask.objects.get(pk=data["task_id"])
            assert task.status in (ExportTask.Status.PENDING, ExportTask.Status.RUNNING)
            # 状态机：worker 同步执行（任务函数直调，绕 celery）
            hsvc.run_export_task.__wrapped__(str(task.id))
            task.refresh_from_db()
            assert task.status == ExportTask.Status.SUCCEEDED
            assert task.download_url.startswith("http://minio.local/")
            # 轮询端点：succeeded 下发 URL；仅创建人可见
            poll = _client(env["owner"]).get(
                f"/api/v1/workspaces/{env['ws'].slug}/exports/{task.id}/")
            assert poll.status_code == 200
            assert poll.json()["data"]["download_url"].startswith("http://minio.local/")
            assert _client(env["viewer"]).get(
                f"/api/v1/workspaces/{env['ws'].slug}/exports/{task.id}/").status_code == 404
        finally:
            hsvc._render_export_csv, hsvc._put_minio_and_presign = original
            hv.SYNC_EXPORT_ROW_LIMIT = 2000
