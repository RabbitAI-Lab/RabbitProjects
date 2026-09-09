"""Wiki 知识库测试（FILE-005，Sprint-9 R3 门禁）。

覆盖：空间权限三态（BR-06 inherit 映射 / BR-07 whitelist 收窄编辑）/
页面树（BR-01 深度与移动环 / BR-02 同级同名）/ 草稿与发布（BR-04 覆盖式 /
BR-05 乐观锁 409）/ 回滚（BR-08 台账只增）/ 回收站与恢复（BR-09 过滤口径）/
知识检索（BR-10 权限前置 / BR-11 标题 3x 权重与片段）。
夹具风格对照 test_portfolio.py。
"""
from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from plane.db.models import (
    Project,
    ProjectMember,
    ProjectRole,
    User,
    WikiPage,
    WikiSpace,
    Workspace,
    WorkspaceMember,
    WorkspaceRole,
)
from plane.db.services.wiki import WikiService, render_tiptap, search_wiki

pytestmark = pytest.mark.django_db

DOC = {"type": "doc", "content": [
    {"type": "heading", "content": [{"type": "text", "text": "错误码约定"}]},
    {"type": "paragraph", "content": [{"type": "text", "text": "所有接口错误码必须从注册表选取，禁止自创。"}]},
]}


@pytest.fixture()
def env(db):
    owner = User.objects.create_user(email="wk-owner@rabbit.dev", password="Rabbit123!",
                                     display_name="管理员")
    ws = Workspace.objects.create(name="WK", slug="w-wiki-test", owner=owner, created_by=owner)
    WorkspaceMember.objects.create(workspace=ws, member=owner,
                                   role=WorkspaceRole.OWNER, created_by=owner)
    proj = Project.objects.create(name="电商重构", identifier="RBT", workspace=ws, created_by=owner)
    ProjectMember.objects.create(project=proj, member=owner, role=ProjectRole.ADMIN, created_by=owner)
    # contributor（editor）与 viewer
    editor = User.objects.create_user(email="wk-editor@rabbit.dev", password="Rabbit123!")
    viewer = User.objects.create_user(email="wk-viewer@rabbit.dev", password="Rabbit123!")
    for u, role in ((editor, WorkspaceRole.MEMBER), (viewer, WorkspaceRole.MEMBER)):
        WorkspaceMember.objects.create(workspace=ws, member=u, role=role, created_by=owner)
    ProjectMember.objects.create(project=proj, member=editor, role=ProjectRole.CONTRIBUTOR)
    ProjectMember.objects.create(project=proj, member=viewer, role=ProjectRole.VIEWER)
    space = WikiSpace.objects.create(project=proj, name="研发规范", created_by=owner)
    return {"owner": owner, "editor": editor, "viewer": viewer,
            "ws": ws, "proj": proj, "space": space}


def _client(user) -> APIClient:
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _base(env) -> str:
    return f"/api/v1/workspaces/{env['ws'].slug}/wiki/"


def _mk_page(env, title, parent=None, space=None) -> WikiPage:
    return WikiPage.objects.create(
        space=space or env["space"], parent_id=parent.id if parent else None,
        title=title, depth=(parent.depth + 1) if parent else 1,
        created_by=env["owner"])


