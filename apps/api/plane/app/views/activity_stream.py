"""项目动态流视图（COLLAB-003 §4.2）。

端点（嵌套在 project 下，强制尾斜杠）：
  GET …/projects/{pid}/activities/                     动态流（合流 + 折叠 + 组感知游标）
  GET …/projects/{pid}/activities/?epoch=<float>       批量明细（轻量行，meta 翻页豁免）

权限：project.read（VIEWER+ 全员）——可见域由 get_project_or_404 一次收口
（rbac §6 第三层：非成员/不可见项目 404 存在性隐藏，BR-01）。
任务时间线 Tab（…/issues/{id}/activities/，TASK-010）零改动，两形态分工固化（§1.2）。
"""
from __future__ import annotations

import uuid
from typing import Any

from rest_framework.views import APIView

from plane.app.permissions import IsAuthenticated
from plane.app.views._access import get_project_or_404
from plane.base.exception import AppException
from plane.base.response import success_response
from plane.db.models import Issue, ProjectRole, User
from plane.db.services.activity_stream import (
    DETAIL_LIMIT,
    EVENT_CHOICES,
    GROUP_PAGE_SIZE,
    MAX_GROUPS_PER_PAGE,
    batch_detail,
    fetch_stream_page,
    parse_epoch,
)

#: 空基 UUID：非法 actor_id 的过滤语义兜底（BR-09：过滤是缩小不是寻址——
#: 坏值按空集处理，不 404、不 500；nil UUID 不匹配任何 actor 行）
_NIL_UUID = uuid.UUID(int=0)


