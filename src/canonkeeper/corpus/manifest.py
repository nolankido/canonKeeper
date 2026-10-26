"""A series is described by a small JSON manifest.

    {
      "series": "Sherlock Holmes",
      "books": [
        {"key": "01-a-study-in-scarlet", "title": "A Study in Scarlet", "gutenberg_id": 244},
        {"key": "02-the-sign-of-four", "title": "The Sign of the Four", "path": "books/sign.txt"}
      ]
    }

`key` order is series order (reading order), which the audit uses for "earlier book said X".
A book is either fetched from Project Gutenberg (`gutenberg_id`) or read from a local
`.txt` / `.md` file (`path`, relative to the manifest).
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field, model_validator


class BookSpec(BaseModel):
    key: str
    title: str
    gutenberg_id: int | None = None
    path: str | None = None

    @model_validator(mode="after")
    def _one_source(self) -> BookSpec:
        if (self.gutenberg_id is None) == (self.path is None):
            raise ValueError(f"book {self.key!r}: give exactly one of gutenberg_id or path")
        return self


class SeriesManifest(BaseModel):
    series: str
    books: list[BookSpec] = Field(min_length=1)
    manifest_dir: Path = Field(default=Path("."), exclude=True)

    @classmethod
    def load(cls, path: str | Path) -> SeriesManifest:
        p = Path(path)
        data = json.loads(p.read_text(encoding="utf-8"))
        m = cls.model_validate(data)
        m.manifest_dir = p.parent
        return m
