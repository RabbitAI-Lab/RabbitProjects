"""项目动态流合流查询服务（COLLAB-003 §4.3.1~§4.3.3）。

核心纪律：
- BR-03 合流排序下推 SQL：activity × comment 在 ``UNION ALL`` 视图内归并，
  边界/取数/计数全在数据库侧完成——本模块不做任何应用层排序；
- BR-04/§4.3.1 组感知两步取数：第一步 ``DISTINCT ON (group_key)`` 按组时间
  倒序取 ``per_page + 1`` 个组边界（+1 探测 has_next），第二步
  ``group_key = ANY(…)`` 整组取回——同组永不跨页割裂，``batch_count``
  恒为整组行数；
- BR-06/07 任务 ID 集走 ``Issue.all_objects`` 整圈口径（含软删/归档），
  软删仅置 ``deleted_at``、项目归属不变（§4.3.1 软删调和）；
- BR-10 游标锚定组边界复合键 ``(组时间, 组键)``：组键 ``a<epoch>``（同 epoch
  整组；epoch 为空的历史行以 ``a~<id>`` 退化为单行组）/ ``c<id>``（评论单行组）；
- >5000 任务 ID 集分片（chunk 2000）：以 ``OR (issue_id = ANY(…))`` 拼进同一
  条 SQL 归并——分片仍零应用层排序（§4.3.1 分片保护）。
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import uuid
from datetime import datetime
from typing import Any

from django.db import connection

from plane.base.exception import AppException
from plane.db.models import Issue, IssueActivity
from plane.db.services.activity_builder import field_label

logger = logging.getLogger("plane.db.services.activity_stream")

#: 30 组/页（api-conventions §6.3 默认/上限 100 的显式豁免，BR-11）
GROUP_PAGE_SIZE = 30
MAX_GROUPS_PER_PAGE = 50
#: 明细抽屉单次 ≤100（BR-11 / §4.2.2 meta.limit）
DETAIL_LIMIT = 100
#: 任务 ID 集分片保护阈值与分片大小（§4.3.1 末注）
ISSUE_IDS_CHUNK_TRIGGER = 5000
ISSUE_IDS_CHUNK_SIZE = 2000
#: api-conventions §6.4：折叠口径 > 50,000 降级估算标记
TOTAL_COUNT_ESTIMATE_THRESHOLD = 50_000

#: 软删评论的流内固定文案（§2.6 边界条件：不显内容）
SOFT_DELETED_COMMENT_TEXT = "删除了一条评论"
_COMMENT_SNIPPET_LEN = 80

#: §2.3 事件语义化过滤表——activity 行 (verb, field) 匹配域（封闭白名单，BR-08）。
#: ``comment`` 组无 activity 谓词（评论合流源，§4.3.1 矩阵）；``lifecycle`` 组
#: issue 分支恒排除（PROJ-003 §2.3 补登——语义落在 project 域分支，见
#: ``_stream_view_sql`` 矩阵）。
_EVENT_GROUP_PREDICATES: dict[str, str | None] = {
    "created": "a.verb = 'created'",
    "state": "a.verb = 'updated' AND a.field = 'state'",
    "assignees": "a.verb = 'updated' AND a.field = 'assignees'",
    "priority": "a.verb = 'updated' AND a.field = 'priority'",
    "dates": "a.verb = 'updated' AND a.field IN ('start_date', 'target_date')",
    "estimate": "a.verb = 'updated' AND a.field = 'estimate_minutes'",
    # 注：cf 族匹配用 starts_with 而非 LIKE 'cf_%'——psycopg3 客户端绑定下
    # SQL 文本中的裸 % 会被视为占位符（cf_% 与语义等价且免转义）
    "custom_fields": "a.verb = 'updated' AND starts_with(a.field, 'cf_')",
    "relations": "a.verb = 'updated' AND a.field = 'relation'",
    "parent": "a.verb = 'updated' AND a.field = 'parent'",
    "worklog": "a.verb = 'updated' AND a.field = 'worklog'",
    "archived": "a.verb = 'updated' AND a.field = 'archived_at'",
    "deleted": "a.verb = 'deleted'",
    "comment": None,
    "lifecycle": "1 = 0",
}
#: event 白名单展示序（details 可用值提示用，§4.2.1 失败响应）
EVENT_CHOICES: tuple[str, ...] = tuple(_EVENT_GROUP_PREDICATES)

#: 组键格式校验（§4.2.1 要点 5：组键 g 格式非法 → 400 VALIDATION_INVALID_CURSOR）
_GROUP_KEY_RE = re.compile(r"^a[0-9][0-9eE+.\-]*$|^a~[0-9a-f\-]{36}$|^c[0-9a-f\-]{36}$")

#: 批量汇总行动词文案（§4.2.1 batch.summary）
_VERB_SUMMARY: dict[str, str] = {"created": "批量创建了", "deleted": "批量删除了"}

# ─────────────────────────────────────────────────────────────────────
# 合流视图（§4.3.1 _STREAM_VIEW）
# ─────────────────────────────────────────────────────────────────────
_STREAM_VIEW = """
    SELECT a.id, 'activity' AS kind, a.actor_id, a.verb, a.field,
           a.old_value, a.new_value, a.comment AS text, a.epoch,
           CASE WHEN a.epoch IS NULL THEN 'a~' || a.id::text
                ELSE 'a' || a.epoch::text END AS group_key,
           a.issue_id, a.created_at,
           NULL::uuid AS root_id,
           NULL::text AS reply_to_id
      FROM issue_activities a
     WHERE {activity_where}
    UNION ALL
    SELECT c.id, 'comment' AS kind, c.actor_id,
           NULL::varchar AS verb, NULL::varchar AS field,
           NULL::text AS old_value, NULL::text AS new_value,
           CASE WHEN c.deleted_at IS NOT NULL THEN %(soft_deleted_text)s
                ELSE left(c.comment_stripped, {snippet_len}) END AS text,
           NULL::float8 AS epoch,
           'c' || c.id::text AS group_key,
           c.issue_id, c.created_at,
           c.parent_id AS root_id,
           COALESCE(c.accessory, '{{}}'::jsonb) -> 'reply_to' ->> 'actor_id' AS reply_to_id
      FROM issue_comments c
     WHERE {comment_where}
    UNION ALL
    SELECT a.id, 'project' AS kind, a.actor_id, a.verb, a.field,
           a.old_value, a.new_value, a.comment AS text, a.epoch,
           CASE WHEN a.epoch IS NULL THEN 'a~' || a.id::text
                ELSE 'a' || a.epoch::text END AS group_key,
           NULL::uuid AS issue_id, a.created_at,
           NULL::uuid AS root_id,
           NULL::text AS reply_to_id
      FROM issue_activities a
     WHERE {project_where}
