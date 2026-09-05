"""任务深拷贝服务（TASK-009 §4.3.1）—— 一次锁 / 连续号段 / 内存映射重建。

复制语义核心是「定义 / 历史」二分法（§1.2）：定义拷贝（标题后缀、描述双格式、
优先级、类型、标签、custom_fields、估算、可选起止日期），历史不拷贝（评论 /
附件 / 依赖 / 工时 / Activity / ``description_binary``）；``sequence_id`` 新分配、
状态落项目默认（BR-02）、已归档后代一并复制为**活跃**副本（BR-15——归档属
「状态 / 历史」，结构完整性优先，杜绝静默跳过）。

深拷贝原子性（BR-06）：一次事务 + 一次项目 advisory lock + ``MAX()+1`` 起的
连续号段（与并发创建互斥——锁内逐节点分配，500 节点事务毫秒级）；父子结构
用内存 ``id_map`` 两次 bulk 落库，无需逐层往返数据库。任何一步失败整体回滚
（pytest 侧 mock 注入锚定）。

500 上限（BR-05）用**专用全量计数 CTE** 判定——不复用 ``subtree/`` 端点的
展示侧截断结果集（外层 ``LIMIT 501`` 裁剪后作为复制输入会静默丢节点、使 409
永不可达）：COPY_TARGET_SQL 不截断、仅排除软删、不排除已归档后代，超限由
服务层全量计数后 409 ``RESOURCE_LIMIT_EXCEEDED``（details 子码 ``LIMIT``，
绝不 500）拒绝。

副本 ↔ 源 ``duplicates`` 关联（BR-01 计数锚点）走 ``link_service.create_relation``
（TASK-005 BR-03 唯一写入口）成对写入：正向行 ``IssueLink(issue=新根,
related_issue=源, relation_type='duplicates')`` 是后缀计数与溯源锚点；镜像行
使源任务详情按 ``issue_id=me`` 单向查询即可见副本。其内部先取同一把项目
advisory lock（同事务重入安全），计数与建号仍处同一临界区，同源并发复制
各自成组（BR-01 / §2.6）。
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

from django.conf import settings as dj_settings
from django.db import transaction
from django.db.models import Max
from django.utils.html import strip_tags

from plane.base.exception import AppException
from plane.db.models import Issue, IssueAssignee, IssueLabel, IssueLink, State
from plane.db.services.issue_archive import IssueArchivedError
from plane.db.services.issue_hierarchy import SubtreeDepthGuardError
from plane.db.services.issue_link import create_relation
from plane.db.services.issue_sequence import acquire_project_lock, next_sequence_id
from plane.db.services.sort_order import DEFAULT_GAP

logger = logging.getLogger(__name__)

#: 深拷贝节点上限（BR-05；数值与 subtree/ 一致，语义不同——展示截断 vs 拒绝）
MAX_COPY_NODES = 500

#: 标题长度上限（Issue.name max_length；UT-02：截断原标题保留后缀）
NAME_MAX_LENGTH = 512

#: 排除清单（§4.3.1）：id/编号/排序/状态/完成时间/审计列/父子/Yjs 协同二进制。
#: 描述 JSON/HTML 双格式**保留**（定义性内容）；``description_stripped`` 由
#: ``_stripped`` 重算（bulk_create 不走 save()，不会自动派生）。
COPY_EXCLUDED_FIELDS = frozenset(
    {
        "id",
        "sequence_id",
        "sort_order",
        "state",
        "completed_at",
        "created_at",
        "updated_at",
        "created_by",
        "updated_by",
        "deleted_at",
        "archived_at",
        "parent",
        "parent_id",
        "description_binary",
        "project",
        "project_id",
        "attachment_count",  # 附件是历史：新任务 0 附件
        "name",              # 根副本单独做后缀处理，子节点直拷
    }
)

#: 深拷贝目标集（BR-05/BR-15）：① 不做展示侧截断，超限由服务层全量计数 409；
#: ② 仅排除软删、不排除已归档后代（根活跃校验由 Service 层显式 409 承担，
#: CTE 不重复设 archived 条件）；③ 按 (depth, sequence_id) 返回即广度优先序
#: （同层编号连续，BR-06 号段按此序分配）；④ 深度保险丝同 TASK-004 CTE_GUARD_DEPTH。
COPY_TARGET_SQL = """
    WITH RECURSIVE target(id, depth) AS (
        SELECT id, 0 FROM issues
         WHERE id = %(root)s AND deleted_at IS NULL
        UNION ALL
        SELECT i.id, t.depth + 1
          FROM issues i JOIN target t ON i.parent_id = t.id
         WHERE i.deleted_at IS NULL               -- BR-15：不排除已归档后代，完整复制
           AND t.depth < %(guard)s                -- 深度保险丝，同 TASK-004 CTE_GUARD_DEPTH
    )
    SELECT id, max(depth) OVER () AS max_depth FROM target ORDER BY depth
