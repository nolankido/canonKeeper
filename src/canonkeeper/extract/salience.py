"""Salience hints: sentences that look like they state a physical or biographical fact.

A Flash-class model reading a 3,500-token chunk about one character skims past a single
sentence describing another ("Mary Morstan ... the brown eyes that had first drawn me to her").
The Holmes benchmark lost 8 of its 12 misses that way. This is a cheap, local pre-pass: it
lists the sentences that match generic patterns for such facts, and the extraction prompt asks
the model to make sure each yields a claim if it concerns a named person. Quotes are still
grounded against the chunk, so a hint can raise recall but cannot invent a fact.

The patterns describe how prose states these facts in general (a colour beside "eyes" or
"hair", "born in", "by birth", a wound, a name someone is called, a death); they are not the
benchmark's injection templates.
"""

from __future__ import annotations

import re

_COLOUR = (
    r"(?:black|brown|dark|fair|blond|blonde|golden|flaxen|red|auburn|ginger|chestnut|grey|gray|"
    r"grizzled|white|silver|sandy|light|hazel|blue|green|amber|violet|tawny|raven|pale)"
)
_PATTERNS = [
    # a colour within a few words of eyes / hair / beard, in either order
    rf"\b{_COLOUR}\b(?:\W+\w+){{0,4}}?\W+(?:eyes?|eyed|hair|haired|beard|moustache|whiskers)\b",
    rf"\b(?:eyes?|hair|beard|moustache)\b(?:\W+\w+){{0,4}}?\W+{_COLOUR}\b",
    r"\bborn\s+(?:in|at|on)\b",
    r"\bnative\s+of\b",
    r"\bby\s+birth\b",
    r"\b(?:wound|wounded|bullet|scar|limp)\w*\b",
    r"\b(?:named|called|christened|the\s+name)\b",
    r"\b(?:died|dead|death|buried|killed|murdered|laid\s+in\s+the\s+ground)\b",
    r"\b(?:feet|foot)\s+(?:high|tall|in\s+height)|\bin\s+height\b",
    r"\b(?:one|two|three|four|five|six|seven|\d+)\s+(?:feet|foot)\b",
]
_RX = re.compile("|".join(f"(?:{p})" for p in _PATTERNS), re.I)
_SENT = re.compile(r"(?:(?<=[.!?])|(?<=[.!?][\"”’']))\s+|\n{2,}")


def salient_sentences(text: str, limit: int = 25, max_chars: int = 240) -> list[str]:
    """Sentences of `text` that match a fact pattern, in order, deduplicated."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in _SENT.split(text):
        s = " ".join(raw.split())
        if len(s) < 12 or not _RX.search(s):
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s[:max_chars])
        if len(out) >= limit:
            break
    return out


def hints_block(text: str) -> str:
    """The text appended to the extraction request, or "" when nothing matched."""
    lines = salient_sentences(text)
    if not lines:
        return ""
    bullet = "\n".join(f"- {s}" for s in lines)
    return (
        "\n\nThese lines of the chunk look like they state a physical or biographical fact. "
        "For each one about a named person, make sure it yields a claim (quote it exactly); "
        "skip any that state nothing about a named person:\n" + bullet
    )
