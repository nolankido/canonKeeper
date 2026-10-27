"""End-to-end orchestration used by the CLI and the eval harness."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from canonkeeper.audit import Adjudicator, find_candidates
from canonkeeper.canon import CanonStore
from canonkeeper.config import Settings
from canonkeeper.corpus import Series
from canonkeeper.extract import Extractor
from canonkeeper.llm import LLM
from canonkeeper.models import AuditReport


def _answered_since(usage: object, before: dict[str, int]) -> str:
    now: dict[str, int] = dict(getattr(usage, "answered_by", {}) or {})
    diff = {m: n - before.get(m, 0) for m, n in now.items() if n - before.get(m, 0) > 0}
    return ", ".join(f"{m} ×{n}" for m, n in sorted(diff.items(), key=lambda kv: -kv[1])) or "-"


def build_bible(
    series: Series,
    llm: LLM,
    settings: Settings,
    store_path: Path,
    *,
    progress: bool = True,
    on_progress: Callable[[str, int, int], None] | None = None,
) -> CanonStore:
    """Extract `series` into the bible at `store_path`.

    The books in `series` are re-ingested from scratch (extraction is cached, so this costs no
    requests) and replace whatever the bible held for them; claims for books *not* in this run
    (`build --books`) are kept. Appending instead would duplicate every claim whose
    normalisation changed between runs — claim ids hash the normalised fact."""
    old = CanonStore.load(store_path)
    rebuilt = {b.key for b in series.books}
    store = CanonStore(store_path)
    store.register_aliases(old.aliases)
    for c in old.claims:
        if c.provenance.book not in rebuilt:
            store.add(c)
    ext = Extractor(llm, settings)
    result = ext.extract_series(series, progress=progress, on_progress=on_progress)
    store.ingest(result)
    store.save()
    store.last_extract_stats = ext.stats
    return store


def audit(
    series: Series,
    store: CanonStore,
    llm: LLM,
    settings: Settings,
    *,
    progress: bool = True,
    on_progress: Callable[[str, int, int], None] | None = None,
    only_book: str | None = None,
) -> AuditReport:
    """Screen the bible and judge every candidate; with `only_book`, judge only candidates
    that involve that book (a draft check must not pay to re-judge the published books)."""
    candidates = find_candidates(store)
    if only_book is not None:
        candidates = [
            c for c in candidates if any(cl.provenance.book == only_book for cl in c.claims)
        ]
    usage = getattr(llm, "usage", None)
    before = dict(usage.answered_by) if usage is not None else {}
    cost_before = float(getattr(usage, "cost_usd", 0.0) or 0.0)
    adj = Adjudicator(llm, settings, series)
    findings = adj.judge_all(
        candidates, store, series.name, progress=progress, on_progress=on_progress
    )
    return AuditReport(
        series=series.name,
        books=[b.key for b in series.books],
        claims_total=len(store),
        candidates_total=len(candidates),
        findings=findings,
        models={
            "extract": settings.extract_model,
            "adjudicate": settings.adjudicate_model,
            "provider": settings.provider,
            # Which models really judged, after fallbacks (a free-tier run can end up on a
            # smaller model when the configured one is overloaded or out of daily budget).
            "adjudicated_by": _answered_since(usage, before),
        },
        cost_usd=None if settings.provider == "mock" else round(llm.usage.cost_usd, 4),
        # Reading cost is known only when this run read the books (a Preflight job does both);
        # an audit of a bible built earlier leaves it None and the report says so.
        read_cost_usd=(
            round(cost_before, 4) if settings.provider != "mock" and cost_before > 0 else None
        ),
        judge_cost_usd=(
            None if settings.provider == "mock" else round(llm.usage.cost_usd - cost_before, 4)
        ),
    )


def check_manuscript(
    series: Series,
    store: CanonStore,
    manuscript: Path,
    llm: LLM,
    settings: Settings,
    *,
    progress: bool = True,
    on_progress: Callable[[str, int, int], None] | None = None,
    title: str | None = None,
) -> AuditReport:
    """Audit a new, unpublished manuscript against the existing bible.

    The new book is extracted into a scratch copy of the store, so the published bible is never
    modified by a draft. Only findings that touch the new book are returned: pre-existing
    contradictions between published books are the `audit` command's business, not the draft's.
    """
    from canonkeeper.corpus.chapterize import chapterize
    from canonkeeper.corpus.loaders import Book, load_text

    text = load_text(manuscript)
    new_book = Book(
        key=f"{len(series.books) + 1:02d}-{manuscript.stem}",
        title=title or manuscript.stem,
        index=len(series.books),
        text=text,
        chapters=chapterize(text),
    )
    extended = Series(series.name, [*series.books, new_book])

    scratch = CanonStore(store.path.with_suffix(".check.jsonl"))
    scratch.register_aliases(store.aliases)
    for c in store.claims:
        scratch.add(c)
    ext = Extractor(llm, settings)
    scratch.ingest(
        ext.extract_series(
            Series(series.name, [new_book]), progress=progress, on_progress=on_progress
        )
    )

    report = audit(
        extended,
        scratch,
        llm,
        settings,
        progress=progress,
        on_progress=on_progress,
        only_book=new_book.key,
    )
    report.books = [*report.books[:-1], f"{new_book.key} (draft)"]
    return report
