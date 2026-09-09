"""Wiki 视图（FILE-005 §4.5，Sprint-9）——13 端点。

权限三态（BR-06）：viewer/editor/manager 经 WikiService 单入口判定（inherit
按项目角色映射；whitelist 收窄编辑 BR-07）。URL 无 project_id 段，故不走
require_permission 装饰器（其角色解析依赖路径段）——三态判定即规格定义的
Permission 单入口。
"""
from __future__ import annotations

from rest_framework.exceptions import NotFound
from rest_framework.views import APIView

from plane.app.permissions import IsAuthenticated
from plane.app.views._access import get_workspace_or_404
from plane.base.exception import AppException
from plane.base.response import created_response, success_response
from plane.db.models import Project, WikiPage, WikiSpace
from plane.db.services.wiki import (
    EditPermissionError,
    RollbackTargetError,
    VersionConflictError,
    WikiError,
    WikiService,
    search_wiki,
)

_SVC = WikiService()


def _require_view(request, space) -> None:
    if not _SVC.can_view_wiki(request.user, space):
        raise AppException("PERM_ROLE_INSUFFICIENT")


def _require_edit(request, space) -> None:
    try:
        _SVC._assert_editable(request.user, space)
    except EditPermissionError:
        raise AppException("PERM_ROLE_INSUFFICIENT", message="仅空间编辑者可执行此操作") from None


def _require_manage(request, space) -> None:
    if not _SVC._is_manager(request.user, space.project_id):
        raise AppException("PERM_ROLE_INSUFFICIENT", message="仅空间管理员可执行此操作")


def _can_edit_space(user, space) -> bool:
    try:
        _SVC._assert_editable(user, space)
        return True
    except EditPermissionError:
        return False


def _get_space_or_404(space_id) -> WikiSpace:
    space = WikiSpace.objects.select_related("project").filter(id=space_id).first()
    if space is None:
        raise NotFound("RESOURCE_NOT_FOUND") from None
    return space


def _get_page_or_404(page_id) -> WikiPage:
    page = (WikiPage.objects.select_related("space", "space__project",
                                            "current_version").filter(id=page_id).first())
    if page is None:
        raise NotFound("RESOURCE_NOT_FOUND") from None
    return page


def _page_payload(page: WikiPage, *, with_content: bool = False) -> dict:
    v = page.current_version
    data = {
        "id": str(page.id), "space_id": str(page.space_id),
        "parent_id": str(page.parent_id) if page.parent_id else None,
        "title": page.title, "depth": page.depth, "sort_order": page.sort_order,
        "version_no": v.version_no if v else 0,
        "has_draft": page.draft_json is not None,
        "updated_at": page.updated_at,
    }
    if with_content and v is not None:
        data.update({
            "description": v.content_json,          # 三格式对齐 unified-issue-model 命名
            "description_html": v.content_html,
            "description_stripped": v.content_text,
            "change_summary": v.change_summary,
        })
    return data


def _tree_payload(space: WikiSpace) -> list[dict]:
    pages = list(space.pages.order_by("depth", "sort_order", "created_at"))
    nodes = {p.id: {**_page_payload(p), "children": []} for p in pages}
    roots = []
    for p in pages:
        node = nodes[p.id]
        if p.parent_id and p.parent_id in nodes:
            nodes[p.parent_id]["children"].append(node)
        else:
            roots.append(node)
    return roots


