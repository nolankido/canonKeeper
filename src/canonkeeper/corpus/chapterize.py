"""Split a book into chapters.

Heuristic, deliberately simple: a chapter heading is a short line matching common patterns
(`CHAPTER I`, `Chapter 3`, `III.`, `PART ONE`, `1`, all-caps title lines preceded by a blank
line). Books with no detectable headings fall back to fixed-size windows so the pipeline never
stalls on an unusual layout. Offsets are kept so provenance can point at exact character spans.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_ROMAN = r"(?:M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3}))"
_HEADING = re.compile(
    rf"""^(?:
        (?:CHAPTER|Chapter|PART|Part|BOOK|Book|LETTER|Letter)\s+(?:{_ROMAN}|\d+|[A-Z][a-z]+)\b.*
      | {_ROMAN}\.?\s*(?:[-—:.]\s*.*)?
      | \d{{1,3}}\.?\s*(?:[-—:.]\s*[A-Z].*)?
    )\s*$""",
    re.VERBOSE,
)
_ALLCAPS = re.compile(r"^[A-Z][A-Z0-9 ,'’\-—:;!?.]{3,60}$")


@dataclass(frozen=True)
class Chapter:
    index: int
    title: str
    text: str
    char_start: int
    char_end: int


def _is_heading(line: str, prev_blank: bool) -> bool:
    s = line.strip()
    if not s or len(s) > 80:
        return False
    if _HEADING.match(s):
        return True
    return prev_blank and bool(_ALLCAPS.match(s)) and len(s.split()) <= 8


def chapterize(text: str, *, min_chars: int = 1500, fallback_window: int = 12_000) -> list[Chapter]:
    lines = text.splitlines(keepends=True)
    marks: list[tuple[int, str]] = []  # (char offset, title)
    pos = 0
    prev_blank = True
    for line in lines:
        if _is_heading(line, prev_blank):
            marks.append((pos, line.strip()))
        prev_blank = not line.strip()
        pos += len(line)

    chapters: list[Chapter] = []
    if len(marks) >= 2:
        bounds = [m[0] for m in marks] + [len(text)]
        for i, (start, title) in enumerate(marks):
            end = bounds[i + 1]
            body = text[start:end]
            if len(body.strip()) < min_chars and chapters:
                # Glue tiny fragments (e.g. a lone "PART I" line) to the previous chapter.
                prev = chapters[-1]
                chapters[-1] = Chapter(
                    prev.index, prev.title, text[prev.char_start : end], prev.char_start, end
                )
                continue
            chapters.append(Chapter(len(chapters), title, body, start, end))
        if chapters:
            return chapters

    # Fallback: fixed windows on paragraph boundaries.
    out: list[Chapter] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + fallback_window)
        if end < len(text):
            nl = text.rfind("\n\n", start, end)
            if nl > start + fallback_window // 2:
                end = nl
        out.append(Chapter(len(out), f"Section {len(out) + 1}", text[start:end], start, end))
        start = end
    return out
