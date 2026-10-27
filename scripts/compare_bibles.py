"""Compare two bibles of the same series without any model calls: facts by predicate, and the
candidate conflicts the local screen raises from each (what judging would then see).

    python scripts/compare_bibles.py BEFORE/bible.jsonl AFTER/bible.jsonl [--show 40]
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from canonkeeper.audit import find_candidates
from canonkeeper.canon import CanonStore
from canonkeeper.eval.compare import Key
from canonkeeper.models import Candidate
from canonkeeper.report.display import headline

DESCRIPTIVE = {
    "eye_color", "hair_color", "height", "build", "age", "wound_location", "birthplace",
    "nationality", "first_name", "surname", "nickname", "dead", "died", "married_to",
    "occupation", "residence", "parent_of", "child_of", "sibling_of",
}  # fmt: skip


def _key(c: Candidate) -> Key:
    first = c.claims[0]
    return (
        c.heuristic,
        first.subject,
        first.predicate,
        tuple(sorted({x.object.lower() for x in c.claims})),
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("before", type=Path)
    ap.add_argument("after", type=Path)
    ap.add_argument("--show", type=int, default=40)
    a = ap.parse_args()
    sa, sb = CanonStore.load(a.before), CanonStore.load(a.after)
    pa, pb = Counter(c.predicate for c in sa.claims), Counter(c.predicate for c in sb.claims)
    da = sum(v for k, v in pa.items() if k in DESCRIPTIVE)
    db = sum(v for k, v in pb.items() if k in DESCRIPTIVE)
    print(f"facts: {len(sa)} -> {len(sb)}   descriptive: {da} -> {db}")
    print(f"entities: {len(sa.entities())} -> {len(sb.entities())}")
    print("\npredicate              before  after")
    for p in sorted(set(pa) | set(pb), key=lambda k: -(pa[k] + pb[k]))[:25]:
        mark = "*" if p in DESCRIPTIVE else " "
        print(f"{mark} {p:20s} {pa[p]:7d} {pb[p]:6d}")
    ca = {_key(c): c for c in find_candidates(sa)}
    cb = {_key(c): c for c in find_candidates(sb)}
    new = [c for k, c in cb.items() if k not in ca]
    gone = [c for k, c in ca.items() if k not in cb]
    print(f"\ncandidates: {len(ca)} -> {len(cb)}  (+{len(new)} new, -{len(gone)} gone)")
    for title, rows in (("NEW", new), ("GONE", gone)):
        print(f"\n== {title}")
        for c in rows[: a.show]:
            print(f"- [{c.heuristic}] {headline(c)}")
            for cl in c.claims[:4]:
                print(f"    “{cl.provenance.quote[:100]}”")


if __name__ == "__main__":
    main()
