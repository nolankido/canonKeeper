"""Chapter → claims, with a quote-grounding guard."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from rapidfuzz import fuzz
from rich.progress import Progress

from canonkeeper.config import Settings
from canonkeeper.corpus import Book, Chapter, Series
from canonkeeper.extract import prompts
from canonkeeper.llm import LLM
from canonkeeper.llm.client import parse_json, repair_json
from canonkeeper.models import Claim, ClaimKind, ExtractionResult, Provenance
from canonkeeper.tokens import count_tokens, split_by_tokens


@dataclass
class ExtractStats:
    chunks: int = 0
    claims_raw: int = 0
    claims_kept: int = 0
    dropped_ungrounded: int = 0
    dropped_invalid: int = 0
    json_repaired: int = 0
    json_failed: int = 0


_ELLIPSIS = re.compile(r"\s*(?:\[\s*\.\.\.\s*\]|\.\.\.|…)\s*")
_WS = re.compile(r"\s+")


def _collapse(text: str) -> tuple[str, list[int]]:
    """Whitespace-collapsed copy of `text` plus a map from collapsed index → original index.
    Gutenberg hard-wraps lines, so a quote that is verbatim in meaning still differs in
    whitespace; matching on the collapsed form keeps the guard strict about words while
    ignoring line breaks."""
    out: list[str] = []
    idx: list[int] = []
    in_ws = False
    for i, ch in enumerate(text):
        if ch.isspace():
            if not in_ws and out:
                out.append(" ")
                idx.append(i)
            in_ws = True
        else:
            out.append(ch)
            idx.append(i)
            in_ws = False
    if out and out[-1] == " ":
        out.pop()
        idx.pop()
    return "".join(out), idx


_STANDARD_KEYS = {"kind", "subject", "predicate", "object", "story_time", "confidence", "quote"}
_KINDS = {k.value for k in ClaimKind}


def repair_claim(c: dict) -> dict | None:
    """Models sometimes emit the predicate as the key: {"kind": "possession", "subject": …,
    "possession": "gold watch"}. Recover predicate/object from the one unexpected key, and
    when `kind` is really a predicate name, use it. Returns None if unrecoverable."""
    c = dict(c)
    if "predicate" not in c or "object" not in c:
        extra = [k for k in c if k not in _STANDARD_KEYS]
        if len(extra) == 1:
            c.setdefault("predicate", extra[0])
            c.setdefault("object", c[extra[0]])
        elif c.get("kind") and str(c["kind"]).lower() not in _KINDS and "predicate" not in c:
            # e.g. {"kind": "occupation", "object": "cabman"}
            c["predicate"] = str(c["kind"])
    if str(c.get("kind", "")).lower() not in _KINDS:
        c["kind"] = "attribute"
    if "subject" not in c or "predicate" not in c:
        return None
    if c.get("object") is None or str(c.get("object")).strip().lower() in {"", "none", "null"}:
        if str(c["predicate"]).lower() in {
            "dead",
            "alive",
            "died",
            "married",
            "injured",
            "imprisoned",
        }:
            c["object"] = "true"
        else:
            return None
    return c


def extraction_passes(settings: Settings) -> list[tuple[str, str]]:
    """(system prompt, reasoning effort) per call, by `extract_mode`."""
    mode = settings.extract_mode
    if mode == "merged":
        return [(prompts.MERGED_SYSTEM, settings.extract_reasoning_effort)]
    if mode == "general":
        return [(prompts.SYSTEM, settings.extract_reasoning_effort)]
    return [
        (prompts.SYSTEM, settings.extract_reasoning_effort),
        (prompts.SWEEP_SYSTEM, settings.extract_sweep_reasoning_effort),
    ]


class Grounder:
    """Locates verbatim quotes in a chunk. One instance per chunk; the collapsed text is
    computed once."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.collapsed, self._idx = _collapse(text)

    def _span(self, start: int, length: int) -> tuple[int, int]:
        return self._idx[start], self._idx[start + length - 1] + 1

    def find(self, quote: str) -> tuple[int, int, str] | None:
        """Return (char_start, char_end, verbatim_quote) or None.

        Order: exact → whitespace-collapsed exact → longest segment of an ellipsis-spliced
        quote → fuzzy window (ratio ≥ 92) on the collapsed text. The returned quote is always
        text that actually appears in the chunk (modulo line wrapping)."""
        q = quote.strip()
        if not q:
            return None
        i = self.text.find(q)
        if i != -1:
            return i, i + len(q), q
        cq = _WS.sub(" ", q)
        j = self.collapsed.find(cq)
        if j != -1:
            a, b = self._span(j, len(cq))
            return a, b, cq
        # Spliced quote ("…" / "[...]"): ground the longest segment we can.
        segs = [x.strip() for x in _ELLIPSIS.split(cq) if len(x.strip()) >= 12]
        for seg in sorted(segs, key=len, reverse=True):
            k = self.collapsed.find(seg)
            if k != -1:
                a, b = self._span(k, len(seg))
                return a, b, seg
        # Fuzzy: best local alignment of the quote inside the collapsed text. Accepting a
        # near miss is safe because the stored quote is the text at the aligned span, not the
        # model's version of it.
        if len(cq) < 20:
            return None
        al = fuzz.partial_ratio_alignment(cq, self.collapsed)
        if al is not None and al.score >= 88 and al.dest_end > al.dest_start:
            a, b = self._span(al.dest_start, al.dest_end - al.dest_start)
            return a, b, self.collapsed[al.dest_start : al.dest_end]
        return None


