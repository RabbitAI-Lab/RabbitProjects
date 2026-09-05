"""工作项查询构建（TASK-003 §4.3.1 + BOARD-002 §4.3.1 单一 FilterSet 三处消费）。

职责：
1. IssueFilterSet.build_query —— 把 query params 转 Q 对象 + meta.applied 回显；
2. IssueFilterSet.apply_order —— priority 走语义权重（urgent>high>medium>low>none），
   其他白名单字段直接映射，非法 order_by 字段回退默认 + warning；
3. TASK-008：``?property.<id>=``（等值/逗号包含/null）与 ``order_by=±cf_<key>``
   类型感知排序（number→::numeric、currency→#>>{amount}、select→array_position
   选项配置序）—— 动态前缀匹配进 known 白名单，与 TASK-011 FilterCompiler 同源；
4. 工具：parse_uuid_list / escape_like。

约定：
- 跨参数恒为 AND；同参数多值恒为 OR（__in）；
- q 的多列 OR 自成括号组后与外部 AND（防 OR 短路）；
- 游标由分页器负责；本模块只造 Q。
"""

from __future__ import annotations

import uuid
from datetime import date

from django.db.models import Case, F, Q, QuerySet, Value, When
from django.db.models.expressions import RawSQL

from plane.utils.exceptions import AppValidationError, field_error

PRIORITY_CHOICES = ("none", "low", "medium", "high", "urgent")
PRIORITY_WEIGHT = {"urgent": 5, "high": 4, "medium": 3, "low": 2, "none": 1}
ORDER_BY_WHITELIST = (
    "created_at",
    "updated_at",
    "sequence_id",
    "priority",
    "target_date",
    "sort_order",
)
MAX_VALUES_PER_PARAM = 20
MAX_Q_LENGTH = 64
NULLS_LAST_FIELDS = {"target_date", "sort_order"}
PROPERTY_PARAM_PREFIX = "property."


def _find_definition(project, *, property_id=None, field_key=None):
    """按 property_id 或 field_key 在项目作用域（全局 ∪ 本项目，启用中）查字段定义。"""
    from django.db.models import Q as _Q

    from plane.db.models import CustomFieldDefinition

    qs = CustomFieldDefinition.objects.filter(
        workspace_id=project.workspace_id,
        is_active=True,
        deleted_at__isnull=True,
    ).filter(_Q(project__isnull=True) | _Q(project_id=project.id))
    if property_id is not None:
        qs = qs.filter(id=property_id)
    if field_key is not None:
        qs = qs.filter(field_key=field_key)
    # 项目私有覆盖同 key 全局（resolve_fields 同口径）
    merged = {}
    for d in qs:
        merged.setdefault(d.field_key, d)
        if d.project_id is not None:
            merged[d.field_key] = d
    if property_id is not None:
        return next((d for d in merged.values() if d.id == property_id), None)
    return merged.get(field_key or "")


def _coerce_filter_value(definition, raw: str):
    """?property. 值的轻量类型转换（URL 一律是字符串；number/checkbox 需还原原生类型）。"""
    if definition.field_type in ("number", "auto_increment"):
        try:
            return float(raw)
        except ValueError as err:
            raise AppValidationError([
                field_error(f"property.{definition.id}", "INVALID", f"{definition.field_key} 需要数字值：{raw}")
            ]) from err
    if definition.field_type == "checkbox":
        return raw.lower() in ("true", "1")
    return raw


def property_filter_q(definition, raw: str) -> Q:
    """?property.<id>= 语法 → GIN 友好 Q 对象（TASK-008 §4.3.6）。

    等值/IN 全部编译为 @> 的 OR 展开（每分支均可走 GIN）；「为空」= NOT has_key——
    本端点恒有 project 作用域（idx_issue_active_by_project 先行缩小），不存在
    跨项目 NOT 键存在全表扫描（IT-04 的强制条件由端点结构保证）。
    """
    from plane.db.models import CustomFieldDefinition

    if raw == "null":
        return ~Q(custom_fields__has_key=definition.field_key)
    values = [v for v in raw.split(",") if v != ""]
    if not values:
        return Q()
    if len(values) > MAX_VALUES_PER_PARAM:
        raise AppValidationError([
            field_error(f"property.{definition.id}", "TOO_LARGE", f"单参数最多 {MAX_VALUES_PER_PARAM} 个值")
        ])
    if definition.field_type in CustomFieldDefinition.MULTI_VALUE_TYPES:
        q = Q()
        for v in values:
            q |= Q(**{f"custom_fields__{definition.field_key}__contains": [v]})
        return q
    q = Q()
    for v in values:
        q |= Q(**{f"custom_fields__{definition.field_key}": _coerce_filter_value(definition, v)})
    return q