class WikiSpaceListCreateView(APIView):
    """GET/POST wiki/spaces/?project_id= —— 空间列表 / 新建。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, slug):
        ws, _ = get_workspace_or_404(slug, request.user)
        project = Project.objects.filter(
            id=request.query_params.get("project_id"), workspace=ws).first()
        if project is None:
            raise NotFound("RESOURCE_NOT_FOUND") from None
        spaces = WikiSpace.objects.filter(project=project).order_by("name")
        visible = [s for s in spaces if _SVC.can_view_wiki(request.user, s)]
        return success_response([{
            "id": str(s.id), "name": s.name, "description": s.description,
            "permission_mode": s.permission_mode,
            "page_count": s.pages.count(),
        } for s in visible], meta={"count": len(visible)})

    def post(self, request, slug):
        ws, _ = get_workspace_or_404(slug, request.user)
        project = Project.objects.filter(
            id=request.data.get("project_id"), workspace=ws).first()
        if project is None:
            raise NotFound("RESOURCE_NOT_FOUND") from None
        if not _SVC.is_manager_of_project(request.user, project.id):
            raise AppException("PERM_ROLE_INSUFFICIENT", message="仅空间管理员可新建空间")
        name = (request.data.get("name") or "").strip()
        if not name:
            raise AppException("VALIDATION_ERROR",
                               details=[{"field": "name", "code": "REQUIRED", "message": "名称必填"}])
        if WikiSpace.objects.filter(project=project, name=name).exists():
            raise AppException("RESOURCE_ALREADY_EXISTS", message="同名空间已存在")
        space = WikiSpace.objects.create(
            project=project, name=name,
            description=request.data.get("description") or "",
            permission_mode=request.data.get("permission_mode") or WikiSpace.PermissionMode.INHERIT,
            editor_whitelist=request.data.get("editor_whitelist") or [],
            created_by=request.user)
        return created_response(
            {"id": str(space.id), "name": space.name},
            location=f"/api/v1/workspaces/{slug}/wiki/spaces/{space.id}/")


class WikiSpaceDetailView(APIView):
    """GET/PATCH/DELETE wiki/spaces/{space_id}/ —— 详情（完整树）/ 设置 / 删除。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, slug, space_id):
        space = _get_space_or_404(space_id)
        _require_view(request, space)
        return success_response({
            "id": str(space.id), "name": space.name, "description": space.description,
            "permission_mode": space.permission_mode,
            "editor_whitelist": space.editor_whitelist,
            "pages": _tree_payload(space),
        })

    def patch(self, request, slug, space_id):
        space = _get_space_or_404(space_id)
        _require_manage(request, space)
        for f in ("name", "description", "permission_mode", "editor_whitelist"):
            if f in request.data:
                setattr(space, f, request.data[f])
        space.updated_by = request.user
        space.save()
        return success_response({"id": str(space.id)})

    def delete(self, request, slug, space_id):
        space = _get_space_or_404(space_id)
        _require_manage(request, space)
        space.soft_delete(actor_id=request.user.id)
        return success_response({"id": str(space.id)})


class WikiPageListCreateView(APIView):
    """GET/POST wiki/pages/?space_id= —— 页面树 / 新建（BR-01 深度）。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, slug):
        space = _get_space_or_404(request.query_params.get("space_id"))
        _require_view(request, space)
        return success_response(_tree_payload(space))

    def post(self, request, slug):
        space = _get_space_or_404(request.data.get("space_id"))
        _require_edit(request, space)
        title = (request.data.get("title") or "").strip()
        if not title:
            raise AppException("VALIDATION_ERROR",
                               details=[{"field": "title", "code": "REQUIRED", "message": "标题必填"}])
        parent_id = request.data.get("parent_id") or None
        depth = 1
        if parent_id:
            parent = WikiPage.objects.filter(id=parent_id, space=space).first()
            if parent is None:
                raise AppException("VALIDATION_ERROR",
                                   details=[{"field": "parent_id", "code": "DOES_NOT_EXIST",
                                             "message": "父页面不存在"}]) from None
            if parent.depth >= WikiPage.MAX_DEPTH:
                raise AppException("RESOURCE_LIMIT_EXCEEDED",
                                   details=[{"field": "parent_id", "code": "DEPTH",
                                             "message": f"页面树深度上限 {WikiPage.MAX_DEPTH}"}])
            depth = parent.depth + 1
        if WikiPage.objects.filter(space=space, parent_id=parent_id, title=title).exists():
            raise AppException("RESOURCE_ALREADY_EXISTS", message="同级同名页面已存在")
        page = WikiPage.objects.create(
            space=space, parent_id=parent_id, title=title, depth=depth,
            draft_json=request.data.get("content") or None,
            created_by=request.user)
        return created_response(
            _page_payload(page),
            location=f"/api/v1/workspaces/{slug}/wiki/pages/{page.id}/")


class WikiPageDetailView(APIView):
    """GET/PATCH/DELETE wiki/pages/{page_id}/ —— 详情三格式 / 改名排序 / 软删。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, slug, page_id):
        page = _get_page_or_404(page_id)
        _require_view(request, page.space)
        return success_response(_page_payload(page, with_content=True))

    def patch(self, request, slug, page_id):
        page = _get_page_or_404(page_id)
        _require_edit(request, page.space)
        for f in ("title", "sort_order"):
            if f in request.data:
                setattr(page, f, request.data[f])
        page.updated_by = request.user
        page.save()
        return success_response({"id": str(page.id)})

    def delete(self, request, slug, page_id):
        """软删整树进回收站（BR-09：无手动彻底删除，30 天 beat 硬删）。"""
        page = _get_page_or_404(page_id)
        _require_edit(request, page.space)
        page.soft_delete(actor_id=request.user.id)
        page.deleted_by = request.user  # 回收站过滤口径（BR-09）
        page.save(update_fields=["deleted_by"])
        _soft_delete_subtree(page, request.user)
        return success_response({"id": str(page.id)})