def _ground(quote: str, text: str) -> tuple[int, int] | None:
    """Back-compat shim: span only."""
    hit = Grounder(text).find(quote)
    return (hit[0], hit[1]) if hit else None


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

    @staticmethod
    def messages_for(
        series_name: str,
        book: Book,
        chapter: Chapter,
        text: str,
        note: str,
        system: str,
        *,
        hints: bool = False,
    ) -> list[dict[str, str]]:
        user = prompts.USER_TEMPLATE.format(
            series=series_name,
            book_index=book.index + 1,
            book_title=book.title,
            chapter_index=chapter.index + 1,
            chapter_title=chapter.title,
            chunk_note=note,
            text=text,
        )
        if hints:
            from canonkeeper.extract.salience import hints_block

            user += hints_block(text)
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def _hints(self, system: str) -> bool:
        return self.settings.hints_for(sweep_pass=system == prompts.SWEEP_SYSTEM)

    def plan(self, series: Series) -> tuple[int, int]:
        """(requests this extraction would make, how many of them are already cached)."""
        total = cached = 0
        checker = getattr(self.llm, "is_cached", None)
        for book in series.books:
            for chapter in book.chapters:
                for text, _offset, note in _chunks(chapter, self.settings):
                    for system, effort in extraction_passes(self.settings):
                        total += 1
                        if checker and checker(
                            self.settings.extract_model,
                            self.messages_for(
                                series.name,
                                book,
                                chapter,
                                text,
                                note,
                                system,
                                hints=self._hints(system),
                            ),
                            json_mode=True,
                            reasoning_effort=effort,
                        ):
                            cached += 1
        return total, cached

    def extract_chunk(
        self,
        series_name: str,
        book: Book,
        chapter: Chapter,
        text: str,
        offset: int,
        note: str,
        *,
        system: str = prompts.SYSTEM,
        reasoning_effort: str | None = None,
    ) -> ExtractionResult:
        messages = self.messages_for(
            series_name, book, chapter, text, note, system, hints=self._hints(system)
        )
        raw = self.llm.complete(
            self.settings.extract_model,
            messages,
            json_mode=True,
            reasoning_effort=reasoning_effort or self.settings.extract_reasoning_effort,
        )
        try:
            data = parse_json(raw)
        except ValueError:
            data = repair_json(self.llm, self.settings.extract_model, raw)
            if data is None:
                self.stats.json_failed += 1
                self.stats.chunks += 1
                return ExtractionResult(claims=[], aliases={})
            self.stats.json_repaired += 1
        if not isinstance(data, dict):
            data = {}
        self.stats.chunks += 1

        claims: list[Claim] = []
        grounder = Grounder(text)
        for raw_claim in data.get("claims", []) or []:
            self.stats.claims_raw += 1
            c = repair_claim(raw_claim) if isinstance(raw_claim, dict) else None
            if c is None:
                self.stats.dropped_invalid += 1
                continue
            hit = grounder.find(str(c.get("quote", "")))
            if hit is None:
                self.stats.dropped_ungrounded += 1
                continue
            span = (hit[0], hit[1])
            quote = hit[2]
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

    def extract_series(
        self,
        series: Series,
        *,
        progress: bool = True,
        on_progress: Callable[[str, int, int], None] | None = None,
    ) -> ExtractionResult:
        """`on_progress(stage, done, total)` is called after each chapter (a job page uses it)."""
        all_claims: list[Claim] = []
        aliases: dict[str, list[str]] = {}
        total = sum(len(b.chapters) for b in series.books)
        done = 0
        if on_progress:
            on_progress("extracting", 0, total)
        with Progress(disable=not progress) as bar:
            task = bar.add_task("extracting", total=total)
            for book in series.books:
                for chapter in book.chapters:
                    for text, offset, note in _chunks(chapter, self.settings):
                        passes = extraction_passes(self.settings)
                        for system, effort in passes:
                            res = self.extract_chunk(
                                series.name,
                                book,
                                chapter,
                                text,
                                offset,
                                note,
                                system=system,
                                reasoning_effort=effort,
                            )
                            all_claims.extend(res.claims)
                            for k, v in res.aliases.items():
                                aliases.setdefault(k, [])
                                aliases[k].extend(a for a in v if a not in aliases[k])
                    bar.advance(task)
                    done += 1
                    if on_progress:
                        on_progress("extracting", done, total)
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
