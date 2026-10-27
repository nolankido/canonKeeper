"""Split a book into chapters, preserving character offsets for provenance.

Tuned against the actual Project Gutenberg layouts of the six Sherlock Holmes volumes, which
between them use every common convention:

| volume                       | heading layout                                   |
|------------------------------|--------------------------------------------------|
| A Study in Scarlet           | `PART I.` then `CHAPTER I.` + ALL-CAPS title on the next line; numbering restarts per part |
| The Sign of the Four         | `Chapter I` + Title Case title on the next line  |
| The Hound of the Baskervilles| `Chapter 1.` + title on the next line            |
| The Adventures / Memoirs     | `I. A SCANDAL IN BOHEMIA` on one line; bare `I.` sub-parts *inside* stories |
| The Return                   | unnumbered `THE ADVENTURE OF THE EMPTY HOUSE`, matching the Contents list |

All of them carry a Contents block whose entries would otherwise be mistaken for headings, and
most have stray all-caps lines in the body (`RACHE.`, signatures, telegrams).

Strategy, in order:
1. **Contents-driven.** If a Contents block is present, its entries are the ground truth for
   chapter titles. Scan the body for lines (or line + next line) that match an entry.
2. **Pattern-driven.** Otherwise pick the strongest heading *family* present (Markdown `#`
   headings / `CHAPTER n` / `Chapter n` / `n. TITLE` / a bare `1`, `II.` run counting up from
   one) and use only that family, so bare sub-part numerals and shouted body lines do not split
   chapters. A `PART` heading sets a prefix and is not itself a chapter. The Markdown and bare
   number families are for authors' own manuscripts; none of them fires on the Holmes texts.
3. **Windows.** No headings at all → fixed-size windows on paragraph boundaries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_ROMAN = r"(?:M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3}))"
_NUM = rf"(?:{_ROMAN}|\d{{1,3}}|[A-Z][a-z]+)"  # I / 1 / One

_CHAPTER = re.compile(
    rf"^(?:CHAPTER|Chapter|ADVENTURE|Adventure|LETTER|Letter)\s+({_NUM})\b\.?\s*(.*)$"
)
_PART = re.compile(rf"^(?:PART|Part|BOOK|Book|VOLUME|Volume)\s+({_NUM})\b\.?\s*(.*)$")
_NUMBERED_TITLE = re.compile(rf"^({_ROMAN}|\d{{1,3}})\.\s+(\S.*)$")  # "I. A SCANDAL IN BOHEMIA"
_BARE_NUMERAL = re.compile(rf"^({_ROMAN}|\d{{1,3}})\.?$")
_CONTENTS = re.compile(r"^\s*(?:CONTENTS|Contents|TABLE OF CONTENTS|Table of Contents)\.?\s*$")
_ALLCAPS = re.compile(r"^[A-Z][A-Z0-9 ,'’\-—:;!?.()]{3,70}$")
_MARKDOWN = re.compile(r"^(#{1,3})\s+(\S.*?)\s*#*\s*$")  # "## Chapter 1", "# The Tide"


@dataclass(frozen=True)
class Chapter:
    index: int
    title: str
    text: str
    char_start: int
    char_end: int


@dataclass
class _Line:
    text: str  # raw line including newline
    start: int

    @property
    def s(self) -> str:
        return self.text.strip()


def _lines(text: str) -> list[_Line]:
    out: list[_Line] = []
    pos = 0
    for raw in text.splitlines(keepends=True):
        out.append(_Line(raw, pos))
        pos += len(raw)
    return out


def _norm(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"^(?:chapter|adventure|part|book)\s+", "", s)
    s = re.sub(rf"^(?:{_ROMAN.lower()}|\d{{1,3}})\.?\s+", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return s


# --- 1. contents-driven --------------------------------------------------------------------


def _contents_entries(lines: list[_Line]) -> tuple[list[str], int] | None:
    """Return (normalised entry titles, index of the line after the block) or None.

    Gutenberg indents Contents entries and starts body text at column 0, so an indented block
    ends at the first non-blank, non-indented line. Non-indented lists end after a long blank
    run instead. The book's own title is dropped only when it is the first entry (Study in
    Scarlet lists it; Hound's last chapter *is* the title and must be kept)."""
    title_norm = next((_norm(ln.s) for ln in lines[:50] if ln.s), "")
    for i, ln in enumerate(lines[:400]):
        if not _CONTENTS.match(ln.text):
            continue
        raw: list[str] = []
        indented: bool | None = None
        blank_run = 0
        j = i + 1
        while j < len(lines):
            text = lines[j].text
            s = text.strip()
            if not s:
                blank_run += 1
                if raw and indented is False and blank_run >= 3:
                    break
            else:
                blank_run = 0
                is_indented = text[:1] in (" ", "\t")
                if indented is None:
                    indented = is_indented
                if indented and not is_indented:
                    break
                if len(s) <= 90:
                    raw.append(s)
            j += 1
        entries: list[str] = []
        for k, s in enumerate(raw):
            if _PART.match(s) or _BARE_NUMERAL.match(s):
                continue
            n = _norm(s)
            if not n or (k == 0 and n == title_norm):
                continue
            entries.append(n)
        if len(entries) >= 2:
            return entries, j
    return None


