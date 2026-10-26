"""Project Gutenberg fetcher with on-disk cache and boilerplate stripping."""

from __future__ import annotations

import re
from pathlib import Path

import httpx

_URLS = (
    "https://www.gutenberg.org/cache/epub/{id}/pg{id}.txt",
    "https://www.gutenberg.org/files/{id}/{id}-0.txt",
    "https://www.gutenberg.org/files/{id}/{id}.txt",
)

_START = re.compile(r"\*\*\*\s*START OF (?:THE|THIS) PROJECT GUTENBERG EBOOK.*?\*\*\*", re.I)
_END = re.compile(r"\*\*\*\s*END OF (?:THE|THIS) PROJECT GUTENBERG EBOOK", re.I)


def strip_boilerplate(text: str) -> str:
    """Remove the PG header/footer so licence text never becomes 'canon'."""
    m = _START.search(text)
    if m:
        text = text[m.end() :]
    m = _END.search(text)
    if m:
        text = text[: m.start()]
    return text.strip()


def fetch_gutenberg(book_id: int, cache_dir: Path) -> str:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f"pg{book_id}.txt"
    if cached.exists():
        return cached.read_text(encoding="utf-8")
    last_err: Exception | None = None
    for tmpl in _URLS:
        url = tmpl.format(id=book_id)
        try:
            r = httpx.get(url, follow_redirects=True, timeout=60)
            if r.status_code == 200 and len(r.text) > 1000:
                text = strip_boilerplate(r.text)
                cached.write_text(text, encoding="utf-8")
                return text
        except httpx.HTTPError as e:  # pragma: no cover - network
            last_err = e
    raise RuntimeError(
        f"could not fetch Project Gutenberg #{book_id} ({last_err or 'no usable response'}). "
        f"If this machine cannot reach gutenberg.org, download "
        f"https://www.gutenberg.org/cache/epub/{book_id}/pg{book_id}.txt by hand and save it as "
        f"{cached}"
    )
