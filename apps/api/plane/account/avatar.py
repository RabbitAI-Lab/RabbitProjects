"""默认头像 SVG 生成器（AUTH-004 §4.2.5）。

按 user_id 哈希从 12 组预置渐变配色中选定颜色；``name`` 参数决定首字符。
SVG 内嵌在 ``<img src>`` 内经 Nginx proxy 缓存与浏览器 immutable 命中；
服务端零存储、零外部依赖（与 ADR-0005 决策一致：头像不存对象存储）。

降级：
- 不抛异常——任何异常都返回合法 SVG（``?`` 首字符 + 灰配色），ST-08 越权探测场景
  必须返回 200 而非 404（防 user_id 枚举）。
"""
from __future__ import annotations

import hashlib
from html import escape as _html_escape

# 12 组预置渐变（足够 10 人小团队不撞色；同人 user_id 必同色）
GRADIENT_PALETTE: list[tuple[str, str]] = [
    ("#6366F1", "#8B5CF6"),
    ("#3B82F6", "#06B6D4"),
    ("#10B981", "#34D399"),
    ("#F59E0B", "#F97316"),
    ("#EF4444", "#F43F5E"),
    ("#8B5CF6", "#D946EF"),
    ("#14B8A6", "#22D3EE"),
    ("#F97316", "#FB923C"),
    ("#EC4899", "#F472B6"),
    ("#84CC16", "#A3E635"),
    ("#6366F1", "#3B82F6"),
    ("#A855F7", "#EC4899"),
]

# 越权探测的固定兜底色（不变色、永远合法）
FALLBACK_PALETTE = ("#9CA3AF", "#6B7280")

_SVG_TEMPLATE = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="160" height="160" viewBox="0 0 160 160">'
    '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
    '<stop offset="0%" stop-color="{c1}"/><stop offset="100%" stop-color="{c2}"/>'
    "</linearGradient></defs>"
    '<rect width="160" height="160" rx="80" fill="url(#g)"/>'
    '<text x="80" y="80" dy=".35em" text-anchor="middle" '
    'font-size="72" fill="#FFFFFF" font-family="system-ui, sans-serif">{char}</text>'
    "</svg>"
)


def _palette_for(user_id: str | None) -> tuple[str, str]:
    if not user_id:
        return FALLBACK_PALETTE
    digest = hashlib.md5(str(user_id).encode()).hexdigest()
    return GRADIENT_PALETTE[int(digest, 16) % len(GRADIENT_PALETTE)]


def render_avatar_svg(*, user_id: str | None, name: str | None) -> str:
    """渲染默认头像 SVG。

    - ``name`` 截首字符；空时回落 ``?``（与 ST-08 探测场景一致）。
    - XSS：``name`` 经 ``html.escape`` 转义后再嵌入 SVG（UT-12）。
    """
    try:
        c1, c2 = _palette_for(user_id)
        char = (name or "?")[:1] or "?"
        return _SVG_TEMPLATE.format(c1=c1, c2=c2, char=_html_escape(char))
    except Exception:
        # 兜底：任何异常返回合法 SVG（不允许 5xx）
        return _SVG_TEMPLATE.format(
            c1=FALLBACK_PALETTE[0], c2=FALLBACK_PALETTE[1], char="?"
        )
