"""End-to-end orchestration used by the CLI and the eval harness."""

from __future__ import annotations

from pathlib import Path

from canonkeeper.audit import Adjudicator, find_candidates
from canonkeeper.canon import CanonStore
from canonkeeper.config import Settings
from canonkeeper.corpus import Series
from canonkeeper.extract import Extractor
from canonkeeper.llm import LLM
from canonkeeper.models import AuditReport


def build_bible(
    series: Series, llm: LLM, settings: Settings, store_path: Path, *, progress: bool = True
) -> CanonStore:
    store = CanonStore.load(store_path)
    ext = Extractor(llm, settings)
    result = ext.extract_series(series, progress=progress)
    store.ingest(result)
    store.save()
    return store


def audit(
    series: Series, store: CanonStore, llm: LLM, settings: Settings, *, progress: bool = True
) -> AuditReport:
    candidates = find_candidates(store)
    adj = Adjudicator(llm, settings, series)
    findings = adj.judge_all(candidates, store, series.name, progress=progress)
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
        },
        cost_usd=None if settings.provider == "mock" else round(llm.usage.cost_usd, 4),
    )
