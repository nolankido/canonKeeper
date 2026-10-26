"""Shared fixtures.

The mock LLM here is a tiny rule-based "extractor": it looks for sentences of the form
`<Name> has <value> eyes.` / `<Name> was born in <place>.` / `<Name> died.` and emits claims
with the exact sentence as the quote. That is enough to exercise grounding, slot indexing,
candidate detection, adjudication plumbing, the report, and the eval harness without a key.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from canonkeeper.config import Settings
from canonkeeper.corpus import Series
from canonkeeper.corpus.chapterize import chapterize
from canonkeeper.corpus.loaders import Book
from canonkeeper.llm import MockLLM

_EYES = re.compile(r"([A-Z][a-z]+(?: [A-Z][a-z]+)?) (?:has|had) ([a-z]+) eyes\.")
_BORN = re.compile(r"([A-Z][a-z]+(?: [A-Z][a-z]+)?) was born in ([A-Z][a-z]+)\.")
_BORN_Q = re.compile(r"\"I was born in ([A-Z][a-z]+),\" said ([A-Z][a-z]+(?: [A-Z][a-z]+)?)")
_EYES_INJ = re.compile(r"([A-Z][a-z]+(?: [A-Z][a-z]+)?) looked up, and the ([a-z]+) eyes")
_DIED = re.compile(r"([A-Z][a-z]+(?: [A-Z][a-z]+)?) died\.")
_DEAD_INJ = re.compile(r"since ([A-Z][a-z]+(?: [A-Z][a-z]+)?) had been laid in the ground")
_WALK = re.compile(r"([A-Z][a-z]+(?: [A-Z][a-z]+)?) walked to ([A-Z][a-z]+)\.")


def _extract(text: str) -> dict:
    claims = []
    for m in _EYES.finditer(text):
        claims.append(
            {
                "kind": "attribute",
                "subject": m.group(1),
                "predicate": "eye_color",
                "object": m.group(2),
                "quote": m.group(0),
            }
        )
    for m in _EYES_INJ.finditer(text):
        claims.append(
            {
                "kind": "attribute",
                "subject": m.group(1),
                "predicate": "eye_color",
                "object": m.group(2),
                "quote": m.group(0),
            }
        )
    for m in _BORN.finditer(text):
        claims.append(
            {
                "kind": "attribute",
                "subject": m.group(1),
                "predicate": "birthplace",
                "object": m.group(2),
                "quote": m.group(0),
            }
        )
    for m in _BORN_Q.finditer(text):
        claims.append(
            {
                "kind": "attribute",
                "subject": m.group(2),
                "predicate": "birthplace",
                "object": m.group(1),
                "quote": m.group(0),
            }
        )
    for m in _DIED.finditer(text):
        claims.append(
            {
                "kind": "event",
                "subject": m.group(1),
                "predicate": "died",
                "object": "true",
                "quote": m.group(0),
            }
        )
    for m in _DEAD_INJ.finditer(text):
        claims.append(
            {
                "kind": "state",
                "subject": m.group(1),
                "predicate": "dead",
                "object": "true",
                "quote": m.group(0),
            }
        )
    for m in _WALK.finditer(text):
        claims.append(
            {
                "kind": "state",
                "subject": m.group(1),
                "predicate": "location",
                "object": m.group(2),
                "quote": m.group(0),
            }
        )
    return {"claims": claims, "aliases": {}}


def mock_handler(model: str, messages: list[dict[str, str]]) -> str:
    user = messages[-1]["content"]
    if "<chunk>" in user:
        chunk = user.split("<chunk>", 1)[1].split("</chunk>", 1)[0]
        return json.dumps(_extract(chunk))
    # Adjudication: contradiction unless the reason mentions change over time.
    if "may be change over time" in user:
        return json.dumps(
            {"verdict": "deliberate", "severity": "low", "explanation": "moved house"}
        )
    return json.dumps(
        {
            "verdict": "contradiction",
            "severity": "high",
            "explanation": "The two passages assert different values.",
            "suggested_fix": "Change the later passage to match.",
        }
    )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        provider="mock",
        workdir=tmp_path / "wd",
        cache_dir=tmp_path / "wd" / "cache",
        extract_chunk_tokens=400,
    )


@pytest.fixture
def mock_llm() -> MockLLM:
    return MockLLM(mock_handler)


BOOK1 = """CHAPTER I

Alice Grey has green eyes. Alice Grey was born in London. Bob Stone had brown eyes.

Alice Grey walked to Dover. It was a long walk and nobody spoke of it afterwards for a while.

CHAPTER II

Bob Stone was born in Leeds. Bob Stone walked to York. The rain was steady.

The two of them sat for an hour and said very little that mattered to anyone.
"""

BOOK2 = """CHAPTER I

Alice Grey has green eyes. She walked to Bath. Bob Stone walked to Hull.

Nothing of consequence happened, though the inn was full and the fire was low.

CHAPTER II

Carol Vane was born in Paris. Carol Vane had grey eyes. Carol Vane walked to Rouen.

The evening passed in the usual way and the lamps were lit at six.
"""

BOOK3 = """CHAPTER I

Alice Grey has blue eyes. Bob Stone walked to Leeds.

Long paragraphs of nothing much, to give the chapter some body and some weight for splitting.

CHAPTER II

Carol Vane walked to Lyon. Carol Vane died.

CHAPTER III

Carol Vane walked to Nice. Alice Grey walked to Dover.
"""


@pytest.fixture
def series() -> Series:
    books = []
    for i, (key, title, text) in enumerate(
        [("01-first", "First", BOOK1), ("02-second", "Second", BOOK2), ("03-third", "Third", BOOK3)]
    ):
        books.append(Book(key, title, i, text, chapterize(text, min_chars=50)))
    return Series("Test Series", books)