"""


def project_issue_ids(project_id: uuid.UUID) -> list[uuid.UUID]:
    """项目域整圈任务 ID 集（BR-06/07：含软删/归档——``all_objects`` 审计口径）。

    取数走 ``idx_issue_proj_state_sort`` 首列前缀（§4.1.1 路径 B 索引论证）。
    """
    return list(
        Issue.all_objects.filter(project_id=project_id).values_list("id", flat=True)
    )


def _chunk_issue_ids(ids: list[uuid.UUID]) -> list[list[uuid.UUID]]:
    """>5000 任务改分片（chunk 2000 多轮，§4.3.1）——OR(any()) 同语句归并。"""
    if len(ids) <= ISSUE_IDS_CHUNK_TRIGGER:
        return [ids]
    return [ids[i:i + ISSUE_IDS_CHUNK_SIZE] for i in range(0, len(ids), ISSUE_IDS_CHUNK_SIZE)]


def _issue_id_where(chunks: list[list[uuid.UUID]], alias: str, params: dict[str, Any]) -> str:
    """任务 ID 集谓词：单分片 ``ANY(%s)``；多分片 OR 拼接（仍单条 SQL）。"""
    if len(chunks) == 1:
        params["issue_ids"] = chunks[0]
        return f"{alias}.issue_id = ANY(%(issue_ids)s)"
    parts = []
    for i, chunk in enumerate(chunks):
        params[f"ids_{i}"] = chunk
        parts.append(f"{alias}.issue_id = ANY(%(ids_{i})s)")
    return "(" + "\n        OR ".join(parts) + ")"


def _stream_view_sql(
    chunks: list[list[uuid.UUID]],
    *,
    event: str | None,
    actor_id: uuid.UUID | None,
    project_id: uuid.UUID,
) -> tuple[str, dict[str, Any]]:
    """按 actor × event 过滤矩阵（§4.3.1 + PROJ-003 §4.3.1 扩域）装配 UNION ALL 视图。

    矩阵：event=comment → activity 行排除（1=0）；event=其它 issue 语义组 →
    comment 行与 project 域行恒排除；event=lifecycle → issue/comment 行恒排除、
    project 域行按 `(verb='created' OR field='status')` 保留；缺省 → 三源全保留。
    actor_id 在 event 决定的行类型域上恒 AND 叠加（project 域行同理参与——
    PROJ-003 §4.3.1 注的「不参与 actor 排除」按对称矩阵口径实现，偏差随
    Sprint-5 ADR 登记）。评论侧不滤软删（软删评论以「删除了一条评论」行
    保留，§2.6）。空任务集项目（chunks 无非空分片）issue/comment 分支 1=0——
    project 域行仍可上流（ANY(空数组) 的类型推断在 PG 不可靠，显式短路）。
    """
    params: dict[str, Any] = {
        "soft_deleted_text": SOFT_DELETED_COMMENT_TEXT,
        "project_id": project_id,
    }
    has_issues = any(chunk for chunk in chunks)

    activity_where: list[str] = []
    if has_issues:
        activity_where.append(_issue_id_where(chunks, "a", params))
    activity_where.append("a.deleted_at IS NULL")
    if event == "comment" or event == "lifecycle" or not has_issues:
        activity_where.append("1 = 0")
    elif event is not None:
        activity_where.append(str(_EVENT_GROUP_PREDICATES[event]))  # 白名单已校验
    if actor_id is not None:
        activity_where.append("a.actor_id = %(actor_id)s")
        params["actor_id"] = actor_id

    comment_where: list[str] = []
    if has_issues:
        comment_where.append(_issue_id_where(chunks, "c", params))
    if event is not None and event != "comment" or not has_issues:
        comment_where.append("1 = 0")
    if actor_id is not None:
        comment_where.append("c.actor_id = %(actor_id)s")

    project_where = [
        "a.issue_id IS NULL",
        "a.project_id = %(project_id)s",
        "a.deleted_at IS NULL",
    ]
    if event == "lifecycle":
        project_where.append("(a.verb = 'created' OR a.field = 'status')")
    elif event is not None:
        project_where.append("1 = 0")
    if actor_id is not None:
        project_where.append("a.actor_id = %(actor_id)s")

    sql = _STREAM_VIEW.format(
        activity_where="\n       AND ".join(activity_where),
        comment_where="\n       AND ".join(comment_where),
        project_where="\n       AND ".join(project_where),
        snippet_len=_COMMENT_SNIPPET_LEN,
    )
    return sql, params


def _dictfetchall(cursor: Any) -> list[dict[str, Any]]:
    cols = [c[0] for c in cursor.description]
    return [dict(zip(cols, row, strict=True)) for row in cursor.fetchall()]


# ─────────────────────────────────────────────────────────────────────
# 组感知两步取数（§4.3.1 BOUNDARY_SQL / ROWS_SQL）
# ─────────────────────────────────────────────────────────────────────
def _boundary_sql(view_sql: str, *, keyset: bool) -> str:
    return f"""
    SELECT group_key, group_at FROM (
        SELECT DISTINCT ON (group_key) group_key, created_at AS group_at
          FROM ({view_sql}) s
         ORDER BY group_key, created_at DESC, id DESC
    ) b
    {'WHERE (group_at, group_key) < (%(cursor_at)s, %(cursor_key)s)' if keyset else ''}
     ORDER BY group_at DESC, group_key DESC
     LIMIT %(groups)s
    """


_ROWS_SQL = """
    SELECT * FROM ({view}) stream
     WHERE stream.group_key = ANY(%(keys)s)
     ORDER BY stream.created_at DESC, stream.id DESC
