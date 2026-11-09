"""商品描述富文本清洗（后台编辑与 MCP 共用）。"""

from __future__ import annotations

ALLOWED_DESC_TAGS = [
    "p", "br", "strong", "b", "em", "i", "u", "h3", "h4",
    "ul", "ol", "li", "blockquote", "a", "img", "span",
]
ALLOWED_DESC_ATTRS = {
    "a": ["href", "title", "target", "rel"],
    "img": ["src", "alt", "width", "height"],
}


def clean_description(value: str) -> str:
    """清洗商品富文本描述，保留常用排版与图片。"""
    raw = str(value or "")
    if not raw.strip():
        return ""
    try:
        import bleach

        cleaned = bleach.clean(
            raw,
            tags=ALLOWED_DESC_TAGS,
            attributes=ALLOWED_DESC_ATTRS,
            protocols=["http", "https"],
            strip=True,
        )
    except ImportError:
        import html

        cleaned = html.escape(raw)
    return cleaned[:20000]
