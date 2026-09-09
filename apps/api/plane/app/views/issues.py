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
  POST   /workspaces/{slug}/projects/{pid}/issues/{iid}/sub-issues/  挂载子任务（深度 ≤5，TASK-004）
  GET    /workspaces/{slug}/projects/{pid}/issues/{iid}/subtree/     整棵子树（CTE，TASK-004）
  GET    /workspaces/{slug}/projects/{pid}/issues/{iid}/activities/ 操作日志
"""

from __future__ import annotations

import base64
import time

from django.db import transaction
from django.db.models import Exists, Max, OuterRef, Q
from rest_framework.exceptions import NotFound
from rest_framework.generics import ListCreateAPIView, RetrieveUpdateDestroyAPIView
from rest_framework.views import APIView

from plane.app.filters.compiler import (
    CompileContext,
    blocked_exists_annotation,
    echo_conditions,
    parse_filters_param,
    resolved_applied,
    tree_references_blocked,
    validate_dsl,
)
from plane.app.filters.compiler import (
    compile as compile_dsl,
)
from plane.app.permissions import IsAuthenticated
from plane.app.serializers.issue import (
    IssueSerializer,
    IssueWriteSerializer,
    diff_labels,
    sync_labels,
)
from plane.app.views._access import get_project_or_404
from plane.base.exception import AppException
from plane.base.response import created_response, success_response
from plane.db.models import ApprovalInstance, Issue, IssueActivity, IssueType, IssueView, Label, State
from plane.db.models.roles import ProjectRole
from plane.db.services.custom_fields import (
    assign_auto_increments,
    diff_custom_fields,
    merge_custom_fields,
    validate_custom_fields,
)
from plane.db.services.issue_archive import assert_issue_writable
from plane.db.services.issue_assignee import sync_assignees_full
from plane.db.services.issue_grouping import (
    column_counts,
    drop_filter_keys,
    get_group_columns,
    group_filter_q,
    in_column_ordering,
    resolve_dimension,
)
from plane.db.services.issue_hierarchy import (
    CircularDependencyError,
    DepthLimitExceeded,
    StateInvalidError,
    SubtreeDepthGuardError,
    check_move,
    delete_subtree,
    depth_of,
    fetch_subtree,
    issue_count_annotations,
)
from plane.db.services.issue_link import TransitionBlockedError
from plane.db.services.issue_query import IssueFilterSet
from plane.db.services.issue_sequence import create_issue as create_issue_svc
from plane.db.services.issue_transition_guard import assert_completable
from plane.db.services.view_service import resolve_view
from plane.settings.features import (
    MAX_ISSUE_DEPTH,
    MAX_SUB_ISSUES_PER_PARENT,
    SUBTREE_NODE_LIMIT,
)

# ── sub-issue / activity 端点的最大子任务数（BR-07 / TASK-002 §2.7，Sprint-2 起集中于 settings/features）──


# ─────────────────────────────────────────────────────────────────────
# 工具：activity 记录、completed_at 派生
# ─────────────────────────────────────────────────────────────────────
def _record_activity(
    issue,
    actor,
    *,
    verb,
    field=None,
    old=None,
    new=None,
    old_identifier=None,
    new_identifier=None,
    comment="",
    epoch: float | None = None,
):
    # TASK-010 全量异步化（用户裁决 2026-09-05 收紧）：主写路径统一经 Worker 管道
    # 落库（行级幂等 + DLQ 兜底）；broker 不可用时 enqueue 内部降级同步直写。
    from plane.bgtasks.issue_activity import enqueue_activity_row

    return enqueue_activity_row(
        issue_id=issue.id,
        actor=actor,
        verb=verb,
        field=field,
        old=old,
        new=new,
        old_identifier=old_identifier,
        new_identifier=new_identifier,
        comment=comment,
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
            details=[{"field": "state_id", "code": "DOES_NOT_EXIST", "message": "状态不属于当前项目"}],
        )
    return state


# ─────────────────────────────────────────────────────────────────────
# 列表 / 创建
# ─────────────────────────────────────────────────────────────────────
class IssueListCreateView(ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = IssueSerializer

    def _base_queryset(self, project, *, include_archived=False):
        # TASK-009 BR-11：默认排除归档树（命中偏索引 idx_issue_active_by_project）；
        # ?archived=true 反向查归档视图（issueQuery FilterSet applied 回显）
        # created_by 进 select_related：IssueSerializer.get_created_by 触 FK 取用户，
        # 分组端点每列 25 行 × N 列的逐行用户查询曾把 1 万任务数据集的分组请求
        # 推到 P95>200ms / 查询数三位数（BOARD-003 IT-02/IT-10 基准门禁暴露）
        qs = Issue.objects.filter(project=project, deleted_at__isnull=True)
        if not include_archived:
            qs = qs.filter(archived_at__isnull=True)
        return (
            qs.select_related("project", "state", "issue_type", "created_by")
            .prefetch_related("issue_assignees", "issue_labels")
            # WF-002 §3.5（补口轮）：审批中徽标——Exists 子查询一次注入（详情等
            # 非 list 上下文不经此路径，SerializerMethodField 回落 False）
            .annotate(_has_pending_approval=Exists(
                ApprovalInstance.objects.filter(
                    issue_id=OuterRef("pk"), status=ApprovalInstance.Status.PENDING)))
        )

    def list(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        include_archived = str(request.query_params.get("archived", "")).lower() in ("true", "1")

        # TASK-012 §4.4：列表请求级一次性 resolve access_map + 序列化剔除 hidden
        from plane.db.models import CustomFieldDefinition as _CFD3
        from plane.db.services.field_permissions import FieldPermissionService

        _cf_defs = list(_CFD3.objects
                         .filter(workspace_id=project.workspace_id)
                         .filter(Q(project=project) | Q(project__isnull=True)))
        self._list_access = FieldPermissionService().cached_resolve(
            request, request.user, project, _cf_defs)

        # ── group_by 维度解析（BOARD-003 §4.2-6：白名单 + 别名归一，非法 → 400）──
        dimension = None
        if request.query_params.get("group_by"):
            dimension = resolve_dimension(project, request.query_params.get("group_by"))
        # ── sub_group_by 二维泳道（BOARD-005 §2.3：白名单同集且不得与列维度相同）──
        sub_dimension = None
        if request.query_params.get("sub_group_by"):
            if not dimension:
                raise AppException(
                    "VALIDATION_INVALID_PARAM",
                    message="sub_group_by 需与 group_by 搭配使用")
            sub_dimension = resolve_dimension(project,
                                              request.query_params.get("sub_group_by"))
            if sub_dimension == dimension:  # BR-07
                raise AppException(
                    "VALIDATION_ERROR",
                    message="行分组维度不得与列分组维度相同",
                    details=[{"field": "sub_group_by", "code": "INVALID",
                              "message": "行分组维度不得与列分组维度相同"}])

        # ── 编译上下文（TASK-011 §4.3：占位符解析与 cf 分派共用）──
        ctx = CompileContext.build(project=project, user=request.user, access_map=self._list_access)

        # ── view_id 展开（② 项目级视图层：filters 树 → Q + 原始值 applied 回显）──
        view_q = Q()
        view_applied: dict = {}
        view_tree: dict | None = None
        view_meta: dict | None = None
        degraded = None
        if raw_vid := request.query_params.get("view_id"):
            view = IssueView.objects.filter(id=raw_vid, project=project, deleted_at__isnull=True).first()
            # 应用面（列表/分组消费）：内置 / 本人 / 共享视图全员可用
            # （BOARD-005 BR-01 放开 shared；personal 他人视图仍存在性隐藏）
            if view is None or not (
                view.is_system or view.owner_id == request.user.id
                or view.access == "shared"
            ):
                raise NotFound("RESOURCE_NOT_FOUND") from None  # 存在性隐藏（§6-9/BR-11）
            view_tree, degraded = resolve_view(view, project=project, user=request.user)
            view_q = compile_dsl(view_tree, ctx)
            view_applied = echo_conditions(view_tree)
            view_meta = {"id": str(view.id), "name": view.name, "access": view.access}

        # ── ?filters=<urlencoded DSL>（③ 视图内临时层；三源恒 AND——BR-12）──
        # 损坏 JSON → 400 VALIDATION_INVALID_PARAM（§2.5）；DSL 四重校验同保存路径
        adhoc_tree = parse_filters_param(request.query_params.get("filters"))
        if adhoc_tree is not None:
            validate_dsl(adhoc_tree, project=project, user=request.user)
        adhoc_q = compile_dsl(adhoc_tree, ctx) if adhoc_tree is not None else Q()

        # ── filter + search（IssueFilterSet 单一实现，TASK-003 §4.3.1）──
        # 分组维度对应的筛选参数动态出域（BOARD-002「减 state_id」的泛化，BOARD-003 §4.2 注）
        drop_keys = drop_filter_keys(dimension) if dimension else ()
        filterset = IssueFilterSet(request, drop_keys=drop_keys, project=project)
        q_obj = filterset.build_query(request.query_params) & view_q & adhoc_q

        annotations = issue_count_annotations()
        qs_base = self._base_queryset(project, include_archived=include_archived)
        if tree_references_blocked([view_tree, adhoc_tree]):
            # 白名单 "blocked" 键映射的注解列（§4.3.1）——仅在树引用时注入
            # （须在 filter 前注解：q_obj 引用该列）
            qs_base = qs_base.annotate(_is_blocked=blocked_exists_annotation())
        if dimension:
            # 分组分支（BOARD-003 IT-02/IT-10 SQL 预算）：计数注解（sub_issues/
            # completed/spent 逐行相关子查询）不进列扫描 / 合并计数 / 总计数查询——
            # 1 万任务数据集下注解随 2000 行/列扫描逐行求值曾把分组请求推到
            # P95>200ms；改为每列先轻量页取 ≤25 个 id，再对页内行水合注解
            qs = qs_base.filter(q_obj).distinct()  # M2M 筛选避免重复行（FLT-12 守护）
        else:
            qs = (
                qs_base.annotate(
                    # 计数 annotate —— 列表与卡片渲染消费
                    **annotations,
                )
                .filter(q_obj)
                .distinct()  # M2M 筛选避免重复行（FLT-12 守护）
            )

        # ── 排序（含 priority 语义权重，BR-05）──
        qs, warning = filterset.apply_order(qs, request.query_params.get("order_by"))

        # ── BR-17 结构化 applied 增量（仅 ?filters= 存在时附加——无该参数响应逐字节不变）──
        applied_extra = self._dsl_applied_extra(
            ctx, view_tree=view_tree, adhoc_tree=adhoc_tree, view_meta=view_meta
        )

        # ── group_by 走分组分支（BOARD-002 契约的维度泛化，BOARD-003 §4.2.2）──
        if dimension and sub_dimension:
            return self._matrix_response(
                request, project, qs, dimension, sub_dimension,
                view_applied, applied_extra, degraded, view_meta)
        if dimension:
            base_unfiltered = self._base_queryset(project, include_archived=include_archived)
            return self._grouped_response(
                request, project, qs, base_unfiltered, filterset, warning,
                dimension, view_applied, applied_extra, degraded, view_id_out=view_meta,
                count_annotations=annotations,
            )

        # ── 平铺列表：游标分页（轻量实现：created_at-desc + id + offset 编码）──
        return self._flat_list_response(qs, filterset, warning, applied_extra)

    @staticmethod
    def _dsl_applied_extra(
        ctx: CompileContext,
        *,
        view_tree: dict | None,
        adhoc_tree: dict | None,
        view_meta: dict | None,
    ) -> dict | None:
        """?filters= 源的 applied 用解析后值 + resolved_placeholders / 计数（BR-17）；
        view_id 同在时合并视图树的占位符与计数，并回显视图标识。"""
        if adhoc_tree is None:
            return None
        echo = resolved_applied(adhoc_tree, ctx)
        extra: dict = {
            "filters": echo["conditions"],
            "resolved_placeholders": echo["resolved_placeholders"],
            "conditions_count": echo["conditions_count"],
            "groups_count": echo["groups_count"],
        }
        if view_tree:
            vecho = resolved_applied(view_tree, ctx)
            extra["resolved_placeholders"] = {
                **vecho["resolved_placeholders"], **echo["resolved_placeholders"]
            }
            extra["conditions_count"] += vecho["conditions_count"]
            extra["groups_count"] += vecho["groups_count"]
        if view_meta:
            extra["view"] = view_meta
        return extra

    def _flat_list_response(self, qs, filterset, warning, applied_extra=None):
        per_page = self._parse_per_page()
        offset = self._parse_cursor_offset()
        total = qs.count()
        rows = qs[offset : offset + per_page]
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
            "applied": {**filterset.applied, **(applied_extra or {})},
        }
        merged_warning = filterset.merge_warnings(warning)
        if merged_warning:
            meta["warning"] = merged_warning
        if filterset.ignored_params:
            meta["ignored_params"] = filterset.ignored_params
        return success_response(_strip_hidden(IssueSerializer(rows, many=True).data, self._list_access), meta=meta)

    def _grouped_response(
        self, request, project, base_qs, base_unfiltered, filterset, warning,
        dimension, view_applied, applied_extra, degraded, view_id_out,
        count_annotations=None,
    ):
        """分组响应（BOARD-003 §4.2.2，BOARD-002 契约的维度泛化）。

        列集合从配置源生成（零 DISTINCT，BR-06）；空列恒在；组内 25 +
        total_results（筛选后）/ unfiltered_total_results（筛选前）；键 = 裸列值
        （State UUID / 枚举值 / 成员·标签 UUID / 选项值 / __none__），响应不内嵌
        组元数据（BR-16）——列头名称与颜色由前端配置源渲染。

        行取数两步（BOARD-003 IT-02 SQL 预算）：轻量页取 id（无计数注解——相关
        子查询不随整列扫描逐行求值）→ 页内 ≤25 行水合注解；组内序两步同键，
        行序与单查询口径一致。
        """
        per_group = self._parse_group_per_page()
        columns = get_group_columns(project, dimension)
        # 单条 GROUP BY 取每列计数（避免 N+1；order_by() 清空防排序列进 GROUP BY）
        filtered_counts = column_counts(base_qs, dimension, project)
        unfiltered_counts = column_counts(base_unfiltered, dimension, project)
        ordering = in_column_ordering(dimension)
        grouped: dict[str, dict] = {}
        group_cursors: dict[str, dict] = {}
        for col in columns:
            key = col["key"]
            total = filtered_counts.get(key, 0)
            offset = self._parse_cursor_offset()
            rows = []
            if total:
                page_ids = list(
                    base_qs.filter(group_filter_q(dimension, key))
                    .order_by(*ordering)
                    .values_list("id", flat=True)[offset : offset + per_group]
                )
                if page_ids:
                    hydrate = base_qs.filter(id__in=page_ids)
                    if count_annotations:
                        hydrate = hydrate.annotate(**count_annotations)
                    rows = list(hydrate.order_by(*ordering))
            next_cursor = (
                self._encode_cursor(offset + per_group, group_id=key) if offset + per_group < total else None
            )
            grouped[key] = {
                "results": _strip_hidden(IssueSerializer(rows, many=True).data, self._list_access),
                "total_results": total,
                "unfiltered_total_results": unfiltered_counts.get(key, 0),
            }
            group_cursors[key] = {"next_cursor": next_cursor}

        meta = {
            "grouped_by": dimension,
            "sub_grouped_by": None,
            "total_count": base_qs.count(),
            "applied": {**view_applied, **filterset.applied, **(applied_extra or {})},
            "group_cursors": group_cursors,
        }
        if view_id_out:
            meta["view_id"] = view_id_out["id"]
        if degraded:
            meta["degraded"] = degraded
        merged_warning = filterset.merge_warnings(warning)
        if merged_warning:
            meta["warning"] = merged_warning
        if filterset.ignored_params:
            meta["ignored_params"] = filterset.ignored_params
        return success_response(grouped, meta=meta)

    def _matrix_response(
        self, request, project, qs, dimension, sub_dimension,
        view_applied, applied_extra, degraded, view_id_out,
    ):
        """二维泳道矩阵（BOARD-005 §2.3/§4.3）。

        服务端聚合（格计数不拉全量卡片）；复用一维 group_filter_q 双键
        AND——M2M 维度语义与一维完全一致。降级两闸（§2.5）：格数预算
        （列×行 ≤ 400）与时间预算（5s）——任一触发降级一维分组 +
        meta.degraded.matrix_*。
        """
        import time as _time

        from plane.db.services.issue_grouping import get_group_columns, group_filter_q

        t0 = _time.monotonic()
        columns = get_group_columns(project, dimension)
        rows_def = get_group_columns(project, sub_dimension)
        matrix_degraded = None
        if len(columns) * len(rows_def) > 400:
            matrix_degraded = "matrix_dimensions"
        if matrix_degraded is None:
            matrix = []
            for col in columns:
                if _time.monotonic() - t0 > 5.0:
                    matrix_degraded = "matrix_timeout"
                    break
                for row in rows_def:
                    cell_qs = qs.filter(
                        group_filter_q(dimension, col["key"]),
                        group_filter_q(sub_dimension, row["key"]))
                    count = cell_qs.count()
                    sample_ids = []
                    if count:
                        sample_ids = [str(i) for i in
                                      cell_qs.order_by("sort_order", "id")
                                      .values_list("id", flat=True)[:8]]
                    matrix.append({
                        "col": col["key"], "row": row["key"],
                        "count": count, "sample_issue_ids": sample_ids,
                    })
        if matrix_degraded:
            # 降级一维（§2.5）：沿用既有分组响应 + meta.degraded
            base_unfiltered = self._base_queryset(project, include_archived=False)
            filterset = IssueFilterSet(request, drop_keys=(), project=project)
            resp = self._grouped_response(
                request, project, qs, base_unfiltered, filterset, None,
                dimension, view_applied, applied_extra, degraded,
                view_id_out=view_id_out)
            resp.data["meta"]["degraded"] = {"sub_group_by": matrix_degraded}
            return resp
        meta = {
            "grouped_by": dimension,
            "sub_grouped_by": sub_dimension,
            "total_count": qs.count(),
            "columns": [{"key": c["key"]} for c in columns],
            "rows": [{"key": r["key"]} for r in rows_def],
            "matrix": matrix,
            "applied": {**view_applied, **(applied_extra or {})},
        }
        if view_id_out:
            meta["view_id"] = view_id_out["id"]
        if degraded:
            meta["degraded"] = degraded
        return success_response({"matrix": matrix,
                                 "columns": meta.pop("columns"),
                                 "rows": meta.pop("rows")}, meta=meta)

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
        label_ids = s.validated_data.get("label_ids", []) or []
        state_id = s.validated_data.get("state_id")
        if state_id is None:
            # WF-001 BR-16 创建接线：经兜底链解析初始状态（类型专属图 is_initial →
            # 项目默认图 is_initial → State.is_default 两级回落）；无 published
            # 工作流时最终回落与 V1.0 行为一致（项目级 is_default，约束保证唯一）。
            from plane.db.models import IssueType
            from plane.workflow.services import WorkflowService

            effective_type_id_early = s.validated_data.get("type_id")
            issue_type_obj = (
                IssueType.objects.filter(pk=effective_type_id_early).first()
                if effective_type_id_early else None
            )
            state_id = WorkflowService().resolve_initial_state(project, issue_type_obj).id

        max_order = Issue.objects.filter(project=project, deleted_at__isnull=True).aggregate(m=Max("sort_order"))["m"]
        epoch = _current_epoch()
        effective_type_id = s.validated_data.get("type_id")

        with transaction.atomic():
            # TASK-008 §2.3：custom_fields 整体校验（未知 key 拒绝 → 逐字段 → 默认值填充
            # → 空值不落 key）+ auto_increment 在事务内取号（advisory lock，BR-09）
            cleaned_cf = validate_custom_fields(project, effective_type_id, request.data.get("custom_fields") or {})
            cleaned_cf = assign_auto_increments(project.id, effective_type_id, cleaned_cf)
            issue = create_issue_svc(
                project_id=project.id,
                actor_id=request.user.id,
                payload={
                    "name": name,
                    "description_html": s.validated_data.get("description_html", "<p></p>"),
                    "description_json": s.validated_data.get("description_json", {}),
                    "state_id": state_id,
                    "issue_type_id": effective_type_id,
                    "priority": s.validated_data.get("priority", Issue.Priority.NONE),
                    "start_date": s.validated_data.get("start_date"),
                    "target_date": s.validated_data.get("target_date"),
                    "parent_id": s.validated_data.get("parent_id"),
                    "prev_sort_order": max_order,
                    "custom_fields": cleaned_cf,
                },
            )
            if assignee_ids:
                # TASK-007：创建首派同样收敛唯一写入口（校验 + 落库 + 通知一体）
                sync_assignees_full(
                    issue_id=issue.id, new_ids=assignee_ids, actor_id=request.user.id
                )
            if label_ids:
                sync_labels(issue, label_ids, request.user.id)
            transaction.on_commit(
                lambda: _record_activity(
                    issue,
                    request.user,
                    verb="created",
                    field="issue_type",
                    new=issue.issue_type.name if issue.issue_type_id else None,
                    new_identifier=issue.issue_type_id,
                    comment=f"创建任务 {issue.project.identifier}-{issue.sequence_id}",
                    epoch=epoch,
                )
            )
        # 重新查询以带 annotate 计数（Serializer 一次取数）
        issue = (
            Issue.objects.select_related("project", "state", "issue_type")
            .prefetch_related("issue_assignees", "issue_labels")
            .annotate(
                **issue_count_annotations(),
            )
            .get(pk=issue.pk)
        )
        return created_response(
            IssueSerializer(issue).data,
            location=request.build_absolute_uri(
                f"/api/v1/workspaces/{kwargs['slug']}/projects/{project.id}/issues/{issue.id}/"
            ),
        )


def _strip_hidden(rows, access: dict[str, str]) -> list:
    """TASK-012 BR-10/12：列表项 custom_fields 的 hidden 键序列化剔除（多端共享）。"""
    from plane.db.services.field_permissions import FieldPermissionService

    for r in rows:
        FieldPermissionService.apply_to_payload(r, access)
    return rows


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
                Issue.objects.select_related("project", "state", "issue_type")
                .prefetch_related("issue_assignees", "issue_labels")
                .annotate(
                    **issue_count_annotations(),
                )
                .get(id=self.kwargs["issue_id"], project_id=project.id, deleted_at__isnull=True)
            )
        except Issue.DoesNotExist:
            raise NotFound("RESOURCE_NOT_FOUND") from None
        return issue

    def retrieve(self, request, *args, **kwargs):
        issue = self.get_object()
        data = IssueSerializer(issue).data
        # WF-004 BR-12：任务当前状态生效锁集（读时派生，仅追加字段——TASK-002
        # 详情冻结契约兼容；无工作流项目恒空，零行为变化）
        from plane.workflow.services import current_field_locks

        data["locked_fields"] = [lk["field"] for lk in current_field_locks(issue)]
        return success_response(data)

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
        # TASK-004 §4.2.5：级联软删整树必须回传受影响数（弃用 204——上游
        # TASK-001 BE-69 / TASK-002 §4.3 已同步回改为 200）
        result = delete_subtree(issue.id, request.user.id)
        return success_response(result)

    def update(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        if project.current_user_role < ProjectRole.CONTRIBUTOR:
            raise AppException("PERM_ROLE_INSUFFICIENT")
        s = IssueWriteSerializer(
            data=request.data,
            partial=True,
            context={"project": project, "is_create": False},
        )
        s.is_valid(raise_exception=True)
        issue = self.get_object()
        assert_issue_writable(issue)  # TASK-009 §4.3.3：归档任务只读（恢复/删除除外）
        # PATCH 安全：validated_data 含 default 字段（assignee_ids=[]、description_html="<p></p>"
        # 等），这些并非用户意图修改。只处理 request.data 中实际出现的字段，否则改优先级会
        # 顺带清空描述和负责人 —— 正是抽屉里优先级/负责人/日期"改不了"的根因。
        data = {k: v for k, v in s.validated_data.items() if k in request.data}

        # ---- WF-004 §2.2/§4.4：受控项目的 PATCH state_id 旁路收口（ADR-0028 #5 裁决）----
        # 有生效工作流时状态变更唯一入口 = POST transitions/（守卫/审批/留痕全链路）；
        # 无工作流项目保持 V1.0 直改（零行为变化）。
        from plane.workflow.services import WorkflowService

        _wf = WorkflowService().resolve_workflow(issue)
        if "state_id" in data and _wf is not None:
            raise AppException(
                "RESOURCE_STATE_INVALID",
                message="该项目已启用工作流，状态变更请走流转端点",
                details=[{"field": "state_id", "code": "INVALID",
                          "message": "受控流转：POST …/transitions/"}],
            )

        # ---- WF-004 §2.3/§4.4：字段锁定拦截（读时派生；PROJ_ADMIN 豁免留痕）----
        from plane.workflow.services import current_field_locks

        _locks = current_field_locks(issue) if _wf is not None else []
        if _locks:
            _lock_fields = {lk["field"] for lk in _locks}
            _hit = [k for k in data if k in _lock_fields]
            if "custom_fields" in data:
                _hit += [ck for ck in (data.get("custom_fields") or {}) if ck in _lock_fields]
            if _hit:
                from plane.workflow.guards import effective_project_role

                if effective_project_role(request.user, issue.project_id) < ProjectRole.ADMIN:
                    raise AppException(
                        "VALIDATION_ERROR",
                        message=f"字段在状态「{issue.state.name}」中锁定，流转出该状态自动解锁",
                        details=[{"field": f, "code": "FIELD_LOCKED",
                                  "message": f"锁定来源：{issue.state.name}"} for f in _hit],
                    )
                # 管理员强制路径留痕（field="field_locks.force" 单字段特例，WF-004 §2.3）
                _epoch_lock = _current_epoch()
                from plane.bgtasks.issue_activity import enqueue_activity

                enqueue_activity(
                    issue_id=issue.id, actor_id=request.user.id, verb="updated",
                    epoch=_epoch_lock,
                    before={"field_locks.force": None}, after={"field_locks.force": _hit},
                    comment=f"管理员强制修改锁定字段：{', '.join(_hit)}",
                )

        # ---- TASK-012 §4.4 BR-16：custom_fields PATCH 静默丢弃 readonly/hidden ----
        # 写入路径的 non-writable 静默（rbac §11.2；meta.warning 透出钩子）——
        # 前端旧缓存兼容（v6 决策）
        if "custom_fields" in data and data["custom_fields"] is not None:
            from django.db.models import Q

            from plane.db.models import CustomFieldDefinition
            from plane.db.services.field_permissions import FieldPermissionService

            _cf_defs = list(CustomFieldDefinition.objects
                             .filter(workspace_id=issue.project.workspace_id)
                             .filter(Q(applicable_types__contains=[str(issue.issue_type_id)])
                                     if issue.issue_type_id else Q())
                             .filter(Q(project=issue.project) | Q(project__isnull=True)))
            _access = FieldPermissionService().cached_resolve(
                request, request.user, issue.project, _cf_defs)
            _dropped = FieldPermissionService.drop_non_writable(
                dict(data["custom_fields"] or {}), _access)
            if _dropped:
                data["custom_fields"] = {k: v for k, v in data["custom_fields"].items()
                                          if k not in _dropped}
                request._dropped_fields = _dropped  # meta.warning 透出钩子

        epoch = _current_epoch()
        activities: list[dict] = []

        # ---- 标题 ----
        if "name" in data and data["name"] != issue.name:
            activities.append(
                {
                    "field": "name",
                    "old": issue.name,
                    "new": data["name"],
                    "comment": "更新了 标题",
                }
            )
            issue.name = data["name"]

        # ---- 描述 ----
        if "description_html" in data and data["description_html"] != issue.description_html:
            activities.append(
                {
                    "field": "description_html",
                    "old": issue.description_html[:120] if issue.description_html else "",
                    "new": data["description_html"][:120],
                    "comment": "更新了 描述",
                }
            )
            issue.description_html = data["description_html"]

        # ---- 类型 ----
        if "type_id" in data and data["type_id"] != issue.issue_type_id:
            old_name = issue.issue_type.name if issue.issue_type else None
            old_id = issue.issue_type_id
            new_type_obj = IssueType.objects.filter(
                pk=data["type_id"],
                workspace_id=project.workspace_id,
                is_active=True,
                deleted_at__isnull=True,
            ).first()
            new_name = new_type_obj.name if new_type_obj else None
            activities.append(
                {
                    "field": "issue_type",
                    "old": old_name,
                    "new": new_name,
                    "old_identifier": old_id,
                    "new_identifier": data["type_id"],
                    "comment": "更新了 任务类型",
                }
            )
            issue.issue_type_id = data["type_id"]

        # ---- 优先级 ----
        if "priority" in data and data["priority"] != issue.priority:
            activities.append(
                {
                    "field": "priority",
                    "old": issue.priority,
                    "new": data["priority"],
                    "comment": "更新了 优先级",
                }
            )
            issue.priority = data["priority"]

        # ---- 状态 ----
        if "state_id" in data and str(data["state_id"] or "") != str(issue.state_id or ""):
            new_state = _resolve_state(data["state_id"], project)
            # TASK-005 §4.3.3：迁入 completed 前的依赖拦截（force 通道仅管理员 + comment ≥5 字）
            force_comment = None
            if new_state.group == "completed" and ((issue.state.group if issue.state else None) != "completed"):
                force = bool(request.data.get("force"))
                if force and len(str(request.data.get("comment") or "").strip()) < 5:
                    raise AppException(
                        "VALIDATION_ERROR",
                        message="强制完成需填写说明",
                        details=[{"field": "comment", "code": "REQUIRED", "message": "强制完成说明不少于 5 个字符"}],
                    )
                try:
                    assert_completable(
                        issue=issue,
                        to_state=new_state,
                        force=force,
                        is_admin=project.current_user_role >= ProjectRole.ADMIN,
                    )
                except TransitionBlockedError as e:
                    raise AppException(
                        "RESOURCE_TRANSITION_BLOCKED",
                        message="存在未完成的前置任务，无法完成该工作项",
                        details=[
                            {
                                "field": "state_id",
                                "code": "BLOCKED_BY",
                                "issue_key": b["issue_key"],
                                "message": f"{b['issue_key']} {b['name']}",
                            }
                            for b in e.blockers
                        ],
                    ) from None
                except PermissionError:
                    raise AppException(
                        "PERM_ROLE_INSUFFICIENT",
                        message="仅项目管理员可强制完成",
                    ) from None
                if force:
                    force_comment = str(request.data.get("comment") or "").strip()
            activities.append(
                {
                    "field": "state",
                    "old": issue.state.name if issue.state else None,
                    "new": new_state.name,
                    "old_identifier": issue.state_id,
                    "new_identifier": new_state.id,
                    "comment": f"强制完成：{force_comment}" if force_comment else "更新了 状态",
                }
            )
            issue.state = new_state

        # ---- 起止日期 ----
        if "start_date" in data and data["start_date"] != issue.start_date:
            activities.append(
                {
                    "field": "start_date",
                    "old": str(issue.start_date),
                    "new": str(data["start_date"]),
                    "comment": "更新了 开始时间",
                }
            )
            issue.start_date = data["start_date"]
        if "target_date" in data and data["target_date"] != issue.target_date:
            activities.append(
                {
                    "field": "target_date",
                    "old": str(issue.target_date),
                    "new": str(data["target_date"]),
                    "comment": "更新了 截止时间",
                }
            )
            issue.target_date = data["target_date"]

        # ---- parent（移动子树：行锁内全校验 BR-01/02/03/13，TASK-004 §4.3.2）----
        if "parent_id" in data and str(data["parent_id"] or "") != str(issue.parent_id or ""):
            try:
                with transaction.atomic():
                    locked = (
                        Issue.objects.select_for_update()
                        .select_related("project")
                        .only("id", "parent_id", "project_id")
                        .get(pk=issue.pk)
                    )
                    check_move(locked, data["parent_id"])
            except CircularDependencyError as e:
                raise AppException(
                    "RESOURCE_CIRCULAR_DEPENDENCY",
                    message="不能将该工作项移动到它自己的子级之下",
                    details=[{"field": "parent_id", "code": "CYCLE", "message": e.path}],
                ) from None
            except DepthLimitExceeded as e:
                raise AppException(
                    "RESOURCE_LIMIT_EXCEEDED",
                    message="层级已达 5 层上限，无法移动",
                    details=[
                        {
                            "field": "parent_id",
                            "code": "DEPTH",
                            "message": f"移动后最深节点将位于第 {e.current_depth} 层",
                        }
                    ],
                ) from None
            except StateInvalidError:
                raise AppException(
                    "RESOURCE_STATE_INVALID",
                    message="目标任务已归档，恢复后才能挂子任务",
                    details=[{"field": "parent_id", "code": "STATE", "message": "新父任务已归档"}],
                ) from None
            except SubtreeDepthGuardError:
                raise AppException(
                    "SERVER_ERROR",
                    message="层级数据异常，已记录告警，请稍后重试",
                ) from None
            activities.append(
                {
                    "field": "parent",
                    "old": str(issue.parent_id),
                    "new": str(data["parent_id"]),
                    "old_identifier": issue.parent_id,
                    "new_identifier": data["parent_id"],
                    "comment": "移动了子树" if data["parent_id"] else "摘出为顶层",
                }
            )
            issue.parent_id = data["parent_id"]

        # ---- 估算工时（TASK-006 §4.2.3：≤525600 分钟）----
        if "estimate_minutes" in data:
            est = data["estimate_minutes"]
            if est is not None and est > 525600:
                raise AppException(
                    "VALIDATION_ERROR",
                    message="估算不能超过 525600 分钟",
                    details=[{"field": "estimate_minutes", "code": "TOO_LARGE", "message": "估算不能超过 525600 分钟"}],
                )
            if est != issue.estimate_minutes:
                activities.append(
                    {
                        "field": "estimate_minutes",
                        "old": str(issue.estimate_minutes),
                        "new": str(est),
                        "comment": "更新了 估算工时",
                    }
                )
                issue.estimate_minutes = est

        # ---- sort_order（看板拖拽）----
        if "sort_order" in data and data["sort_order"] != issue.sort_order:
            activities.append(
                {
                    "field": "sort_order",
                    "old": str(issue.sort_order),
                    "new": str(data["sort_order"]),
                    "comment": "更新了 排序",
                }
            )
            issue.sort_order = data["sort_order"]

        # ---- custom_fields（TASK-008 §4.2.4 PATCH 合并语义 + BR-14 逐键 diff）----
        if "custom_fields" in data:
            # 类型变更与字段值同请求提交时，按**新类型**的作用域校验（issue_type 段已先行赋值）
            merged_cf = merge_custom_fields(
                project, issue.issue_type_id, issue.custom_fields or {}, data["custom_fields"]
            )
            for change in diff_custom_fields(issue.custom_fields, merged_cf):
                activities.append(
                    {
                        "field": change["key"],  # cf_<key>（key 本身已带前缀）
                        "old": change["old"],
                        "new": change["new"],
                        "comment": f"更新了自定义字段 {change['key']}",
                    }
                )
            issue.custom_fields = merged_cf

        # ---- 负责人（TASK-007：兼容路径收敛 sync_assignees_full 唯一写入口，
        #      保留原有 assignees 汇总 Activity 行；逐人明细行由 on_commit 任务补写 BR-10）----
        if "assignee_ids" in data:
            new_ids = list(data["assignee_ids"] or [])
            old_ids = sorted(str(ia.assignee_id) for ia in issue.issue_assignees.all())
            new_ids_str = sorted(str(x) for x in new_ids)
            if old_ids != new_ids_str:
                activities.append(
                    {
                        "field": "assignees",
                        "old": ",".join(old_ids) or "previous",
                        "new": ",".join(new_ids_str),
                        "comment": "更新了 负责人",
                    }
                )
                sync_assignees_full(
                    issue_id=issue.id, new_ids=new_ids, actor_id=request.user.id
                )

        with transaction.atomic():
            issue.save()
            for a in activities:
                _record_activity(
                    issue,
                    request.user,
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
            issue = (
                Issue.objects.select_related("project")
                .prefetch_related("issue_labels")
                .get(id=kwargs["issue_id"], project_id=project.id, deleted_at__isnull=True)
            )
        except Issue.DoesNotExist:
            raise NotFound("RESOURCE_NOT_FOUND") from None

        payload = request.data or {}
        new_ids = payload.get("label_ids") or []
        if not isinstance(new_ids, list):
            raise AppException(
                "VALIDATION_ERROR",
                details=[{"field": "label_ids", "code": "INVALID", "message": "label_ids 必须为列表"}],
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
            label_name_map = dict(Label.objects.filter(pk__in=new_ids_set | old_ids).values_list("id", "name"))
            for lid in added:
                transaction.on_commit(
                    lambda lid=lid: _record_activity(
                        issue,
                        request.user,
                        verb="updated",
                        field="labels",
                        new_identifier=lid,
                        new=label_name_map.get(lid),
                        comment=f"添加了 标签 {label_name_map.get(lid, '')}",
                        epoch=epoch,
                    )
                )
            for lid in removed:
                transaction.on_commit(
                    lambda lid=lid: _record_activity(
                        issue,
                        request.user,
                        verb="updated",
                        field="labels",
                        old_identifier=lid,
                        old=label_name_map.get(lid),
                        comment=f"移除了 标签 {label_name_map.get(lid, '')}",
                        epoch=epoch,
                    )
                )
        return success_response({"id": str(issue.id), "label_ids": sorted(new_ids_set)})


# ─────────────────────────────────────────────────────────────────────
# 子任务
# ─────────────────────────────────────────────────────────────────────
class IssueSubtreeView(APIView):
    """GET /workspaces/{slug}/projects/{pid}/issues/{iid}/subtree/（TASK-004 §4.2.2）。

    一次 CTE 整树：root 单列 + nodes 平铺（相对根 depth，根=0）+ stats（含根口径）。
    归档根 404（BR-09 归档树整体不可见，UT-07）；truncated=true 不装配 stats。
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        exists = Issue.objects.filter(
            id=kwargs["issue_id"], project_id=project.id, deleted_at__isnull=True, archived_at__isnull=True
        ).exists()
        if not exists:
            raise NotFound("RESOURCE_NOT_FOUND") from None
        data = fetch_subtree(kwargs["issue_id"])
        return success_response(
            data,
            meta={"truncated": "stats" not in data, "node_limit": SUBTREE_NODE_LIMIT},
        )