"""

#: 折叠口径计数（§4.2.1 要点 4）：total_count 由一次 GROUP BY 折算
#: （总行数 − Σ 跨任务组的（行数 − 1））；total_groups 供 total_pages 计算。
_TOTALS_SQL = """
    SELECT group_key, kind, COUNT(*) AS row_count, COUNT(DISTINCT issue_id) AS issue_count
      FROM ({view}) s
     GROUP BY group_key, kind
"""


def filter_fingerprint(*, event: str | None, actor_id: str | None) -> str:
    """归一化 actor_id/event 参数的 sha1 前 8 位（§4.2.1 要点 5：过滤指纹）。

    过滤变更后旧游标即失效（f 与当前过滤不符 → 400）。
    """
    raw = f"actor={actor_id or ''};event={event or ''}"
    return hashlib.sha1(raw.encode()).hexdigest()[:8]


# ─────────────────────────────────────────────────────────────────────
# 游标编解码（§4.2.1 要点 5：三段式外壳 + base64url 组边界锚载荷）
# ─────────────────────────────────────────────────────────────────────
def encode_stream_cursor(
    *, group_at: datetime, group_key: str, fingerprint: str, page_index: int
) -> str:
    """:{value}:{offset}:{is_prev} 外壳；value = base64url 锚载荷 {"a","g","f"}。

    offset 段为页序号（目标页 0 基索引，§6.2 示例流程口径）；is_prev 恒 0
    （按钮式「加载更早」仅向后翻页，§4.2.1 要点 5 显式豁免）。
    """
    payload = json.dumps(
        {"a": group_at.isoformat(), "g": group_key, "f": fingerprint},
        separators=(",", ":"), ensure_ascii=False,
    )
    value = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    return f"{value}:{page_index}:0"


def decode_stream_cursor(raw: str) -> tuple[datetime, str, str, int]:
    """解码并校验：损坏 / 锚字段缺失 / 组键非法 / offset 与锚不符 → 400。

    返回 ``(组时间, 组键, 过滤指纹, 页序号 0 基)``。
    """
    parts = raw.split(":")  # value 段为 base64url，字母表不含冒号
    if len(parts) != 3 or parts[2] != "0":
        raise AppException("VALIDATION_INVALID_CURSOR") from None
    value, offset_raw, _ = parts
    try:
        offset = int(offset_raw)
        if offset < 1:
            raise ValueError("offset 必须为正整数（首页不带游标）")
        padded = value + "=" * (-len(value) % 4)
        anchor = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        group_at = datetime.fromisoformat(anchor["a"])
        group_key = anchor["g"]
        fingerprint = anchor["f"]
        if group_at.tzinfo is None or not _GROUP_KEY_RE.fullmatch(group_key):
            raise ValueError("锚载荷非法")
        if not isinstance(fingerprint, str) or len(fingerprint) != 8:
            raise ValueError("指纹非法")
    except AppException:
        raise
    except Exception:                                            # noqa: BLE001
        logger.warning("activity_stream.cursor_decode_failed")
        raise AppException("VALIDATION_INVALID_CURSOR") from None
    return group_at, group_key, fingerprint, offset


# ─────────────────────────────────────────────────────────────────────
# 折叠装配（§4.3.2）
# ─────────────────────────────────────────────────────────────────────
def _epoch_int(epoch: float | None) -> float | int | None:
    """epoch 契约层展示：整数值毫秒时间戳直出为 int（§4.2.1 示例形态）。"""
    if epoch is None:
        return None
    return int(epoch) if float(epoch).is_integer() else float(epoch)


def _brief(row: dict[str, Any]) -> str | None:
    """单行变更摘要：有 old/new → 「标签 旧 → 新」；否则退 comment 文案。"""
    old, new = row.get("old_value"), row.get("new_value")
    if old is None and new is None:
        return row.get("text") or (field_label(row["field"]) if row.get("field") else None)
    label = field_label(row["field"]) if row.get("field") else ""
    return f"{label} {old or '空'} → {new or '空'}".strip()


def _batch_row(run: list[dict[str, Any]]) -> dict[str, Any]:
    """BR-04 折叠：batch_count = len(run)（组感知两步取数保证 run 为整组行）。"""
    first = run[0]
    verbs = {r["verb"] for r in run}
    verb = next(iter(verbs)) if len(verbs) == 1 else "updated"
    summary = f"{_VERB_SUMMARY.get(verb, '批量更新了')} {len(run)} 个任务"
    briefs = {b for b in (_brief(r) for r in run) if b}
    return {
        "kind": "batch",
        "epoch": _epoch_int(first["epoch"]),
        "actor_id": str(first["actor_id"]) if first["actor_id"] else None,
        "summary": summary,
        "change_brief": briefs.pop() if len(briefs) == 1 else ("多种变更" if briefs else None),
        "batch_count": len(run),
        "created_at": max(r["created_at"] for r in run),
    }


def _plain_row(r: dict[str, Any]) -> dict[str, Any]:
    if r["kind"] == "comment":
        return {
            "kind": "comment",
            "id": str(r["id"]),
            "actor_id": str(r["actor_id"]) if r["actor_id"] else None,
            "text": r["text"],
            "root_id": str(r["root_id"]) if r["root_id"] else None,
            "reply_to_actor_id": str(r["reply_to_id"]) if r["reply_to_id"] else None,
            "issue_id": str(r["issue_id"]) if r["issue_id"] else None,
            "created_at": r["created_at"],
        }
    # project 域行（lifecycle / file.* / 后续 github.*）：形态同 activity、
    # kind='project' 且无 issue 归属——file 事件语义由 field 前缀区分，
    # lifecycle 里程碑由 comment='milestone' 读取位标识（PROJ-003 §4.3.1）。
    kind = r["kind"]
    return {
        "kind": kind,
        "id": str(r["id"]),
        "epoch": _epoch_int(r["epoch"]),
        "actor_id": str(r["actor_id"]) if r["actor_id"] else None,
        "verb": r["verb"],
        "field": r["field"],
        "text": r["text"],
        "is_system": r["actor_id"] is None,  # BR-13：actor 为空才归系统行
        "issue_id": str(r["issue_id"]) if r["issue_id"] else None,
        "created_at": r["created_at"],
    }


def assemble_stream(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """BR-04 折叠装配：同 epoch 且跨 ≥2 任务的 Activity 折叠为 batch 行。

    归并按组键字典（组首现位置 = 视觉位），不依赖物理相邻——comment 行
    时间上穿插也不拆散 epoch 组；同任务同 epoch 不折叠（任务级一组语义）。
    折叠键 = ``group_key``（非裸 epoch）：epoch 为空的历史行为 ``a~<id>``
    单行组，不得与其它空 epoch 行误折。project 域行（kind='project'）同
    comment 直出原位——不参与任务批量折叠（无 issue 归属）。
    """
    runs: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if r["kind"] == "activity":
            runs.setdefault(r["group_key"], []).append(r)
    out: list[dict[str, Any]] = []
    emitted: set[str] = set()
    for r in rows:                                # 全局时间序遍历：comment/project 行原位直出
        if r["kind"] in ("comment", "project"):
            out.append(_plain_row(r))
        elif r["group_key"] not in emitted:
            emitted.add(r["group_key"])
            run = runs[r["group_key"]]
            if len(run) > 1 and len({x["issue_id"] for x in run}) > 1:
                out.append(_batch_row(run))
            else:
                out.extend(_plain_row(x) for x in run)
    return out


# ─────────────────────────────────────────────────────────────────────
# 流首页（§4.3.1 project_activity_stream）
# ─────────────────────────────────────────────────────────────────────
def fetch_stream_page(
    *,
    project_id: uuid.UUID,
    event: str | None,
    actor_id: uuid.UUID | None,
    cursor: str | None,
    per_page: int,
) -> dict[str, Any]:
    """组感知两步取数 + 折叠装配 + 折叠口径计数。

    返回 keys：rows（折叠后视觉行，引用未装配：actor_id/issue_id 仍为 ID）、
    has_next、next_cursor、page、total_count、total_groups、
    total_count_estimated、stream_cursor（仅首页携带，BR-12）。
    """
    per_page = min(max(per_page, 1), MAX_GROUPS_PER_PAGE)
    fingerprint = filter_fingerprint(
        event=event, actor_id=str(actor_id) if actor_id else None)

    page_index = 0                     # 0 基页序号（首页）
    anchor_at: datetime | None = None
    anchor_key: str | None = None
    if cursor:
        anchor_at, anchor_key, anchor_fingerprint, page_index = decode_stream_cursor(cursor)
        # 过滤指纹不符 → 旧游标失效（§4.2.1 要点 5：不作静默重解释）
        if anchor_fingerprint != fingerprint:
            raise AppException("VALIDATION_INVALID_CURSOR")

    ids = project_issue_ids(project_id)
    chunks = _chunk_issue_ids(ids) if ids else [[]]
    view_sql, base_params = _stream_view_sql(
        chunks, event=event, actor_id=actor_id, project_id=project_id)

    # 第一步：组边界（+1 探测 has_next）；游标页走组级 keyset（BR-10）
    boundary_params = {**base_params, "groups": per_page + 1}
    keyset = anchor_at is not None and anchor_key is not None
    if keyset:
        boundary_params["cursor_at"] = anchor_at
        boundary_params["cursor_key"] = anchor_key
    with connection.cursor() as cur:
        cur.execute(_boundary_sql(view_sql, keyset=keyset), boundary_params)
        boundaries = _dictfetchall(cur)
    has_next = len(boundaries) > per_page
    boundaries = boundaries[:per_page]
    keys = [b["group_key"] for b in boundaries]

    # 第二步：整组取回（组不跨页割裂；终键 -id：api-conventions §5.4 全序）
    rows: list[dict[str, Any]] = []
    if keys:
        with connection.cursor() as cur:
            cur.execute(_ROWS_SQL.format(view=view_sql), {**base_params, "keys": keys})
            rows = _dictfetchall(cur)

    # 折叠口径计数（要点 4）：一次 GROUP BY 折算视觉行数
    with connection.cursor() as cur:
        cur.execute(_TOTALS_SQL.format(view=view_sql), base_params)
        totals = _dictfetchall(cur)
    total_count = 0
    for t in totals:
        if t["kind"] == "activity" and t["row_count"] > 1 and t["issue_count"] > 1:
            total_count += 1          # 跨任务批量组计 1 行
        else:
            total_count += t["row_count"]
    total_groups = len(totals)
    # api-conventions §6.4：>50,000 降级估算标记。EXPLAIN 估算换算（免全量
    # GROUP BY 扫描）归 Phase 4 基准（BR-14）落地——当前先按精确值 + 标记口径。
    total_count_estimated = total_count > TOTAL_COUNT_ESTIMATE_THRESHOLD

    next_cursor = None
    if has_next and boundaries:
        last = boundaries[-1]
        next_cursor = encode_stream_cursor(
            group_at=last["group_at"], group_key=last["group_key"],
            fingerprint=fingerprint, page_index=page_index + 1)

    # BR-12 stream_cursor：首页最新一条 (created_at, id) 水位；首页首行为
    # batch 行时取其底层最新一条 Activity 的锚（rows[0] 即全局最新行）
    stream_cursor = None
    if cursor is None and rows:
        stream_cursor = f"{rows[0]['created_at'].isoformat()}:{rows[0]['id']}"

    return {
        "rows": assemble_stream(rows),
        "has_next": has_next,
        "next_cursor": next_cursor,
        "page": page_index + 1,
        "total_count": total_count,
        "total_groups": total_groups,
        "total_count_estimated": total_count_estimated,
        "stream_cursor": stream_cursor,
    }


# ─────────────────────────────────────────────────────────────────────
# 明细抽屉查询（§4.3.3）
# ─────────────────────────────────────────────────────────────────────
def parse_epoch(epoch_raw: str) -> float:
    """寻址型参数校验（§2.5）：非数值 → 400 VALIDATION_INVALID_PARAM。"""
    try:
        return float(epoch_raw)
    except (TypeError, ValueError):
        raise AppException(
            "VALIDATION_INVALID_PARAM",
            message="查询参数非法",
            details=[{"field": "epoch", "code": "NOT_A_NUMBER",
                      "message": "epoch 必须为数值（批量组毫秒时间戳）"}],
        ) from None


def batch_detail(*, project_id: uuid.UUID, epoch: float, limit: int = DETAIL_LIMIT) -> dict[str, Any]:
    """?epoch= 明细：轻量字段直查（不与 comments 合流，BR-05）。

    软删任务的动态同样保留（issue__project_id 传导不受 Issue 软删过滤影响，
    BR-06）；≤limit 截断 + truncated（BR-11 / §4.2.2 meta 豁免）。
    """
    limit = max(1, min(limit, DETAIL_LIMIT))
    base = IssueActivity.objects.filter(issue__project_id=project_id, epoch=epoch)
    total = base.count()
    raw = list(
        base.order_by("issue__sequence_id").values(
            "issue_id", "issue__sequence_id", "issue__name",
            "issue__project__identifier", "field", "old_value", "new_value",
        )[:limit]
    )
    data = [
        {
            "issue_id": str(r["issue_id"]),
            "issue_key": f"{r['issue__project__identifier']}-{r['issue__sequence_id']}",
            "name": r["issue__name"],
            "field": r["field"],
            "old_value": r["old_value"],
            "new_value": r["new_value"],
        }
        for r in raw
    ]
    return {
        "data": data,
        "count": len(data),
        "total_count": total,
        "truncated": total > limit,
        "limit": limit,
    }
