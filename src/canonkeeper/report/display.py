"""How facts read in the HTML report: for an author, not for the pipeline.

The pipeline's own strings (`Candidate.reason`, `subject.predicate = object`) are what the
adjudicator is shown and what the JSON carries; they stay as they are, because changing them
would change the prompts. These helpers only change what a person reads.
"""

from __future__ import annotations

import re

from canonkeeper.models import Candidate, Claim, Provenance

LABELS = {
    "eye_color": "eye colour",
    "hair_color": "hair colour",
    "wound_location": "wound",
    "first_name": "first name",
    "child_of": "parent",
    "parent_of": "child",
    "sibling_of": "sibling",
    "married_to": "spouse",
    "engaged_to": "fiancé(e)",
    "employed_by": "employer",
    "residence": "home",
    "birth_year": "year of birth",
    "death_year": "year of death",
}
# values that are places or peoples read better capitalised; the bible stores them lowercased
_PROPER = {"birthplace", "nationality", "residence", "location"}
_SMALL = {"a", "an", "and", "at", "by", "for", "from", "in", "of", "on", "or", "the", "to", "with"}
_ROMAN = re.compile(r"^[IVXLC]+\.?$")


CHECKS = {
    "slot_mismatch": "differing detail",
    "name_drift": "name changes",
    "dead_speaks": "back from the dead",
    "timeline": "dates disagree",
    "origin_mismatch": "origin disagrees",
    "role_collision": "one role, two people",
}


def check(heuristic: str) -> str:
    return CHECKS.get(heuristic, heuristic.replace("_", " "))


def label(predicate: str) -> str:
    return LABELS.get(predicate, predicate.replace("_", " "))


def value(claim: Claim) -> str:
    v = claim.object
    if claim.predicate in _PROPER and v.islower():
        return " ".join(w if w in _SMALL else w[:1].upper() + w[1:] for w in v.split())
    return v


def chapter(title: str) -> str:
    """Gutenberg chapter headings are often shouted ("X. THE ADVENTURE OF THE NOBLE
    BACHELOR"); title-case those, keeping Roman numerals. Mixed-case titles are left alone."""
    if not title.isupper():
        return title
    words = title.split()
    out = []
    for i, w in enumerate(words):
        if _ROMAN.match(w):
            out.append(w)
        elif i > 0 and w.lower() in _SMALL and not out[-1].endswith((".", ":")):
            out.append(w.lower())
        else:
            out.append(w[:1] + w[1:].lower())
    text = " ".join(out)
    if text.endswith(".") and len(words) > 1:
        text = text[:-1]  # "THE SCIENCE OF DEDUCTION." is a heading, not a sentence
    return text


def where(p: Provenance) -> str:
    book = p.book_title or f"Book {p.book_index + 1}"
    return f"{book} · {chapter(p.chapter)}"


def _book(cl: Claim) -> str:
    return cl.provenance.book_title or cl.provenance.book


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def headline(c: Candidate) -> str:
    """One sentence an author can read: whose fact, what it is, and where each version is."""
    if c.heuristic in ("slot_mismatch", "name_drift") and c.claims:
        first = c.claims[0]
        by_book: dict[str, list[str]] = {}
        seen: set[str] = set()
        for cl in sorted(
            c.claims, key=lambda x: (x.provenance.book_index, x.provenance.chapter_index)
        ):
            v = value(cl)
            if v.lower() not in seen:
                seen.add(v.lower())
                by_book.setdefault(_book(cl), []).append(v)
        head = f"{first.subject}’s {label(first.predicate)}: "
        if len(by_book) == 1:
            ((book, vals),) = by_book.items()
            both = "both" if len(vals) == 2 else "all"
            return head + " vs ".join(vals) + f" ({both} in {book})"
        parts = [f"{' / '.join(vals)} in {book}" for book, vals in by_book.items()]
        if len(parts) == 2:
            return head + " vs ".join(parts)
        more = f", and {len(parts) - 3} more" if len(parts) > 3 else ""
        return head + ", ".join(parts[:3]) + more
    if c.heuristic == "origin_mismatch" and len(c.claims) == 2:
        a, b = c.claims
        second = value(b) if b.predicate == a.predicate else f"{label(b.predicate)} {value(b)}"
        where_b = "the same book" if _book(a) == _book(b) else _book(b)
        return (
            f"{a.subject}: {label(a.predicate)} {value(a)} in {_book(a)}, but {second} in {where_b}"
        )
    text = c.reason
    for cl in c.claims:  # the reason is readable prose here; only its locators shout
        text = text.replace(cl.provenance.locator, where(cl.provenance))
    text = text.replace(" but acts in ", " but appears again in ")
    text = re.sub(
        r"\b([A-Z][\w.' -]*?)\.([a-z]+(?:_[a-z]+)*)\b", lambda m: f"{m[1]}’s {label(m[2])}", text
    )
    return text[:1].upper() + text[1:]


def fact(claim: Claim) -> str:
    return f"{label(claim.predicate)}: {value(claim)}"
