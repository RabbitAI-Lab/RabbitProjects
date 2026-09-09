"""Wiki 服务（FILE-005 §4.3/§4.4，Sprint-9）——发布/回滚/权限三态/知识检索。

发布乐观锁（BR-05 base_version 比对 → 409 RESOURCE_CONFLICT）；回滚生成新
版本（BR-08 台账只增）；content_html/text 服务端派生（BR-03，Issue 描述同
管线）；检索经 raw SQL（BR-10 权限 EXISTS 前置 + BR-11 标题 3x 权重 +
ts_headline 片段 ≤160 字）。
"""
from __future__ import annotations

import uuid

from django.db import connection, transaction

from plane.db.models import ProjectMember, ProjectRole, WikiPage, WikiPageVersion, WikiSpace


class WikiError(Exception):
    """业务失败基类（视图层转 AppException）。"""


class VersionConflictError(WikiError):
    """BR-05 乐观锁：他人已发布更新版本 → 409 RESOURCE_CONFLICT。"""


class RollbackTargetError(WikiError):
    """BR-08：目标即当前版本，无需回滚 → 400。"""


class EditPermissionError(WikiError):
    """BR-06/07：非 editor/manager → 403。"""


def render_tiptap(node: dict | None) -> tuple[str, str]:
    """Tiptap JSON → (html, text)（服务端派生 BR-03；Issue 描述同管线的文档域实现）。

    纯结构遍历，不做白名单清洗（发布者受 wiki.update 门槛约束——与 Issue
    description_html 同信任级别）。
    """
    if not isinstance(node, dict):
        return "", ""

    def walk(n, mode: str) -> list[str]:
        if not isinstance(n, dict):
            return []
        t = n.get("type", "")
        if mode == "html":
            if t == "text":
                return [str(n.get("text", ""))]
            if t == "hardBreak":
                return ["<br>"]
            kids = "".join(x for c in n.get("content", []) for x in walk(c, mode))
            tags = {
                "doc": None, "paragraph": "p", "heading": "h3",
                "bulletList": "ul", "orderedList": "ol", "listItem": "li",
                "blockquote": "blockquote", "codeBlock": "pre",
                "bold": "strong", "italic": "em", "code": "code",
            }
            tag = tags.get(t)
            if tag is None:
                return [kids] if kids else []
            return [f"<{tag}>{kids}</{tag}>"]
        # text 态：文本节点 + 块级换行
        if t == "text":
            return [str(n.get("text", ""))]
        if t == "hardBreak":
            return ["\n"]
        parts: list[str] = []
        for c in n.get("content", []):
            parts.append("".join(walk(c, mode)))
        if t in ("paragraph", "heading", "listItem", "codeBlock", "blockquote"):
            parts.append("\n")
        return ["".join(parts)]

    html = "".join(walk(node, "html"))
    text = "".join(walk(node, "text")).strip()
    return html, text


