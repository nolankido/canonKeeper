"""Extract the one chunk that contains a phrase, and print the claims about a subject.

    python scripts/probe_chunk.py "sent James off to bed" --subject Watson

A two-request check of a prompt change on the exact passage it is meant for (the build's
answers for that chunk are cached, so the current prompt costs nothing to compare against).
"""

from __future__ import annotations

import argparse

from canonkeeper.config import load_settings
from canonkeeper.corpus import SeriesManifest, load_series
from canonkeeper.extract.extractor import Extractor, _chunks, extraction_passes
from canonkeeper.llm import make_llm


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("phrase")
    ap.add_argument("--subject", default="", help="only print claims whose subject contains this")
    ap.add_argument("--manifest", default="evals/corpora/sherlock-holmes/series.json")
    a = ap.parse_args()
    settings = load_settings()
    series = load_series(SeriesManifest.load(a.manifest), settings.cache_dir)
    llm = make_llm(settings)
    ext = Extractor(llm, settings)
    for b in series.books:
        for ch in b.chapters:
            for text, off, note in _chunks(ch, settings):
                if a.phrase not in " ".join(text.split()):
                    continue
                print(f"{b.key} · {ch.title}")
                for system, effort in extraction_passes(settings):
                    res = ext.extract_chunk(
                        series.name, b, ch, text, off, note, system=system, reasoning_effort=effort
                    )
                    for c in res.claims:
                        if a.subject.lower() in c.subject.lower():
                            print(
                                f"  {c.subject}.{c.predicate} = {c.object!r} | {c.provenance.quote[:90]}"
                            )
                print(f"calls {llm.usage.calls} (cached {llm.usage.cached_calls})")
                return
    print("phrase not found")


if __name__ == "__main__":
    main()
