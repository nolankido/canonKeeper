"""Render the series bible as a browsable HTML page: one card per entity, facts grouped by
predicate, every fact linked to its book/chapter/quote, slots with disagreeing values flagged."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape

from canonkeeper.audit.candidates import _NAME_PREDS, SINGLE_VALUED
from canonkeeper.canon import CanonStore
from canonkeeper.models import Claim
from canonkeeper.report.display import chapter, label, value, where

_DISPUTABLE = SINGLE_VALUED | _NAME_PREDS

_env = Environment(
    loader=PackageLoader("canonkeeper.report", "templates"),
    autoescape=select_autoescape(["html"]),
)
_env.filters.update(label=label, shown=value, where=where)


@dataclass
class SlotView:
    predicate: str
    values: list[tuple[str, list[Claim]]]  # value -> supporting claims

    @property
    def disputed(self) -> bool:
        return self.predicate in _DISPUTABLE and len(self.values) > 1


@dataclass
class EntityView:
    name: str
    aliases: list[str]
    n_claims: int
    first_seen: str
    slots: list[SlotView]

    @property
    def disputed(self) -> int:
        return sum(1 for s in self.slots if s.disputed)


def build_views(store: CanonStore, *, min_claims: int = 1) -> list[EntityView]:
    views: list[EntityView] = []
    for name in store.entities():
        claims = store.for_entity(name)
        if len(claims) < min_claims:
            continue
        by_pred: dict[str, dict[str, list[Claim]]] = defaultdict(lambda: defaultdict(list))
        for c in sorted(
            claims, key=lambda c: (c.provenance.book_index, c.provenance.chapter_index)
        ):
            by_pred[c.predicate][c.object].append(c)
        slots = [
            SlotView(pred, sorted(vals.items(), key=lambda kv: -len(kv[1])))
            for pred, vals in sorted(by_pred.items())
        ]
        first = min(claims, key=lambda c: (c.provenance.book_index, c.provenance.chapter_index))
        views.append(
            EntityView(
                name=name,
                aliases=store.aliases.get(name, []),
                n_claims=len(claims),
                first_seen=where(first.provenance),
                slots=slots,
            )
        )
    views.sort(key=lambda v: (-v.n_claims, v.name))
    return views


def write_bible(
    store: CanonStore, series_name: str, out_path: Path, *, min_claims: int = 1
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    views = build_views(store, min_claims=min_claims)
    tmpl = _env.get_template("bible.html")
    write_bible_csv(views, out_path.with_suffix(".csv"))
    write_bible_grid(views, series_name, out_path.with_name("bible-grid.html"), n_claims=len(store))
    out_path.write_text(
        tmpl.render(
            series=series_name,
            entities=views,
            n_claims=len(store),
            n_entities=len(views),
            n_disputed=sum(v.disputed for v in views),
        ),
        encoding="utf-8",
    )
    return out_path


GRID_SCRIPT = (
    "/static/ag-grid-community-36.2.0.min.js"  # served by Preflight; the page falls back to the CDN
)


def bible_rows(views: list[EntityView]) -> list[dict[str, object]]:
    """One row per sourced fact, as the CSV and the grid show it."""
    rows: list[dict[str, object]] = []
    for v in views:
        for slot in v.slots:
            for _, claims in slot.values:
                for c in claims:
                    rows.append(
                        {
                            "entity": v.name,
                            "fact": label(slot.predicate),
                            "value": value(c),
                            "disputed": slot.disputed,
                            "book": c.provenance.book_title or c.provenance.book,
                            "chapter": chapter(c.provenance.chapter),
                            "story_time": c.story_time or "",
                            "quote": c.provenance.quote,
                        }
                    )
    return rows


def write_bible_grid(
    views: list[EntityView],
    series_name: str,
    out_path: Path,
    *,
    n_claims: int,
    card_view: str | None = "bible.html",
    csv: str | None = "bible.csv",
) -> Path:
    """The bible as an AG Grid (Community, MIT): every fact sortable, filterable and searchable,
    disputed facts highlighted, the passage one click away, CSV out of whatever is shown."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows = bible_rows(views)
    out_path.write_text(
        _env.get_template("bible_grid.html").render(
            series=series_name,
            rows=rows,
            # what the grid holds (facts about named entities), so the header and the row
            # count agree; `n_claims` (the whole bible) is kept for the caller's own summary
            n_claims=len(rows),
            n_entities=len(views),
            n_disputed=sum(1 for r in rows if r["disputed"]),
            card_view=card_view,
            csv=csv,
            local_script=GRID_SCRIPT,
        ),
        encoding="utf-8",
    )
    return out_path


def write_bible_csv(views: list[EntityView], path: Path) -> Path:
    """The bible as a spreadsheet, one row per sourced fact: what an author keeps today by
    hand, filled in with the passage behind every row. UTF-8 with a BOM so Excel reads accents."""
    import csv

    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(
            ["entity", "fact", "value", "disputed", "book", "chapter", "story time", "quote"]
        )
        for r in bible_rows(views):
            w.writerow(
                [
                    r["entity"],
                    r["fact"],
                    r["value"],
                    "yes" if r["disputed"] else "",
                    r["book"],
                    r["chapter"],
                    r["story_time"],
                    r["quote"],
                ]
            )
    return path
