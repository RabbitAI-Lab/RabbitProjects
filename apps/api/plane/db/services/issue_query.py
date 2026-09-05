"""工作项查询构建（TASK-003 §4.3.1 + BOARD-002 §4.3.1 单一 FilterSet 三处消费）。

职责：
1. IssueFilterSet.build_query —— 把 query params 转 Q 对象 + meta.applied 回显；
2. IssueFilterSet.apply_order —— priority 走语义权重（urgent>high>medium>low>none），
   其他白名单字段直接映射，非法 order_by 字段回退默认 + warning；
3. 工具：parse_uuid_list / escape_like。

约定：
- 跨参数恒为 AND；同参数多值恒为 OR（__in）；
- q 的多列 OR 自成括号组后与外部 AND（防 OR 短路）；
- 游标由分页器负责；本模块只造 Q。
"""
from __future__ import annotations

import uuid
from datetime import date

from django.db.models import Case, F, Q, QuerySet, Value, When

from plane.utils.exceptions import AppValidationError, field_error

PRIORITY_CHOICES = ("none", "low", "medium", "high", "urgent")
PRIORITY_WEIGHT = {"urgent": 5, "high": 4, "medium": 3, "low": 2, "none": 1}
ORDER_BY_WHITELIST = (
    "created_at", "updated_at", "sequence_id",
    "priority", "target_date", "sort_order",
)
MAX_VALUES_PER_PARAM = 20
MAX_Q_LENGTH = 64
NULLS_LAST_FIELDS = {"target_date", "sort_order"}


