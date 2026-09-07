"""甘特图端点（GANTT-001 §4.2 —— 渲染与取数地基，交互层归 GANTT-002）。

端点：
  GET  /workspaces/{slug}/projects/{pid}/gantt/                    视窗行取数
  POST /workspaces/{slug}/projects/{pid}/gantt/relations/bulk/     可见行连线批量
  GET  /workspaces/{slug}/projects/{pid}/gantt/unscheduled/        未排期任务列表
  GET  /workspaces/{slug}/projects/{pid}/gantt/overdue-summary/    延期概览聚合（GANTT-002 §4.2.1）

设计要点（规格 §1 硬承诺）：
  - 视窗取数：时间×行双重裁剪（BR-02 相交判定 + CursorPagination 行窗口），
    绝不全量拉取——1 万任务/5 年跨度首屏 P95 < 1.5s、平移预取 P95 < 300ms；
  - 连线数据零二次加工：直接消费 TASK-005 冻结的 IssueLink 成对存储，
    仅增 violation 派生标记与镜像去重（§4.3.3）；
  - 进度单源：服务端按 §1.2 口径下发 progress/progress_source（BR-04）；
  - tz 请求级解析（BR-05，RPT-001 同款范式）：?tz= > X-Client-TZ > Asia/Shanghai；
  - 延期概览（GANTT-002 §2.3）：逾期口径与行级 is_overdue 真同源
    （``is_overdue`` / ``overdue_q`` 唯一真源）；端点级限流 10/min·user
    （Valkey 固定窗口，§4.2.1 契约要点 4）。
"""
from __future__ import annotations

import base64
import logging
import uuid
from collections import defaultdict
from datetime import date, datetime
from zoneinfo import ZoneInfo

from django.db.models import (
    Count,
    F,
    Min,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
)
from django.db.models.functions import Coalesce
from rest_framework.exceptions import ErrorDetail, NotFound, ValidationError
from rest_framework.views import APIView

from plane.app.filters.compiler import (
    compile as compile_dsl,
)
from plane.app.filters.compiler import (
    parse_filters_param,
    tree_references_blocked,
    validate_dsl,
)
from plane.app.permissions import IsAuthenticated
from plane.app.views._access import get_project_or_404
from plane.base.exception import AppException
from plane.base.response import success_response
from plane.base.throttling import BASE_THROTTLES, ReportRateThrottle
from plane.db.models import Issue, IssueAssignee, IssueLink, IssueView, Project
from plane.db.services.issue_hierarchy import issue_count_annotations
from plane.db.services.stats import DEFAULT_TZ, _is_valid_tz
from plane.db.services.view_service import resolve_view
from plane.settings.features import MAX_ISSUE_DEPTH

logger = logging.getLogger("plane.app.views.gantt")

#: 行分页（BR-09 / §2.5）：默认 60、上限 100 静默截断
GANTT_PER_PAGE_DEFAULT = 60
GANTT_PER_PAGE_MAX = 100
#: 连线批量上限（§2.5：一次 ≤ 60 issue，超限由前端分批）
GANTT_BULK_MAX = 60
#: 三粒度枚举（§1.4——服务端仅校验与回显，不影响取数）
GRANULARITIES = ("day", "week", "month")
#: 语义组 → 进度约定值（§1.2 唯一口径：无子任务时按语义组）
STATE_PROGRESS: dict[str, int] = {
    "completed": 100,
    "started": 50,
    "unstarted": 0,
    "backlog": 0,
    "cancelled": 0,
}
#: 连线下发类型全集（镜像 is_blocked_by 由 FORWARD_TYPES 排除，§4.3.3）
FORWARD_RELATION_TYPES = ("blocks", "relates_to", "duplicates")
#: 明细截断上限（GANTT-002 §3.2 / §4.2.1 契约要点 1：按逾期天数降序前 20）
GANTT_OVERDUE_ITEMS_MAX = 20
#: 逾期豁免语义组（GANTT-002 §2.3 唯一口径常量）：行级 is_overdue 与
#: overdue-summary 聚合共用——两处禁止重列完成/取消组（防口径漂移）。
OVERDUE_EXEMPT_STATE_GROUPS: tuple[str, ...] = ("completed", "cancelled")


