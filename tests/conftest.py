"""Shared fixtures.

The mock provider's rule-based extractor/adjudicator lives in `canonkeeper.llm.rules` so that
`--provider mock` works for users too; tests import it from there. The fixture series below
plants two defects (Alice's eye colour drifts in book 3; Carol dies in book 3 ch. 2 and acts in
ch. 3) and one name drift (Bob is "Robert" in book 1 and "Bob" in book 3).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from canonkeeper.config import Settings
from canonkeeper.corpus import Series
from canonkeeper.corpus.chapterize import chapterize
from canonkeeper.corpus.loaders import Book
from canonkeeper.llm import MockLLM
from canonkeeper.llm.rules import handler as mock_handler

__all__ = ["mock_handler"]


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

Bob Stone was born in Leeds. Bob Stone walked to York. Bob Stone was known to everyone as Robert. The rain was steady.

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

Alice Grey has blue eyes. Bob Stone walked to Leeds. Bob Stone was known to everyone as Bob.

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
