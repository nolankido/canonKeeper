from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from canonkeeper.corpus.chapterize import Chapter, chapterize
from canonkeeper.corpus.gutenberg import fetch_gutenberg
from canonkeeper.corpus.manifest import SeriesManifest


@dataclass
class Book:
    key: str
    title: str
    index: int
    text: str
    chapters: list[Chapter]

    @property
    def words(self) -> int:
        return len(self.text.split())


@dataclass
class Series:
    name: str
    books: list[Book]

    @property
    def words(self) -> int:
        return sum(b.words for b in self.books)

    def book(self, key: str) -> Book:
        for b in self.books:
            if b.key == key:
                return b
        raise KeyError(key)


def load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def load_series(manifest: SeriesManifest, cache_dir: Path) -> Series:
    books: list[Book] = []
    for i, spec in enumerate(manifest.books):
        if spec.gutenberg_id is not None:
            text = fetch_gutenberg(spec.gutenberg_id, cache_dir / "gutenberg")
        else:
            text = load_text((manifest.manifest_dir / str(spec.path)).resolve())
        books.append(Book(spec.key, spec.title, i, text, chapterize(text)))
    return Series(manifest.series, books)
