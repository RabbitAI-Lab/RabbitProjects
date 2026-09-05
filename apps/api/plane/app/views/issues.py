"""工作项视图（TASK-002 §4.3 / TASK-003 §4.2 / BOARD-002 §4.2）。

端点：
  GET    /workspaces/{slug}/projects/{pid}/issues/             列表（filter+search+order+group）
  POST   /workspaces/{slug}/projects/{pid}/issues/             创建（type_id 必填）
  GET    /workspaces/{slug}/projects/{pid}/issues/{iid}/       详情
  PATCH  /workspaces/{slug}/projects/{pid}/issues/{iid}/       局部更新（全 P1 字段）
  DELETE /workspaces/{slug}/projects/{pid}/issues/{iid}/       软删除（PROJ_CONTRIBUTOR 或创建者）

  GET    /workspaces/{slug}/projects/{pid}/issue-types/        类型列表（= WS active）
  GET    /workspaces/{slug}/projects/{pid}/issues/{iid}/labels/    （预留 P2，本迭代不需要）
  PUT    /workspaces/{slug}/projects/{pid}/issues/{iid}/labels/    集合替换
  GET    /workspaces/{slug}/projects/{pid}/issues/{iid}/sub-issues/  子任务列表
  POST   /workspaces/{slug}/projects/{pid}/issues/{iid}/sub-issues/  挂载子任务（严格一层）
  GET    /workspaces/{slug}/projects/{pid}/issues/{iid}/activities/ 操作日志
"""
from __future__ import annotations

import base64
import time

from django.db import transaction
from django.db.models import Count, Max, Q
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.generics import ListCreateAPIView, RetrieveUpdateDestroyAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from plane.app.permissions import IsAuthenticated
from plane.app.serializers.issue import (
    IssueSerializer,
    IssueWriteSerializer,
    diff_labels,
    sync_assignees,
    sync_labels,
    validate_assignees,
)
from plane.app.views._access import get_project_or_404
from plane.base.exception import AppException
from plane.base.response import created_response, success_response
from plane.db.models import Issue, IssueActivity, IssueType, Label, State
from plane.db.models.roles import ProjectRole
from plane.db.services.issue_query import IssueFilterSet
from plane.db.services.issue_sequence import create_issue as create_issue_svc

# ── sub-issue / activity 端点的最大子任务数（BR-07 / TASK-002 §2.7）──
MAX_SUB_ISSUES_PER_PARENT = 100


# ─────────────────────────────────────────────────────────────────────
# 工具：activity 记录、completed_at 派生
# ─────────────────────────────────────────────────────────────────────
def _record_activity(issue, actor, *, verb, field=None, old=None, new=None,
                     old_identifier=None, new_identifier=None, comment="",
                     epoch: float | None = None):
    return IssueActivity.objects.create(
        issue=issue,
        actor=actor,
        verb=verb,
        field=field,
        old_value=str(old) if old is not None else None,
        new_value=str(new) if new is not None else None,
        old_identifier=old_identifier,
        new_identifier=new_identifier,
        comment=comment or "",
        epoch=epoch,
    )


def _current_epoch() -> float:
    return time.time() * 1000.0


def _resolve_state(state_id, project):
    """校验 state 归属项目并返回对象；找不到抛 AppValidationError。"""
    state = State.objects.filter(pk=state_id, project=project, deleted_at__isnull=True).first()
    if state is None:
        raise AppException(
            "VALIDATION_ERROR",
            message="状态不存在",
            details=[{"field": "state_id", "code": "DOES_NOT_EXIST",
                      "message": "状态不属于当前项目"}],
        )
    return state


