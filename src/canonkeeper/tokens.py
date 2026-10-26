"""Token counting and token-bounded splitting.

Meta does not publish a tokenizer for the Muse models; cl100k_base is close enough for
budgeting (within ~10% on English prose). All budgets in this project leave headroom for that.
"""

from __future__ import annotations

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


def split_by_tokens(text: str, max_tokens: int) -> list[tuple[str, int]]:
    """Split on paragraph boundaries into pieces of <= max_tokens. Returns (piece, char_offset)."""
    if count_tokens(text) <= max_tokens:
        return [(text, 0)]
    pieces: list[tuple[str, int]] = []
    paras = text.split("\n\n")
    buf: list[str] = []
    buf_tokens = 0
    offset = 0
    buf_start = 0
    for p in paras:
        t = count_tokens(p) + 1
        if buf and buf_tokens + t > max_tokens:
            piece = "\n\n".join(buf)
            pieces.append((piece, buf_start))
            buf, buf_tokens = [], 0
            buf_start = offset
        buf.append(p)
        buf_tokens += t
        offset += len(p) + 2
    if buf:
        pieces.append(("\n\n".join(buf), buf_start))
    return pieces
