"""canonkeeper command line.

canonkeeper fetch   series.json              # download / cache the books, show chapter stats
canonkeeper build   series.json              # extract the bible (Muse Spark)
canonkeeper audit   series.json              # find + adjudicate contradictions (Muse Wonder)
canonkeeper check   series.json new.md       # audit one new manuscript against the bible
canonkeeper eval    series.json --n 20       # seeded-defect benchmark → precision / recall
canonkeeper estimate series.json             # token + dollar pre-flight
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from canonkeeper import __version__
from canonkeeper.config import Settings, load_settings
from canonkeeper.corpus import Series, SeriesManifest, load_series
from canonkeeper.corpus.chapterize import chapterize
from canonkeeper.corpus.loaders import Book, load_text
from canonkeeper.llm import make_llm
from canonkeeper.models import AuditReport

app = typer.Typer(add_completion=False, no_args_is_help=True, help=__doc__)
console = Console()

ManifestArg = Annotated[Path, typer.Argument(help="Path to series.json manifest")]


def _settings(provider: str | None, workdir: Path | None) -> Settings:
    overrides: dict[str, object] = {}
    if provider:
        overrides["provider"] = provider
    if workdir:
        overrides["workdir"] = workdir
        overrides["cache_dir"] = workdir / "cache"
    return load_settings(**overrides)


def _paths(settings: Settings, manifest: SeriesManifest) -> tuple[Path, Path]:
    slug = "".join(ch if ch.isalnum() else "-" for ch in manifest.series.lower()).strip("-")
    base = settings.workdir / slug
    return base / "bible.jsonl", base / "reports"


def _load(manifest_path: Path, settings: Settings) -> tuple[SeriesManifest, Series]:
    manifest = SeriesManifest.load(manifest_path)
    series = load_series(manifest, settings.cache_dir)
    return manifest, series


def _version(value: bool) -> None:
    if value:
        console.print(f"canonkeeper {__version__}")
        raise typer.Exit()


@app.callback()
def _main(
    version: bool = typer.Option(False, "--version", callback=_version, is_eager=True),
) -> None:
    """Series-continuity engine: give it the whole series, it finds the contradictions."""


@app.command()
def fetch(manifest: ManifestArg, workdir: Path | None = None) -> None:
    """Download (or load) every book and show chapter statistics."""
    settings = _settings(None, workdir)
    _, series = _load(manifest, settings)
    t = Table(title=series.name)
    t.add_column("#", justify="right")
    t.add_column("book")
    t.add_column("chapters", justify="right")
    t.add_column("words", justify="right")
    for b in series.books:
        t.add_row(str(b.index + 1), b.title, str(len(b.chapters)), f"{b.words:,}")
    console.print(t)
    console.print(f"[dim]{series.words:,} words total[/dim]")


@app.command()
def estimate(manifest: ManifestArg, workdir: Path | None = None) -> None:
    """Token count and approximate cost for one build + one full-context audit."""
    from canonkeeper.extract import estimate_cost

    settings = _settings(None, workdir)
    _, series = _load(manifest, settings)
    est = estimate_cost(series, settings)
    console.print(f"series tokens:            {int(est['series_tokens']):,}")
    console.print(
        f"extraction pass:          ${est['extract_usd']:.2f}  ({settings.extract_model})"
    )
    console.print(
        f"one full-context verdict: ${est['adjudicate_full_pass_usd']:.2f}  ({settings.adjudicate_model})"
    )


@app.command()
def build(manifest: ManifestArg, provider: str | None = None, workdir: Path | None = None) -> None:
    """Extract claims from every chapter into the series bible."""
    from canonkeeper.pipeline import build_bible

    settings = _settings(provider, workdir)
    m, series = _load(manifest, settings)
    bible_path, _ = _paths(settings, m)
    llm = make_llm(settings)
    store = build_bible(series, llm, settings, bible_path)
    console.print(
        f"[green]bible:[/green] {bible_path}  ({len(store)} claims, {len(store.entities())} entities)"
    )
    console.print(
        f"[dim]cost ${llm.usage.cost_usd:.3f} · {llm.usage.calls} calls ({llm.usage.cached_calls} cached)[/dim]"
    )


@app.command()
def audit(
    manifest: ManifestArg,
    provider: str | None = None,
    workdir: Path | None = None,
    open_report: bool = False,
) -> None:
    """Screen the bible for conflicts and adjudicate them against the source text."""
    from canonkeeper.canon import CanonStore
    from canonkeeper.pipeline import audit as run_audit
    from canonkeeper.report import write_report

    settings = _settings(provider, workdir)
    m, series = _load(manifest, settings)
    bible_path, report_dir = _paths(settings, m)
    if not bible_path.exists():
        console.print("[red]no bible yet — run `canonkeeper build` first[/red]")
        raise typer.Exit(2)
    store = CanonStore.load(bible_path)
    llm = make_llm(settings)
    report = run_audit(series, store, llm, settings)
    html, _ = write_report(report, report_dir)
    _print_summary(report)
    console.print(f"[green]report:[/green] {html}")
    if open_report:
        typer.launch(str(html))


@app.command()
def check(
    manifest: ManifestArg,
    manuscript: Annotated[
        Path, typer.Argument(help="New .txt/.md manuscript to check against the bible")
    ],
    provider: str | None = None,
    workdir: Path | None = None,
) -> None:
    """Audit a new manuscript (next book in the series) against the existing bible."""
    from canonkeeper.canon import CanonStore
    from canonkeeper.extract import Extractor
    from canonkeeper.pipeline import audit as run_audit
    from canonkeeper.report import write_report

    settings = _settings(provider, workdir)
    m, series = _load(manifest, settings)
    bible_path, report_dir = _paths(settings, m)
    if not bible_path.exists():
        console.print("[red]no bible yet — run `canonkeeper build` first[/red]")
        raise typer.Exit(2)
    text = load_text(manuscript)
    new_book = Book(
        key=f"{len(series.books) + 1:02d}-{manuscript.stem}",
        title=manuscript.stem,
        index=len(series.books),
        text=text,
        chapters=chapterize(text),
    )
    extended = Series(series.name, [*series.books, new_book])

    store = CanonStore.load(bible_path)
    llm = make_llm(settings)
    # Extract only the new book into a scratch copy of the store; the published bible is not modified.
    scratch = CanonStore(bible_path.with_suffix(".check.jsonl"))
    for c in store.claims:
        scratch.add(c)
    scratch.register_aliases(store.aliases)
    scratch.ingest(Extractor(llm, settings).extract_series(Series(series.name, [new_book])))
    report = run_audit(extended, scratch, llm, settings)
    # Only findings that touch the new book are interesting here.
    report.findings = [
        f
        for f in report.findings
        if any(c.provenance.book == new_book.key for c in f.candidate.claims)
    ]
    html, _ = write_report(report, report_dir / "check")
    _print_summary(report)
    console.print(f"[green]report:[/green] {html}")


@app.command("eval")
def eval_cmd(
    manifest: ManifestArg,
    n: int = typer.Option(20, help="Number of defects to inject"),
    seed: int = 0,
    provider: str | None = None,
    workdir: Path | None = None,
) -> None:
    """Seeded-defect benchmark: inject N contradictions, run the full pipeline, report P/R/F1."""
    from canonkeeper.canon import CanonStore
    from canonkeeper.eval import inject, score
    from canonkeeper.eval.score import finding_key
    from canonkeeper.pipeline import audit as run_audit
    from canonkeeper.pipeline import build_bible

    settings = _settings(provider, workdir)
    m, series = _load(manifest, settings)
    bible_path, report_dir = _paths(settings, m)
    if not bible_path.exists():
        console.print("[red]no bible yet — run `canonkeeper build` first[/red]")
        raise typer.Exit(2)
    clean_store = CanonStore.load(bible_path)
    llm = make_llm(settings)

    console.rule("baseline (clean series)")
    baseline = run_audit(series, clean_store, llm, settings)
    baseline_ids = {finding_key(f) for f in baseline.errors}
    console.print(f"pre-existing contradictions in source: {len(baseline_ids)}")

    console.rule(f"injecting {n} defects (seed={seed})")
    iset = inject(series, clean_store, n, seed=seed)
    for inj in iset.injections:
        console.print(
            f"  • {inj.subject}.{inj.predicate}: {inj.original!r} → {inj.injected!r} in {inj.book}"
        )

    eval_dir = report_dir / f"eval-seed{seed}"
    eval_dir.mkdir(parents=True, exist_ok=True)
    mutated_store = build_bible(iset.series, llm, settings, eval_dir / "bible.jsonl", progress=True)
    report = run_audit(iset.series, mutated_store, llm, settings)
    s = score(iset.injections, report.findings, baseline_ids=baseline_ids)

    console.rule("result")
    console.print(s.markdown())
    for inj in s.missed:
        console.print(
            f"[yellow]missed[/yellow] {inj.subject}.{inj.predicate} → {inj.injected!r} ({inj.book})"
        )
    for f in s.extra:
        console.print(f"[red]false positive[/red] {f.candidate.reason}")
    (eval_dir / "result.md").write_text(s.markdown() + "\n", encoding="utf-8")
    console.print(f"[dim]cost ${llm.usage.cost_usd:.3f}[/dim]")


def _print_summary(report: AuditReport) -> None:
    t = Table(show_header=True)
    t.add_column("verdict")
    t.add_column("count", justify="right")
    for v in ("contradiction", "deliberate", "ambiguous", "not_conflict"):
        t.add_row(v, str(sum(1 for f in report.findings if f.verdict == v)))
    console.print(t)
    for f in report.errors[:10]:
        console.print(f"[bold red]{f.severity.upper()}[/bold red] {f.candidate.reason}")
        console.print(f"   {f.explanation}")


if __name__ == "__main__":
    app()
