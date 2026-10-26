"""Measure extraction recall on planted facts without running a full audit.

    python scripts/probe_extraction.py [--n 20] [--seed 0] [--books 1,2,...]

Plants the benchmark's defects into the Holmes series, finds the chunks that contain each
planted sentence, extracts only those chunks (general pass and sweep pass separately), and
reports which injections each pass recovered. ~2 calls per injection; a couple of minutes on
Flash-Lite. Use it to iterate on extraction prompts before paying for a full `eval`.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from rapidfuzz import fuzz

from canonkeeper.canon import CanonStore
from canonkeeper.canon.normalize import normalize_claim
from canonkeeper.config import load_settings
from canonkeeper.corpus import SeriesManifest, load_series
from canonkeeper.eval import inject
from canonkeeper.extract import prompts
from canonkeeper.extract.extractor import Extractor
from canonkeeper.llm import make_llm
from canonkeeper.tokens import split_by_tokens


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest", nargs="?", default="evals/corpora/sherlock-holmes/series.json")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    settings = load_settings()
    manifest = SeriesManifest.load(args.manifest)
    series = load_series(manifest, settings.cache_dir)
    slug = "".join(ch if ch.isalnum() else "-" for ch in manifest.series.lower()).strip("-")
    store = CanonStore.load(settings.workdir / slug / "bible.jsonl")
    if not len(store):
        raise SystemExit("build the bible first (canonkeeper build)")
    iset = inject(series, store, args.n, seed=args.seed)
    llm = make_llm(settings)
    ext = Extractor(llm, settings)

    results: dict[str, dict[str, bool]] = {}
    for inj in iset.injections:
        book = iset.series.book(inj.book)
        chapter = (
            book.chapters[inj.chapter_index] if inj.chapter_index < len(book.chapters) else None
        )
        # Find the chunk containing the sentence (chapter index may have shifted slightly).
        target = None
        for ch in ([chapter] if chapter else []) + list(book.chapters):
            if ch is None:
                continue
            for text, offset in split_by_tokens(ch.text, settings.extract_chunk_tokens):
                if inj.sentence in text:
                    target = (ch, text, offset)
                    break
            if target:
                break
        label = f"{inj.subject}.{inj.predicate} -> {inj.injected}"
        if target is None:
            results[label] = {"general": False, "sweep": False, "found_chunk": False}
            continue
        ch, text, offset = target
        row: dict[str, bool] = {"found_chunk": True}
        for name, system in (("general", prompts.SYSTEM), ("sweep", prompts.SWEEP_SYSTEM)):
            res = ext.extract_chunk(series.name, book, ch, text, offset, "", system=system)
            hit = False
            for c in res.claims:
                pred, val = normalize_claim(c.predicate, c.object)
                subj_ok = fuzz.token_set_ratio(c.subject.lower(), inj.subject.lower()) >= 85
                if inj.predicate == "dead":
                    ok = subj_ok and pred in {"dead", "died"}
                else:
                    ok = (
                        subj_ok
                        and pred == inj.predicate
                        and fuzz.partial_ratio(val, inj.injected.lower()) >= 85
                    )
                if ok:
                    hit = True
                    break
            row[name] = hit
        results[label] = row

    g = sum(r.get("general", False) for r in results.values())
    sw = sum(r.get("sweep", False) for r in results.values())
    either = sum(r.get("general", False) or r.get("sweep", False) for r in results.values())
    print(f"\n{'injection':60s} general sweep")
    for label, r in results.items():
        if not r["found_chunk"]:
            print(f"{label[:60]:60s} (chunk not found)")
            continue
        print(
            f"{label[:60]:60s} {'yes' if r['general'] else ' - ':7s} {'yes' if r['sweep'] else ' - '}"
        )
    n = len(results)
    print(f"\nrecall at extraction: general {g}/{n}  sweep {sw}/{n}  either {either}/{n}")
    print(
        f"calls {llm.usage.calls} · list-price ${llm.usage.cost_usd:.3f} · retries {llm.usage.retries}"
    )
    Path("probe_extraction.txt").write_text(
        "\n".join(f"{k}\t{v}" for k, v in results.items())
        + f"\n\ngeneral {g}/{n} sweep {sw}/{n} either {either}/{n}\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
