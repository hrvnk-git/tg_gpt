from __future__ import annotations

import html as _html
import re

TELEGRAM_MESSAGE_MAX_CHARS = 4096


def split_text(text: str, max_chars: int = TELEGRAM_MESSAGE_MAX_CHARS) -> list[str]:
    """
    Telegram: max message length is 4096 symbols.

    Best-effort splitter that prefers breaking on newline/space to reduce the
    chance of breaking words/entities.
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be > 0")

    text = text or ""
    if len(text) <= max_chars:
        return [text]

    parts: list[str] = []
    start = 0
    n = len(text)

    while start < n:
        end = min(start + max_chars, n)
        if end >= n:
            parts.append(text[start:end])
            break

        chunk = text[start:end]
        # Prefer newline, then space, then hard cut.
        nl = chunk.rfind("\n")
        if nl != -1 and nl >= int(max_chars * 0.5):
            cut = nl + 1  # include newline
            parts.append(text[start : start + cut])
            start = start + cut
            continue

        sp = chunk.rfind(" ")
        if sp != -1 and sp >= int(max_chars * 0.5):
            cut = sp + 1  # include space
            parts.append(text[start : start + cut])
            start = start + cut
            continue

        parts.append(chunk)
        start = end

    return parts


_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
# Supports both:
# - ```\ncode\n```
# - ```lang\ncode\n```
_FENCED_CODE_RE = re.compile(r"```[^\n]*\n([\s\S]*?)```", re.MULTILINE)


def _escape_inline_code(text: str) -> str:
    """
    Escape text for Telegram HTML parse_mode, and convert `inline code` to <code>.
    """
    # We escape everything first, but we need to insert <code> tags around the raw
    # inline-code content, so we work on raw slices.
    parts: list[str] = []
    pos = 0
    for match in _INLINE_CODE_RE.finditer(text):
        start, end = match.span()
        parts.append(_html.escape(text[pos:start], quote=True))
        code_raw = match.group(1)
        parts.append(f"<code>{_html.escape(code_raw, quote=True)}</code>")
        pos = end
    parts.append(_html.escape(text[pos:], quote=True))
    return "".join(parts)


def render_telegram_html(text: str) -> str:
    """
    Convert model output with Markdown-ish code fences/backticks into Telegram HTML.

    - ```...``` -> <pre><code>...</code></pre>
    - `...` -> <code>...</code>

    Everything else is escaped to avoid Telegram/HTML injection.
    """
    text = text or ""
    out: list[str] = []
    pos = 0

    for match in _FENCED_CODE_RE.finditer(text):
        start, end = match.span()
        out.append(_escape_inline_code(text[pos:start]))
        code_raw = match.group(1)
        out.append(
            "<pre><code>"
            + _html.escape(code_raw, quote=True)
            + "</code></pre>"
        )
        pos = end

    out.append(_escape_inline_code(text[pos:]))
    return "".join(out)