class IssueFilterSet:
    """单一 FilterSet —— 列表 / 看板 / P2 组合筛选器共用。

    看板视图裁剪掉 ``state_id``（列即状态分组，BOARD-002 §2.2）；
    本类不做裁剪，由调用方在 view 层决定哪些 key 参与 build_query。
    """

    def __init__(self, request, *, drop_keys: tuple[str, ...] = ()):
        self.request = request
        self.applied: dict = {}
        self.drop_keys = set(drop_keys)
        self._ignored_params: list[str] = []

    # -----------------------------------------------------------------
    # 公共入口
    # -----------------------------------------------------------------
    def build_query(self, params: dict) -> Q:
        q = Q()
        # ---- UUID 列表类 ----
        uuid_list_keys = (
            ("state_id", "state_id__in"),
            ("type_id", "issue_type_id__in"),
            ("created_by", "created_by_id__in"),
        )
        for key, lookup in uuid_list_keys:
            if key in self.drop_keys:
                continue
            values = self._parse_uuid_list(params.get(key), field_name=key)
            if values:
                q &= Q(**{lookup: values})
                self.applied[key] = [str(v) for v in values]

        # ---- assignee_ids（支持 'me' 占位展开）----
        if "assignee_ids" not in self.drop_keys:
            # 同时认 ?assignee_ids= 和 ?assignee_id=（BOARD-002 单数历史参数；TASK-003 用复数）
            raw = params.get("assignee_ids") or params.get("assignee_id")
            assignees = self._parse_uuid_list(
                raw, field_name="assignee_ids", alias_me=True
            )
            if assignees:
                q &= Q(assignees__id__in=assignees)
                self.applied["assignee_ids"] = [str(v) for v in assignees]

        # ---- label_id / label_ids（M2M 反查；认两种 key）----
        if "label_id" not in self.drop_keys and "label_ids" not in self.drop_keys:
            raw = params.get("label_id") or params.get("label_ids")
            labels = self._parse_uuid_list(raw, field_name="label_id")
            if labels:
                q &= Q(labels__id__in=labels)
                self.applied["label_id"] = [str(v) for v in labels]

        # ---- priority 枚举 ----
        if "priority" not in self.drop_keys:
            if (raw_priority := params.get("priority")) is not None:
                parts = [p.strip() for p in raw_priority.split(",") if p.strip()]
                if not parts:
                    pass
                else:
                    invalid = set(parts) - set(PRIORITY_CHOICES)
                    if invalid:
                        raise AppValidationError([
                            field_error("priority", "NOT_A_CHOICE",
                                        f"priority 取值非法：{','.join(sorted(invalid))}"),
                        ])
                    if len(parts) > MAX_VALUES_PER_PARAM:
                        raise AppValidationError([
                            field_error("priority", "TOO_LARGE",
                                        f"单参数最多 {MAX_VALUES_PER_PARAM} 个值"),
                        ])
                    q &= Q(priority__in=parts)
                    self.applied["priority"] = parts

        # ---- target_date 区间 ----
        if "target_date" not in self.drop_keys:
            if (raw_date := params.get("target_date")) is not None:
                parsed = self._parse_target_date(raw_date)
                if parsed is not None:
                    q &= parsed
                    self.applied["target_date"] = raw_date

        # ---- 关键词搜索 q ----
        if "q" not in self.drop_keys:
            if (raw_q := params.get("q")) is not None:
                match = self._build_text_match(raw_q)
                if match is not None:
                    q &= match
                    self.applied["q"] = raw_q

        # ---- 记录忽略的未知参数（不回显在 applied 中）----
        known = {
            "q", "state_id", "type_id", "priority", "label_id", "label_ids",
            "assignee_ids", "assignee_id", "created_by", "target_date",
            "order_by", "cursor", "per_page",
            "group_by", "group_id", "group_per_page",
        }
        for key in params:
            if key in known:
                continue
            self._ignored_params.append(key)

        return q

    # -----------------------------------------------------------------
    # 排序（BR-05/06/07）
    # -----------------------------------------------------------------
    def apply_order(self, qs: QuerySet, raw_order: str | None) -> tuple[QuerySet, str | None]:
        warning = None
        desc = bool(raw_order and raw_order.startswith("-"))
        field = (raw_order or "").lstrip("-")

        if field not in ORDER_BY_WHITELIST:
            if raw_order:
                warning = f"order_by={raw_order} 不在白名单，已回退 -created_at"
            field, desc = "created_at", True

        if field == "priority":
            weight = Case(*[When(priority=k, then=Value(v)) for k, v in PRIORITY_WEIGHT.items()])
            qs = qs.annotate(_prio=weight).order_by(("-" if desc else "") + "_prio", "-id")
        else:
            prefix = "-" if desc else ""
            if field in NULLS_LAST_FIELDS:
                qs = qs.order_by(
                    F(field).asc(nulls_last=True) if not desc
                    else F(field).desc(nulls_last=True),
                    "-id",
                )
            else:
                qs = qs.order_by(prefix + field, "-id")
        # 回显最终生效排序（即使回退也用真值）
        self.applied["order_by"] = ("-" if desc else "") + field
        return qs, warning

    # -----------------------------------------------------------------
    # 内部工具
    # -----------------------------------------------------------------
    def _parse_uuid_list(
        self, raw: str | None, *, field_name: str, alias_me: bool = False
    ) -> list[uuid.UUID]:
        if not raw:
            return []
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        if len(parts) > MAX_VALUES_PER_PARAM:
            raise AppValidationError([
                field_error(field_name, "TOO_LARGE", f"单参数最多 {MAX_VALUES_PER_PARAM} 个值"),
            ])
        result: list[uuid.UUID] = []
        for p in parts:
            if alias_me and p == "me":
                if not self.request.user or not self.request.user.is_authenticated:
                    # 业务层在认证后才有意义；抛 INVALID 让前端知道
                    raise AppValidationError([
                        field_error(field_name, "INVALID", "me 仅对登录用户有效"),
                    ])
                result.append(self.request.user.id)
                continue
            try:
                result.append(uuid.UUID(p))
            except ValueError as err:
                raise AppValidationError([
                    field_error(field_name, "INVALID_UUID", f"UUID 格式非法：{p}"),
                ]) from err
        return result

    @staticmethod
    def _parse_target_date(raw: str) -> Q | None:
        value, _, modifier = raw.partition(";")
        modifier = modifier or "on"
        try:
            day = date.fromisoformat(value.strip())
        except ValueError as err:
            raise AppValidationError([
                field_error("target_date", "INVALID_DATE",
                            "格式应为 YYYY-MM-DD;before|after|on"),
            ]) from err
        return {
            "before": Q(target_date__lt=day),
            "after": Q(target_date__gt=day),
            "on": Q(target_date=day),
        }.get(modifier)

    def _build_text_match(self, raw_q: str) -> Q | None:
        keyword = (raw_q or "").strip()
        if not keyword:
            return None
        if len(keyword) > MAX_Q_LENGTH:
            raise AppValidationError([
                field_error("q", "TOO_LONG", f"关键词最长 {MAX_Q_LENGTH} 字符"),
            ])

        # 序列号匹配（'128' 或 'RBT-128'）
        seq_match = None
        if keyword.isdigit():
            seq_match = Q(sequence_id=int(keyword))
        elif "-" in keyword and keyword.split("-", 1)[1].isdigit():
            seq_match = Q(sequence_id=int(keyword.split("-", 1)[1]))

        esc = self._escape_like(keyword)
        if len(esc) >= 3:
            text_match = (
                Q(name__icontains=esc) | Q(description_stripped__icontains=esc)
            )
        else:
            # 短词仅标题前缀（trigram 对 <3 字符无效）
            text_match = Q(name__istartswith=esc)
        return text_match | seq_match if seq_match is not None else text_match

    @staticmethod
    def _escape_like(s: str) -> str:
        return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    @property
    def ignored_params(self) -> list[str]:
        return list(self._ignored_params)