def _unbalanced_quotes(s: str) -> bool:
    """A dialogue fragment like `Sir Henry Baskerville.”` is not a heading."""
    return s.count("“") != s.count("”") or s.count('"') % 2 == 1


def _match_contents(
    lines: list[_Line], entries: list[str], body_start: int
) -> list[tuple[int, str]]:
    """Find (char offset, title) for each contents entry, in body order."""
    wanted = {e: k for k, e in enumerate(entries)}
    found: list[tuple[int, str, int]] = []
    used: set[int] = set()
    i = body_start
    while i < len(lines):
        s = lines[i].s
        if s and len(s) <= 90 and (i == 0 or not lines[i - 1].s) and not _unbalanced_quotes(s):
            cands: list[tuple[str, str]] = [(_norm(s), s)]
            # Heading number on one line, title on the next ("CHAPTER I." / "MR. SHERLOCK HOLMES.").
            if i + 1 < len(lines) and lines[i + 1].s and len(lines[i + 1].s) <= 90:
                nxt = lines[i + 1].s
                joined = f"{s} {nxt}"
                cands.append((_norm(joined), joined))
                if _CHAPTER.match(s) or _BARE_NUMERAL.match(s):
                    cands.append((_norm(nxt), joined))
            for n, title in cands:
                k = wanted.get(n)
                if k is not None and k not in used and (not found or k > found[-1][2]):
                    found.append((lines[i].start, title, k))
                    used.add(k)
                    break
        i += 1
    # Also strip "The Adventure of Black Peter." vs "...Peter" punctuation drift: _norm handles it.
    if len(found) < max(2, len(entries) // 2):
        return []
    return [(pos, title) for pos, title, _ in found]


# --- 2. pattern-driven ---------------------------------------------------------------------


def _markdown_marks(lines: list[_Line], skip_before: int) -> list[tuple[int, str]]:
    """Markdown headings, which an author uploading .md has marked on purpose. The shallowest
    level used at least twice is the chapter level ("# My Novel" once, "## Chapter 1" many
    times → chapters are the "##" lines)."""
    by_level: dict[int, list[tuple[int, str]]] = {}
    in_fence = False
    for i in range(skip_before, len(lines)):
        s = lines[i].s
        if s.startswith("```"):
            in_fence = not in_fence
        if in_fence or not s.startswith("#"):
            continue
        if m := _MARKDOWN.match(s):
            title = m.group(2).strip("*_ ").strip()
            if title:
                by_level.setdefault(len(m.group(1)), []).append((lines[i].start, title))
    for level in sorted(by_level):
        marks = by_level[level]
        if len(marks) >= 2:
            # "# My Novel" above a by-line and then "# Chapter 1": a title page, not a chapter
            if len(marks) >= 3 and marks[1][0] - marks[0][0] < 1500:
                marks = marks[1:]
            return marks
    return []


def _bare_numeral_marks(lines: list[_Line], skip_before: int) -> list[tuple[int, str]]:
    """Chapters headed by a number alone ("1", "II."), standing between blank lines. Only a
    run counting up from one or I qualifies, so a stray number in the text does not split."""
    marks: list[tuple[int, str]] = []
    for i in range(skip_before, len(lines)):
        s = lines[i].s
        if (
            s
            and _BARE_NUMERAL.match(s)
            and (i == 0 or not lines[i - 1].s)
            and (i + 1 >= len(lines) or not lines[i + 1].s)
        ):
            marks.append((lines[i].start, s.rstrip(".")))
    if len(marks) < 3:
        return []
    numbers = [_numeral_value(t) for _, t in marks]
    if numbers != list(range(1, len(numbers) + 1)):
        return []
    return [(pos, f"Chapter {t}") for pos, t in marks]


def _numeral_value(s: str) -> int:
    if s.isdigit():
        return int(s)
    vals = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    for a, b in zip(s, [*s[1:], ""], strict=True):
        v = vals.get(a, 0)
        total += -v if b and vals.get(b, 0) > v else v
    return total


def _family_marks(lines: list[_Line], skip_before: int) -> list[tuple[int, str]]:
    if md := _markdown_marks(lines, skip_before):
        return md
    chapter: list[tuple[int, str]] = []
    numbered: list[tuple[int, str]] = []
    part_prefix = ""
    for i in range(skip_before, len(lines)):
        ln = lines[i]
        s = ln.s
        if not s or len(s) > 90 or (i > 0 and lines[i - 1].s):
            continue
        if m := _PART.match(s):
            part_prefix = f"Part {m.group(1)} · "
            continue
        if m := _CHAPTER.match(s):
            title = s
            if (
                not m.group(2)
                and i + 1 < len(lines)
                and lines[i + 1].s
                and len(lines[i + 1].s) <= 90
            ):
                title = f"{s} {lines[i + 1].s}"
            chapter.append((ln.start, part_prefix + title))
            continue
        if _NUMBERED_TITLE.match(s) and len(s.split()) >= 2:
            numbered.append((ln.start, part_prefix + s))
    if len(chapter) >= 2:
        return chapter
    if len(numbered) >= 2:
        return numbered
    if bare := _bare_numeral_marks(lines, skip_before):
        return bare
    # Last resort: standalone ALL-CAPS lines followed by a blank line, excluding obvious noise.
    caps: list[tuple[int, str]] = []
    for i in range(skip_before, len(lines)):
        s = lines[i].s
        if (
            s
            and _ALLCAPS.match(s)
            and len(s.split()) <= 10
            and (i == 0 or not lines[i - 1].s)
            and (i + 1 >= len(lines) or not lines[i + 1].s)
            and not s.endswith((",", ":"))
            and s not in {"THE END", "FINIS"}
        ):
            caps.append((lines[i].start, s))
    return caps if len(caps) >= 2 else []


# --- assembly -------------------------------------------------------------------------------


def _build(text: str, marks: list[tuple[int, str]], min_chars: int) -> list[Chapter]:
    chapters: list[Chapter] = []
    bounds = [m[0] for m in marks] + [len(text)]
    for i, (start, title) in enumerate(marks):
        end = bounds[i + 1]
        if len(text[start:end].strip()) < min_chars and chapters:
            prev = chapters[-1]
            chapters[-1] = Chapter(
                prev.index, prev.title, text[prev.char_start : end], prev.char_start, end
            )
            continue
        chapters.append(Chapter(len(chapters), title, text[start:end], start, end))
    # Front matter before the first heading belongs to chapter 0 only if it is substantial;
    # a title page and contents list are not canon, so they are dropped from provenance spans.
    return chapters


def _windows(text: str, fallback_window: int) -> list[Chapter]:
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


def chapterize(text: str, *, min_chars: int = 1500, fallback_window: int = 12_000) -> list[Chapter]:
    lines = _lines(text)
    body_start = 0
    marks: list[tuple[int, str]] = []

    toc = _contents_entries(lines)
    if toc:
        entries, body_start = toc
        marks = _match_contents(lines, entries, body_start)
    if not marks:
        marks = _family_marks(lines, body_start)
    if len(marks) >= 2:
        chapters = _build(text, marks, min_chars)
        if chapters:
            return chapters
    return _windows(text, fallback_window)
