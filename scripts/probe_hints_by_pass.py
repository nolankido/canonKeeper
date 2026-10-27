"""Which pass do salience hints help or hurt on real text? Splits probe_hints_real.py's totals
by extraction pass and scores the "hints on the sweep pass only" configuration.

    python scripts/probe_hints_by_pass.py [--chunks 15] [--seed 1] [--allow-requests]

Uses the same random chunks as probe_hints_real.py (same seed), so after that script has run
every response is cached and this costs no requests; without --allow-requests it stops
before making an uncached one.
"""

from __future__ import annotations

import argparse
import random
from collections import Counter

from canonkeeper.config import load_settings
from canonkeeper.corpus import SeriesManifest, load_series
from canonkeeper.extract.extractor import Extractor, _chunks, extraction_passes
from canonkeeper.llm import make_llm

DESCRIPTIVE = {
    "eye_color", "hair_color", "height", "build", "age", "wound_location", "birthplace",
    "nationality", "first_name", "surname", "nickname", "dead", "died", "married_to",
    "occupation", "residence", "parent_of", "child_of", "sibling_of",
}  # fmt: skip


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest", nargs="?", default="evals/corpora/sherlock-holmes/series.json")
    ap.add_argument("--chunks", type=int, default=15)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--allow-requests", action="store_true")
    a = ap.parse_args()
    settings = load_settings()
    series = load_series(SeriesManifest.load(a.manifest), settings.cache_dir)
    llm = make_llm(settings)
    pool = [
        (b, ch, text, off, note)
        for b in series.books
        for ch in b.chapters
        for text, off, note in _chunks(ch, settings)
    ]
    random.Random(a.seed).shuffle(pool)
    picked = pool[: a.chunks]
    passes = extraction_passes(settings)  # [(general system, effort), (sweep system, effort)]

    # claims[(pass_index, hints)] -> list of (subject, predicate, object, quote, where)
    claims: dict[tuple[int, bool], list[tuple[str, str, str, str, str]]] = {}
    for hints in (False, True):
        settings.extract_hints = hints
        ext = Extractor(llm, settings)
        for i, (system, effort) in enumerate(passes):
            got = []
            for b, ch, text, off, note in picked:
                msgs = ext.messages_for(series.name, b, ch, text, note, system, hints=hints)
                if not a.allow_requests and not llm.is_cached(  # type: ignore[attr-defined]
                    settings.extract_model, msgs, json_mode=True, reasoning_effort=effort
                ):
                    raise SystemExit(
                        f"not cached: pass {i} hints={hints} {b.key}/{ch.title}; "
                        "run probe_hints_real.py first or pass --allow-requests"
                    )
                res = ext.extract_chunk(
                    series.name, b, ch, text, off, note, system=system, reasoning_effort=effort
                )
                where = f"{b.key} · {ch.title[:28]}"
                got += [
                    (c.subject, c.predicate, c.object, c.provenance.quote, where)
                    for c in res.claims
                ]
            claims[(i, hints)] = got

    def key(c: tuple[str, str, str, str, str]) -> tuple[str, str, str]:
        return (c[0].lower(), c[1], c[2].lower())

    def config(general_hints: bool, sweep_hints: bool) -> dict[tuple[str, str, str], tuple]:
        out: dict[tuple[str, str, str], tuple] = {}
        for c in claims[(0, general_hints)] + claims[(1, sweep_hints)]:
            out.setdefault(key(c), c)
        return out

    configs = {
        "no hints (current)": config(False, False),
        "hints on sweep only": config(False, True),
        "hints on both": config(True, True),
    }
    print(f"chunks: {len(picked)}   requests made: {llm.usage.calls - llm.usage.cached_calls}")
    print(f"\n{'pass':10s} {'hints':6s} {'facts':>6s} {'descriptive':>12s}")
    for (i, hints), cs in sorted(claims.items()):
        name = ["general", "sweep"][i]
        d = sum(c[1] in DESCRIPTIVE for c in cs)
        print(f"{name:10s} {str(hints):6s} {len({key(c) for c in cs}):6d} {d:12d}")
    print(f"\n{'configuration':22s} {'facts':>6s} {'descriptive':>12s}")
    for name, cs in configs.items():
        d = sum(c[1] in DESCRIPTIVE for c in cs.values())
        print(f"{name:22s} {len(cs):6d} {d:12d}")

    base, sweep_only = configs["no hints (current)"], configs["hints on sweep only"]
    gained = [c for k, c in sweep_only.items() if k not in base]
    lost = [c for k, c in base.items() if k not in sweep_only]
    print(f"\nsweep-only vs current: +{len(gained)} facts, -{len(lost)} facts")
    print("predicates gained:", Counter(c[1] for c in gained).most_common(8))
    print("predicates lost:  ", Counter(c[1] for c in lost).most_common(8))
    with open("probe_hints_by_pass.txt", "w", encoding="utf-8") as fh:
        for tag, rows in (("GAINED", gained), ("LOST", lost)):
            fh.write(f"== {tag} ({len(rows)})\n")
            for s, p, o, q, w in rows:
                fh.write(f"{w} | {s}.{p} = {o!r} | “{q[:110]}”\n")
    print("\nwrote probe_hints_by_pass.txt (every gained and lost fact with its quote)")


if __name__ == "__main__":
    main()