def is_overdue(*, state_group: str, target_date: date | None, today: date) -> bool:
    """逾期判定唯一实现（GANTT-001 §4.3.2 行级 is_overdue ＝ GANTT-002 §2.3
    概览口径——真同源）：语义组不在豁免组且 ``target_date`` 非空且早于今天。
    **不要求 start_date**——start 为空、仅 target 逾期的开放端条同样计入（§2.3）。"""
    return (
        state_group not in OVERDUE_EXEMPT_STATE_GROUPS
        and target_date is not None
        and target_date < today
    )


def overdue_q(today: date) -> Q:
    """``is_overdue`` 的 SQL 集合投影（overdue-summary 聚合端点专用）——同一
    口径、同一豁免组常量。state 为 NULL 的行按 unstarted 折算（与
    ``_serialize_row`` 的 ``state.group if state else "unstarted"`` 一致：不豁免、
    可逾期）——Django 的取反自动生成 ``NOT (group IN (…) AND group IS NOT NULL)``，
    NULL state 行天然计入，无需显式 ``state__isnull`` 支路（IT-04 一致性用例守护）。"""
    return Q(target_date__isnull=False, target_date__lt=today) & ~Q(
        state__group__in=OVERDUE_EXEMPT_STATE_GROUPS
    )


# ─────────────────────────────────────────────────────────────────────
# 参数解析（§4.2.1 契约要点 8 / §2.4 异常表）
# ─────────────────────────────────────────────────────────────────────
def _parse_date_param(request, key: str) -> date:
    raw = request.query_params.get(key)
    if not raw:
        raise AppException(
            "VALIDATION_INVALID_PARAM",
            message="查询参数非法",
            details=[{"field": key, "code": "REQUIRED", "message": f"{key} 为必填（YYYY-MM-DD）"}],
        )
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()  # noqa: DTZ007 —— 纯日期参数无时区语义
    except ValueError:
        raise AppException(
            "VALIDATION_INVALID_PARAM",
            message="查询参数非法",
            details=[{"field": key, "code": "INVALID_DATE", "message": "日期格式应为 YYYY-MM-DD"}],
        ) from None


def _parse_granularity(request) -> str:
    gran = request.query_params.get("granularity") or "day"
    if gran not in GRANULARITIES:
        raise AppException(
            "VALIDATION_INVALID_PARAM",
            message="查询参数非法",
            details=[{"field": "granularity", "code": "NOT_A_CHOICE",
                      "message": "granularity 仅支持 day|week|month"}],
        )
    return gran


def _resolve_tz(request) -> ZoneInfo:
    """BR-05 请求级时区：?tz= > X-Client-TZ 头 > 默认 Asia/Shanghai；
    非法值（非 IANA 时区名）→ 400 VALIDATION_ERROR 子码 INVALID（不静默回退，
    ErrorDetail 显式指定大写子码——§2.4 异常表）。"""
    name = request.query_params.get("tz") or request.headers.get("X-Client-TZ") or DEFAULT_TZ
    if not _is_valid_tz(name):
        raise ValidationError({"tz": ErrorDetail(
            "tz 须为合法 IANA 时区名，如 Asia/Shanghai", code="INVALID")})
    return ZoneInfo(name)


def _parse_per_page(request) -> tuple[int, bool]:
    """per_page 默认 60、上限 100 静默截断（返回 (生效值, 是否截断)）。"""
    raw = request.query_params.get("per_page")
    try:
        v = int(raw) if raw is not None else GANTT_PER_PAGE_DEFAULT
    except (TypeError, ValueError):
        return GANTT_PER_PAGE_DEFAULT, False
    if v < 1:
        return GANTT_PER_PAGE_DEFAULT, False
    return (min(v, GANTT_PER_PAGE_MAX), v > GANTT_PER_PAGE_MAX)


def _decode_cursor(raw: str | None) -> int:
    """游标解码：``{value}:{offset}:{is_prev}`` Base64 → 行偏移量
    （api-conventions §6.2；解码失败 400 VALIDATION_INVALID_CURSOR）。"""
    if not raw:
        return 0
    try:
        return max(int(base64.b64decode(raw).decode().split(":")[1]), 0)
    except Exception:  # noqa: BLE001 —— 游标非法按 400 契约
        raise AppException("VALIDATION_INVALID_CURSOR") from None


def _encode_cursor(offset: int, *, is_prev: bool, per_page: int) -> str:
    return base64.b64encode(f"{per_page}:{offset}:{1 if is_prev else 0}".encode()).decode()