def _soft_delete_subtree(root: WikiPage, actor) -> None:
    from django.utils import timezone
    now = timezone.now()
    frontier = [root]
    while frontier:
        children = list(WikiPage.all_objects.filter(parent_id__in=[p.id for p in frontier]))
        for c in children:
            c.deleted_at = now
            c.deleted_by = actor
            c.save(update_fields=["deleted_at", "deleted_by", "updated_at"])
        frontier = children


class WikiPageDraftView(APIView):
    """PATCH wiki/pages/{page_id}/draft/ —— 草稿保存（覆盖式，BR-04）。"""

    permission_classes = [IsAuthenticated]

    def patch(self, request, slug, page_id):
        page = _get_page_or_404(page_id)
        _require_edit(request, page.space)
        page.draft_json = request.data.get("content")
        if "base_version_id" in request.data:
            page.base_version_id = request.data["base_version_id"] or None
        page.updated_by = request.user
        page.save(update_fields=["draft_json", "base_version", "updated_at"])
        return success_response({"id": str(page.id), "saved_at": page.updated_at})


class WikiPagePublishView(APIView):
    """POST wiki/pages/{page_id}/publish/ —— {base_version_id, change_summary}（BR-05）。"""

    permission_classes = [IsAuthenticated]

    def post(self, request, slug, page_id):
        page = _get_page_or_404(page_id)
        try:
            version = _SVC.publish(
                page_id=page.id, actor=request.user,
                base_version_id=request.data.get("base_version_id"),
                change_summary=request.data.get("change_summary") or "")
        except VersionConflictError as e:
            raise AppException("RESOURCE_CONFLICT",
                               details=[{"field": "base_version_id", "code": "INVALID",
                                         "message": str(e)}],
                               message="发布冲突：他人已发布更新版本") from None
        except EditPermissionError:
            raise AppException("PERM_ROLE_INSUFFICIENT") from None
        return created_response(
            {"version_no": version.version_no, "id": str(version.id)},
            location=f"/api/v1/workspaces/{slug}/wiki/pages/{page.id}/versions/{version.id}/")