# ────────────────────────────────────────────────────────────────
# 1. 渲染派生与发布链（BR-03/04/05/08）
# ────────────────────────────────────────────────────────────────
class TestPublishChain:
    def test_render_tiptap_html_text(self):
        html, text = render_tiptap(DOC)
        assert "<h3>错误码约定</h3>" in html
        assert "所有接口错误码" in text and "禁止自创" in text

    def test_publish_and_version_chain(self, env):
        page = _mk_page(env, "API 设计")
        page.draft_json = DOC
        page.save()
        v1 = WikiService().publish(page_id=page.id, actor=env["owner"],
                                   base_version_id=None, change_summary="初版")
        assert v1.version_no == 1
        page.refresh_from_db()
        assert page.current_version_id == v1.id and page.draft_json is None
        # 再发布：基于 v1（refresh 防旧对象全量 save 回写 current_version）
        page.refresh_from_db()
        page.draft_json = DOC
        page.base_version = v1
        page.save(update_fields=["draft_json", "base_version", "updated_at"])
        v2 = WikiService().publish(page_id=page.id, actor=env["owner"],
                                   base_version_id=v1.id)
        assert v2.version_no == 2

    def test_publish_conflict_409(self, env):
        """BR-05 乐观锁：base_version 落后 → RESOURCE_CONFLICT。"""
        page = _mk_page(env, "冲突页")
        page.draft_json = DOC
        page.save()
        v1 = WikiService().publish(page_id=page.id, actor=env["owner"], base_version_id=None)
        # 第二人基于同一 v1 发布 → 冲突
        page.refresh_from_db()
        page.draft_json = DOC
        page.base_version = v1
        page.save(update_fields=["draft_json", "base_version", "updated_at"])
        WikiService().publish(page_id=page.id, actor=env["editor"],
                                        base_version_id=v1.id)
        # 本人（落后 base）再发 → 409
        page.refresh_from_db()
        page.draft_json = DOC
        page.base_version = v1
        page.save(update_fields=["draft_json", "base_version", "updated_at"])
        from plane.db.services.wiki import VersionConflictError
        with pytest.raises(VersionConflictError):
            WikiService().publish(page_id=page.id, actor=env["owner"],
                                  base_version_id=v1.id)

    def test_rollback_creates_new_version(self, env):
        """BR-08：回滚生成新版本（台账只增）+ 溯源。"""
        page = _mk_page(env, "回滚页")
        page.draft_json = DOC
        page.save()
        v1 = WikiService().publish(page_id=page.id, actor=env["owner"], base_version_id=None)
        v2_doc = {"type": "doc", "content": [{"type": "paragraph", "content": [
            {"type": "text", "text": "v2 内容"}]}]}
        page.refresh_from_db()
        page.draft_json = v2_doc
        page.base_version = v1
        page.save(update_fields=["draft_json", "base_version", "updated_at"])
        WikiService().publish(page_id=page.id, actor=env["owner"], base_version_id=v1.id)
        v3 = WikiService().rollback(page_id=page.id, actor=env["owner"],
                                    target_version_id=v1.id)
        assert v3.version_no == 3
        assert str(v3.rolled_back_from_id) == str(v1.id)
        assert "回滚自 v1" in v3.change_summary
        # 目标即当前版本 → 400
        from plane.db.services.wiki import RollbackTargetError
        with pytest.raises(RollbackTargetError):
            WikiService().rollback(page_id=page.id, actor=env["owner"],
                                   target_version_id=v3.id)


# ────────────────────────────────────────────────────────────────
# 2. 权限三态（BR-06/07）
# ────────────────────────────────────────────────────────────────
class TestPermissionTiers:
    def test_inherit_mapping(self, env):
        svc = WikiService()
        assert svc.can_view_wiki(env["viewer"], env["space"])       # VIEWER → viewer
        assert svc.can_view_wiki(env["editor"], env["space"])       # CONTRIBUTOR → viewer+
        assert svc._is_manager(env["owner"], env["space"])          # ADMIN → manager
        from plane.db.services.wiki import EditPermissionError
        with pytest.raises(EditPermissionError):                    # viewer 不可编辑
            svc._assert_editable(env["viewer"], env["space"])
        svc._assert_editable(env["editor"], env["space"])           # CONTRIBUTOR → editor

    def test_whitelist_narrows_edit_only(self, env):
        """BR-07：whitelist 收窄编辑、不收窄查看。"""
        env["space"].permission_mode = WikiSpace.PermissionMode.WHITELIST
        env["space"].editor_whitelist = []
        env["space"].save()
        svc = WikiService()
        assert svc.can_view_wiki(env["viewer"], env["space"])        # 查看不收窄
        from plane.db.services.wiki import EditPermissionError
        with pytest.raises(EditPermissionError):                     # 不在白名单 → 不可编辑
            svc._assert_editable(env["editor"], env["space"])
        env["space"].editor_whitelist = [{"type": "member", "id": str(env["editor"].id)}]
        env["space"].save()
        svc._assert_editable(env["editor"], env["space"])            # 白名单放行

    def test_api_permission_matrix(self, env):
        """owner(manager) 建 ✓ / editor 发布 ✓ / viewer 403。"""
        resp = _client(env["owner"]).post(
            f"{_base(env)}spaces/", {"project_id": str(env["proj"].id), "name": "新空间"},
            format="json")
        assert resp.status_code == 201
        assert _client(env["viewer"]).post(
            f"{_base(env)}spaces/", {"project_id": str(env["proj"].id), "name": "V 建"},
            format="json").status_code == 403
        page = _mk_page(env, "P1")
        resp = _client(env["editor"]).post(
            f"{_base(env)}pages/{page.id}/publish/", {"base_version_id": None}, format="json")
        assert resp.status_code == 201
        page2 = _mk_page(env, "P2")
        assert _client(env["viewer"]).post(
            f"{_base(env)}pages/{page2.id}/publish/", {"base_version_id": None},
            format="json").status_code == 403


