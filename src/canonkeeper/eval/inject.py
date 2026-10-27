"""Seeded-defect injection.

Takes a series plus the bible extracted from it, picks N single-valued facts, and plants a
sentence in a *later* book asserting a different value. The result is a mutated series and a
ground-truth list, so precision/recall of the whole pipeline (extraction → candidates →
adjudication) can be measured rather than eyeballed.

Injection is textual, not at the claim level, on purpose: a defect the extractor fails to read
back out of prose is a defect the tool would miss in real life, and the benchmark should say so.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from canonkeeper.canon import CanonStore
from canonkeeper.corpus import Book, Series
from canonkeeper.corpus.chapterize import chapterize
from canonkeeper.models import Claim

# predicate -> (alternative values, sentence template)
TEMPLATES: dict[str, tuple[list[str], str]] = {
    "eye_color": (
        ["grey", "blue", "brown", "green", "hazel", "black"],
        "{subject} looked up, and the {value} eyes that everyone remarked upon were fixed on the door.",
    ),
    "hair_color": (
        ["fair", "dark", "red", "grey", "black", "chestnut"],
        "{subject} pushed back a lock of {value} hair and said nothing.",
    ),
    "birthplace": (
        ["Edinburgh", "Dublin", "Bristol", "York", "Cardiff", "Norwich"],
        '"I was born in {value}," said {subject}, "and I have never once regretted leaving it."',
    ),
    "wound_location": (
        ["shoulder", "leg", "arm", "hand", "side", "knee"],
        "The old wound in {subject}'s {value}, the one from the war, ached in the damp.",
    ),
    "nationality": (
        ["Scottish", "Irish", "Welsh", "French", "German", "American"],
        "{subject}, being {value} by birth, took the remark more lightly than the rest.",
    ),
    "first_name": (
        ["James", "Henry", "Arthur", "Edward", "Thomas", "William"],
        '"{value}," she said softly, using the name only his family used for {subject}.',
    ),
    "dead": (
        ["true"],
        "It was a week since {subject} had been laid in the ground, and the house was quiet.",
    ),
}


@dataclass
class Injection:
    subject: str
    predicate: str
    original: str
    injected: str
    book: str
    book_index: int
    chapter_index: int
    sentence: str


@dataclass
class InjectionSet:
    series: Series
    injections: list[Injection] = field(default_factory=list)


# Values that can describe the same thing, so planting one against the other is not a clear
# contradiction: a thumb is on the hand, dark eyes are often brown. The benchmark should only
# plant errors a careful editor would call errors.
_OVERLAPS: list[set[str]] = [
    {"hand", "thumb", "finger", "fingers", "palm", "wrist", "arm", "left arm", "right arm"},
    {"leg", "knee", "thigh", "ankle", "foot", "shin", "calf", "left leg", "right leg"},
    {"shoulder", "arm", "collarbone", "left shoulder", "right shoulder"},
    {"side", "ribs", "chest", "right side", "left side", "body"},
    {"dark", "black", "brown", "hazel", "raven"},
    {"fair", "blond", "blonde", "golden", "flaxen", "light", "yellow"},
    {"grey", "gray", "white", "silver", "grizzled", "light"},
    {"red", "auburn", "ginger", "chestnut", "sandy", "fiery red"},
]


def clearly_different(original: str, alt: str, predicate: str) -> bool:
    """True when planting `alt` against `original` is an unambiguous contradiction."""
    from canonkeeper.audit.candidates import _same_value

    o, a = original.strip().lower(), alt.strip().lower()
    if _same_value(o, a, predicate):
        return False
    o_words = set(o.replace("-", " ").split()) | {o}
    a_words = set(a.replace("-", " ").split()) | {a}
    return not any(o_words & group and a_words & group for group in _OVERLAPS)


def _looks_like_a_person(name: str) -> bool:
    """Skip groups and things the extractor sometimes makes subjects of ('Andaman Islanders')."""
    from canonkeeper.canon.normalize import is_generic_name

    words = name.split()
    plural_group = len(words) > 1 and words[-1].endswith(("ers", "ians", "ans", "ese", "men"))
    return not is_generic_name(name) and not plural_group


def _eligible(store: CanonStore, min_book_gap: int = 1) -> list[Claim]:
    """Facts asserted in a book that has at least one later book to inject into."""
    n_books = max((c.provenance.book_index for c in store.claims), default=-1) + 1
    out = []
    for c in store.claims:
        if c.predicate in TEMPLATES and c.provenance.book_index + min_book_gap < n_books:
            if c.predicate == "dead":
                continue  # death is injected as a new fact, handled separately
            if not _looks_like_a_person(c.subject):
                continue
            out.append(c)
    return out


def _insert_sentence(book: Book, chapter_index: int, sentence: str, rng: random.Random) -> Book:
    ch = book.chapters[chapter_index]
    paras = ch.text.split("\n\n")
    if len(paras) > 2:
        i = rng.randrange(1, len(paras) - 1)
        paras.insert(i, sentence)
        new_ch_text = "\n\n".join(paras)
    else:
        new_ch_text = ch.text + "\n\n" + sentence + "\n"
    new_text = book.text[: ch.char_start] + new_ch_text + book.text[ch.char_end :]
    return Book(book.key, book.title, book.index, new_text, chapterize(new_text))


def inject(
    series: Series, store: CanonStore, n: int, *, seed: int = 0, include_deaths: bool = True
) -> InjectionSet:
    rng = random.Random(seed)
    books = [Book(b.key, b.title, b.index, b.text, b.chapters) for b in series.books]
    out = InjectionSet(Series(series.name, books))

    pool = _eligible(store)
    rng.shuffle(pool)
    used: set[tuple[str, str]] = set()

    for claim in pool:
        if len(out.injections) >= n:
            break
        key = (claim.subject.lower(), claim.predicate)
        if key in used:
            continue
        values, tmpl = TEMPLATES[claim.predicate]
        alts = [v for v in values if clearly_different(claim.object, v, claim.predicate)]
        if not alts:
            continue
        value = rng.choice(alts)
        target_idx = rng.randrange(claim.provenance.book_index + 1, len(books))
        target = books[target_idx]
        ch_idx = rng.randrange(len(target.chapters))
        sentence = tmpl.format(subject=claim.subject, value=value)
        books[target_idx] = _insert_sentence(target, ch_idx, sentence, rng)
        used.add(key)
        out.injections.append(
            Injection(
                claim.subject,
                claim.predicate,
                claim.object,
                value,
                target.key,
                target_idx,
                ch_idx,
                sentence,
            )
        )

    if include_deaths and len(out.injections) < n:
        # Kill a character early, let them keep acting later.
        entities = [e for e in store.entities() if len(store.for_entity(e)) >= 5]
        rng.shuffle(entities)
        for ent in entities:
            if len(out.injections) >= n or len(books) < 2:
                break
            if (ent.lower(), "dead") in used:
                continue
            target_idx = rng.randrange(0, len(books) - 1)
            target = books[target_idx]
            ch_idx = rng.randrange(len(target.chapters))
            sentence = TEMPLATES["dead"][1].format(subject=ent, value="")
            books[target_idx] = _insert_sentence(target, ch_idx, sentence, rng)
            used.add((ent.lower(), "dead"))
            out.injections.append(
                Injection(ent, "dead", "false", "true", target.key, target_idx, ch_idx, sentence)
            )

    out.series = Series(series.name, books)
    return out