# ─────────────────────────────────────────────────────────────────────
# 查询构造（§4.3.1）
# ─────────────────────────────────────────────────────────────────────
def _relation_count_annotation():
    """§2.1 连线批量判定信号：该行为端点的关联关系数（四种类型全计）。

    成对存储下每条业务关系恰有一行 issue_id=本行（正向行或镜像行二择一），
    故只计 issue 侧行即逻辑关系数（UT-21：2 条关联 → 2）。``values('issue_id')``
    先于 annotate 落 GROUP BY 相关键——否则 Django 退化为 GROUP BY id + LIMIT 1
    取任意行（Subquery 聚合的已知坑，与 spent_minutes 同款范式）。
    """
    rel_sq = (
        IssueLink.objects.filter(
            issue_id=OuterRef("pk"), deleted_at__isnull=True,
            related_issue__deleted_at__isnull=True,
        ).order_by().values("issue_id").annotate(c=Count("id")).values("c")
    )
    return Coalesce(Subquery(rel_sq), 0)


def _gantt_annotations() -> dict:
    """行契约注解：TASK-004 计数 + TASK-006 spent + 关联计数。

    子树聚合区间（BR-11）不进 SQL——多跳子孙谓词（EXISTS/JOIN 形态）在
    OR 视窗分支下会被规划器展开成数十万次循环的连接计划（1 万任务实测
    P95>2s，IT-01/02 门禁失守）；改由 _subtree_ranges 在 Python 侧对双 NULL
    行求值，聚合条父走独立 ``id__in`` 支路（见 _viewport_branches 及
    GanttRowsView.get 的子树分析段）。
    """
    return {
        **issue_count_annotations(),  # sub_issues_count / completed_sub_issues_count / spent_minutes
        "relation_count": _relation_count_annotation(),
    }


def _filtered_base(request, project: Project) -> tuple[QuerySet, dict | None]:
    """筛选管道（BR-01 复用 TASK-011 语义）：view_id 视图层 + ?filters= 临时层。

    返回 (带 DSL 过滤的活跃 QuerySet（未注解）, 视图 display_props)——注解由
    调用方叠加（rows 与 unscheduled 各自的注解面不同）。
    """
    view_tree: dict | None = None
    adhoc_tree: dict | None = parse_filters_param(request.query_params.get("filters"))
    if adhoc_tree is not None:
        validate_dsl(adhoc_tree, project=project, user=request.user)
    display_props: dict = {}
    if raw_vid := request.query_params.get("view_id"):
        # view_id 展开先例 = issues.py list：仅内置或本人可用（存在性隐藏 404）
        view = IssueView.objects.filter(id=raw_vid, project=project, deleted_at__isnull=True).first()
        if view is None or not (view.is_system or view.owner_id == request.user.id):
            raise NotFound("RESOURCE_NOT_FOUND") from None
        view_tree, _degraded = resolve_view(view, project=project, user=request.user)
        display_props = view.display_props or {}
    qs = Issue.objects.filter(project=project, deleted_at__isnull=True, archived_at__isnull=True)
    trees = [t for t in (view_tree, adhoc_tree) if t]
    if trees:
        from plane.app.filters.compiler import CompileContext

        ctx = CompileContext.build(project=project, user=request.user)
        if tree_references_blocked(trees):
            from plane.app.filters.compiler import blocked_exists_annotation

            qs = qs.annotate(_is_blocked=blocked_exists_annotation())
        for tree in trees:
            qs = qs.filter(compile_dsl(tree, ctx))
        qs = qs.distinct()  # M2M 筛选避免重复行（FLT-12 守护）
    return qs, display_props


#: 行序（BR-11 / §5.4）：sort_order 稳定序，尾追加唯一键
GANTT_ROW_ORDER = ("sort_order", "-created_at", "-id")


