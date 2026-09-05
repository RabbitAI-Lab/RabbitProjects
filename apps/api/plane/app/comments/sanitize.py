"""评论 HTML 净化（COLLAB-001 §4.4.1）。

bleach 不在依赖里（apps/api/pyproject.toml）——服务端唯一可信边界（BR-03）
用 stdlib ``html.parser`` 手写最小净化器：标签白名单 + 属性白名单 +
URL 协议白名单。

不在范围：HTML 渲染（前端 Tiptap 解析 comment_json 与 comment_html，
本模块只保证 comment_html 是安全的纯子集）。
"""
from __future__ import annotations

import re
import uuid
from html.parser import HTMLParser

# ── 白名单（与 COLLAB-001 §4.4.1 一一对应）────────────────────
ALLOWED_TAGS = {"p", "br", "strong", "em", "code", "a", "span"}
ALLOWED_ATTRS = {
    "a": {"href"},
    "span": {"data-mention-id", "class"},
}
ALLOWED_PROTOCOLS = ("http:", "https:")
ALLOWED_CLASSES = {"mention"}

# data-mention-id 必须是合法 UUID（小写带连字符），其他视为非法
_MENTION_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
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


class _Sanitizer(HTMLParser):
    """HTMLParser 子类 —— 白名单外的标签与其属性剥离（保留正文文本）。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []

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
        # 只对允许的标签闭合（自闭合 br 不在此出现）
        if tag in ALLOWED_TAGS and tag != "br":
            self._out.append(f"</{tag}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._emit(tag, attrs, close=True)

    def _emit(self, tag: str, attrs: list[tuple[str, str | None]], *, close: bool) -> None:
        if tag not in ALLOWED_TAGS:
            # 危险标签整体剥离（脚本、img、iframe、onclick/onerror 载体…）；
            # convert_charrefs=True 已自动把 &lt;script&gt; 当文本处理，
            # 这里只能拦「真标签」——符合 BR-03「标签与属性全剥离」。
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
            kept.append(f'{k}="{v}"')
        attr_str = (" " + " ".join(kept)) if kept else ""
        if close:
            self._out.append(f"<{tag}{attr_str}/>")
        else:
            self._out.append(f"<{tag}{attr_str}>")

    def get_output(self) -> str:
        return "".join(self._out)


def sanitize_comment_html(html: str) -> str:
    """白名单净化 —— 服务端唯一可信边界（BR-03）。"""
    if not html:
        return ""
    parser = _Sanitizer()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        # 解析失败返回空串，避免上游误入库非法 HTML
        return ""
    return parser.get_output()


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