# ─────────────────────────────────────────────────────────────────────
# 列表 / 创建
# ─────────────────────────────────────────────────────────────────────
class IssueListCreateView(ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = IssueSerializer

    def _base_queryset(self, project):
        return (
            Issue.objects
            .filter(project=project, deleted_at__isnull=True)
            .select_related("project", "state", "issue_type")
            .prefetch_related("issue_assignees", "issue_labels")
        )

    def list(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)

        # ── filter + search（IssueFilterSet 单一实现，TASK-003 §4.3.1）──
        # 看板模式裁剪 state_id（BOARD-002 §2.2）；list 默认全集
        drop_keys = ("state_id",) if request.query_params.get("group_by") == "state_id" else ()
        filterset = IssueFilterSet(request, drop_keys=drop_keys)
        q_obj = filterset.build_query(request.query_params)

        qs = (
            self._base_queryset(project)
            .annotate(
                # 计数 annotate —— 列表与卡片渲染消费
                sub_issues_count=Count(
                    "sub_issues",
                    filter=Q(sub_issues__deleted_at__isnull=True)
                          & ~Q(sub_issues__state__group="cancelled"),
                    distinct=True,
                ),
                completed_sub_issues_count=Count(
                    "sub_issues",
                    filter=Q(sub_issues__deleted_at__isnull=True,
                             sub_issues__state__group="completed"),
                    distinct=True,
                ),
            )
            .filter(q_obj)
            .distinct()   # M2M 筛选避免重复行（FLT-12 守护）
        )

        # ── 排序（含 priority 语义权重，BR-05）──
        qs, warning = filterset.apply_order(qs, request.query_params.get("order_by"))

        # ── group_by=state_id 走看板分支（BOARD-002 §4.2.1）──
        if request.query_params.get("group_by") == "state_id":
            return self._kanban_grouped_response(request, project, qs, filterset, warning)

        # ── 平铺列表：游标分页（轻量实现：created_at-desc + id + offset 编码）──
        return self._flat_list_response(qs, filterset, warning)

    def _flat_list_response(self, qs, filterset, warning):
        per_page = self._parse_per_page()
        offset = self._parse_cursor_offset()
        total = qs.count()
        rows = qs[offset: offset + per_page]
        next_cursor = self._encode_cursor(offset + per_page) if offset + per_page < total else None
        prev_cursor = self._encode_cursor(max(offset - per_page, 0)) if offset > 0 else None
        meta = {
            "next_cursor": next_cursor,
            "prev_cursor": prev_cursor,
            "next_page_results": (offset + per_page) < total,
            "prev_page_results": offset > 0,
            "count": len(rows),
            "total_count": total,
            "total_pages": (total + per_page - 1) // per_page,
            "page": (offset // per_page) + 1,
            "per_page": per_page,
            "applied": filterset.applied,
        }
        if warning:
            meta["warning"] = warning
        if filterset.ignored_params:
            meta["ignored_params"] = filterset.ignored_params
        return success_response(IssueSerializer(rows, many=True).data, meta=meta)

    def _kanban_grouped_response(self, request, project, base_qs, filterset, warning):
        """看板分组：每 State 一键覆盖全量 State（含 cancelled 第 4 列）；组内 25 条。"""
        per_group = self._parse_group_per_page()
        states = (
            State.objects.filter(project=project, deleted_at__isnull=True)
            .order_by("sort_order")
        )
        # 用单条 GROUP BY 一次性取每组 count（避免 N+1）
        # order_by() 清空排序是必须的：base_qs 已经过 apply_order()，Django 会把排序列
        # 一并塞进 GROUP BY，于是每行自成一组、n 恒为 1，dict() 再把同 state 的组覆盖成
        # 最后一条 —— 各组 total_results 之和会远小于真实总数（BRD-04 守护）。
        # distinct=True 与上游 .distinct()（M2M 筛选去重）保持同口径。
        counts_by_state = dict(
            base_qs.order_by().values_list("state_id").annotate(n=Count("id", distinct=True))
        )
        grouped: dict[str, dict] = {}
        group_cursors: dict[str, dict] = {}
        for st in states:
            group_qs = base_qs.filter(state_id=st.id)
            offset = self._parse_cursor_offset()
            total = counts_by_state.get(st.id, 0)
            rows = list(
                group_qs.order_by("sort_order", "-created_at", "-id")[offset: offset + per_group]
            )
            next_cursor = (
                self._encode_cursor(offset + per_group, group_id=str(st.id))
                if offset + per_group < total else None
            )
            grouped[str(st.id)] = {
                "results": IssueSerializer(rows, many=True).data,
                "total_results": total,
            }
            group_cursors[str(st.id)] = {"next_cursor": next_cursor}

        meta = {
            "grouped_by": "state_id",
            "sub_grouped_by": None,
            "total_count": base_qs.count(),
            "applied": filterset.applied,
            "group_cursors": group_cursors,
        }
        if warning:
            meta["warning"] = warning
        if filterset.ignored_params:
            meta["ignored_params"] = filterset.ignored_params
        return success_response(grouped, meta=meta)

    # ----------------------- 游标与 per_page -----------------------
    def _parse_per_page(self) -> int:
        try:
            return min(int(self.request.query_params.get("per_page", 100)), 100)
        except (TypeError, ValueError):
            return 100

    def _parse_group_per_page(self) -> int:
        try:
            return min(int(self.request.query_params.get("group_per_page", 25)), 100)
        except (TypeError, ValueError):
            return 25

    def _parse_cursor_offset(self) -> int:
        cur = self.request.query_params.get("cursor")
        if not cur:
            return 0
        try:
            raw = base64.urlsafe_b64decode(cur + "=" * (-len(cur) % 4)).decode()
            offset = int(raw.split(":")[0])
            return max(offset, 0)
        except (ValueError, UnicodeDecodeError, IndexError) as err:
            raise AppException("VALIDATION_INVALID_CURSOR") from err

    @staticmethod
    def _encode_cursor(offset: int, group_id: str | None = None) -> str:
        payload = f"{offset}:{group_id or ''}"
        return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")

    # ----------------------- 创建 -----------------------
    def create(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        if project.current_user_role < ProjectRole.CONTRIBUTOR:
            raise AppException("PERM_ROLE_INSUFFICIENT")
        s = IssueWriteSerializer(
            data=request.data,
            context={"project": project, "is_create": True},
        )
        s.is_valid(raise_exception=True)
        name = s.validated_data["name"]
        if not name.strip():
            raise AppException(
                "VALIDATION_ERROR",
                message="请求参数校验失败",
                details=[{"field": "name", "code": "REQUIRED", "message": "标题不能为空"}],
            )
        assignee_ids = s.validated_data.get("assignee_ids", []) or []
        validate_assignees(project.id, assignee_ids)
        label_ids = s.validated_data.get("label_ids", []) or []
        state_id = s.validated_data.get("state_id")
        if state_id is None:
            default_state = State.objects.filter(
                project=project, is_default=True, deleted_at__isnull=True
            ).first()
            state_id = default_state.id if default_state else None

        max_order = Issue.objects.filter(
            project=project, deleted_at__isnull=True
        ).aggregate(m=Max("sort_order"))["m"]
        epoch = _current_epoch()

        with transaction.atomic():
            issue = create_issue_svc(
                project_id=project.id,
                actor_id=request.user.id,
                payload={
                    "name": name,
                    "description_html": s.validated_data.get("description_html", "<p></p>"),
                    "description_json": s.validated_data.get("description_json", {}),
                    "state_id": state_id,
                    "issue_type_id": s.validated_data.get("type_id"),
                    "priority": s.validated_data.get("priority", Issue.Priority.NONE),
                    "start_date": s.validated_data.get("start_date"),
                    "target_date": s.validated_data.get("target_date"),
                    "parent_id": s.validated_data.get("parent_id"),
                    "prev_sort_order": max_order,
                },
            )
            if assignee_ids:
                sync_assignees(issue, assignee_ids, request.user.id)
            if label_ids:
                sync_labels(issue, label_ids, request.user.id)
            transaction.on_commit(
                lambda: _record_activity(
                    issue, request.user,
                    verb="created",
                    field="issue_type",
                    new=issue.issue_type.name if issue.issue_type_id else None,
                    new_identifier=issue.issue_type_id,
                    comment=f"创建任务 {issue.project.identifier}-{issue.sequence_id}",
                    epoch=epoch,
                )
            )
        # 重新查询以带 annotate 计数（Serializer 一次取数）
        issue = Issue.objects.select_related("project", "state", "issue_type").prefetch_related(
            "issue_assignees", "issue_labels"
        ).annotate(
            sub_issues_count=Count(
                "sub_issues",
                filter=Q(sub_issues__deleted_at__isnull=True)
                      & ~Q(sub_issues__state__group="cancelled"),
                distinct=True,
            ),
            completed_sub_issues_count=Count(
                "sub_issues",
                filter=Q(sub_issues__deleted_at__isnull=True,
                         sub_issues__state__group="completed"),
                distinct=True,
            ),
        ).get(pk=issue.pk)
        return created_response(
            IssueSerializer(issue).data,
            location=request.build_absolute_uri(
                f"/api/v1/workspaces/{kwargs['slug']}/projects/{project.id}/issues/{issue.id}/"
            ),
        )


# ─────────────────────────────────────────────────────────────────────
# 详情 / 更新 / 删除
# ─────────────────────────────────────────────────────────────────────
class IssueDetailView(RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = IssueSerializer

    def get_object(self):
        project, _, _ = get_project_or_404(self.kwargs["slug"], self.kwargs["project_id"], self.request.user)
        try:
            issue = (
                Issue.objects
                .select_related("project", "state", "issue_type")
                .prefetch_related("issue_assignees", "issue_labels")
                .annotate(
                    sub_issues_count=Count(
                        "sub_issues",
                        filter=Q(sub_issues__deleted_at__isnull=True)
                              & ~Q(sub_issues__state__group="cancelled"),
                        distinct=True,
                    ),
                    completed_sub_issues_count=Count(
                        "sub_issues",
                        filter=Q(sub_issues__deleted_at__isnull=True,
                                 sub_issues__state__group="completed"),
                        distinct=True,
                    ),
                )
                .get(id=self.kwargs["issue_id"], project_id=project.id, deleted_at__isnull=True)
            )
        except Issue.DoesNotExist:
            raise NotFound("RESOURCE_NOT_FOUND") from None
        return issue

    def retrieve(self, request, *args, **kwargs):
        return success_response(IssueSerializer(self.get_object()).data)

    def destroy(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        role = project.current_user_role
        issue = self.get_object()
        # rbac §5.3：destroy 限本人创建 OR 项目 ADMIN
        if role < ProjectRole.ADMIN and issue.created_by_id != request.user.id:
            raise AppException(
                "PERM_ROLE_INSUFFICIENT",
                message="只能删除自己创建的任务",
            )
        issue.soft_delete(actor_id=request.user.id)
        return Response(status=status.HTTP_204_NO_CONTENT)

    def update(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        if project.current_user_role < ProjectRole.CONTRIBUTOR:
            raise AppException("PERM_ROLE_INSUFFICIENT")
        s = IssueWriteSerializer(
            data=request.data, partial=True,
            context={"project": project, "is_create": False},
        )
        s.is_valid(raise_exception=True)
        issue = self.get_object()
        # PATCH 安全：validated_data 含 default 字段（assignee_ids=[]、description_html="<p></p>"
        # 等），这些并非用户意图修改。只处理 request.data 中实际出现的字段，否则改优先级会
        # 顺带清空描述和负责人 —— 正是抽屉里优先级/负责人/日期"改不了"的根因。
        data = {k: v for k, v in s.validated_data.items() if k in request.data}
        epoch = _current_epoch()
        activities: list[dict] = []

        # ---- 标题 ----
        if "name" in data and data["name"] != issue.name:
            activities.append({
                "field": "name", "old": issue.name, "new": data["name"],
                "comment": "更新了 标题",
            })
            issue.name = data["name"]

        # ---- 描述 ----
        if "description_html" in data and data["description_html"] != issue.description_html:
            activities.append({
                "field": "description_html",
                "old": issue.description_html[:120] if issue.description_html else "",
                "new": data["description_html"][:120],
                "comment": "更新了 描述",
            })
            issue.description_html = data["description_html"]

        # ---- 类型 ----
        if "type_id" in data and data["type_id"] != issue.issue_type_id:
            old_name = issue.issue_type.name if issue.issue_type else None
            old_id = issue.issue_type_id
            new_type_obj = IssueType.objects.filter(
                pk=data["type_id"], workspace_id=project.workspace_id,
                is_active=True, deleted_at__isnull=True,
            ).first()
            new_name = new_type_obj.name if new_type_obj else None
            activities.append({
                "field": "issue_type",
                "old": old_name, "new": new_name,
                "old_identifier": old_id, "new_identifier": data["type_id"],
                "comment": "更新了 任务类型",
            })
            issue.issue_type_id = data["type_id"]

        # ---- 优先级 ----
        if "priority" in data and data["priority"] != issue.priority:
            activities.append({
                "field": "priority",
                "old": issue.priority, "new": data["priority"],
                "comment": "更新了 优先级",
            })
            issue.priority = data["priority"]

        # ---- 状态 ----
        if "state_id" in data and str(data["state_id"] or "") != str(issue.state_id or ""):
            new_state = _resolve_state(data["state_id"], project)
            activities.append({
                "field": "state",
                "old": issue.state.name if issue.state else None,
                "new": new_state.name,
                "old_identifier": issue.state_id, "new_identifier": new_state.id,
                "comment": "更新了 状态",
            })
            issue.state = new_state

        # ---- 起止日期 ----
        if "start_date" in data and data["start_date"] != issue.start_date:
            activities.append({
                "field": "start_date",
                "old": str(issue.start_date), "new": str(data["start_date"]),
                "comment": "更新了 开始时间",
            })
            issue.start_date = data["start_date"]
        if "target_date" in data and data["target_date"] != issue.target_date:
            activities.append({
                "field": "target_date",
                "old": str(issue.target_date), "new": str(data["target_date"]),
                "comment": "更新了 截止时间",
            })
            issue.target_date = data["target_date"]

        # ---- parent（sub-issue mount 校验已在 Serializer 走通）----
        if "parent_id" in data and data["parent_id"] != issue.parent_id:
            activities.append({
                "field": "parent",
                "old": str(issue.parent_id), "new": str(data["parent_id"]),
                "comment": "更新了 父工作项",
            })
            issue.parent_id = data["parent_id"]

        # ---- sort_order（看板拖拽）----
        if "sort_order" in data and data["sort_order"] != issue.sort_order:
            activities.append({
                "field": "sort_order",
                "old": str(issue.sort_order), "new": str(data["sort_order"]),
                "comment": "更新了 排序",
            })
            issue.sort_order = data["sort_order"]

        # ---- 负责人 ----
        if "assignee_ids" in data:
            new_ids = list(data["assignee_ids"] or [])
            old_ids = sorted(str(ia.assignee_id) for ia in issue.issue_assignees.all())
            new_ids_str = sorted(str(x) for x in new_ids)
            if old_ids != new_ids_str:
                activities.append({
                    "field": "assignees",
                    "old": ",".join(old_ids) or "previous",
                    "new": ",".join(new_ids_str),
                    "comment": "更新了 负责人",
                })
                validate_assignees(project.id, new_ids)
                sync_assignees(issue, new_ids, request.user.id)

        with transaction.atomic():
            issue.save()
            for a in activities:
                _record_activity(
                    issue, request.user,
                    verb="updated",
                    field=a["field"],
                    old=a.get("old"),
                    new=a.get("new"),
                    old_identifier=a.get("old_identifier"),
                    new_identifier=a.get("new_identifier"),
                    comment=a.get("comment", ""),
                    epoch=epoch,
                )
        # 重新查询以带 annotate
        issue = self.get_object()
        return success_response(IssueSerializer(issue).data)


# ─────────────────────────────────────────────────────────────────────
# 标签 PUT（集合替换）
# ─────────────────────────────────────────────────────────────────────
class IssueLabelsView(APIView):
    """PUT /workspaces/{slug}/projects/{pid}/issues/{iid}/labels/

    全量替换任务标签集合（TASK-002 §2.3 PUT 白名单）；幂等（diff 为空 → 无 activity）。
    """

    permission_classes = [IsAuthenticated]

    def put(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        if project.current_user_role < ProjectRole.CONTRIBUTOR:
            raise AppException("PERM_ROLE_INSUFFICIENT")
        try:
            issue = Issue.objects.select_related("project").prefetch_related("issue_labels").get(
                id=kwargs["issue_id"], project_id=project.id, deleted_at__isnull=True
            )
        except Issue.DoesNotExist:
            raise NotFound("RESOURCE_NOT_FOUND") from None

        payload = request.data or {}
        new_ids = payload.get("label_ids") or []
        if not isinstance(new_ids, list):
            raise AppException(
                "VALIDATION_ERROR",
                details=[{"field": "label_ids", "code": "INVALID",
                          "message": "label_ids 必须为列表"}],
            )
        # 校验（项目 active 标签 + ≤ 10）—— 复用 IssueWriteSerializer 校验口径
        IssueWriteSerializer(  # 仅触发校验逻辑
            data={"label_ids": new_ids},
            context={"project": project},
        ).is_valid(raise_exception=True)

        old_ids = {str(il.label_id) for il in issue.issue_labels.all()}
        new_ids_set = {str(x) for x in new_ids}
        added, removed = diff_labels(old_ids, new_ids_set)
        epoch = _current_epoch()

        with transaction.atomic():
            sync_labels(issue, new_ids, request.user.id)
            label_name_map = dict(
                Label.objects.filter(pk__in=new_ids_set | old_ids).values_list("id", "name")
            )
            for lid in added:
                transaction.on_commit(
                    lambda lid=lid: _record_activity(
                        issue, request.user,
                        verb="updated", field="labels",
                        new_identifier=lid, new=label_name_map.get(lid),
                        comment=f"添加了 标签 {label_name_map.get(lid, '')}",
                        epoch=epoch,
                    )
                )
            for lid in removed:
                transaction.on_commit(
                    lambda lid=lid: _record_activity(
                        issue, request.user,
                        verb="updated", field="labels",
                        old_identifier=lid, old=label_name_map.get(lid),
                        comment=f"移除了 标签 {label_name_map.get(lid, '')}",
                        epoch=epoch,
                    )
                )
        return success_response({"id": str(issue.id), "label_ids": sorted(new_ids_set)})


# ─────────────────────────────────────────────────────────────────────
# 子任务
# ─────────────────────────────────────────────────────────────────────
class IssueSubIssueListCreateView(APIView):
    """GET/POST /workspaces/{slug}/projects/{pid}/issues/{iid}/sub-issues/"""

    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        try:
            issue = Issue.objects.get(
                id=kwargs["issue_id"], project_id=project.id, deleted_at__isnull=True
            )
        except Issue.DoesNotExist:
            raise NotFound("RESOURCE_NOT_FOUND") from None
        subs = (
            Issue.objects
            .filter(parent_id=issue.id, deleted_at__isnull=True)
            .select_related("state", "issue_type")
            .prefetch_related("issue_assignees")
            .order_by("sort_order", "-created_at")
        )
        data = IssueSerializer(subs, many=True).data
        return success_response(
            data,
            meta={
                "count": len(data),
                "total_count": len(data),
                "parent_id": str(issue.id),
            },
        )

    def post(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        if project.current_user_role < ProjectRole.CONTRIBUTOR:
            raise AppException("PERM_ROLE_INSUFFICIENT")
        try:
            parent = Issue.objects.select_related("project").get(
                id=kwargs["issue_id"], project_id=project.id, deleted_at__isnull=True
            )
        except Issue.DoesNotExist:
            raise NotFound("RESOURCE_NOT_FOUND") from None

        # BR-07：严格一层
        if parent.parent_id is not None:
            raise AppException(
                "RESOURCE_LIMIT_EXCEEDED",
                message="MVP 阶段子任务仅支持一层",
                details=[{"field": "parent_id", "code": "NESTING",
                          "message": "层级上限 1（P2 开放多层）"}],
            )
        # 边界：单父 100 上限
        existing = Issue.objects.filter(
            parent_id=parent.id, deleted_at__isnull=True
        ).count()
        if existing >= MAX_SUB_ISSUES_PER_PARENT:
            raise AppException(
                "RESOURCE_LIMIT_EXCEEDED",
                details=[{"field": "parent_id", "code": "TOO_LARGE",
                          "message": f"单个任务最多 {MAX_SUB_ISSUES_PER_PARENT} 个子任务",
                          "limit": MAX_SUB_ISSUES_PER_PARENT}],
            )

        # 用 IssueWriteSerializer 走完整校验（type / priority / label_ids 等）
        s = IssueWriteSerializer(
            data=request.data,
            context={"project": project, "is_create": True},
        )
        s.is_valid(raise_exception=True)
        assignee_ids = s.validated_data.get("assignee_ids", []) or []
        validate_assignees(project.id, assignee_ids)
        label_ids = s.validated_data.get("label_ids", []) or []
        # 缺省 state 取项目默认
        state_id = s.validated_data.get("state_id")
        if state_id is None:
            default_state = State.objects.filter(
                project=project, is_default=True, deleted_at__isnull=True
            ).first()
            state_id = default_state.id if default_state else None
        max_order = Issue.objects.filter(
            project=project, deleted_at__isnull=True
        ).aggregate(m=Max("sort_order"))["m"]
        epoch = _current_epoch()

        with transaction.atomic():
            sub = create_issue_svc(
                project_id=project.id,
                actor_id=request.user.id,
                payload={
                    "name": s.validated_data["name"],
                    "description_html": s.validated_data.get("description_html", "<p></p>"),
                    "description_json": s.validated_data.get("description_json", {}),
                    "state_id": state_id,
                    "issue_type_id": s.validated_data.get("type_id"),
                    "priority": s.validated_data.get("priority", Issue.Priority.NONE),
                    "parent": parent,
                    "parent_id": parent.id,
                    "start_date": s.validated_data.get("start_date"),
                    "target_date": s.validated_data.get("target_date"),
                    "prev_sort_order": max_order,
                },
            )
            if assignee_ids:
                sync_assignees(sub, assignee_ids, request.user.id)
            if label_ids:
                sync_labels(sub, label_ids, request.user.id)
            transaction.on_commit(
                lambda: _record_activity(
                    sub, request.user,
                    verb="created",
                    field="parent",
                    new_identifier=str(parent.id),
                    new=parent.name,
                    comment=f"作为 {parent.project.identifier}-{parent.sequence_id} 的子任务创建",
                    epoch=epoch,
                )
            )
        sub = Issue.objects.select_related("project", "state", "issue_type").prefetch_related(
            "issue_assignees", "issue_labels"
        ).annotate(
            sub_issues_count=Count(
                "sub_issues",
                filter=Q(sub_issues__deleted_at__isnull=True)
                      & ~Q(sub_issues__state__group="cancelled"),
                distinct=True,
            ),
            completed_sub_issues_count=Count(
                "sub_issues",
                filter=Q(sub_issues__deleted_at__isnull=True,
                         sub_issues__state__group="completed"),
                distinct=True,
            ),
        ).get(pk=sub.pk)
        return created_response(
            IssueSerializer(sub).data,
            location=request.build_absolute_uri(
                f"/api/v1/workspaces/{kwargs['slug']}/projects/{project.id}/issues/{sub.id}/"
            ),
        )


# ─────────────────────────────────────────────────────────────────────
# 活动日志（操作时间线）
# ─────────────────────────────────────────────────────────────────────
class IssueActivityListView(APIView):
    """GET /workspaces/{slug}/projects/{pid}/issues/{iid}/activities/ —— 游标分页 30/页。"""

    permission_classes = [IsAuthenticated]
    PER_PAGE = 30

    def get(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        try:
            issue = Issue.objects.get(
                id=kwargs["issue_id"], project_id=project.id, deleted_at__isnull=True
            )
        except Issue.DoesNotExist:
            raise NotFound("RESOURCE_NOT_FOUND") from None

        qs = (
            IssueActivity.objects
            .filter(issue=issue)
            .select_related("actor")
            .order_by("-created_at", "-id")
        )
        try:
            per_page = min(int(request.query_params.get("per_page", self.PER_PAGE)), 100)
        except (TypeError, ValueError):
            per_page = self.PER_PAGE
        offset = self._parse_cursor(request.query_params.get("cursor"))
        total = qs.count()
        rows = list(qs[offset: offset + per_page])
        next_cursor = self._encode_cursor(offset + per_page) if offset + per_page < total else None
        data = [
            {
                "id": str(r.id),
                "actor_id": str(r.actor_id) if r.actor_id else None,
                "actor_name": r.actor.display_name if r.actor else None,
                "verb": r.verb,
                "field": r.field,
                "old_value": r.old_value,
                "new_value": r.new_value,
                "old_identifier": str(r.old_identifier) if r.old_identifier else None,
                "new_identifier": str(r.new_identifier) if r.new_identifier else None,
                "comment": r.comment,
                "epoch": r.epoch,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
        return success_response(data, meta={
            "next_cursor": next_cursor,
            "prev_cursor": None,
            "next_page_results": (offset + per_page) < total,
            "prev_page_results": offset > 0,
            "count": len(rows),
            "total_count": total,
            "total_pages": (total + per_page - 1) // per_page,
            "page": (offset // per_page) + 1,
            "per_page": per_page,
        })

    @staticmethod
    def _encode_cursor(offset: int) -> str:
        return base64.urlsafe_b64encode(f"{offset}".encode()).decode().rstrip("=")

    @staticmethod
    def _parse_cursor(cur: str | None) -> int:
        if not cur:
            return 0
        try:
            return max(int(base64.urlsafe_b64decode(cur + "=" * (-len(cur) % 4)).decode().split(":")[0]), 0)
        except (ValueError, UnicodeDecodeError, IndexError) as err:
            raise AppException("VALIDATION_INVALID_CURSOR") from err


# ─────────────────────────────────────────────────────────────────────
# IssueType 列表（项目可用类型 = WS active）
# ─────────────────────────────────────────────────────────────────────
class IssueTypeListView(APIView):
    """GET /workspaces/{slug}/projects/{pid}/issue-types/ —— TASK-002 §4.3.1 第 12 行。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        types = (
            Issue.objects.model._meta.get_field("issue_type").related_model.objects
            .filter(workspace_id=project.workspace_id, is_active=True, deleted_at__isnull=True)
            .order_by("sort_order", "created_at")
        )
        data = [
            {
                "id": str(t.id),
                "name": t.name,
                "icon": t.icon,
                "color": t.color,
                "is_default": t.is_default,
                "is_active": t.is_active,
                "is_system": t.is_system,
                "sort_order": t.sort_order,
            }
            for t in types
        ]
        return success_response(data)
