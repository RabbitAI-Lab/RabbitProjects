"""自定义字段定义模型（TASK-008 §4.1.1，架构 dynamic-fields-design.md §3.1 完整落地）。

字段定义元数据 —— 自定义字段的唯一真相来源：
- 作用域：project 为 NULL = Workspace 全局字段；否则项目私有字段；
- applicable_types 为空列表 = 对全部任务类型生效；
- P3/P4 扩展列（permission_config / cascade_config / formula）本迭代建列不启用
  （零 DDL 升级预留，架构 §7.2「先建列后启用」）。
"""
from __future__ import annotations

import re

from django.contrib.postgres.indexes import GinIndex
from django.core.exceptions import ValidationError
from django.db import models

from plane.db.models.base import BaseModel

FIELD_KEY_RE = re.compile(r"cf_[a-z][a-z0-9_]{1,60}")


class CustomFieldDefinition(BaseModel):
    class FieldType(models.TextChoices):
        TEXT = "text", "单行文本"
        TEXTAREA = "textarea", "多行文本"
        NUMBER = "number", "数字"
        SELECT = "select", "单选下拉"
        MULTI_SELECT = "multi_select", "多选下拉"
        DATE = "date", "日期"
        MEMBER = "member", "人员单选"
        MEMBER_MULTI = "member_multi", "人员多选"
        CHECKBOX = "checkbox", "复选框"
        URL = "url", "URL 链接"
        CURRENCY = "currency", "金额"
        AUTO_INCREMENT = "auto_increment", "自增编号"
        # ---- P3 企业版高级类型（建表即定义枚举，管理入口与校验器不开放）----
        CASCADE = "cascade", "级联下拉"
        RELATION = "relation", "关联工作项"
        DATE_RANGE = "date_range", "日期区间"
        ATTACHMENT = "attachment", "附件"
        # ---- P4 ----
        FORMULA = "formula", "公式计算"

    #: 多值类型集合——值在 JSONB 中以数组存储，筛选走 @> 包含语义（不可排序）
    MULTI_VALUE_TYPES = frozenset({FieldType.MULTI_SELECT, FieldType.MEMBER_MULTI, FieldType.ATTACHMENT})
    #: 需要 options 配置的类型
    OPTION_REQUIRED_TYPES = frozenset({FieldType.SELECT, FieldType.MULTI_SELECT, FieldType.CASCADE})
    #: P2 管理入口白名单（TASK-008 §1.2：12 种基础类型；P3/P4 枚举仅占位）
    P2_ALLOWED_TYPES = frozenset(list(FieldType.values)[:12])

    workspace = models.ForeignKey(
        "db.Workspace",
        on_delete=models.CASCADE,
        related_name="custom_field_definitions",
        verbose_name="所属工作空间",
    )
    project = models.ForeignKey(
        "db.Project",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="custom_field_definitions",
        verbose_name="所属项目",
        help_text="为空表示 Workspace 全局字段，对所有项目生效",
    )
    name = models.CharField(max_length=128, verbose_name="字段显示名", help_text="可随时修改，不影响已存数据")
    field_key = models.CharField(
        max_length=64,
        verbose_name="字段键名",
        help_text="JSONB 中的键名，cf_ 前缀 snake_case，创建后不可修改（BR-01）",
    )
    field_type = models.CharField(
        max_length=24,
        choices=FieldType.choices,
        verbose_name="字段类型",
        help_text="决定表单控件、筛选控件与值的 JSON 类型，创建后不可修改（BR-06）",
    )
    description = models.TextField(blank=True, verbose_name="字段帮助说明", help_text="表单中以问号提示展示")

    is_required = models.BooleanField(default=False, verbose_name="是否必填")
    default_value = models.JSONField(
        null=True,
        blank=True,
        verbose_name="默认值",
        help_text="新建工作项时预填，类型须与 field_type 匹配（BR-05）",
    )
    options = models.JSONField(
        default=list,
        blank=True,
        verbose_name="选项配置",
        help_text='[{"label":"高","value":"high","color":"#EF4444","sort_order":1}]；'
                  "option value 创建后不可改（BR-04）",
    )

    sort_order = models.FloatField(default=65535.0, verbose_name="显示排序", help_text="浮点插值，支持拖拽排序")
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        verbose_name="是否启用",
        help_text="停用后表单与筛选器不再展示，历史数据保留（BR-12）",
    )
    applicable_types = models.JSONField(
        default=list,
        blank=True,
        verbose_name="适用任务类型",
        help_text="IssueType UUID 字符串列表，空列表表示适用于全部类型",
    )

    # ---- 查询优化标记 ----
    is_indexed = models.BooleanField(
        default=False,
        verbose_name="建立专用表达式索引",
        help_text="标记后由异步任务 CONCURRENTLY 创建表达式偏索引（每 WS ≤10 个，BR-10）",
    )
    is_searchable = models.BooleanField(default=False, verbose_name="纳入全文搜索", help_text="P2 末视性能评估启用")

    # ---- P3 / P4 企业版扩展（列先建好，功能后开启，避免大表 DDL）----
    permission_config = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="字段级权限配置",
        help_text='P3：{"read":["role:member"],"write":["role:admin"],"required_for":["role:member"]}',
    )
    cascade_config = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="级联/联动配置",
        help_text='P3：{"parent_field_key":"cf_product_line","visible_when":{"cf_type":["bug"]}}',
    )
    formula = models.TextField(blank=True, verbose_name="公式表达式", help_text="P4：如 {cf_price} * {cf_quantity}")

    class Meta(BaseModel.Meta):
        db_table = "custom_field_definitions"
        verbose_name = "自定义字段定义"
        verbose_name_plural = verbose_name
        ordering = ("sort_order", "created_at")  # type: ignore[assignment]  # stub 声明过窄（同 IssueType）
        constraints = [
            # BR-02：同一 workspace 的全局字段 key 唯一（偏条件排除软删）
            models.UniqueConstraint(
                fields=["workspace", "field_key"],
                condition=models.Q(project__isnull=True, deleted_at__isnull=True),
                name="uniq_global_field_key_per_workspace",
            ),
            # BR-02：同一项目的私有字段 key 唯一
            models.UniqueConstraint(
                fields=["project", "field_key"],
                condition=models.Q(project__isnull=False, deleted_at__isnull=True),
                name="uniq_project_field_key",
            ),
        ]
        indexes = [
            models.Index(fields=["workspace", "is_active", "sort_order"], name="idx_cfd_ws_active"),
            models.Index(fields=["project", "is_active", "sort_order"], name="idx_cfd_proj_active"),
            GinIndex(fields=["applicable_types"], name="idx_cfd_applicable_types"),
        ]

    def clean(self) -> None:
        """配置合法性校验（BR-01/03/05）—— Serializer 与 Admin 双端口径。

        注：P2 不启用 formula 公式校验（P4 字段类型不在管理入口开放，
        见 TASK-008 用户裁决反向统一到架构文档 §3.1）。
        """
        if not FIELD_KEY_RE.fullmatch(self.field_key or ""):
            raise ValidationError({"field_key": "字段键名必须为 cf_ 前缀的 snake_case（小写字母/数字/下划线）"})
        if self.field_type in self.OPTION_REQUIRED_TYPES and not self.options:
            raise ValidationError({"options": "该类型必须配置至少一个选项（BR-03）"})
        if self.options:
            values = [o.get("value") for o in self.options]
            values_ok = len(values) == len(set(values)) and all(values)
            labels_ok = all(o.get("label") for o in self.options)
            if not (values_ok and labels_ok):
                raise ValidationError({"options": "选项 value 唯一且 label/value 必填（BR-03）"})
        if self.default_value is not None:
            # 函数级导入避免 models ↔ services 循环；default_value 复用值校验器（BR-05）
            from plane.db.services.custom_fields import validate_field_value

            validate_field_value(self, self.default_value)

    def save(self, *args, **kwargs):
        # BR-01 / BR-06：field_key 与 field_type 创建后不可变。
        # 注意 pk 带 default（uuid4），未落库实例 pk 已有值——须用 _state.adding 判定新建。
        if not self._state.adding:
            orig_key, orig_type = (
                type(self).all_objects.values_list("field_key", "field_type").get(pk=self.pk)
            )
            if (orig_key, orig_type) != (self.field_key, self.field_type):
                raise ValidationError("字段键名与类型创建后不可修改（BR-01/BR-06）")
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.field_key}({self.field_type})"
