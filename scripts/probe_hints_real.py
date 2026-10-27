"""Precision check for salience hints on real (unplanted) text.

    python scripts/probe_hints_real.py [--chunks 15] [--seed 1]

Picks random chunks of the series, extracts each with hints (new requests) and without (the
build's cached answers, so free), and prints every claim that appears only with hints, with
its quote, for a person to judge. Also reports claims per chunk and ungrounded drops for both.
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

from canonkeeper.config import load_settings
from canonkeeper.corpus import SeriesManifest, load_series
from canonkeeper.extract.extractor import Extractor, _chunks, extraction_passes
from canonkeeper.llm import make_llm


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest", nargs="?", default="evals/corpora/sherlock-holmes/series.json")
    ap.add_argument("--chunks", type=int, default=15)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    settings = load_settings()
    manifest = SeriesManifest.load(a.manifest)
    series = load_series(manifest, settings.cache_dir)
    llm = make_llm(settings)
    pool = [
        (b, ch, text, off, note)
        for b in series.books
        for ch in b.chapters
        for text, off, note in _chunks(ch, settings)
    ]
    random.Random(a.seed).shuffle(pool)
    picked = pool[: a.chunks]

    totals = {False: [0, 0], True: [0, 0]}  # claims kept, ungrounded drops
    only_with: list[str] = []
    for b, ch, text, off, note in picked:
        keys: dict[bool, set[tuple[str, str, str]]] = {}
        claims_by: dict[bool, list] = {}
        for hints in (False, True):
            settings.extract_hints = hints
            ext = Extractor(llm, settings)
            got = []
            for system, effort in extraction_passes(settings):
                res = ext.extract_chunk(
                    series.name, b, ch, text, off, note, system=system, reasoning_effort=effort
                )
                got.extend(res.claims)
            totals[hints][0] += ext.stats.claims_kept
            totals[hints][1] += ext.stats.dropped_ungrounded
            keys[hints] = {(c.subject.lower(), c.predicate, c.object.lower()) for c in got}
            claims_by[hints] = got
        for c in claims_by[True]:
            k = (c.subject.lower(), c.predicate, c.object.lower())
            if k not in keys[False]:
                only_with.append(
                    f"{b.key} · {ch.title[:30]} | {c.subject}.{c.predicate} = {c.object!r} | "
                    f"“{c.provenance.quote[:110]}”"
                )
    n = len(picked)
    print(f"chunks: {n}")
    for hints in (False, True):
        kept, drop = totals[hints]
        label = "with hints" if hints else "without   "
        print(f"{label}: {kept} claims kept ({kept / n:.1f}/chunk), {drop} dropped ungrounded")
    print(f"\nclaims only with hints: {len(only_with)}")
    for line in only_with:
        print("  " + line)
    print(
        f"\ncalls {llm.usage.calls} (cached {llm.usage.cached_calls}) · ${llm.usage.cost_usd:.3f}"
    )
    Path("probe_hints_real.txt").write_text("\n".join(only_with) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