# ────────────────────────────────────────────────────────────────
# 3. 页面树（BR-01/02）
# ────────────────────────────────────────────────────────────────
class TestPageTree:
    def test_depth_limit_five(self, env):
        node = None
        for i in range(5):
            node = _mk_page(env, f"L{i+1}", parent=node)
        resp = _client(env["owner"]).post(
            f"{_base(env)}pages/",
            {"space_id": str(env["space"].id), "title": "L6", "parent_id": str(node.id)},
            format="json")
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "RESOURCE_LIMIT_EXCEEDED"

    def test_same_level_duplicate_title(self, env):
        _mk_page(env, "同名")
        resp = _client(env["owner"]).post(
            f"{_base(env)}pages/",
            {"space_id": str(env["space"].id), "title": "同名"}, format="json")
        assert resp.status_code == 409

    def test_move_cycle_rejected(self, env):
        p1 = _mk_page(env, "A")
        p2 = _mk_page(env, "B", parent=p1)
        resp = _client(env["owner"]).post(
            f"{_base(env)}pages/{p1.id}/move/", {"parent_id": str(p2.id)}, format="json")
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "RESOURCE_CIRCULAR_DEPENDENCY"


# ────────────────────────────────────────────────────────────────
# 4. 回收站（BR-09）
# ────────────────────────────────────────────────────────────────
class TestTrash:
    def test_soft_delete_tree_and_restore(self, env):
        p1 = _mk_page(env, "根")
        p2 = _mk_page(env, "子", parent=p1)
        resp = _client(env["editor"]).delete(f"{_base(env)}pages/{p1.id}/")
        assert resp.status_code == 200
        assert WikiPage.objects.filter(id=p2.id).first() is None   # 整树软删（默认管理器不可见）
        # 回收站：editor（删除人）可见
        trash = _client(env["editor"]).get(
            f"{_base(env)}pages/trash/?space_id={env['space'].id}").json()["data"]
        assert any(t["id"] == str(p1.id) for t in trash)
        # viewer 无 wiki.update 权限 → 403
        assert _client(env["viewer"]).get(
            f"{_base(env)}pages/trash/?space_id={env['space'].id}").status_code == 403
        # 恢复
        resp = _client(env["editor"]).post(f"{_base(env)}pages/{p1.id}/restore/")
        assert resp.status_code == 200
        assert WikiPage.objects.filter(id=p2.id).first() is not None


# ────────────────────────────────────────────────────────────────
# 5. 知识检索（BR-10/11）
# ────────────────────────────────────────────────────────────────
class TestSearch:
    def _publish_page(self, env, title, text, space=None):
        page = WikiPage.objects.create(
            space=space or env["space"], title=title, created_by=env["owner"],
            draft_json={"type": "doc", "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": text}]}]})
        WikiService().publish(page_id=page.id, actor=env["owner"], base_version_id=None)
        return page

    def test_search_title_weight_and_snippet(self, env):
        self._publish_page(env, "错误码规范总纲", "正文提及约定的其它内容")
        self._publish_page(env, "上线 checklist", "确认错误码监控面板无异常峰值后方可放量")
        hits = search_wiki(actor_id=env["viewer"].id, q="错误码")
        assert len(hits) == 2
        assert hits[0]["title"] == "错误码规范总纲"     # 标题 3x 权重置顶
        assert "错误码" in hits[1]["snippet"]            # ts_headline 命中片段

    def test_search_permission_prefiltered(self, env):
        """BR-10：非项目成员且非 WS 管理员 → 检索不可见。"""
        self._publish_page(env, "机密页", "错误码内部约定")
        outsider = User.objects.create_user(email="wk-out@rabbit.dev", password="Rabbit123!")
        WorkspaceMember.objects.create(workspace=env["ws"], member=outsider,
                                       role=WorkspaceRole.MEMBER, created_by=env["owner"])
        assert search_wiki(actor_id=outsider.id, q="错误码") == []
        # WS 管理员（非项目成员）隐式可见（rbac §7.4）
        ws_admin = User.objects.create_user(email="wk-wsadmin@rabbit.dev", password="Rabbit123!")
        WorkspaceMember.objects.create(workspace=env["ws"], member=ws_admin,
                                       role=WorkspaceRole.ADMIN, created_by=env["owner"])
        assert len(search_wiki(actor_id=ws_admin.id, q="错误码")) == 1

    def test_search_endpoint(self, env):
        self._publish_page(env, "规范页", "错误码约定正文")
        resp = _client(env["viewer"]).get(
            f"{_base(env)}search/?q=错误码&space_id={env['space'].id}")
        assert resp.status_code == 200
        assert resp.json()["meta"]["count"] >= 1