def _viewport_branches(viewport_start: date, viewport_end: date) -> list[Q]:
    """BR-02/BR-03 相交判定的**无 OR 互斥分解**：带日期行按 (start, target)
    的 NULL 组合拆三支——每支均为复合索引的紧界条件（Bitmap Index Scan 全
    进 Index Cond，1 万任务实测三支全命中 idx_issue_gantt_viewport）。
    OR 复合形态（NULL 容忍）会把规划器估算放大 4~18×而漂移到 Seq Scan
    （IT-07 索引门禁随统计抖动翻转），故分而治之。

    真值表等价：s 有值 t 有值 → s≤ve ∧ t≥vs（主支）；s NULL t 有值 →
    t≥vs（开放左端，BR-02 无穷端）；s 有值 t NULL → s≤ve（开放右端）；
    双 NULL 不入任何分支（BR-03——聚合条父走 agg 支路）。
    """
    return [
        Q(start_date__isnull=False, target_date__isnull=False,
          start_date__lte=viewport_end, target_date__gte=viewport_start),
        Q(start_date__isnull=True, target_date__isnull=False,
          target_date__gte=viewport_start),
        Q(start_date__isnull=False, target_date__isnull=True,
          start_date__lte=viewport_end),
    ]


def _page_meta(
    *, total: int, offset: int, per_page: int, extra: dict | None = None,
) -> dict:
    """分页 meta 全字段（api-conventions §6.3）+ 端点自有字段。"""
    next_cursor = (
        _encode_cursor(offset + per_page, is_prev=False, per_page=per_page)
        if offset + per_page < total else None
    )
    prev_cursor = (
        _encode_cursor(max(offset - per_page, 0), is_prev=True, per_page=per_page)
        if offset > 0 else None
    )
    meta: dict = {
        "next_cursor": next_cursor,
        "prev_cursor": prev_cursor,
        "next_page_results": next_cursor is not None,
        "prev_page_results": prev_cursor is not None,
        "count": 0,
        "total_count": total,
        "total_pages": (total + per_page - 1) // per_page,
        "page": offset // per_page + 1,
        "per_page": per_page,
    }
    if extra:
        meta.update(extra)
    return meta


def _row_depths(rows: list[Issue]) -> dict[uuid.UUID, int]:
    """行树深度（根 = 0）：页内 parent 链向上补齐（≤ MAX_ISSUE_DEPTH 跳，
    每跳一条小查询；深度是纯展示字段，不进主查询的 JOIN/子查询预算）。"""
    parent_of: dict[uuid.UUID, uuid.UUID | None] = {r.id: r.parent_id for r in rows}
    wanted = {pid for pid in parent_of.values() if pid and pid not in parent_of}
    while wanted:
        for iid, pid in Issue.objects.filter(id__in=wanted).values_list("id", "parent_id"):
            parent_of[iid] = pid
        wanted = {pid for pid in parent_of.values() if pid and pid not in parent_of}
    depths: dict[uuid.UUID, int] = {}
    for r in rows:
        depth, cur, walked = 0, r.parent_id, set()
        while cur is not None and cur not in walked:
            walked.add(cur)
            depth += 1
            cur = parent_of.get(cur)
        depths[r.id] = depth
    return depths


def _subtree_ranges(roots: list[uuid.UUID]) -> dict[uuid.UUID, tuple[date | None, date | None]]:
    """双 NULL 行的子树聚合区间（BR-11）：{root: (min_start, max_target)}。

    逐层下探 ≤ MAX_ISSUE_DEPTH（每层一条 parent_id__in 小查询），层内汇总
    后自底向上合并；MIN/MAX 忽略 NULL——无日期后代不贡献，全无日期 → (None,
    None)（不构成聚合条）。仅供页内（≤100 行）双 NULL 行求值。
    """
    if not roots:
        return {}
    children_of: dict[uuid.UUID, list[tuple[uuid.UUID, date | None, date | None]]] = {}
    seen: set[uuid.UUID] = set(roots)
    pending = list(roots)
    for _level in range(MAX_ISSUE_DEPTH):
        if not pending:
            break
        level = Issue.objects.filter(
            parent_id__in=pending, deleted_at__isnull=True, archived_at__isnull=True,
        ).values_list("id", "parent_id", "start_date", "target_date")
        nxt: list[uuid.UUID] = []
        for cid, pid, sd, td in level:
            children_of.setdefault(pid, []).append((cid, sd, td))
            if cid not in seen:
                seen.add(cid)
                nxt.append(cid)
        pending = nxt

    memo: dict[uuid.UUID, tuple[date | None, date | None]] = {}

    def combine(nid: uuid.UUID) -> tuple[date | None, date | None]:
        if nid in memo:
            return memo[nid]
        memo[nid] = (None, None)  # 环兜底（父子环由服务层防线阻断，此处防御性）
        mn = mx = None
        for cid, sd, td in children_of.get(nid, ()):
            cmn, cmx = combine(cid)
            for d, pick_min in ((sd, True), (cmn, True), (td, False), (cmx, False)):
                if d is None:
                    continue
                if pick_min:
                    mn = d if mn is None or d < mn else mn
                else:
                    mx = d if mx is None or d > mx else mx
        memo[nid] = (mn, mx)
        return mn, mx

    return {rid: combine(rid) for rid in roots}


