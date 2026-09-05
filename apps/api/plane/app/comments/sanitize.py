"""评论 HTML 净化（COLLAB-001 §4.4.1 + COLLAB-002 §4.3.3 img 扩展）。

bleach 不在依赖里（apps/api/pyproject.toml）——服务端唯一可信边界（BR-03）
用 stdlib ``html.parser`` 手写最小净化器：标签白名单 + 属性白名单 +
URL 协议白名单 + 图片 src 受控重写（BR-15）。

img 扩展（COLLAB-002）：
  - ``img`` 进入标签白名单，属性仅 ``src`` / ``alt``；
  - ``src`` 仅允许**本任务附件下载端点相对路径**（``…/issues/{issue_id}/
    attachments/{asset_id}/download/``，可选 ``?variant=thumb``），且 asset
    必须属于当前任务评论图域（BR-07：entity_type='comment_image' ∧
    entity_id=issue_id ∧ status='uploaded' ∧ uploaded_by=当前用户）；
  - 外链图片（http/https src）静默剥离为链接文本（防盗链与隐私引用）；
  - 其余 src（盗链 asset / 跨任务 / 非法形态）替换「图片不可用」占位文本。

不在范围：HTML 渲染（前端 Tiptap 解析 comment_json 与 comment_html，
本模块只保证 comment_html 是安全的纯子集）。
"""
from __future__ import annotations

import re
import uuid
from html import escape
from html.parser import HTMLParser

# ── 白名单（COLLAB-001 §4.4.1 + COLLAB-002 §4.3.3 img）────────
ALLOWED_TAGS = {"p", "br", "strong", "em", "code", "a", "span", "img"}
ALLOWED_ATTRS = {
    "a": {"href"},
    "span": {"data-mention-id", "class"},
    "img": {"src", "alt"},          # src 仅附件下载端点形态存活（下方重写）
}
ALLOWED_PROTOCOLS = ("http:", "https:")
ALLOWED_CLASSES = {"mention"}

# 图片不可用占位（BR-07 域校验失败的降级形态，纯文本节点）
IMAGE_UNAVAILABLE_PLACEHOLDER = "图片不可用"

# data-mention-id 必须是合法 UUID（小写带连字符），其他视为非法
_MENTION_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)

# 受控 src 形态：FILE-001 §4.3.4 既有附件下载端点（302 预签名），
# 不引入 /api/v1/files/ 路由族；?variant=thumb 为缩略变体（原图无参）
ASSET_SRC_RE = re.compile(
    r"^/api/v1/workspaces/[\w-]+/projects/[0-9a-fA-F-]{36}"
    r"/issues/(?P<issue_id>[0-9a-fA-F-]{36})"
    r"/attachments/(?P<asset_id>[0-9a-fA-F-]{36})/download/"
    r"(?:\?variant=thumb)?$"
)


def _is_safe_href(url: str) -> bool:
    """a[href] 协议白名单：仅 http/https。空 href 也接受（前端用作锚点）。"""
    if not url:
        return True
    url = url.strip()
    if url.startswith(("/", "#")):
        return True  # 站内相对 / 锚点
    lowered = url.lower()
    return any(lowered.startswith(p) for p in ALLOWED_PROTOCOLS)


def _is_external_src(url: str) -> bool:
    """外链图片（BR-15 剥离对象）：http/https 开头。"""
    return url.lower().startswith(("http://", "https://"))


