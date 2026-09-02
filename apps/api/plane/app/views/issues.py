from django.db import transaction
from django.db.models import Max
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.generics import ListCreateAPIView, RetrieveUpdateAPIView, RetrieveUpdateDestroyAPIView
from rest_framework.response import Response

from plane.app.permissions import IsAuthenticated
from plane.app.serializers.common import envelope
from plane.app.serializers.issue import IssueSerializer, IssueWriteSerializer, sync_assignees, validate_assignees
from plane.app.views.projects import _get_project_or_404
from plane.db.models import Issue, IssueActivity, State
from plane.db.models.roles import ProjectRole
from plane.db.services.issue_sequence import create_issue as create_issue_svc
from plane.db.services.sort_order import calculate_sort_order


def _serialize_issue(issue):
    return IssueSerializer(issue).data


def _record_activity(issue, actor, verb, field=None, old=None, new=None, comment=""):
    IssueActivity.objects.create(
        issue=issue,
        actor=actor,
        verb=verb,
        field=field,
        old_value=old,
        new_value=new,
        comment=comment,
    )


class IssueListCreateView(ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = IssueSerializer

    def list(self, request, *args, **kwargs):
        project, _, _ = _get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        ordering = request.query_params.get("ordering", "sort_order")
        per_page = min(int(request.query_params.get("per_page", 100)), 100)
        group_by = request.query_params.get("group_by")
        qs = Issue.objects.filter(project=project, deleted_at__isnull=True).order_by(ordering, "sequence_id")[:per_page]
        data = [_serialize_issue(i) for i in qs]
        if group_by == "state_id":
            grouped = {}
            for d in data:
                key = d["state_id"] or "none"
                grouped.setdefault(key, []).append(d)
            return envelope(True, grouped, {"total": len(data), "group_by": group_by})
        return envelope(True, data, {"total": len(data), "ordering": ordering})

    def create(self, request, *args, **kwargs):
        project, _, _ = _get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        if project.current_user_role < ProjectRole.CONTRIBUTOR:
            return envelope(False, None, {"code": "PERM_PROJECT_CONTRIBUTOR_REQUIRED"}, http_status=403)
        s = IssueWriteSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        name = s.validated_data["name"]
        if not name.strip():
            raise ValidationError({"name": "REQUIRED"})
        state_id = s.validated_data.get("state_id")
        assignee_ids = s.validated_data.get("assignee_ids", [])
        validate_assignees(project.id, assignee_ids)
        # 默认 state：项目种子未启动 OR 用户未传 → 取 is_default
        if state_id is None:
            default = State.objects.filter(project=project, is_default=True, deleted_at__isnull=True).first()
            state_id = default.id if default else None
        # sort_order：列尾追加
        max_order = Issue.objects.filter(project=project, deleted_at__isnull=True).aggregate(m=Max("sort_order"))["m"]
        sort_order = (max_order or 0) + calculate_sort_order(prev_order=None, next_order=None) or 65535.0
        # sort_order 用 65535 步长（BR-8 末任务追加 = 列尾）
        with transaction.atomic():
            issue = create_issue_svc(
                project_id=project.id,
                actor_id=request.user.id,
                payload={
                    "name": name,
                    "description_html": s.validated_data.get("description_html", "<p></p>"),
                    "description_json": s.validated_data.get("description_json", {}),
                    "state_id": state_id,
                                        "start_date": s.validated_data.get("start_date"),
                    "target_date": s.validated_data.get("target_date"),
                },
            )
            sync_assignees(issue, assignee_ids, request.user.id)
            transaction.on_commit(
                lambda: _record_activity(
                    issue, request.user, "created", comment=f"创建任务 {issue.project.identifier}-{issue.sequence_id}"
                )
            )
        return envelope(True, _serialize_issue(issue), http_status=201)


class IssueDetailView(RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = IssueSerializer

    def get_object(self):
        project, _, _ = _get_project_or_404(self.kwargs["slug"], self.kwargs["project_id"], self.request.user)
        try:
            issue = Issue.objects.select_related("project", "state").get(
                id=self.kwargs["issue_id"],
                project_id=project.id,
                deleted_at__isnull=True,
            )
        except Issue.DoesNotExist:
            raise NotFound("RESOURCE_NOT_FOUND")
        return issue

    def retrieve(self, request, *args, **kwargs):
        """GET 详情 —— 统一信封（api-conventions §4）。"""
        return envelope(True, _serialize_issue(self.get_object()))

    def destroy(self, request, *args, **kwargs):
        """DELETE —— 软删除 + PROJ_CONTRIBUTOR 角色校验（TASK-001 §4.2，P0 UI 唯一删除任务入口）。"""
        project, _, _ = _get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        if project.current_user_role < ProjectRole.CONTRIBUTOR:
            return envelope(False, None, {"code": "PERM_PROJECT_CONTRIBUTOR_REQUIRED"}, http_status=403)
        issue = self.get_object()
        issue.soft_delete(actor_id=request.user.id)
        return Response(status=204)  # 204 禁带 body

    def update(self, request, *args, **kwargs):
        project, _, _ = _get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        if project.current_user_role < ProjectRole.CONTRIBUTOR:
            return envelope(False, None, {"code": "PERM_PROJECT_CONTRIBUTOR_REQUIRED"}, http_status=403)
        s = IssueWriteSerializer(data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        try:
            issue = Issue.objects.get(id=kwargs["issue_id"], project_id=project.id, deleted_at__isnull=True)
        except Issue.DoesNotExist:
            raise NotFound("RESOURCE_NOT_FOUND")
        # 字段 diff：仅记录允许追踪的字段（name/target_date/state/assignees）
        diffs = []
        if "name" in s.validated_data and s.validated_data["name"] != issue.name:
            diffs.append(("name", issue.name, s.validated_data["name"]))
            issue.name = s.validated_data["name"]
        if "description_html" in s.validated_data and s.validated_data["description_html"] != issue.description_html:
            issue.description_html = s.validated_data["description_html"]
        if "target_date" in s.validated_data and s.validated_data["target_date"] != issue.target_date:
            diffs.append(("target_date", str(issue.target_date), str(s.validated_data["target_date"])))
            issue.target_date = s.validated_data["target_date"]
        if "state_id" in s.validated_data and str(s.validated_data["state_id"]) != str(issue.state_id):
            new_state = State.objects.filter(
                id=s.validated_data["state_id"], project=project, deleted_at__isnull=True
            ).first()
            if not new_state:
                raise ValidationError({"state_id": "DOES_NOT_EXIST"})
            diffs.append(("state", issue.state.name if issue.state else None, new_state.name))
            issue.state = new_state
        if "sort_order" in s.validated_data:
            issue.sort_order = s.validated_data["sort_order"]
        if "assignee_ids" in s.validated_data:
            validate_assignees(project.id, s.validated_data["assignee_ids"])
            sync_assignees(issue, s.validated_data["assignee_ids"], request.user.id)
            diffs.append(("assignees", "previous", "updated"))
        with transaction.atomic():
            issue.save()
            for field, old, new in diffs:
                _record_activity(issue, request.user, "updated", field=field, old=old, new=new)
        return envelope(True, _serialize_issue(issue))