class WikiService:
    # ── 权限三态（BR-06 单入口）─────────────────────────────────

    @staticmethod
    def project_role(actor, project_id) -> int:
        """有效项目角色（隐式提升：WS_ADMIN+ → PROJ_ADMIN，rbac §7.4）。"""
        row = ProjectMember.objects.filter(
            project_id=project_id, member=actor, is_active=True).values_list("role", flat=True).first()
        if row is not None:
            return row
        from plane.db.models import WorkspaceMember, WorkspaceRole
        ws_role = WorkspaceMember.objects.filter(
            workspace__projects__id=project_id, member=actor, is_active=True
        ).values_list("role", flat=True).first()
        if ws_role is not None and ws_role >= WorkspaceRole.ADMIN:
            return ProjectRole.ADMIN
        return 0

    def can_view_wiki(self, actor, space) -> bool:
        """BR-06：viewer 门槛（白名单仅收窄编辑，不收窄查看 BR-07）。"""
        return self.project_role(actor, space.project_id) >= ProjectRole.VIEWER

    def _is_manager(self, actor, space) -> bool:
        return self.is_manager_of_project(actor, space.project_id)

    def is_manager_of_project(self, actor, project_id) -> bool:
        return self.project_role(actor, project_id) >= ProjectRole.ADMIN

    def _assert_editable(self, actor, space) -> None:
        """editor/manager 可编辑；whitelist 模式下白名单成员额外放行（BR-07）。"""
        if self._is_manager(actor, space):
            return
        if self.project_role(actor, space.project_id) >= ProjectRole.CONTRIBUTOR:
            if space.permission_mode != WikiSpace.PermissionMode.WHITELIST:
                return
            if any(str(e.get("id")) == str(actor.id) for e in (space.editor_whitelist or [])):
                return
        raise EditPermissionError("仅空间编辑者可执行此操作")

    # ── 发布 / 回滚（BR-04/05/08）──────────────────────────────

    @transaction.atomic
    def publish(self, *, page_id, actor, base_version_id, change_summary: str = "") -> WikiPageVersion:
        page = (WikiPage.objects.select_for_update(of=("self",))
                .select_related("current_version").get(pk=page_id))
        self._assert_editable(actor, page.space)
        current_id = str(page.current_version_id) if page.current_version_id else None
        if current_id != (str(base_version_id) if base_version_id else None):
            raise VersionConflictError(
                f"他人已发布 v{page.current_version.version_no}，你的草稿基于更早版本"
                if page.current_version else "页面尚无发布版本")
        html, text = render_tiptap(page.draft_json)
        next_no = (page.current_version.version_no + 1) if page.current_version else 1
        version = WikiPageVersion.objects.create(
            page=page, version_no=next_no,
            content_json=page.draft_json or {}, content_html=html, content_text=text,
            change_summary=change_summary, created_by=actor)
        page.current_version = version
        page.base_version = version
        page.draft_json = None
        page.save(update_fields=["current_version", "base_version", "draft_json", "updated_at"])
        return version

    @transaction.atomic
    def rollback(self, *, page_id, actor, target_version_id) -> WikiPageVersion:
        """BR-08：回滚 = 以历史版本内容生成新版本（台账只增）。

        先将目标内容落回滚草稿并 save，再走 publish——publish 会在事务内重查
        页面，未 save 直接调用会读到库中旧草稿、把旧稿发布出去（规格 §4.3 注）。
        """
        page = (WikiPage.objects.select_for_update(of=("self",))
                .select_related("space").get(pk=page_id))
        self._assert_editable(actor, page.space)
        target = WikiPageVersion.objects.filter(pk=target_version_id, page=page).first()
        if target is None:
            raise WikiError("目标版本不存在")
        if str(target.id) == str(page.current_version_id):
            raise RollbackTargetError("目标版本即当前发布版本")
        page.draft_json = target.content_json          # 覆盖式回滚草稿（BR-04）
        page.base_version = page.current_version
        page.save(update_fields=["draft_json", "base_version", "updated_at"])  # 先落库
        version = self.publish(page_id=page.id, actor=actor,
                               base_version_id=page.current_version_id,
                               change_summary=f"回滚自 v{target.version_no}")
        version.rolled_back_from = target
        version.save(update_fields=["rolled_back_from"])
        return version


# ────────────────────────────────────────────────────────────────
# 知识检索（BR-10/11，§4.4 raw SQL——权限 EXISTS 前置 + 标题 3x + ts_headline）
# ────────────────────────────────────────────────────────────────
SEARCH_SQL = """
SELECT p.id, p.title, s.name AS space_name, pr.identifier,
       ts_headline('simple', v.content_text, plainto_tsquery('simple', %(q)s),
                   'MaxWords=80, MinWords=20, MaxFragments=2') AS snippet,
       (similarity(p.title, %(q)s) * 3 + similarity(v.content_text, %(q)s)) AS rank
  FROM wiki_pages p
  JOIN wiki_spaces s   ON s.id = p.space_id AND s.deleted_at IS NULL
  JOIN projects pr     ON pr.id = s.project_id AND pr.deleted_at IS NULL
  JOIN wiki_page_versions v ON v.id = p.current_version_id
 WHERE p.deleted_at IS NULL
   AND (   EXISTS (SELECT 1 FROM project_members pm
                    WHERE pm.project_id = pr.id
                      AND pm.member_id = %(actor)s AND pm.deleted_at IS NULL
                      AND pm.is_active = TRUE)
        OR EXISTS (SELECT 1 FROM workspace_members wm
                    WHERE wm.workspace_id = pr.workspace_id
                      AND wm.member_id = %(actor)s AND wm.role >= 15
                      AND wm.deleted_at IS NULL AND wm.is_active = TRUE))
   AND (p.title ILIKE '%%' || %(q)s || '%%'
        OR v.content_text ILIKE '%%' || %(q)s || '%%'
        OR similarity(p.title, %(q)s) >= 0.3
        OR similarity(v.content_text, %(q)s) >= 0.05)
   AND (%(project_id)s::uuid IS NULL OR pr.id = %(project_id)s::uuid)
   AND (%(space_id)s::uuid IS NULL OR s.id = %(space_id)s::uuid)
 ORDER BY rank DESC, v.created_at DESC, v.id DESC
 LIMIT %(per_page)s
"""


def search_wiki(*, actor_id: uuid.UUID, q: str, project_id=None, space_id=None,
                per_page: int = 100) -> list[dict]:
    if not q or not q.strip():
        return []
    with connection.cursor() as cursor:
        cursor.execute(SEARCH_SQL, {
            "q": q.strip(), "actor": str(actor_id),
            "project_id": str(project_id) if project_id else None,
            "space_id": str(space_id) if space_id else None,
            "per_page": min(per_page, 100)})
        rows = cursor.fetchall()
    return [{"page_id": str(r[0]), "title": r[1], "space_name": r[2],
             "identifier": r[3], "snippet": r[4], "rank": round(r[5], 6)}
            for r in rows]