"""


@dataclass(frozen=True)
class DuplicateOptions:
    """五选项（§4.2.1）：默认 子树✓ 执行人✗ 标签✓ 字段✓ 日期✗。"""

    include_subtrees: bool = True
    include_assignees: bool = False
    include_labels: bool = True
    include_custom_fields: bool = True
    include_dates: bool = False


def copy_target_ids(source_id: uuid.UUID) -> list[uuid.UUID]:
    """全量取回深拷贝目标集 id（不截断），保持广度优先序（§4.3.1）。

    保险丝在 SELECT 之后判定而非 SQL 侧静默截断（issue_hierarchy 同款论证）：
    链长触达 CTE_GUARD_DEPTH 即脏数据（人为改库成环），快速失败并告警。
    """
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute(COPY_TARGET_SQL, {"root": source_id, "guard": dj_settings.CTE_GUARD_DEPTH})
        rows = cursor.fetchall()
    ids = [row[0] for row in rows]
    # 保险丝按**深度**判定而非行数：宽树（501+ 节点、深度 2）是 BR-05 的 409 范畴，
    # 只有深链（max_depth ≥ CTE_GUARD_DEPTH = 环状脏数据）才 500——两者口径不得
    # 互换（sprint-overview 风险 #1；实现期曾把行数当链深误判，flow T9-06 锚定）
    max_depth = max((row[1] for row in rows), default=0)
    if max_depth >= dj_settings.CTE_GUARD_DEPTH:
        logger.error(
            "issue_copy.guard_triggered op=copy_target source_id=%s max_depth=%d guard=%d",
            source_id,
            max_depth,
            dj_settings.CTE_GUARD_DEPTH,
        )
        raise SubtreeDepthGuardError(f"子树深度触达保险丝 {dj_settings.CTE_GUARD_DEPTH}，疑似环状脏数据")
    return ids


def _copy_target_nodes(source: Issue) -> list[Issue]:
    """目标集 ORM 对象（filter 幂等保持 CTE 序；软删行已被 SQL 排除）。"""
    ids = copy_target_ids(source.id)
    by_id = {i.id: i for i in Issue.objects.filter(id__in=ids).select_related("project", "state", "issue_type")}
    return [by_id[i] for i in ids]


def _stripped(html: str | None) -> str | None:
    """description_stripped 派生（Issue.save 同款）：空描述 → NULL。"""
    if not html or html == "<p></p>":
        return None
    return strip_tags(html)


def _default_state(project) -> State | None:
    """项目默认状态（BR-02）：is_default 且无类型作用域（全局默认）。"""
    return State.objects.filter(
        project=project, is_default=True, issue_type__isnull=True, deleted_at__isnull=True
    ).first()


def _column_tail(project_id: uuid.UUID) -> float:
    """当前列尾 sort_order（BR-02：副本列尾追加，65535 步进）。"""
    return (
        Issue.objects.filter(project_id=project_id, deleted_at__isnull=True).aggregate(m=Max("sort_order"))["m"]
        or 0.0
    )


def _copy_suffix_ordinal(source: Issue) -> int:
    """BR-01：现存（未软删）duplicates 正向行计数 + 1（advisory lock 临界区内，无竞态）。

    正向行 = ``related_issue=源`` 方向（issue=某副本根）；镜像行 ``issue=源`` 不在
    该方向上，成对存储不重复计数。已软删关联（源树此前被删过副本）不计数。
    """
    n = IssueLink.objects.filter(
        related_issue_id=source.id,
        relation_type=IssueLink.RelationType.DUPLICATES,
        deleted_at__isnull=True,
    ).count()
    return n + 1


def _suffixed_name(name: str, ordinal: int) -> str:
    """标题 + 后缀（UT-02）：510 字标题 → 截断原标题保留后缀（总长 ≤512）。"""
    suffix = " (副本)" if ordinal == 1 else f" (副本 {ordinal})"
    if len(name) + len(suffix) > NAME_MAX_LENGTH:
        name = name[: NAME_MAX_LENGTH - len(suffix)]
    return name + suffix


def _copy_fields(node: Issue, options: DuplicateOptions) -> dict[str, Any]:
    """按 §1.2 表构造副本字段（排除清单外的定义性内容 + include_* 边界）。"""
    fields: dict[str, Any] = {
        "issue_type_id": node.issue_type_id,
        "priority": node.priority,
        "description_html": node.description_html,
        "description_json": dict(node.description_json or {}),
        "description_stripped": _stripped(node.description_html),
        "estimate_minutes": node.estimate_minutes,
        "completed_at": None,   # BR-02：恒 NULL（状态落默认，新任务未完成）
        "archived_at": None,    # BR-15：归档态不随副本继承
        "attachment_count": 0,  # 附件是历史
    }
    if options.include_custom_fields:
        fields["custom_fields"] = dict(node.custom_fields or {})  # 整体复制（同项目同定义，无需重校验）
    else:
        fields["custom_fields"] = {}
    if options.include_dates:
        fields["start_date"] = node.start_date
        fields["target_date"] = node.target_date
    else:
        fields["start_date"] = None
        fields["target_date"] = None
    return fields


def duplicate_issue(
    *, issue_id: uuid.UUID, actor_id: uuid.UUID, options: DuplicateOptions | None = None
) -> dict:
    """深拷贝主入口（BR-01~06 / BR-15）——视图层负责 404 / 角色与项目归档前置。

    返回 ``{"root": 新根 Issue, "copies": [(新节点, 源节点), ...],
    "total_created": int, "source_id": uuid, "suffix_ordinal": int}``；
    Activity（verb=created，comment=「由 RBT-12 复制创建（共 N 个任务）」）
    经 on_commit 异步投递（epoch 入口生成，TASK-010 BR-04）。
    """
    from plane.bgtasks.issue_hierarchy import record_duplicate

    if options is None:
        options = DuplicateOptions()
    source = Issue.objects.select_related("project").get(id=issue_id, deleted_at__isnull=True)
    if source.archived_at:  # BR-08/§2.5：归档源显式 409（与视图层 Permission 拦截同口径双保险）
        raise IssueArchivedError(
            field="source_issue_id",
            message="任务已归档，恢复后才能复制",
            detail=f"{source.project.identifier}-{source.sequence_id} 已归档",
        )
    project = source.project
    default_state = _default_state(project)

    nodes = [source] if not options.include_subtrees else _copy_target_nodes(source)
    if len(nodes) > MAX_COPY_NODES:  # BR-05：全量计数（非截断集）→ 409，绝不 500
        raise AppException(
            "RESOURCE_LIMIT_EXCEEDED",
            message="任务树过大，无法整体复制",
            details=[
                {
                    "field": "include_subtrees",
                    "code": "LIMIT",
                    "message": f"子树共 {len(nodes)} 个节点，上限 {MAX_COPY_NODES}；可关闭子任务选项或先拆分",
                }
            ],
        )

    epoch = time.time() * 1000  # TASK-010 BR-04：epoch 在动作入口生成（毫秒）
    source_key = f"{project.identifier}-{source.sequence_id}"
    with transaction.atomic():
        acquire_project_lock(project.id)  # 一次取锁（与序列号生成 / link 服务同锁空间）
        start_seq = next_sequence_id(project.id)  # 连续号段（锁内 MAX()+1 起批量分配）
        ordinal = _copy_suffix_ordinal(source)  # 锁内计数（BR-01：同源并发复制各自成组）

        tail = _column_tail(project_id=project.id)
        id_map: dict[uuid.UUID, Issue] = {}
        rows: list[tuple[Issue, Issue]] = []  # (新, 旧) —— 广度优先序
        for offset, node in enumerate(nodes):
            new = Issue(
                project=project,
                sequence_id=start_seq + offset,
                name=_suffixed_name(node.name, ordinal) if node.id == source.id else node.name,
                state=default_state,  # BR-02：落项目默认状态（待办）
                sort_order=tail + DEFAULT_GAP * (offset + 1),  # 列尾追加 + 65535 步进
                created_by_id=actor_id,
                **_copy_fields(node, options),
            )
            id_map[node.id] = new
            rows.append((new, node))
        Issue.objects.bulk_create([r[0] for r in rows], batch_size=100)

        # 重建父子映射：子节点 parent 引用同批新父（两次 bulk，无逐层往返）
        reparent = []
        for new, old in rows:
            if old.parent_id is not None:
                new.parent_id = id_map[old.parent_id].id
                reparent.append(new)
        if reparent:
            Issue.objects.bulk_update(reparent, ["parent_id"], batch_size=100)

        old_ids = [old.id for _, old in rows]
        if options.include_labels:
            label_map: dict[uuid.UUID, list[uuid.UUID]] = {}
            for iid, lid in IssueLabel.objects.filter(issue_id__in=old_ids).values_list("issue_id", "label_id"):
                label_map.setdefault(iid, []).append(lid)
            label_rows = [
                # 同 sync_labels 字段语义（物理行、created_by=操作者）；新副本无既有行，直接构造
                IssueLabel(issue_id=id_map[iid].id, label_id=lid, created_by_id=actor_id)
                for iid, lids in label_map.items()
                for lid in lids
            ]
            if label_rows:
                IssueLabel.objects.bulk_create(label_rows, batch_size=100)

        if options.include_assignees:
            assignee_map: dict[uuid.UUID, list[uuid.UUID]] = {}
            for iid, uid in IssueAssignee.objects.filter(issue_id__in=old_ids).values_list(
                "issue_id", "assignee_id"
            ):
                assignee_map.setdefault(iid, []).append(uid)
            # 同 sync_assignees_full 字段语义（BR-04 assigned_by=实际操作者）；
            # 源执行人已通过成员资格校验且同项目不变，批量构造不重走 sync 入口
            assignee_rows = [
                IssueAssignee(issue_id=id_map[iid].id, assignee_id=uid, assigned_by_id=actor_id)
                for iid, uids in assignee_map.items()
                for uid in uids
            ]
            if assignee_rows:
                IssueAssignee.objects.bulk_create(assignee_rows, batch_size=100)

        # BR-01 计数/溯源锚点：经 TASK-005 BR-03 唯一写入口成对写入（同事务同锁重入安全）
        create_relation(
            issue_id=id_map[source.id].id,
            related_issue_id=source.id,
            relation_type=IssueLink.RelationType.DUPLICATES,
            actor_id=actor_id,
            epoch=epoch,
        )

        root = id_map[source.id]
        total = len(rows)

        def _dispatch() -> None:
            try:
                record_duplicate.delay(str(root.id), str(source.id), str(actor_id), total, source_key, epoch)
            except Exception as exc:  # noqa: BLE001 —— on_commit 回调抛错会打穿已提交请求
                logger.warning("issue_copy.activity_delivery_failed root=%s exc=%s", root.id, exc)

        transaction.on_commit(_dispatch)

    return {
        "root": root,
        "copies": rows[1:],  # (新, 源) 对；根已单列
        "total_created": total,
        "source_id": source.id,
        "suffix_ordinal": ordinal,
    }
