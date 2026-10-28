"""Rebuild the packaged sample report (`/sample` on Preflight) from a real audit, with the
author's rulings beside the model's and the full list-price cost. No model calls.

    python scripts/build_sample.py .canonkeeper/sherlock-holmes/reports/report.json \\
        --reviews src/canonkeeper/preflight/samples/sherlock-holmes-reviews.json --read-cost 1.40 \\
        --bible .canonkeeper/sherlock-holmes/bible.jsonl

`--read-cost`: what reading the books cost at list price, when the audit ran on a bible built
in an earlier run (the README's figure comes from the request log of the Holmes build).
A review matches a finding by the subject and predicate of its first fact.
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path

from canonkeeper.models import AuditReport, AuthorReview
from canonkeeper.report.render import write_report

SAMPLE = (
    Path(__file__).resolve().parent.parent
    / "src/canonkeeper/preflight/samples/sherlock-holmes-report.html"
)


def apply_reviews(report: AuditReport, reviews: list[dict[str, str]]) -> list[str]:
    """Attach each review to its finding; returns the reviews that matched nothing."""
    unmatched = []
    for rv in reviews:
        hits = [
            f
            for f in report.findings
            if f.candidate.claims[0].subject == rv["subject"]
            and f.candidate.claims[0].predicate == rv["predicate"]
        ]
        for f in hits:
            f.author_review = AuthorReview(verdict=rv["verdict"], note=rv.get("note", ""))  # type: ignore[arg-type]
        if not hits:
            unmatched.append(f"{rv['subject']} · {rv['predicate']}")
    return unmatched


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("report", type=Path)
    ap.add_argument("--reviews", type=Path)
    ap.add_argument("--read-cost", type=float)
    ap.add_argument("--out", type=Path, default=SAMPLE)
    ap.add_argument("--bible", type=Path, help="bible.jsonl: also build the bible explorer sample")
    a = ap.parse_args()
    if a.bible:
        from canonkeeper.canon import CanonStore
        from canonkeeper.report.bible import build_views, write_bible_grid

        store = CanonStore.load(a.bible)
        grid = SAMPLE.with_name("sherlock-holmes-bible-grid.html")
        write_bible_grid(
            build_views(store),
            "Sherlock Holmes",
            grid,
            n_claims=len(store),
            card_view=None,
            csv=None,
        )
        print(f"wrote {grid}")
    report = AuditReport.model_validate_json(a.report.read_text(encoding="utf-8"))
    if a.reviews:
        missing = apply_reviews(report, json.loads(a.reviews.read_text(encoding="utf-8")))
        if missing:
            raise SystemExit(f"reviews that match no finding: {', '.join(missing)}")
    if a.read_cost is not None:
        report.read_cost_usd = a.read_cost
        if report.judge_cost_usd is None:
            report.judge_cost_usd = report.cost_usd
    with tempfile.TemporaryDirectory() as tmp:
        html, _ = write_report(
            report,
            Path(tmp),
            customer=True,
            back_url="/",
            back_label="← Preflight: audit your own series",
            bible_url="/sample/bible",
            store=CanonStore.load(a.bible) if a.bible else None,
        )
        shutil.copyfile(html, a.out)
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