class WikiPageMoveView(APIView):
    """POST wiki/pages/{page_id}/move/ —— {parent_id, sort_order}（BR-01 深度/环）。"""

    permission_classes = [IsAuthenticated]

    def post(self, request, slug, page_id):
        page = _get_page_or_404(page_id)
        _require_edit(request, page.space)
        new_parent_id = request.data.get("parent_id") or None
        if new_parent_id:
            if str(new_parent_id) == str(page.id):
                raise AppException("RESOURCE_CIRCULAR_DEPENDENCY",
                                   details=[{"field": "parent_id", "code": "CYCLE",
                                             "message": "父链成环"}])
            parent = WikiPage.objects.filter(id=new_parent_id, space=page.space).first()
            if parent is None:
                raise AppException("VALIDATION_ERROR",
                                   details=[{"field": "parent_id", "code": "DOES_NOT_EXIST",
                                             "message": "目标父页面不存在"}]) from None
            hop, cur = 0, parent
            while cur is not None and hop <= WikiPage.MAX_DEPTH:
                if str(cur.id) == str(page.id):
                    raise AppException("RESOURCE_CIRCULAR_DEPENDENCY",
                                       details=[{"field": "parent_id", "code": "CYCLE",
                                                 "message": "父链成环"}]) from None
                cur = cur.parent
                hop += 1
            subtree_depth = _subtree_depth(page)
            if parent.depth + subtree_depth > WikiPage.MAX_DEPTH:
                raise AppException("RESOURCE_LIMIT_EXCEEDED",
                                   details=[{"field": "parent_id", "code": "DEPTH",
                                             "message": f"页面树深度上限 {WikiPage.MAX_DEPTH}"}]) from None
            page.depth = parent.depth + 1
        else:
            page.depth = 1
        page.parent_id = new_parent_id
        if "sort_order" in request.data:
            page.sort_order = request.data["sort_order"]
        page.updated_by = request.user
        page.save()
        _resync_depths(page)
        return success_response({"id": str(page.id)})


def _subtree_depth(page: WikiPage) -> int:
    def d(p):
        kids = list(WikiPage.objects.filter(parent_id=p.id))
        return 1 + max((d(c) for c in kids), default=0)
    return d(page)


def _resync_depths(root: WikiPage) -> None:
    def walk(p, depth):
        for c in WikiPage.objects.filter(parent_id=p.id).exclude(id=p.id):
            if c.depth != depth:
                c.depth = depth
                c.save(update_fields=["depth", "updated_at"])
            walk(c, depth + 1)
    walk(root, root.depth)


class WikiPageVersionsView(APIView):
    """GET wiki/pages/{page_id}/versions/ —— 版本台账（游标分页）。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, slug, page_id):
        page = _get_page_or_404(page_id)
        _require_view(request, page.space)
        versions = (page.versions.order_by("-version_no")
                    .select_related("created_by")[:100])
        return success_response([{
            "id": str(v.id), "version_no": v.version_no,
            "change_summary": v.change_summary,
            "rolled_back_from": str(v.rolled_back_from_id) if v.rolled_back_from_id else None,
            "created_by": v.created_by.display_name if v.created_by else None,
            "created_at": v.created_at,
        } for v in versions], meta={"count": len(versions)})


class WikiPageVersionDetailView(APIView):
    """GET wiki/pages/{page_id}/versions/{version_id}/ —— 单版本三格式。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, slug, page_id, version_id):
        page = _get_page_or_404(page_id)
        _require_view(request, page.space)
        v = page.versions.filter(id=version_id).first()
        if v is None:
            raise NotFound("RESOURCE_NOT_FOUND") from None
        return success_response({
            "id": str(v.id), "version_no": v.version_no,
            "description": v.content_json,
            "description_html": v.content_html,
            "description_stripped": v.content_text,
            "change_summary": v.change_summary,
            "created_at": v.created_at,
        })


class WikiPageRollbackView(APIView):
    """POST wiki/pages/{page_id}/rollback/ —— {version_id}（BR-08 生成新版本）。"""

    permission_classes = [IsAuthenticated]

    def post(self, request, slug, page_id):
        page = _get_page_or_404(page_id)
        try:
            version = _SVC.rollback(
                page_id=page.id, actor=request.user,
                target_version_id=request.data.get("version_id"))
        except RollbackTargetError as e:
            raise AppException("VALIDATION_ERROR",
                               details=[{"field": "version_id", "code": "INVALID",
                                         "message": str(e)}]) from None
        except EditPermissionError:
            raise AppException("PERM_ROLE_INSUFFICIENT") from None
        except WikiError as e:
            raise AppException("VALIDATION_ERROR",
                               details=[{"field": "version_id", "code": "DOES_NOT_EXIST",
                                         "message": str(e)}]) from None
        return created_response(
            {"version_no": version.version_no, "id": str(version.id),
             "rolled_back_from": str(version.rolled_back_from_id)},
            location=f"/api/v1/workspaces/{slug}/wiki/pages/{page_id}/versions/{version.id}/")


