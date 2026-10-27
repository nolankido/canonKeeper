"""Token counting and token-bounded splitting.

Providers do not all publish tokenizers; cl100k_base is close enough for
budgeting (within ~10% on English prose). All budgets in this project leave headroom for that.
"""

from __future__ import annotations

import re
from functools import lru_cache


@lru_cache(maxsize=1)
def _enc():  # noqa: ANN202
    import tiktoken

    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    try:
        return len(_enc().encode(text, disallowed_special=()))
    except Exception:  # pragma: no cover - tiktoken download failure offline
        return max(1, len(text) // 4)


_SENTENCE_END = re.compile(r"(?<=[.!?])[\"”’']?\s+")


def _subdivide(
    text: str, start: int, end: int, max_tokens: int, level: int = 0
) -> list[tuple[int, int]]:
    """Spans of text[start:end], each within max_tokens where possible: split at line breaks,
    then sentence ends, then spaces, and only as a last resort mid-word."""
    if count_tokens(text[start:end]) + 1 <= max_tokens or end - start <= 1:
        return [(start, end)]
    seps = [re.compile(r"\n"), _SENTENCE_END, re.compile(r"\s+")]
    if level < len(seps):
        cuts = [m.end() for m in seps[level].finditer(text, start, end) if start < m.end() < end]
        if cuts:
            out: list[tuple[int, int]] = []
            for a, b in zip([start, *cuts], [*cuts, end], strict=True):
                out.extend(_subdivide(text, a, b, max_tokens, level + 1))
            return out
        return _subdivide(text, start, end, max_tokens, level + 1)
    mid = (start + end) // 2  # no separator at all: halve
    return _subdivide(text, start, mid, max_tokens, level) + _subdivide(
        text, mid, end, max_tokens, level
    )


def split_by_tokens(text: str, max_tokens: int) -> list[tuple[str, int]]:
    """Split into pieces of <= max_tokens, at paragraph boundaries where possible.
    Returns (piece, char_offset) with piece == text[offset : offset + len(piece)].

    A paragraph that alone exceeds the budget (a manuscript with single newlines between
    paragraphs is one "paragraph" per chapter) is cut at line breaks, then sentence ends, then
    spaces, so no chunk silently grows past what the extractor reads well.
    """
    if count_tokens(text) <= max_tokens:
        return [(text, 0)]
    units: list[tuple[int, int]] = []
    offset = 0
    for para in text.split("\n\n"):
        end = offset + len(para)
        units.extend(_subdivide(text, offset, end, max_tokens))
        offset = end + 2
    pieces: list[tuple[str, int]] = []
    buf_start = buf_end = -1
    buf_tokens = 0
    for a, b in units:
        t = count_tokens(text[a:b]) + 1
        if buf_start >= 0 and buf_tokens + t > max_tokens:
            pieces.append((text[buf_start:buf_end], buf_start))
            buf_start, buf_tokens = -1, 0
        if buf_start < 0:
            buf_start = a
        buf_end = b
        buf_tokens += t
    if buf_start >= 0:
        pieces.append((text[buf_start:buf_end], buf_start))
    return pieces