def _serialize_row(
    issue: Issue, *, today: date, depths: dict[uuid.UUID, int], collapsed_ids: frozenset[str],
    subtree: dict[uuid.UUID, tuple[date | None, date | None]],
) -> dict:
    """§4.3.2 进度/逾期/聚合条派生（行序列化，tz 已折算为 today）。"""
    subtree_min, subtree_max = subtree.get(issue.id, (None, None))
    is_aggregated = (
        issue.start_date is None
        and issue.target_date is None
        and subtree_min is not None
    )
    state_group = issue.state.group if issue.state else "unstarted"
    # annotate 派生列（模型无字段声明）——getattr 与 IssueSerializer.get_spent_minutes 同款
    sub_n = getattr(issue, "sub_issues_count", 0) or 0
    if sub_n > 0:  # BR-04 唯一口径：有子任务（取消不计）走子任务比例
        progress = round(100 * (getattr(issue, "completed_sub_issues_count", 0) or 0) / sub_n)
        source = "subtasks"
    else:
        progress = STATE_PROGRESS[state_group]  # 五组全枚举：缺键 KeyError → 500（§4.3.2）
        source = "state"
    return {
        "id": str(issue.id),
        "issue_key": f"{issue.project.identifier}-{issue.sequence_id}" if issue.project else f"{issue.sequence_id}",
        "name": issue.name,
        "depth": depths.get(issue.id, 0),
        "has_children": sub_n > 0,
        "collapsed": str(issue.id) in collapsed_ids,
        "start_date": subtree_min if is_aggregated else issue.start_date,
        "target_date": subtree_max if is_aggregated else issue.target_date,
        "progress": progress,
        "progress_source": source,
        "state_group": state_group,
        "state_color": issue.state.color if issue.state else None,
        "is_overdue": is_overdue(
            state_group=state_group, target_date=issue.target_date, today=today),
        "assignee_ids": [str(a.id) for a in issue.assignees.all()],
        "is_aggregated": is_aggregated,
        "relation_count": getattr(issue, "relation_count", 0),
        "estimate_minutes": issue.estimate_minutes,
        "spent_minutes": getattr(issue, "spent_minutes", 0),
    }