class WikiTrashView(APIView):
    """GET wiki/pages/trash/?space_id=&project_id= —— 回收站（BR-09 过滤口径）。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, slug):
        ws, _ = get_workspace_or_404(slug, request.user)
        # 回收站是软删域的合法反查面：SoftDeleteManager 恒滤软删，走 all_objects
        qs = WikiPage.all_objects.filter(deleted_at__isnull=False)
        space_id = request.query_params.get("space_id")
        if space_id:
            qs = qs.filter(space_id=space_id)
        project_id = request.query_params.get("project_id")
        if project_id:
            qs = qs.filter(space__project_id=project_id)
        else:
            qs = qs.filter(space__project__workspace=ws)
        # wiki.update 门槛（editor+）：按 space_id/project_id 显式判（与项无关）
        sid = request.query_params.get("space_id")
        if sid:
            _require_edit(request, _get_space_or_404(sid))
        elif project_id:
            from plane.db.models import Project as _P
            proj = _P.objects.filter(id=project_id, workspace=ws).first()
            if proj is None:
                raise NotFound("RESOURCE_NOT_FOUND") from None
            from plane.db.models import ProjectRole as _PR
            if _SVC.project_role(request.user, proj.id) < _PR.CONTRIBUTOR:
                raise AppException("PERM_ROLE_INSUFFICIENT")
        # BR-09 过滤：manager 全量、非 manager 仅本人删除项
        candidates = qs.select_related("space", "space__project")[:100]
        visible = [p for p in candidates
                   if _SVC.is_manager_of_project(request.user, p.space.project_id)
                   or p.deleted_by_id == request.user.id]
        return success_response([{
            "id": str(p.id), "title": p.title, "space_id": str(p.space_id),
            "deleted_by": str(p.deleted_by_id) if p.deleted_by_id else None,
            "deleted_at": p.deleted_at,
        } for p in visible], meta={"count": len(visible)})


class WikiRestoreView(APIView):
    """POST wiki/pages/{page_id}/restore/ —— 整树恢复（BR-09；父已硬删回空间根）。"""

    permission_classes = [IsAuthenticated]

    def post(self, request, slug, page_id):
        page = WikiPage.all_objects.select_related("space", "space__project").filter(
            id=page_id).first()
        if page is None or page.deleted_at is None:
            raise NotFound("RESOURCE_NOT_FOUND") from None
        if not (_SVC.is_manager_of_project(request.user, page.space.project_id)
                or page.deleted_by_id == request.user.id):
            raise AppException("PERM_ROLE_INSUFFICIENT")
        if page.parent_id and WikiPage.objects.filter(
                id=page.parent_id, deleted_at__isnull=True).first() is None:
            page.parent_id = None   # 父不可用 → 恢复到空间根
            page.depth = 1
        page.deleted_at = None
        page.deleted_by = None
        page.updated_by = request.user
        page.save()
        _restore_subtree(page)
        return success_response({"id": str(page.id)})


def _restore_subtree(root: WikiPage) -> None:
    frontier = [root]
    while frontier:
        children = list(WikiPage.all_objects.filter(parent_id__in=[p.id for p in frontier]))
        for c in children:
            c.deleted_at = None
            c.deleted_by = None
            c.save(update_fields=["deleted_at", "deleted_by", "updated_at"])
        frontier = children


class WikiSearchView(APIView):
    """GET wiki/search/?q=&project_id=&space_id= —— 知识检索（BR-10/11）。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, slug):
        get_workspace_or_404(slug, request.user)  # WS 成员门槛（含非项目成员管理员）
        q = request.query_params.get("q", "")
        results = search_wiki(
            actor_id=request.user.id, q=q,
            project_id=request.query_params.get("project_id") or None,
            space_id=request.query_params.get("space_id") or None)
        return success_response(results, meta={"count": len(results), "query": q})
