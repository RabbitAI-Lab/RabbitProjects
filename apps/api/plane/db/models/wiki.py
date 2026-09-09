"""Wiki 知识库模型（FILE-005 §4.2，Sprint-9）。

WikiSpace 权限载体（inherit/whitelist 两态）+ WikiPage 页面树（深度 ≤5，
三格式内容经版本表承载——页表只挂草稿与版本指针）+ WikiPageVersion
追加式台账（BR-08 只增；content_html/text 服务端派生 BR-03）。
"""
from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.db.models import Q

from plane.db.models.base import BaseModel


class WikiSpace(BaseModel):
    """Wiki 空间 —— 权限载体（BR-06/07）"""

    class PermissionMode(models.TextChoices):
        INHERIT = "inherit", "继承项目角色"
        WHITELIST = "whitelist", "编辑白名单"

    project = models.ForeignKey("db.Project", on_delete=models.CASCADE,
                                related_name="wiki_spaces", verbose_name="所属项目")
    name = models.CharField(max_length=128, verbose_name="空间名称")
    description = models.TextField(blank=True, verbose_name="说明")
    permission_mode = models.CharField(max_length=16, choices=PermissionMode.choices,
                                       default=PermissionMode.INHERIT, verbose_name="权限模式")
    editor_whitelist = models.JSONField(default=list, blank=True, verbose_name="编辑白名单",
        help_text='[{"type": "member", "id": "<uuid>"}, {"type": "department", "id": "<uuid>"}]')

    class Meta(BaseModel.Meta):
        db_table = "wiki_spaces"
        constraints = [models.UniqueConstraint(fields=["project", "name"],
                                               condition=Q(deleted_at__isnull=True),
                                               name="uniq_wiki_space_name")]

    def __str__(self) -> str:
        return self.name


class WikiPage(BaseModel):
    MAX_DEPTH = 5

    space = models.ForeignKey(WikiSpace, on_delete=models.CASCADE,
                              related_name="pages", verbose_name="所属空间")
    parent = models.ForeignKey("self", on_delete=models.CASCADE, null=True, blank=True,
                               related_name="children", verbose_name="父页面")
    title = models.CharField(max_length=200, verbose_name="标题")
    depth = models.PositiveSmallIntegerField(default=1, verbose_name="层级（冗余）")
    sort_order = models.FloatField(default=65535.0, verbose_name="排序值")
    draft_json = models.JSONField(null=True, blank=True, verbose_name="草稿（覆盖式，BR-04）")
    base_version = models.ForeignKey("db.WikiPageVersion", on_delete=models.SET_NULL,
                                     null=True, blank=True, related_name="+",
                                     verbose_name="草稿基线版本")
    current_version = models.ForeignKey("db.WikiPageVersion", on_delete=models.SET_NULL,
                                        null=True, blank=True, related_name="+",
                                        verbose_name="当前发布版本")
    deleted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name="+",
                                   verbose_name="删除人（回收站过滤，BR-09：非 manager 仅见本人删除项）")
    collab_doc_id = models.UUIDField(null=True, blank=True, verbose_name="P4 协同文档 ID 预留")

    class Meta(BaseModel.Meta):
        db_table = "wiki_pages"
        constraints = [
            models.UniqueConstraint(fields=["space", "parent", "title"],
                                    condition=Q(deleted_at__isnull=True),
                                    name="uniq_wiki_page_title_per_parent"),   # BR-02
            models.CheckConstraint(condition=Q(depth__gte=1, depth__lte=5),
                                   name="chk_wiki_page_depth"),
        ]
        indexes = [models.Index(fields=["space", "parent"], name="idx_wiki_page_tree")]

    def __str__(self) -> str:
        return f"{self.title}(d{self.depth})"


class WikiPageVersion(BaseModel):
    """追加式版本台账（BR-08 只增）——FILE-003 版本范式在文档域的同构"""

    page = models.ForeignKey(WikiPage, on_delete=models.CASCADE,
                             related_name="versions", verbose_name="页面")
    version_no = models.PositiveIntegerField(verbose_name="版本号（页内递增）")
    content_json = models.JSONField(verbose_name="Tiptap JSON（编辑源）")
    content_html = models.TextField(verbose_name="渲染态（服务端派生，BR-03）")
    content_text = models.TextField(verbose_name="剥离文本（检索态）")
    change_summary = models.CharField(max_length=200, blank=True, verbose_name="变更摘要")
    rolled_back_from = models.ForeignKey("self", on_delete=models.SET_NULL,
                                         null=True, blank=True, related_name="+",
                                         verbose_name="回滚溯源")

    class Meta(BaseModel.Meta):
        db_table = "wiki_page_versions"
        constraints = [models.UniqueConstraint(fields=["page", "version_no"],
                                               name="uniq_wiki_page_version_no")]
        indexes = [
            models.Index(fields=["page", "-version_no"], name="idx_wpv_page_version"),
            # BR-11 正文 trgm（pg_trgm 扩展 0001 已建；标题索引见迁移内 GIN）
            GinIndex(fields=["content_text"], name="idx_wpv_text_trgm",
                     opclasses=["gin_trgm_ops"]),
        ]

    def __str__(self) -> str:
        return f"v{self.version_no}({self.page_id})"
