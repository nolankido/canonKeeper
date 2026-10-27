"""Which extraction requests for a series are already cached, per pass.

    python scripts/cache_coverage.py evals/corpora/sherlock-holmes/series.json

`canonkeeper build --dry-run` gives the total; this says which pass the misses belong to, so a
day's budget can be aimed (the general pass carries most of the recall; the sweep is cheaper
to skip than to half-finish)."""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

from canonkeeper.cli import _load
from canonkeeper.config import load_settings
from canonkeeper.extract import prompts
from canonkeeper.extract.extractor import Extractor, _chunks, extraction_passes
from canonkeeper.llm import make_llm

NAMES = {prompts.SYSTEM: "general", prompts.SWEEP_SYSTEM: "sweep", prompts.MERGED_SYSTEM: "merged"}


def main(manifest: str) -> None:
    s = load_settings()
    llm = make_llm(s)
    _, series = _load(Path(manifest), s)
    ex = Extractor(llm, s)
    counts: Counter[tuple[str, str, str]] = Counter()
    for b in series.books:
        for ch in b.chapters:
            for text, _off, note in _chunks(ch, s):
                for system, effort in extraction_passes(s):
                    msgs = ex.messages_for(series.name, b, ch, text, note, system)
                    hit = getattr(llm, "is_cached", lambda *a, **k: False)(
                        s.extract_model, msgs, json_mode=True, reasoning_effort=effort
                    )
                    counts[(NAMES.get(system, "?"), effort, "cached" if hit else "to send")] += 1
    for (name, effort, state), n in sorted(counts.items()):
        print(f"{name:8} {effort:7} {state:8} {n}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "evals/corpora/sherlock-holmes/series.json")
