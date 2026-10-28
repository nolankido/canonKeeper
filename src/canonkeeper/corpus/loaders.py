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


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MAX_DOCX_XML = 200 * 10**6  # the document part, unzipped: far beyond any novel, short of a bomb


def docx_text(data: bytes, name: str = "file.docx") -> str:
    """The text of a Word (.docx) manuscript: one paragraph per block, separated by blank lines
    (how the chapterizer and the extractor read a book). Headings, tabs and line breaks are
    kept; comments, footnotes and tracked deletions are not part of the book and are left out.
    Uses only the standard library."""
    import io
    import xml.etree.ElementTree as ET
    import zipfile

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            info = z.getinfo("word/document.xml")
            if info.file_size > MAX_DOCX_XML:
                raise ValueError(f"{Path(name).name} is too large to read")
            root = ET.fromstring(z.read(info))
    except (zipfile.BadZipFile, KeyError, ET.ParseError):
        raise ValueError(
            f"{Path(name).name} could not be read as a Word document; save it as .docx again, "
            "or as plain text (.txt), and upload that"
        ) from None
    paragraphs = []
    for p in root.iter(f"{_W}p"):
        parts = []
        for el in p.iter():
            if el.tag == f"{_W}t" and el.text:
                parts.append(el.text)
            elif el.tag == f"{_W}tab":
                parts.append("\t")
            elif el.tag in (f"{_W}br", f"{_W}cr"):
                parts.append("\n")
            elif el.tag == f"{_W}delText":
                continue
        text = "".join(parts).strip()
        if text:
            paragraphs.append(text)
    return "\n\n".join(paragraphs) + "\n"


def decode_text(data: bytes, name: str = "file") -> str:
    """Text from a manuscript file, whatever a word processor saved it as, with "\n" line
    endings (as `Path.read_text` gives): Windows files end lines with "\r\n", and paragraphs
    are found by blank lines.

    UTF-8 (with or without a BOM) is the norm; Word's "Unicode text" is UTF-16 with a BOM;
    older Windows tools write Windows-1252, whose curly quotes are invalid UTF-8. A Word .docx
    is read for its text (`docx_text`). Anything else with NUL bytes (a PDF, an old .doc) is
    refused with a message that says what to do.
    """
    if name.lower().endswith(".docx"):
        return docx_text(data, name)
    return _decode(data, name).replace("\r\n", "\n").replace("\r", "\n")


def _decode(data: bytes, name: str) -> str:
    for bom, codec in (
        (b"\xef\xbb\xbf", "utf-8-sig"),
        (b"\xff\xfe\x00\x00", "utf-32"),
        (b"\x00\x00\xfe\xff", "utf-32"),
        (b"\xff\xfe", "utf-16"),
        (b"\xfe\xff", "utf-16"),
    ):
        if data.startswith(bom):
            return data.decode(codec, errors="replace")
    head = data[:4096]
    if head and b"\x00" in head:
        # UTF-16 without a BOM: every other byte of ASCII prose is NUL
        if head[1::2].count(0) > len(head) // 4 and not head[0::2].count(0):
            return data.decode("utf-16-le", errors="replace")
        if head[0::2].count(0) > len(head) // 4 and not head[1::2].count(0):
            return data.decode("utf-16-be", errors="replace")
        raise ValueError(
            f"{Path(name).name} is not a text file; save it as Word (.docx), plain text (.txt)"
            " or Markdown (.md) and upload that"
        )
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def load_text(path: Path) -> str:
    return decode_text(path.read_bytes(), path.name)


def load_series(manifest: SeriesManifest, cache_dir: Path) -> Series:
    books: list[Book] = []
    for i, spec in enumerate(manifest.books):
        if spec.gutenberg_id is not None:
            text = fetch_gutenberg(spec.gutenberg_id, cache_dir / "gutenberg")
        else:
            text = load_text((manifest.manifest_dir / str(spec.path)).resolve())
        books.append(Book(spec.key, spec.title, i, text, chapterize(text)))
    return Series(manifest.series, books)