class _Sanitizer(HTMLParser):
    """HTMLParser 子类 —— 白名单外的标签与其属性剥离（保留正文文本）。

    COLLAB-002 起 img 三分支处置：受控保留 / 外链剥成链接 / 其余占位文本。
    """

    def __init__(self, *, allowed_asset_ids: set[str] | None = None,
                 issue_id: str | None = None) -> None:
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        self._allowed_asset_ids = allowed_asset_ids or set()
        self._issue_id = (issue_id or "").lower()
        #: 存活 asset_id 列表（按出现顺序，供 accessory.images 服务端聚合）
        self.images: list[str] = []

    def handle_data(self, data: str) -> None:
        # 保留文本节点（必要 —— ``convert_charrefs=True`` 已自动把字符实体转文本）
        self._out.append(data)

    def handle_entityref(self, name: str) -> None:
        self._out.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self._out.append(f"&#{name};")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._emit(tag, attrs, close=False)

    def handle_endtag(self, tag: str) -> None:
        # 只对允许的容器标签闭合（自闭合 br/img 不在此出现）
        if tag in ALLOWED_TAGS and tag not in ("br", "img"):
            self._out.append(f"</{tag}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._emit(tag, attrs, close=True)

    # ── img 三分支（BR-15 / BR-07）─────────────────────────────
    def _emit_img(self, attrs: list[tuple[str, str | None]]) -> None:
        src = next((v for k, v in attrs if k == "src" and v), "")
        if not src:
            self._out.append(IMAGE_UNAVAILABLE_PLACEHOLDER)
            return
        if _is_external_src(src):
            # 外链图片 → 剥离为链接文本（防盗链与隐私引用，静默降级）
            safe = escape(src, quote=True)
            self._out.append(f'<a href="{safe}">{safe}</a>')
            return
        m = ASSET_SRC_RE.match(src.strip())
        if (
            m is not None
            and m.group("issue_id").lower() == self._issue_id
            and m.group("asset_id").lower() in self._allowed_asset_ids
        ):
            asset_id = m.group("asset_id").lower()
            alt = next((v for k, v in attrs if k == "alt" and v), "")
            alt_attr = f' alt="{escape(alt, quote=True)}"' if alt else ""
            self._out.append(f'<img src="{escape(src.strip(), quote=True)}"{alt_attr}/>')
            self.images.append(asset_id)
        else:
            # 盗链 / 跨任务 / 非法形态 → 「图片不可用」占位（不 500，BR-07）
            self._out.append(IMAGE_UNAVAILABLE_PLACEHOLDER)

    def _emit(self, tag: str, attrs: list[tuple[str, str | None]], *, close: bool) -> None:
        if tag not in ALLOWED_TAGS:
            # 危险标签整体剥离（脚本、iframe、onclick/onerror 载体…）；
            # convert_charrefs=True 已自动把 &lt;script&gt; 当文本处理，
            # 这里只能拦「真标签」——符合 BR-03「标签与属性全剥离」。
            return
        if tag == "img":
            self._emit_img(attrs)
            return
        allowed = ALLOWED_ATTRS.get(tag, set())
        kept: list[str] = []
        for k, v in attrs:
            if k not in allowed or v is None:
                continue
            if tag == "a" and k == "href" and not _is_safe_href(v):
                continue
            if tag == "span" and k == "data-mention-id" and not _MENTION_ID_RE.match(v):
                continue
            if tag == "span" and k == "class" and v not in ALLOWED_CLASSES:
                continue
            kept.append(f'{k}="{escape(v, quote=True)}"')
        attr_str = (" " + " ".join(kept)) if kept else ""
        if close:
            self._out.append(f"<{tag}{attr_str}/>")
        else:
            self._out.append(f"<{tag}{attr_str}>")

    def get_output(self) -> str:
        return "".join(self._out)


def sanitize_comment(html: str, *, allowed_asset_ids: set[str] | None = None,
                     issue_id: str | None = None) -> tuple[str, list[str]]:
    """白名单净化 + 图片域校验 —— 服务端唯一可信边界（BR-03 / BR-07 / BR-15）。

    返回 ``(净化 HTML, 存活 asset_id 列表)``；``images`` 供 accessory 服务端聚合
    （UT-18：客户端不直传 accessory——单一真相）。
    """
    if not html:
        return "", []
    parser = _Sanitizer(allowed_asset_ids=allowed_asset_ids, issue_id=issue_id)
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        # 解析失败返回空串，避免上游误入库非法 HTML
        return "", []
    return parser.get_output(), parser.images


_MENTION_EXTRACT_RE = re.compile(
    r'data-mention-id="([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"'
)


def extract_mention_ids(sanitized_html: str) -> set[str]:
    """从净化后 HTML 提取 @ 锚点 ID（小写 UUID 集）。"""
    if not sanitized_html:
        return set()
    return {m.lower() for m in _MENTION_EXTRACT_RE.findall(sanitized_html)}


def is_valid_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False