# ─────────────────────────────────────────────────────────────────────
# 端点 1：GET …/gantt/ —— 视窗行取数（§4.2.1）
# ─────────────────────────────────────────────────────────────────────
class GanttRowsView(APIView):
    """视窗行取数：时间×行双重裁剪相交判定 + 进度派生 + 未排期计数 +
    工时对照字段下发 + relation_count 连线判定信号。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        viewport_start = _parse_date_param(request, "viewport_start")
        viewport_end = _parse_date_param(request, "viewport_end")
        if viewport_start > viewport_end:
            raise AppException(
                "VALIDATION_INVALID_PARAM",
                message="查询参数非法",
                details=[{"field": "viewport_start", "code": "INVALID_DATE_RANGE",
                          "message": "视窗起始晚于结束"}],
            )
        granularity = _parse_granularity(request)
        tz = _resolve_tz(request)
        today = datetime.now(tz).date()  # BR-05：请求级时区折算的「今天」（RPT-001 同款）

        base, display_props = _filtered_base(request, project)

        # ── 子树分析（BR-03/BR-11）：双 NULL 行 → (聚合区间, 是否有日期后代)。
        # 双 NULL 行天然命中偏索引且数量小（bench：200/10100）；逐层下探 ≤5 条
        # 小查询后，聚合条父走独立 ``id__in`` 支路、未排期计数纯 Python 求得
        # ——多跳子孙谓词不进 SQL（缘由见 _gantt_annotations 注）。
        dbl_null_ids = list(
            base.filter(start_date__isnull=True, target_date__isnull=True).values_list("id", flat=True)
        )
        ranges = _subtree_ranges(dbl_null_ids)
        agg_ids = [
            rid for rid, (mn, mx) in ranges.items()
            if mn is not None and mn <= viewport_end and mx >= viewport_start
        ]
        unscheduled = sum(1 for mn, mx in ranges.values() if mn is None and mx is None)

        branches = _viewport_branches(viewport_start, viewport_end)
        total = sum(base.filter(b).count() for b in branches) + len(agg_ids)  # 四支互斥
        active = (
            base.select_related("project", "state")
            .prefetch_related("assignees")
            .annotate(**_gantt_annotations())
        )
        per_page, degraded = _parse_per_page(request)
        offset = _decode_cursor(request.query_params.get("cursor"))
        # 各支同键有序：各取 [0, offset+per_page) 归并后按全局行序切窗
        # （前缀长度与 OFFSET 定位同阶——offset 游标范式本身的代价）
        window_end = offset + per_page
        streams = [list(active.filter(b).order_by(*GANTT_ROW_ORDER)[0:window_end]) for b in branches]
        if agg_ids:  # 聚合条父（BR-11）：子树区间与视窗相交者
            streams.append(
                list(active.filter(id__in=agg_ids).order_by(*GANTT_ROW_ORDER)[0:window_end]))
        # (-sort_order, created_at, id) 升序再整体翻转 == (sort_order, -created_at, -id)
        # 行序（BR-11）；四支互斥（NULL 组合 + 双 NULL），归并无重复
        merged = sorted((r for s in streams for r in s), key=lambda r: (-r.sort_order, r.created_at, r.id))
        merged.reverse()
        rows = merged[offset : offset + per_page]

        collapsed_ids = frozenset(display_props.get("collapsed") or ())
        depths = _row_depths(rows)
        payload = [
            _serialize_row(
                r, today=today, depths=depths,
                collapsed_ids=collapsed_ids, subtree=ranges,  # 双 NULL 全集覆盖页内行
            )
            for r in rows
        ]
        meta = _page_meta(
            total=total, offset=offset, per_page=per_page,
            extra={
                "granularity": granularity,
                "viewport": {"start": viewport_start.isoformat(), "end": viewport_end.isoformat()},
                "today": today.isoformat(),
            },
        )
        meta["count"] = len(payload)
        if degraded:  # §2.5：per_page 超限静默截断（meta.degraded 告知）
            meta["degraded"] = {"per_page": f"超出上限 {GANTT_PER_PAGE_MAX}，已截断"}
        return success_response({"rows": payload, "unscheduled_count": unscheduled}, meta=meta)


# ─────────────────────────────────────────────────────────────────────
# 端点 2：POST …/gantt/relations/bulk/ —— 连线批量（§4.2.2 / §4.3.3）
# ─────────────────────────────────────────────────────────────────────
class GanttRelationsBulkView(APIView):
    """可见行连线批量（合并请求杜绝 N+1）：消费 TASK-005 成对存储，
    正向类型 + 对称类型按 id 序去重，仅 blocks 边派生 violation。"""

    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        raw_ids = request.data.get("issue_ids") if isinstance(request.data, dict) else None
        if not isinstance(raw_ids, list):
            raise AppException(
                "VALIDATION_ERROR",
                message="请求参数校验失败",
                details=[{"field": "issue_ids", "code": "INVALID",
                          "message": "issue_ids 必须为字符串数组"}],
            )
        requested = len(raw_ids)  # 截断前计数（§4.2.2 meta.requested，UT-14）
        ids: list[uuid.UUID] = []
        for raw in raw_ids:
            try:
                ids.append(uuid.UUID(str(raw)))
            except (ValueError, AttributeError, TypeError):
                raise AppException(
                    "VALIDATION_ERROR",
                    message="请求参数校验失败",
                    details=[{"field": "issue_ids", "code": "INVALID",
                              "message": "issue_ids 元素须为 UUID"}],
                ) from None
        ids = list(dict.fromkeys(ids))[:GANTT_BULK_MAX]  # 去重（保序）+ §2.5 批量上限

        links = (
            IssueLink.objects
            .filter(issue_id__in=ids,  # BR-12 行集经项目过滤（两端——跨项目边仅可能
                    issue__project_id=project.id,  # 为脏数据，TASK-005 BR-02 服务层已拦；
                    related_issue__project_id=project.id,  # 此处防御纵深不外泄他项目编号/日期）
                    deleted_at__isnull=True, related_issue__deleted_at__isnull=True,
                    relation_type__in=FORWARD_RELATION_TYPES)
            .filter(Q(relation_type="blocks") |            # blocks 族成对异型：只取正向行
                    Q(issue_id__lt=F("related_issue_id")))  # 对称类型成对同型：按 id 序取一行去重
            .select_related("issue__project", "related_issue__project")
            .order_by("created_at", "id")
        )
        edges = []
        for link in links:
            src, dst = link.issue, link.related_issue
            violation = (
                link.relation_type == "blocks"
                and src.target_date is not None
                and dst.start_date is not None
                and dst.start_date < src.target_date
            )
            edges.append({
                "from_issue_id": str(src.id),
                "to_issue_id": str(dst.id),
                "relation_type": link.relation_type,
                "from": {
                    "issue_key": f"{src.project.identifier}-{src.sequence_id}" if src.project else f"{src.sequence_id}",
                    "target_date": src.target_date,
                },
                "to": {
                    "issue_key": f"{dst.project.identifier}-{dst.sequence_id}" if dst.project else f"{dst.sequence_id}",
                    "start_date": dst.start_date,
                    "violation": bool(violation),
                },
            })
        return success_response(
            {"edges": edges}, meta={"requested": requested, "edges": len(edges)},
        )


# ─────────────────────────────────────────────────────────────────────
# 端点 3：GET …/gantt/unscheduled/ —— 未排期区（§4.2.3）
# ─────────────────────────────────────────────────────────────────────
class GanttUnscheduledView(APIView):
    """未排期任务列表（标准游标分页；fields 裁剪为编号/标题/状态/执行人，
    meta.total_count 供折叠区徽标）。与 rows 端点的 unscheduled_count 同一
    筛选语义（BR-03：双 NULL 且子树全无日期；自身双 NULL 但子树有日期的
    父行走 BR-11 聚合条，不入未排期区）。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        base, _ = _filtered_base(request, project)
        # 与 rows 端点的 unscheduled_count 同一口径：双 NULL 且子树全无日期
        # （BR-03）——子树有日期的双 NULL 父行走 BR-11 聚合条，排除之。
        dbl_null_ids = list(
            base.filter(start_date__isnull=True, target_date__isnull=True).values_list("id", flat=True)
        )
        dated_subtree_roots = [
            rid for rid, (mn, mx) in _subtree_ranges(dbl_null_ids).items()
            if not (mn is None and mx is None)
        ]
        qs = (
            base.select_related("project", "state")
            .prefetch_related("assignees")
            .filter(start_date__isnull=True, target_date__isnull=True)
            .exclude(id__in=dated_subtree_roots)
            .order_by(*GANTT_ROW_ORDER)
        )
        per_page, degraded = _parse_per_page(request)
        offset = _decode_cursor(request.query_params.get("cursor"))
        total = qs.count()
        rows = list(qs[offset : offset + per_page])
        payload = [
            {
                "id": str(r.id),
                "issue_key": f"{r.project.identifier}-{r.sequence_id}" if r.project else f"{r.sequence_id}",
                "name": r.name,
                "state_group": r.state.group if r.state else "unstarted",
                "state_color": r.state.color if r.state else None,
                "assignee_ids": [str(a.id) for a in r.assignees.all()],
            }
            for r in rows
        ]
        meta = _page_meta(total=total, offset=offset, per_page=per_page)
        meta["count"] = len(payload)
        if degraded:
            meta["degraded"] = {"per_page": f"超出上限 {GANTT_PER_PAGE_MAX}，已截断"}
        return success_response(payload, meta=meta)


