"""Pure-Python filename cleaning shared by output workflows."""

from __future__ import annotations

import hashlib
import re


_STRIP_PATTERNS = (
    r"【[^】]*】",
    r"\[[^\]]*\]",
    r"（[^）]*）",
    r"\([^)]*\)",
    r"@[\w\u4e00-\u9fff]+",
    r"公众号[：:·]?\s*[\w\u4e00-\u9fff]*",
    r"小红书[：:·]?\s*[\w\u4e00-\u9fff]*",
    r"抖音[：:·]?\s*[\w\u4e00-\u9fff]*",
    r"微博[：:·]?\s*[\w\u4e00-\u9fff]*",
    r"[Bb]站[：:·]?\s*[\w\u4e00-\u9fff]*",
    r"知乎[：:·]?\s*[\w\u4e00-\u9fff]*",
    r"快手[：:·]?\s*[\w\u4e00-\u9fff]*",
    r"微信[：:·]?\s*[\w\u4e00-\u9fff]*",
    r"头条[：:·]?\s*[\w\u4e00-\u9fff]*",
    r"如有侵权[\w\u4e00-\u9fff]*",
    r"侵权?删除?",
    r"侵删",
    r"转载[\w\u4e00-\u9fff]*",
    r"版权[\w\u4e00-\u9fff]*",
    r"免责[\w\u4e00-\u9fff]*",
    r"仅供[\w\u4e00-\u9fff]*学习[\w\u4e00-\u9fff]*",
    r"禁止[\w\u4e00-\u9fff]*商用[\w\u4e00-\u9fff]*",
    r"来源[：:][\w\u4e00-\u9fff]*",
    r"作者[：:][\w\u4e00-\u9fff]*",
    r"出处[：:][\w\u4e00-\u9fff]*",
    r"同名",
)

_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MULTI_SEPARATOR = re.compile(r"[\s_\-—]+")
_WINDOWS_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}


def clean_filename(original: str, *, max_length: int = 80) -> str:
    """Clean a filename stem while preserving useful human-readable text."""
    if max_length < 1:
        raise ValueError("max_length 必须大于 0")

    name = str(original)
    for pattern in _STRIP_PATTERNS:
        name = re.sub(pattern, " ", name)

    name = _ILLEGAL_CHARS.sub("", name)
    name = _MULTI_SEPARATOR.sub(" ", name).strip()
    name = name.strip(" _-—.·,，、;；!！~")
    name = name[:max_length].rstrip(" .")

    if not name:
        name = hashlib.sha256(str(original).encode("utf-8")).hexdigest()[:8]
    if name.casefold() in _WINDOWS_RESERVED:
        name = f"{name}_file"
    return name