class ProjectActivityStreamView(APIView):
    """GET /workspaces/{slug}/projects/{pid}/activities/ —— §4.2.1 / §4.2.2。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(
            kwargs["slug"], kwargs["project_id"], request.user)
        # project.read（VIEWER+）：成员角色恒 ≥ VIEWER，此闸为显式冗余防护
        if (getattr(project, "current_user_role", None) or 0) < ProjectRole.VIEWER:
            raise AppException("PERM_ROLE_INSUFFICIENT", message="当前角色权限不足")

        # event 白名单（BR-08：未知值 400，不静默忽略）
        event = request.query_params.get("event")
        if event is not None and event not in EVENT_CHOICES:
            raise AppException(
                "VALIDATION_INVALID_PARAM",
                message="查询参数非法",
                details=[{"field": "event", "code": "NOT_A_CHOICE",
                          "message": f"可用值：{' / '.join(EVENT_CHOICES)}"}],
            )

        # §4.2.2 批量明细分支（?epoch=）：同端点参数分支，轻量行 + meta 显式豁免
        if (epoch_raw := request.query_params.get("epoch")) is not None:
            return self._detail(request, project.id, epoch_raw)

        # actor_id（BR-09）：合法但域外 → 空集；非 UUID 坏值 → 同空集语义
        actor_raw = request.query_params.get("actor_id")
        actor_id = self._parse_actor(actor_raw)
        per_page, degraded = self._parse_per_page(request)

        result = fetch_stream_page(
            project_id=project.id, event=event, actor_id=actor_id,
            cursor=request.query_params.get("cursor"), per_page=per_page,
        )
        data = self._hydrate(result["rows"])

        # §6.3 必含 9 字段 + BR-12 stream_cursor（仅首页）
        meta: dict[str, Any] = {
            "next_cursor": result["next_cursor"],
            "prev_cursor": None,                  # is_prev 恒 0 的显式豁免（要点 5）
            "next_page_results": result["has_next"],
            "prev_page_results": False,
            "count": len(data),                   # 折叠后视觉行数（batch 组计 1 行）
            "total_count": result["total_count"],
            "total_pages": (result["total_groups"] + per_page - 1) // per_page,
            "page": result["page"],
            "per_page": per_page,                 # 按组数计（BR-11 显式豁免）
        }
        if result["total_count_estimated"]:
            meta["total_count_estimated"] = True  # api-conventions §6.4
        if result["stream_cursor"]:
            meta["stream_cursor"] = result["stream_cursor"]
        if degraded:
            meta["degraded"] = degraded
        return success_response(data, meta=meta)

    # ── 批量明细（§4.2.2）──
    @staticmethod
    def _detail(request, project_id: uuid.UUID, epoch_raw: str):
        epoch = parse_epoch(epoch_raw)            # 非数值 → 400（寻址型参数，§2.5）
        limit = DETAIL_LIMIT
        if raw := request.query_params.get("per_page"):
            try:
                limit = min(max(int(raw), 1), DETAIL_LIMIT)
            except (TypeError, ValueError):
                pass
        result = batch_detail(project_id=project_id, epoch=epoch, limit=limit)
        # meta 显式豁免（§4.2.2）：定长 ≤100 截断端点无翻页语义——
        # 仅 count/total_count/truncated/limit 四字段
        return success_response(result["data"], meta={
            "count": result["count"],
            "total_count": result["total_count"],
            "truncated": result["truncated"],
            "limit": result["limit"],
        })

    # ── 参数解析 ──
    @staticmethod
    def _parse_actor(actor_raw: str | None) -> uuid.UUID | None:
        if not actor_raw:
            return None
        try:
            return uuid.UUID(actor_raw)
        except (ValueError, AttributeError):
            return _NIL_UUID                      # BR-09 空集语义（不寻址报错）

    @staticmethod
    def _parse_per_page(request) -> tuple[int, dict[str, str] | None]:
        """per_page 按组数计：默认 30、上限 50（BR-11 显式豁免——超限静默截断）。"""
        raw = request.query_params.get("per_page")
        try:
            requested = int(raw) if raw is not None else GROUP_PAGE_SIZE
        except (TypeError, ValueError):
            requested = GROUP_PAGE_SIZE
        per_page = min(max(requested, 1), MAX_GROUPS_PER_PAGE)
        if requested > MAX_GROUPS_PER_PAGE:
            return per_page, {"per_page": f"per_page 已截断为 {MAX_GROUPS_PER_PAGE}"
                                          "（动态流按组数分页，上限 50）"}
        return per_page, None

    # ── 引用装配（actor / issue 内联投影）──
    @staticmethod
    def _hydrate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        actor_ids: set[str] = set()
        issue_ids: set[str] = set()
        for r in rows:
            if r.get("actor_id"):
                actor_ids.add(r["actor_id"])
            if r.get("reply_to_actor_id"):
                actor_ids.add(r["reply_to_actor_id"])
            if r.get("issue_id"):
                issue_ids.add(r["issue_id"])

        users = {
            str(u.id): u
            for u in User.objects.filter(id__in=[uuid.UUID(x) for x in actor_ids])
        } if actor_ids else {}
        # 整圈取任务（all_objects）：软删/归档任务的动态保留在流中（BR-06/07），
        # 投影 is_deleted/is_archived 供前端链接态渲染（§4.1 双层口径）
        issues = {
            str(i["id"]): i
            for i in Issue.all_objects.filter(
                id__in=[uuid.UUID(x) for x in issue_ids]
            ).values("id", "name", "sequence_id", "deleted_at", "archived_at",
                     "project__identifier")
        } if issue_ids else {}

        def actor_payload(uid: str | None) -> dict[str, Any] | None:
            if not uid:
                return None                       # actor 为空 → 前端 ⚙系统兜底行（BR-13）
            u = users.get(uid)
            if u is None:
                return {"id": uid, "display_name": "已注销用户", "avatar_url": None}
            return {"id": uid, "display_name": u.display_name,
                    "avatar_url": u.avatar_url or None}

        def issue_payload(iid: str | None) -> dict[str, Any] | None:
            i = issues.get(iid or "")
            if i is None:
                return None
            return {
                "id": str(i["id"]),
                "issue_key": f"{i['project__identifier']}-{i['sequence_id']}",
                "name": i["name"],
                "is_deleted": i["deleted_at"] is not None,
                "is_archived": i["archived_at"] is not None,
            }

        out: list[dict[str, Any]] = []
        for r in rows:
            row = dict(r)
            row["actor"] = actor_payload(r.get("actor_id"))
            row["issue"] = issue_payload(r.get("issue_id"))
            row["created_at"] = r["created_at"].isoformat()
            row.pop("actor_id", None)
            row.pop("issue_id", None)
            if r["kind"] == "comment":
                # reply_to_actor 展示闭包沿用 COLLAB-002 §4.2 词汇（已注销兜底同款）
                reply_id = r.get("reply_to_actor_id")
                reply_user = users.get(reply_id) if reply_id else None
                row["reply_to_actor"] = (
                    {"id": reply_id,
                     "display_name": reply_user.display_name if reply_user else "已注销用户"}
                    if reply_id else None
                )
                row.pop("reply_to_actor_id", None)
            out.append(row)
        return out