# ─────────────────────────────────────────────────────────────────────
# 端点级限流（GANTT-002 §4.2.1 契约要点 4）——INFRA-005 收编
# ─────────────────────────────────────────────────────────────────────
#: 原 Valkey 自带实现（``_agg_redis`` 家族 + ``GanttAggregationThrottle``）
#: 已收编入 ``plane.base.throttling.ReportRateThrottle``（10/min·user、user_id
#: 计数键维度、Redis 不可达 fail-open 语义均不变；计数载体自裸 redis-py 改
#: django cache——dev LocMem 单进程、prod CACHES 指向 Redis 多副本共享）。


# ─────────────────────────────────────────────────────────────────────
# 端点 4：GET …/gantt/overdue-summary/ —— 延期概览聚合（GANTT-002 §4.2.1）
# ─────────────────────────────────────────────────────────────────────
class GanttOverdueSummaryView(APIView):
    """延期概览：逾期数 / 最长逾期天数 / 按执行人分布 + 前 20 条明细。

    口径（§2.3）：统计三数字在**完整逾期集**上聚合（items 前 20 截断不影响）；
    逾期谓词 = ``is_overdue`` / ``overdue_q`` 唯一真源（与 ``gantt/`` 行级
    is_overdue 同源——含 start_date 为空、仅 target 逾期的开放端条）；
    分布按 ``IssueAssignee`` 分组计数（多人任务每人各计 1，RPT-001 口径）；
    ``by_assignee`` 与 ``items[].assignee_ids`` 各为单条批量查询（防逐行 N+1）。
    筛选与甘特行集同源管道（``_filtered_base``：view_id 视图层 + ?filters=
    临时层）；``today`` 按请求级 tz 折算（BR-05 同款）。聚合与时间视窗无关——
    不接受 ``granularity/viewport_*``；无分页（聚合结果单包返回）。"""

    permission_classes = [IsAuthenticated]
    throttle_classes = [*BASE_THROTTLES, ReportRateThrottle]

    def get(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        tz = _resolve_tz(request)
        today = datetime.now(tz).date()  # BR-05：与行级 is_overdue 同一判定基准

        base, _display_props = _filtered_base(request, project)
        overdue = base.filter(overdue_q(today))

        # 统计三数字：完整逾期集（§2.3），不基于 items 前 20 截断集。
        # max_overdue_days 取完整集最早 target_date：today 固定时
        # ``today - min(target)`` == ``max(today - target)``（严格单调等价）。
        # SQL 侧不做日期减法——Django 的 Date-Date 运算按 duration 语义编译为
        # ``interval '1 day' * (...)``，聚合侧 COALESCE(interval, 0) 在 PG 报
        # 型不匹配；改为 Python 侧派生（同结果零语义差）。
        agg = overdue.aggregate(
            overdue_count=Count("id"),
            earliest_target=Min("target_date"),
        )
        max_overdue_days = (
            (today - agg["earliest_target"]).days if agg["earliest_target"] is not None else 0
        )
        # 明细前 20（§4.2.1 契约要点 1）：逾期天数降序——target_date 升序严格
        # 等价（today 固定、严格单调），次键 id 稳定序；overdue_days 逐行派生。
        rows = list(
            overdue.order_by("target_date", "id")
            .values("id", "sequence_id", "name", "target_date")[:GANTT_OVERDUE_ITEMS_MAX]
        )
        overdue_ids = list(overdue.values_list("id", flat=True))

        # 明细行执行人：单条批量查询组装（§4.2.1 契约要点 2，防逐行 N+1）
        items_assignees: dict[uuid.UUID, list[str]] = defaultdict(list)
        if rows:
            for issue_id, assignee_id in (
                IssueAssignee.objects
                .filter(issue_id__in=[r["id"] for r in rows], deleted_at__isnull=True)
                .order_by("assignee_id")
                .values_list("issue_id", "assignee_id")
            ):
                items_assignees[issue_id].append(str(assignee_id))
        items = [
            {
                "id": str(r["id"]),
                "issue_key": f"{project.identifier}-{r['sequence_id']}",  # 服务端拼接（unified-issue-model）
                "name": r["name"],
                "target_date": r["target_date"],
                "overdue_days": (today - r["target_date"]).days,
                "assignee_ids": items_assignees[r["id"]],
            }
            for r in rows
        ]
        # 分布：完整逾期集上单条聚合 SQL（display_name 经 annotate 别名展开，
        # 键名对齐 §4.2.1 示例——不带 __ 路径；次键 assignee_id 保稳定序）
        by_assignee = [
            {
                "assignee_id": str(r["assignee_id"]),
                "display_name": r["display_name"],
                "count": r["count"],
            }
            for r in (
                IssueAssignee.objects
                .filter(issue_id__in=overdue_ids, deleted_at__isnull=True)
                .annotate(display_name=F("assignee__display_name"))
                .values("assignee_id", "display_name")
                .annotate(count=Count("issue"))
                .order_by("-count", "assignee_id")
            )
        ]
        return success_response(
            {
                "overdue_count": agg["overdue_count"],
                "max_overdue_days": max_overdue_days,
                "by_assignee": by_assignee,
                "items": items,
                "items_truncated": agg["overdue_count"] > len(rows),
            },
            meta={"today": today.isoformat()},
        )
