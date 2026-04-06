from __future__ import annotations

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

