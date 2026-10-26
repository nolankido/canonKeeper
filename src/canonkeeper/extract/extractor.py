"""Chapter → claims, with a quote-grounding guard."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from rapidfuzz import fuzz
from rich.progress import Progress

from canonkeeper.config import Settings
from canonkeeper.corpus import Book, Chapter, Series
from canonkeeper.extract import prompts
from canonkeeper.llm import LLM
from canonkeeper.llm.client import parse_json
from canonkeeper.models import Claim, ClaimKind, ExtractionResult, Provenance
from canonkeeper.tokens import count_tokens, split_by_tokens


@dataclass
class ExtractStats:
    chunks: int = 0
    claims_raw: int = 0
    claims_kept: int = 0
    dropped_ungrounded: int = 0
    dropped_invalid: int = 0


def _ground(quote: str, text: str) -> tuple[int, int] | None:
    """Return the char span of `quote` in `text`, exact first then fuzzy-window. None if absent."""
    if not quote:
        return None
    i = text.find(quote)
    if i != -1:
        return i, i + len(quote)
    # Fuzzy: scan windows of the same length in steps; accept >= 90 partial ratio.
    n = len(quote)
    step = max(20, n // 4)
    best, best_i = 0.0, -1
    for start in range(0, max(1, len(text) - n), step):
        score = fuzz.ratio(quote, text[start : start + n])
        if score > best:
            best, best_i = score, start
    if best >= 90:
        return best_i, best_i + n
    return None


def _chunks(chapter: Chapter, settings: Settings) -> Iterator[tuple[str, int, str]]:
    pieces = split_by_tokens(chapter.text, settings.extract_chunk_tokens)
    for i, (piece, offset) in enumerate(pieces):
        note = f"(chunk {i + 1} of {len(pieces)})" if len(pieces) > 1 else ""
        yield piece, offset, note


class Extractor:
    def __init__(self, llm: LLM, settings: Settings) -> None:
        self.llm = llm
        self.settings = settings
        self.stats = ExtractStats()

    def extract_chunk(
        self, series_name: str, book: Book, chapter: Chapter, text: str, offset: int, note: str
    ) -> ExtractionResult:
        messages = [
            {"role": "system", "content": prompts.SYSTEM},
            {
                "role": "user",
                "content": prompts.USER_TEMPLATE.format(
                    series=series_name,
                    book_index=book.index + 1,
                    book_title=book.title,
                    chapter_index=chapter.index + 1,
                    chapter_title=chapter.title,
                    chunk_note=note,
                    text=text,
                ),
            },
        ]
        raw = self.llm.complete(self.settings.extract_model, messages, json_mode=True)
        data = parse_json(raw)
        self.stats.chunks += 1

        claims: list[Claim] = []
        for c in data.get("claims", []) or []:
            self.stats.claims_raw += 1
            quote = str(c.get("quote", "")).strip()
            span = _ground(quote, text)
            if span is None:
                self.stats.dropped_ungrounded += 1
                continue
            try:
                kind = ClaimKind(str(c.get("kind", "attribute")).lower())
            except ValueError:
                kind = ClaimKind.ATTRIBUTE
            try:
                claims.append(
                    Claim(
                        kind=kind,
                        subject=str(c["subject"]).strip(),
                        predicate=str(c["predicate"]).strip().lower().replace(" ", "_"),
                        object=str(c["object"]).strip(),
                        story_time=(str(c["story_time"]).strip() if c.get("story_time") else None),
                        confidence=float(c.get("confidence", 0.9)),
                        provenance=Provenance(
                            book=book.key,
                            book_index=book.index,
                            book_title=book.title,
                            chapter=chapter.title,
                            chapter_index=chapter.index,
                            quote=quote[:300],
                            char_start=chapter.char_start + offset + span[0],
                            char_end=chapter.char_start + offset + span[1],
                        ),
                    )
                )
                self.stats.claims_kept += 1
            except (KeyError, ValueError, TypeError):
                self.stats.dropped_invalid += 1
        aliases = {str(k): [str(a) for a in v] for k, v in (data.get("aliases") or {}).items()}
        return ExtractionResult(claims=claims, aliases=aliases)

    def extract_series(self, series: Series, *, progress: bool = True) -> ExtractionResult:
        all_claims: list[Claim] = []
        aliases: dict[str, list[str]] = {}
        total = sum(len(b.chapters) for b in series.books)
        with Progress(disable=not progress) as bar:
            task = bar.add_task("extracting", total=total)
            for book in series.books:
                for chapter in book.chapters:
                    for text, offset, note in _chunks(chapter, self.settings):
                        res = self.extract_chunk(series.name, book, chapter, text, offset, note)
                        all_claims.extend(res.claims)
                        for k, v in res.aliases.items():
                            aliases.setdefault(k, [])
                            aliases[k].extend(a for a in v if a not in aliases[k])
                    bar.advance(task)
        return ExtractionResult(claims=all_claims, aliases=aliases)


def estimate_cost(series: Series, settings: Settings, *, candidates: int = 40) -> dict[str, float]:
    """Rough pre-flight. Extraction reads every token once; adjudication reads the series
    (capped at the context budget) once per *batch*, plus a few hundred output tokens per
    candidate. `candidates` is a guess at how many conflicts the screen will raise."""
    from canonkeeper.llm.client import PRICING

    toks = sum(count_tokens(b.text) for b in series.books)
    overhead = 1.2  # prompt + JSON output overhead
    ex_in, ex_out = PRICING.get(settings.extract_model, (0, 0))
    ad_in, ad_out = PRICING.get(settings.adjudicate_model, (0, 0))
    ctx = min(toks, settings.adjudicate_context_tokens)
    batches = max(1, -(-candidates // max(1, settings.adjudicate_batch_size)))
    per_batch_in = ctx + candidates / batches * 1500
    per_batch_out = candidates / batches * 250
    return {
        "series_tokens": float(toks),
        "context_tokens": float(ctx),
        "extract_usd": toks * overhead / 1e6 * ex_in + toks * 0.08 / 1e6 * ex_out,
        "adjudicate_batches": float(batches),
        "adjudicate_usd": batches * (per_batch_in / 1e6 * ad_in + per_batch_out / 1e6 * ad_out),
    }