class IssueFilterSet:
    """单一 FilterSet —— 列表 / 看板 / P2 组合筛选器共用。

    看板视图裁剪掉 ``state_id``（列即状态分组，BOARD-002 §2.2）；
    本类不做裁剪，由调用方在 view 层决定哪些 key 参与 build_query。
    """

    def __init__(self, request, *, drop_keys: tuple[str, ...] = (), project=None):
        self.request = request
        self.applied: dict = {}
        self.drop_keys = set(drop_keys)
        self.project = project  # TASK-008：?property./order_by=cf_ 需要 project 上下文
        self._ignored_params: list[str] = []
        self.warnings: list[str] = []

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

        # ---- assignee_ids（'me' 占位展开 + 'null' 未指派糖值，TASK-007 §4.2.5）----
        if "assignee_ids" not in self.drop_keys:
            # 同时认 ?assignee_ids= 和 ?assignee_id=（BOARD-002 单数历史参数；TASK-003 用复数）
            raw = params.get("assignee_ids") or params.get("assignee_id")
            assignees, has_null = self._parse_assignee_values(raw)
            if has_null and assignees:
                # me,null 组合禁止（§4.2.5）：null 无意义 → 丢弃并 warning（沿用 BR-06 通道）
                self.warnings.append("assignee_ids=null 已忽略（与具体执行人同时给出时无意义）")
                has_null = False
            if has_null:
                # null 糖值编译为 is_empty 逻辑算子：中间表物理删除（被移出即行消失），
                # 不存在任何 IssueAssignee 行 = 未指派；与 BR-06 清空中间态构成「待分派池」
                q &= Q(assignees__id__isnull=True)
                self.applied["assignee_ids"] = ["null"]
            elif assignees:
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
                        raise AppValidationError(
                            [
                                field_error(
                                    "priority", "NOT_A_CHOICE", f"priority 取值非法：{','.join(sorted(invalid))}"
                                ),
                            ]
                        )
                    if len(parts) > MAX_VALUES_PER_PARAM:
                        raise AppValidationError(
                            [
                                field_error("priority", "TOO_LARGE", f"单参数最多 {MAX_VALUES_PER_PARAM} 个值"),
                            ]
                        )
                    q &= Q(priority__in=parts)
                    self.applied["priority"] = parts

        # ---- target_date 区间 ----
        if "target_date" not in self.drop_keys:
            if (raw_date := params.get("target_date")) is not None:
                parsed = self._parse_target_date(raw_date)
                if parsed is not None:
                    q &= parsed
                    self.applied["target_date"] = raw_date

        # ---- blocked（TASK-005 §4.2.5：只看被未完成前置阻塞的任务）----
        if "blocked" not in self.drop_keys:
            raw_blocked = params.get("blocked")
            if raw_blocked is not None and str(raw_blocked).lower() in ("true", "1"):
                from django.db.models import Exists, OuterRef

                from plane.db.models import IssueLink
                blocked_exists = IssueLink.objects.filter(
                    issue_id=OuterRef("pk"),
                    relation_type="is_blocked_by",
                    deleted_at__isnull=True,
                    related_issue__deleted_at__isnull=True,
                ).exclude(related_issue__state__group__in=["completed", "cancelled"])
                q &= Q(Exists(blocked_exists))  # Exists 是 Combinable，包 Q 统一类型
                self.applied["blocked"] = True

        # ---- parent_id（TASK-004 §4.2.3：列表页树形行级懒加载入口）----
        if "parent_id" not in self.drop_keys:
            if (raw_parent := params.get("parent_id")) is not None:
                parent_id = self._parse_uuid_list(raw_parent, field_name="parent_id")
                if parent_id:
                    q &= Q(parent_id__in=parent_id)
                    self.applied["parent_id"] = [str(v) for v in parent_id]

        # ---- 自定义字段筛选（TASK-008 §4.2.5：?property.<property_id>= 等值/逗号包含/null）----
        for key, raw_value in params.items():
            if not key.startswith(PROPERTY_PARAM_PREFIX):
                continue
            raw_id = key[len(PROPERTY_PARAM_PREFIX):]
            if self.project is None:
                # 无项目上下文（不应发生——列表端点恒项目内）：按未知参数丢弃并提示
                self.warnings.append(f"{key} 需要项目上下文，已忽略")
                continue
            try:
                property_id = uuid.UUID(raw_id)
            except ValueError as err:
                raise AppValidationError([
                    field_error(key, "INVALID_UUID", f"property id 格式非法：{raw_id}")
                ]) from err
            definition = _find_definition(self.project, property_id=property_id)
            if definition is None:
                raise AppValidationError([
                    field_error(key, "DOES_NOT_EXIST", "自定义字段不存在或已停用")
                ])
            q &= property_filter_q(definition, str(raw_value or ""))
            self.applied[key] = str(raw_value)

        # ---- 关键词搜索 q ----
        if "q" not in self.drop_keys:
            if (raw_q := params.get("q")) is not None:
                match = self._build_text_match(raw_q)
                if match is not None:
                    q &= match
                    self.applied["q"] = raw_q

        # ---- 记录忽略的未知参数（不回显在 applied 中）----
        known = {
            "q",
            "state_id",
            "type_id",
            "priority",
            "label_id",
            "label_ids",
            "assignee_ids",
            "assignee_id",
            "created_by",
            "target_date",
            "parent_id",
            "order_by",
            "cursor",
            "per_page",
            "group_by",
            "group_id",
            "group_per_page",
        }
        for key in params:
            if key in known or key.startswith(PROPERTY_PARAM_PREFIX):
                continue
            self._ignored_params.append(key)

        return q

    # -----------------------------------------------------------------
    # 排序（BR-05/06/07 + TASK-008 §4.3.5 类型感知 cf 排序）
    # -----------------------------------------------------------------
    def apply_order(self, qs: QuerySet, raw_order: str | None) -> tuple[QuerySet, str | None]:
        warning = None
        desc = bool(raw_order and raw_order.startswith("-"))
        field = (raw_order or "").lstrip("-")

        # ---- order_by=±cf_<key>：类型感知表达式（number ::numeric / select 配置序）----
        if field.startswith("cf_"):
            qs, warning = self._apply_custom_order(qs, field, desc)
            self.applied["order_by"] = ("-" if desc else "") + field
            return qs, warning

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
                    F(field).asc(nulls_last=True) if not desc else F(field).desc(nulls_last=True),
                    "-id",
                )
            else:
                qs = qs.order_by(prefix + field, "-id")
        # 回显最终生效排序（即使回退也用真值）
        self.applied["order_by"] = ("-" if desc else "") + field
        return qs, warning

    def _apply_custom_order(self, qs: QuerySet, field: str, desc: bool) -> tuple[QuerySet, str | None]:
        """cf_<key> 类型感知排序：数字 ::numeric（9<10<100）、currency 嵌套路径、
        select array_position 选项配置序；NULLS LAST + 游标稳定键（-created_at,-id）。"""
        from django.db.models import IntegerField

        from plane.db.models import CustomFieldDefinition
        from plane.db.services.custom_fields import custom_order_expression

        warning = None
        definition = _find_definition(self.project, field_key=field) if self.project else None
        if definition is None:
            warning = f"order_by={field} 未定义或已停用，已回退 -created_at"
            return qs.order_by("-created_at", "-id"), warning
        if definition.field_type in CustomFieldDefinition.MULTI_VALUE_TYPES:
            warning = f"order_by={field} 为多值类型不可排序，已回退 -created_at"
            return qs.order_by("-created_at", "-id"), warning
        sql, params = custom_order_expression(definition)
        expr = RawSQL(sql, params, output_field=IntegerField())
        ordered = expr.desc(nulls_last=True) if desc else expr.asc(nulls_last=True)
        return qs.order_by(ordered, "-created_at", "-id"), warning

    # -----------------------------------------------------------------
    # 内部工具
    # -----------------------------------------------------------------
    def _parse_assignee_values(self, raw: str | None) -> tuple[list[uuid.UUID], bool]:
        """assignee_ids 专用解析：``me`` 占位（TASK-001 §4.2.2 前例）+ ``null``
        未指派糖值（TASK-007 §4.2.5）。

        返回 ``(ids, has_null)``；两者同真表示 ``me,null`` 组合，由调用方丢弃
        null 并出 ``meta.warning``。``__isnull`` 直写不在白名单（两写法并存会
        破坏「URL → 结果集」纯函数），落入 ignored_params 通道。
        """
        if not raw:
            return [], False
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        if len(parts) > MAX_VALUES_PER_PARAM:
            raise AppValidationError(
                [
                    field_error("assignee_ids", "TOO_LARGE", f"单参数最多 {MAX_VALUES_PER_PARAM} 个值"),
                ]
            )
        ids: list[uuid.UUID] = []
        has_null = False
        for p in parts:
            if p.lower() == "null":
                has_null = True
                continue
            if p == "me":
                if not self.request.user or not self.request.user.is_authenticated:
                    raise AppValidationError(
                        [
                            field_error("assignee_ids", "INVALID", "me 仅对登录用户有效"),
                        ]
                    )
                ids.append(self.request.user.id)
                continue
            try:
                ids.append(uuid.UUID(p))
            except ValueError as err:
                raise AppValidationError(
                    [
                        field_error("assignee_ids", "INVALID_UUID", f"UUID 格式非法：{p}"),
                    ]
                ) from err
        return ids, has_null

    def merge_warnings(self, order_warning: str | None) -> str | None:
        """把 order_by 回退 warning 与 FilterSet 自身 warnings 合并成 meta.warning 文案。"""
        parts = ([order_warning] if order_warning else []) + list(self.warnings)
        return "; ".join(parts) if parts else None

    def _parse_uuid_list(self, raw: str | None, *, field_name: str, alias_me: bool = False) -> list[uuid.UUID]:
        if not raw:
            return []
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        if len(parts) > MAX_VALUES_PER_PARAM:
            raise AppValidationError(
                [
                    field_error(field_name, "TOO_LARGE", f"单参数最多 {MAX_VALUES_PER_PARAM} 个值"),
                ]
            )
        result: list[uuid.UUID] = []
        for p in parts:
            if alias_me and p == "me":
                if not self.request.user or not self.request.user.is_authenticated:
                    # 业务层在认证后才有意义；抛 INVALID 让前端知道
                    raise AppValidationError(
                        [
                            field_error(field_name, "INVALID", "me 仅对登录用户有效"),
                        ]
                    )
                result.append(self.request.user.id)
                continue
            try:
                result.append(uuid.UUID(p))
            except ValueError as err:
                raise AppValidationError(
                    [
                        field_error(field_name, "INVALID_UUID", f"UUID 格式非法：{p}"),
                    ]
                ) from err
        return result

    @staticmethod
    def _parse_target_date(raw: str) -> Q | None:
        value, _, modifier = raw.partition(";")
        modifier = modifier or "on"
        try:
            day = date.fromisoformat(value.strip())
        except ValueError as err:
            raise AppValidationError(
                [
                    field_error("target_date", "INVALID_DATE", "格式应为 YYYY-MM-DD;before|after|on"),
                ]
            ) from err
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
            raise AppValidationError(
                [
                    field_error("q", "TOO_LONG", f"关键词最长 {MAX_Q_LENGTH} 字符"),
                ]
            )

        # 序列号匹配（'128' 或 'RBT-128'）
        seq_match = None
        if keyword.isdigit():
            seq_match = Q(sequence_id=int(keyword))
        elif "-" in keyword and keyword.split("-", 1)[1].isdigit():
            seq_match = Q(sequence_id=int(keyword.split("-", 1)[1]))

        esc = self._escape_like(keyword)
        if len(esc) >= 3:
            text_match = Q(name__icontains=esc) | Q(description_stripped__icontains=esc)
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