class IssueSubIssueListCreateView(APIView):
    """GET/POST /workspaces/{slug}/projects/{pid}/issues/{iid}/sub-issues/"""

    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        try:
            issue = Issue.objects.get(id=kwargs["issue_id"], project_id=project.id, deleted_at__isnull=True)
        except Issue.DoesNotExist:
            raise NotFound("RESOURCE_NOT_FOUND") from None
        subs = (
            Issue.objects.filter(parent_id=issue.id, deleted_at__isnull=True)
            .select_related("state", "issue_type")
            .prefetch_related("issue_assignees")
            .order_by("sort_order", "-created_at")
        )
        # TASK-012 §4.4：hidden 剔除须请求级 resolve access（与列表视图同源；
        # 原实现误用 self._list_access——该属性只在列表视图 get 内设置，此处 500）
        from plane.db.models import CustomFieldDefinition as _CFD4
        from plane.db.services.field_permissions import FieldPermissionService

        _cf_defs = list(_CFD4.objects
                        .filter(workspace_id=project.workspace_id)
                        .filter(Q(project=project) | Q(project__isnull=True)))
        _access = FieldPermissionService().cached_resolve(request, request.user, project, _cf_defs)
        data = _strip_hidden(IssueSerializer(subs, many=True).data, _access)
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

        # BR-13：挂到已归档父 = 对其写入（归档写保护，TASK-009 §4.3.3 收口的补位）
        if parent.archived_at is not None:
            raise AppException(
                "RESOURCE_STATE_INVALID",
                message="目标任务已归档，恢复后才能挂子任务",
                details=[{"field": "parent_id", "code": "STATE", "message": "父任务已归档"}],
            )
        # BR-02：业务深度 ≤5（写入层唯一防线；CTE_GUARD_DEPTH 是查询侧保险丝，不在此用）
        parent_depth = depth_of(parent.id)
        if parent_depth + 1 > MAX_ISSUE_DEPTH:
            raise AppException(
                "RESOURCE_LIMIT_EXCEEDED",
                message="层级已达 5 层上限，无法再创建子任务",
                details=[{"field": "parent_id", "code": "DEPTH", "message": f"当前父任务位于第 {parent_depth} 层"}],
            )
        # 边界：单父 100 上限
        existing = Issue.objects.filter(parent_id=parent.id, deleted_at__isnull=True).count()
        if existing >= MAX_SUB_ISSUES_PER_PARENT:
            raise AppException(
                "RESOURCE_LIMIT_EXCEEDED",
                details=[
                    {
                        "field": "parent_id",
                        "code": "TOO_LARGE",
                        "message": f"单个任务最多 {MAX_SUB_ISSUES_PER_PARENT} 个子任务",
                        "limit": MAX_SUB_ISSUES_PER_PARENT,
                    }
                ],
            )

        # 用 IssueWriteSerializer 走完整校验（type / priority / label_ids 等）
        s = IssueWriteSerializer(
            data=request.data,
            context={"project": project, "is_create": True},
        )
        s.is_valid(raise_exception=True)
        assignee_ids = s.validated_data.get("assignee_ids", []) or []
        label_ids = s.validated_data.get("label_ids", []) or []
        # 缺省 state 取项目默认（WF-001 BR-16：经兜底链解析初始状态）
        state_id = s.validated_data.get("state_id")
        if state_id is None:
            from plane.db.models import IssueType
            from plane.workflow.services import WorkflowService

            sub_type_id = s.validated_data.get("type_id")
            issue_type_obj = (
                IssueType.objects.filter(pk=sub_type_id).first() if sub_type_id else None
            )
            state_id = WorkflowService().resolve_initial_state(project, issue_type_obj).id
        max_order = Issue.objects.filter(project=project, deleted_at__isnull=True).aggregate(m=Max("sort_order"))["m"]
        epoch = _current_epoch()

        with transaction.atomic():
            # TASK-008：子任务创建同样走 12 类型校验 + auto_increment 取号
            cleaned_cf = validate_custom_fields(
                project, s.validated_data.get("type_id"), request.data.get("custom_fields") or {}
            )
            cleaned_cf = assign_auto_increments(project.id, s.validated_data.get("type_id"), cleaned_cf)
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
                    "custom_fields": cleaned_cf,
                },
            )
            if assignee_ids:
                # TASK-007：子任务创建首派同样收敛唯一写入口
                sync_assignees_full(
                    issue_id=sub.id, new_ids=assignee_ids, actor_id=request.user.id
                )
            if label_ids:
                sync_labels(sub, label_ids, request.user.id)
            transaction.on_commit(
                lambda: _record_activity(
                    sub,
                    request.user,
                    verb="created",
                    field="parent",
                    new_identifier=str(parent.id),
                    new=parent.name,
                    comment=f"作为 {parent.project.identifier}-{parent.sequence_id} 的子任务创建",
                    epoch=epoch,
                )
            )
        sub = (
            Issue.objects.select_related("project", "state", "issue_type")
            .prefetch_related("issue_assignees", "issue_labels")
            .annotate(
                **issue_count_annotations(),
            )
            .get(pk=sub.pk)
        )
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
    """GET /workspaces/{slug}/projects/{pid}/issues/{iid}/activities/ —— TASK-010 §4.3.3。

    epoch 组感知分页（30 组/页，§6.3 显式豁免）：两步取数（DISTINCT ON epoch 组边界
    +1 探测 → epoch IN 整组取回），组永不跨页；游标 = 本页末组 epoch 毫秒 Base64
    （keyset，偏离 §6.2 三段式的显式豁免）；排序 -epoch,-created_at,-id 全序。
    field_label 服务端解析；?field= 与 ?actor_id= 过滤。
    """

    permission_classes = [IsAuthenticated]
    GROUP_PAGE_SIZE = 30

    def get(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        try:
            issue = Issue.objects.get(id=kwargs["issue_id"], project_id=project.id, deleted_at__isnull=True)
        except Issue.DoesNotExist:
            raise NotFound("RESOURCE_NOT_FOUND") from None

        base = IssueActivity.objects.filter(issue=issue).select_related("actor")
        if field := request.query_params.get("field"):
            base = base.filter(field=field)
        if actor_id := request.query_params.get("actor_id"):
            base = base.filter(actor_id=actor_id)
        cursor_epoch = None
        if cursor := request.query_params.get("cursor"):
            cursor_epoch = self._decode_cursor(cursor)

        qs_filter = base
        if cursor_epoch is not None:
            qs_filter = base.filter(epoch__lt=cursor_epoch)
        epochs = list(
            qs_filter.order_by("-epoch").values_list("epoch", flat=True)
            .distinct("epoch")[: self.GROUP_PAGE_SIZE + 1])
        has_next = len(epochs) > self.GROUP_PAGE_SIZE
        epochs = epochs[: self.GROUP_PAGE_SIZE]
        rows = list(
            base.filter(epoch__in=epochs).order_by("-epoch", "-created_at", "-id"))

        groups, current = [], None
        for r in rows:
            if current is not None and current["epoch"] == r.epoch:
                current["items"].append(self._item(r))
            else:
                current = {
                    "id": str(r.id),
                    "epoch": r.epoch,
                    "actor": {
                        "id": str(r.actor_id) if r.actor_id else None,
                        "display_name": r.actor.display_name if r.actor else None,
                    },
                    "verb": r.verb,
                    "comment": r.comment,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "items": [self._item(r)],
                }
                groups.append(current)
        total_groups = base.values("epoch").distinct().count()
        per_page = self.GROUP_PAGE_SIZE
        return success_response(
            groups,
            meta={
                "next_cursor": self._encode_cursor(epochs[-1]) if has_next and epochs else None,
                "prev_cursor": None,
                "next_page_results": has_next,
                "prev_page_results": False,
                "count": len(groups),
                "total_count": total_groups,
                "total_pages": (total_groups + per_page - 1) // per_page,
                "page": 1 if cursor_epoch is None else None,
                "per_page": per_page,
                "grouped_by": "epoch",
            },
        )

    @staticmethod
    def _item(r) -> dict:
        from plane.db.services.activity_builder import field_label

        return {
            "field": r.field,
            "field_label": field_label(r.field),
            "old_value": r.old_value,
            "new_value": r.new_value,
            "old_identifier": str(r.old_identifier) if r.old_identifier else None,
            "new_identifier": str(r.new_identifier) if r.new_identifier else None,
        }

    @staticmethod
    def _encode_cursor(epoch: float) -> str:
        import base64

        return base64.b64encode(str(int(epoch)).encode()).decode()

    @staticmethod
    def _decode_cursor(cursor: str) -> float:
        import base64

        try:
            return float(base64.b64decode(cursor.encode()).decode())
        except Exception:
            raise AppException("VALIDATION_INVALID_CURSOR") from None


# ─────────────────────────────────────────────────────────────────────
# IssueType 列表（项目可用类型 = WS active）
# ─────────────────────────────────────────────────────────────────────
class IssueTypeListView(APIView):
    """GET /workspaces/{slug}/projects/{pid}/issue-types/ —— TASK-002 §4.3.1 第 12 行。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        types = (
            Issue.objects.model._meta.get_field("issue_type")
            .related_model.objects.filter(workspace_id=project.workspace_id, is_active=True, deleted_at__isnull=True)
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
