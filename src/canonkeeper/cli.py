"""canonkeeper command line.

canonkeeper demo                             # no key needed: invented series, full pipeline
canonkeeper fetch    series.json             # download / cache the books, show chapter stats
canonkeeper estimate series.json             # token + dollar pre-flight
canonkeeper build    series.json             # extract the bible (extraction model)
canonkeeper bible    series.json             # browsable HTML of the bible
canonkeeper audit    series.json             # find + adjudicate contradictions (adjudication model)
canonkeeper check    series.json new.md      # audit one new manuscript against the bible
canonkeeper eval     series.json --n 20      # seeded-defect benchmark → precision / recall
canonkeeper serve                            # Preflight web app: quote → PayPal → audit → report
canonkeeper mcp                              # Preflight as an MCP server for an author's assistant
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from canonkeeper import __version__
from canonkeeper.config import Settings, load_settings
from canonkeeper.corpus import Series, SeriesManifest, load_series
from canonkeeper.llm import make_llm
from canonkeeper.models import AuditReport

app = typer.Typer(add_completion=False, no_args_is_help=True, help=__doc__)


def _today_usage(settings: Settings) -> dict[str, int]:
    """Successful requests per model today (provider-local day ≈ Pacific for Gemini), from the
    request log. Free tiers cap requests per day per model, so this is the number that matters."""
    import json
    from datetime import UTC, datetime, timedelta

    log = settings.request_log
    if not log.exists():
        return {}
    # Gemini's day boundary is midnight Pacific (UTC-7/-8); use UTC-8 as the conservative one.
    today = (datetime.now(UTC) - timedelta(hours=8)).date().isoformat()
    counts: dict[str, int] = {}
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("ok") and d.get("ts", "").startswith(today) and "latency_s" in d:
            counts[d["model"]] = counts.get(d["model"], 0) + 1
    return counts


def _utf8_stdio() -> None:
    """Windows consoles default to a legacy code page; make stdout/stderr UTF-8 so Rich output
    (arrows, middle dots, curly quotes in quoted passages) never raises UnicodeEncodeError."""
    import contextlib
    import sys

    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]


_utf8_stdio()
console = Console()
_DEMO_DEST = typer.Option(Path(".canonkeeper/demo"), help="Where to write the demo series")

ManifestArg = Annotated[Path, typer.Argument(help="Path to series.json manifest")]


def _run_or_explain(fn, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
    """Run a pipeline step; on a spent daily quota print what to do instead of a traceback."""
    import openai

    from canonkeeper.llm.client import DailyQuotaExhausted

    try:
        return fn(*args, **kwargs)
    except DailyQuotaExhausted as e:
        console.print(f"[yellow]stopped:[/yellow] {e}")
        raise typer.Exit(3) from None
    except (openai.APIStatusError, openai.APIConnectionError) as e:
        status = getattr(e, "status_code", None)
        what = f"HTTP {status}" if status else type(e).__name__
        console.print(
            f"[yellow]stopped:[/yellow] the provider kept failing ({what}) after every retry and "
            "fallback model. Nothing is lost — every answer so far is cached — so rerun the same "
            "command in a few minutes to resume. Details in <workdir>/requests.jsonl."
        )
        raise typer.Exit(2) from None


def _llm(settings: Settings, *, sends_text: bool = True):  # noqa: ANN202
    """Build the LLM and, when the provider may train on prompts, say so once per command."""
    llm = make_llm(settings)
    if sends_text and settings.trains_on_prompts:
        console.print(
            "[yellow]note:[/yellow] the Gemini free tier may use prompts to improve Google's "
            "products. Fine for public-domain corpora; do not send unpublished manuscripts "
            "through it. Use a paid tier or --provider openweights (Ollama / LM Studio) for those."
        )
    return llm


def _settings(provider: str | None, workdir: Path | None) -> Settings:
    overrides: dict[str, object] = {}
    if provider:
        overrides["provider"] = provider
    if workdir:
        overrides["workdir"] = workdir
        if not os.environ.get("CANONKEEPER_CACHE_DIR"):  # an explicit cache wins: reuse it
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
def doctor(
    provider: str | None = None,
    workdir: Path | None = None,
    paypal: bool = typer.Option(
        False, "--paypal", help="Also check PayPal: OAuth token and a test order (never captured)"
    ),
    round_trip: bool = typer.Option(
        False,
        "--round-trip",
        help="Sandbox only: a $1.00 order you approve as the sandbox buyer, then capture, "
        "verify and refund it — the whole money path against the real API",
    ),
) -> None:
    """Check the configured provider: key present, endpoint reachable, model answers, pacing."""
    if round_trip:
        _doctor_round_trip()
        return
    settings = _settings(provider, workdir)
    if paypal:
        _doctor_paypal()
        console.print()
    console.print(f"provider:          {settings.provider}")
    console.print(f"base url:          {settings.base_url or '(none)'}")
    console.print(f"extract model:     {settings.extract_model}")
    console.print(f"adjudicate model:  {settings.adjudicate_model}")
    console.print(
        f"pacing:            {settings.requests_per_minute or 'unlimited'} requests/min · "
        f"{settings.max_retries} retries with backoff"
    )
    if settings.trains_on_prompts:
        console.print(
            "prompt privacy:    [yellow]free tier may train on prompts — public-domain text only[/yellow]"
        )
    if settings.provider == "mock":
        console.print("[green]ok[/green] mock provider needs nothing")
        return
    try:
        llm = make_llm(settings)
    except RuntimeError as e:
        console.print(f"[red]key:[/red] {e}")
        raise typer.Exit(1) from None
    console.print("key:               present")
    usage = _today_usage(settings)
    if usage:
        console.print(
            "today's requests:  " + " · ".join(f"{m} {n}" for m, n in sorted(usage.items()))
        )
        if settings.provider == "gemini":
            console.print(
                "[dim]free-tier daily caps seen so far: flash-lite 500, 3.6/3.8 flash 20 per model; "
                "reset at midnight Pacific[/dim]"
            )
    import time

    for role, model in (
        ("extract", settings.extract_model),
        ("adjudicate", settings.adjudicate_model),
    ):
        t0 = time.monotonic()
        try:
            out = llm.complete(
                model,
                [
                    {
                        "role": "user",
                        "content": 'Reply with exactly this JSON and nothing else: {"ok": true}',
                    }
                ],
                json_mode=True,
            )
        except Exception as e:  # noqa: BLE001
            console.print(f"[red]{role} ({model}) failed:[/red] {type(e).__name__}: {e}")
            raise typer.Exit(1) from None
        ms = (time.monotonic() - t0) * 1000
        ok = '"ok"' in out
        console.print(
            f"[{'green' if ok else 'yellow'}]{role} ({model}):[/{'green' if ok else 'yellow'}] "
            f"{'answered' if ok else 'answered oddly'} in {ms:.0f} ms → {out.strip()[:60]!r}"
        )
    console.print(
        f"[dim]list-price cost of this check: ${llm.usage.cost_usd:.5f} · retries: {llm.usage.retries}[/dim]"
    )


def _doctor_paypal() -> None:
    """OAuth + create a sandbox order and print its approval link. Nothing is captured, so
    nothing is charged; the order expires on PayPal's side. Refuses to run against live."""
    from canonkeeper.preflight.app import make_paypal
    from canonkeeper.preflight.pricing import CURRENCY, MIN_USD

    try:
        pp = make_paypal()
    except RuntimeError as e:
        console.print(f"[red]paypal:[/red] {e}")
        raise typer.Exit(1) from None
    console.print(f"paypal mode:       {pp.mode}")
    if pp.mode == "fake":
        console.print(
            "[yellow]paypal:[/yellow] no PAYPAL_CLIENT_ID in .env or the environment; "
            "`canonkeeper serve` will use the in-process fake"
        )
        return
    if pp.mode != "sandbox":
        console.print("[yellow]paypal:[/yellow] live mode — not creating a test order")
        return
    import time

    t0 = time.monotonic()
    try:
        o = pp.create_order(
            amount=f"{MIN_USD:.2f}",
            currency=CURRENCY,
            description="Preflight doctor check (not captured)",
            return_url="http://127.0.0.1:8787/paypal/return",
            cancel_url="http://127.0.0.1:8787/paypal/cancel?job=doctor",
            reference=f"doctor-{int(time.time())}",
        )
    except Exception as e:  # noqa: BLE001
        detail = getattr(getattr(e, "response", None), "text", "")
        console.print(f"[red]paypal failed:[/red] {type(e).__name__}: {e} {detail[:300]}")
        raise typer.Exit(1) from None
    ms = (time.monotonic() - t0) * 1000
    console.print(
        f"[green]paypal (sandbox):[/green] token + order {o.id} status {o.status} "
        f"for {o.currency} {o.amount} in {ms:.0f} ms"
    )
    console.print(f"[dim]approve link (unused, expires): {o.approve_url}[/dim]")


def _doctor_round_trip(*, wait_s: float = 900.0, poll_s: float = 3.0) -> None:
    """Create a $1.00 sandbox order, wait for the person to approve it as the sandbox buyer,
    capture it, check the capture the way Preflight does, refund it. Never runs against live.
    In fake mode the approval is simulated, which makes this an offline test of the same code."""
    import time

    from canonkeeper.preflight.app import make_paypal
    from canonkeeper.preflight.paypal import (
        UNAPPROVED,
        FakePayPal,
        capture_id_of,
        capture_problem,
    )
    from canonkeeper.preflight.pricing import CURRENCY

    try:
        pp = make_paypal()
    except RuntimeError as e:
        console.print(f"[red]paypal:[/red] {e}")
        raise typer.Exit(1) from None
    if pp.mode == "live":
        console.print("[red]paypal:[/red] live mode — the round trip only runs on the sandbox")
        raise typer.Exit(1)
    amount = "1.00"
    o = pp.create_order(
        amount=amount,
        currency=CURRENCY,
        description="Preflight doctor round trip (refunded)",
        return_url="http://127.0.0.1:8787/return",
        cancel_url="http://127.0.0.1:8787/cancel?job=doctor",
        reference=f"doctor-rt-{int(time.time())}",
    )
    console.print(f"1. order {o.id} created: {o.status}, {o.amount or amount} {CURRENCY}")
    if isinstance(pp, FakePayPal):
        console.print("2. [dim](fake mode: approving in-process)[/dim]")
        pp.approve(o.id)
    else:
        console.print(
            "2. open this link and approve as the [bold]sandbox personal (buyer)[/bold] account. "
            "Afterwards PayPal sends the browser to 127.0.0.1:8787, which may not load — "
            f"that is fine, come back here.\n   {o.approve_url}"
        )
    deadline = time.monotonic() + wait_s
    while (status := pp.get_order(o.id).status) in UNAPPROVED:
        if time.monotonic() > deadline:
            console.print(f"[red]not approved within {wait_s / 60:.0f} minutes ({status})[/red]")
            raise typer.Exit(1)
        time.sleep(poll_s)
    console.print(f"   approved: {status}")
    c = pp.capture_order(o.id)
    problem = capture_problem(c, amount=amount, currency=CURRENCY)
    cap = capture_id_of(c)
    if problem or not cap:
        console.print(f"[red]3. capture rejected:[/red] {problem or 'no capture id'}")
        raise typer.Exit(1)
    console.print(f"3. captured {c.amount or amount} {CURRENCY}: capture {cap}, COMPLETED")
    r = pp.refund_capture(cap)
    if r.status not in ("COMPLETED", "PENDING"):
        console.print(f"[red]4. refund {r.status or 'failed'}[/red]")
        raise typer.Exit(1)
    console.print(f"4. refunded: {r.id or '(already refunded)'} {r.status}")
    again = pp.refund_capture(cap)
    console.print(f"5. refunding again is harmless: {again.status}")
    console.print(
        "[green]paypal round trip: create → approve → capture → verify → refund OK[/green]"
    )


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
def estimate(
    manifest: ManifestArg,
    workdir: Path | None = None,
    candidates: int = typer.Option(40, help="Expected number of candidate conflicts"),
) -> None:
    """Token count and approximate cost for one build + one batched audit."""
    from canonkeeper.extract import estimate_cost

    settings = _settings(None, workdir)
    _, series = _load(manifest, settings)
    est = estimate_cost(series, settings, candidates=candidates)
    console.print(f"series tokens:     {int(est['series_tokens']):,}")
    console.print(
        f"context per batch: {int(est['context_tokens']):,} (cap {settings.adjudicate_context_tokens:,})"
    )
    console.print(f"extraction pass:   ${est['extract_usd']:.2f}  ({settings.extract_model})")
    console.print(
        f"audit, {candidates} candidates in {int(est['adjudicate_batches'])} batches of "
        f"{settings.adjudicate_batch_size}: ${est['adjudicate_usd']:.2f}  ({settings.adjudicate_model})"
    )
    console.print(
        "[dim]lower CANONKEEPER_ADJUDICATE_CONTEXT_TOKENS or raise the batch size to spend less; "
        "repeat runs hit the disk cache and cost nothing[/dim]"
    )


@app.command()
def bible(
    manifest: ManifestArg,
    workdir: Path | None = None,
    min_claims: int = typer.Option(1, help="Hide entities with fewer facts than this"),
    open_page: bool = False,
) -> None:
    """Render the bible as a browsable HTML page (one card per entity, every fact sourced)."""
    from canonkeeper.canon import CanonStore
    from canonkeeper.report.bible import write_bible

    settings = _settings(None, workdir)
    m = SeriesManifest.load(manifest)
    bible_path, report_dir = _paths(settings, m)
    if not bible_path.exists():
        console.print("[red]no bible yet — run `canonkeeper build` first[/red]")
        raise typer.Exit(2)
    store = CanonStore.load(bible_path)
    out = write_bible(store, m.series, report_dir / "bible.html", min_claims=min_claims)
    console.print(f"[green]bible page:[/green] {out}  ({len(store.entities())} entities)")
    console.print(f"[green]spreadsheet:[/green] {out.with_suffix('.csv')}")
    if open_page:
        typer.launch(str(out))


@app.command()
def demo(
    dest: Path = _DEMO_DEST,
    open_report: bool = False,
) -> None:
    """Run the whole pipeline offline on an invented three-book series with five planted errors."""
    from canonkeeper.demo import DRAFT_PLANTED, PLANTED, write_demo_series
    from canonkeeper.pipeline import audit as run_audit
    from canonkeeper.pipeline import build_bible, check_manuscript
    from canonkeeper.report import write_report
    from canonkeeper.report.bible import write_bible
    from canonkeeper.report.display import label

    settings = _settings("mock", dest)
    manifest_path = write_demo_series(dest)
    m, series = _load(manifest_path, settings)
    bible_path, report_dir = _paths(settings, m)
    if bible_path.exists():
        bible_path.unlink()
    llm = _llm(settings)

    console.rule(f"[bold]{m.series}[/bold] — {len(series.books)} books, {series.words:,} words")
    console.print("planted errors:")
    for who, what, how in PLANTED:
        console.print(f"  • {who}’s {label(what)}: {how}")

    store = build_bible(series, llm, settings, bible_path)
    console.print(
        f"\n[green]bible:[/green] {len(store)} facts about {len(store.entities())} entities"
    )
    bible_html = write_bible(store, m.series, report_dir / "bible.html")
    report = run_audit(series, store, llm, settings)
    html, _ = write_report(report, report_dir)
    console.print()
    _print_summary(report)
    console.print(f"\n[green]report:[/green] {html}\n[green]bible: [/green] {bible_html}")

    draft = dest / "draft-04-the-ledger.txt"
    console.rule(f"[bold]check[/bold] — unpublished draft {draft.name} against the bible")
    console.print("planted in the draft:")
    for who, what, how in DRAFT_PLANTED:
        console.print(f"  • {who}’s {label(what)}: {how}")
    check_report = check_manuscript(series, store, draft, llm, settings)
    check_html, _ = write_report(check_report, report_dir / "check")
    console.print()
    _print_summary(check_report)
    for f in check_report.findings:
        if f.verdict == "deliberate":
            console.print(f"[bold green]DELIBERATE[/bold green] {f.candidate.reason}")
            console.print(f"   {f.explanation}")
    console.print(f"\n[green]check report:[/green] {check_html}")
    console.print("[dim]provider=mock: rule-based stand-in, no API calls were made[/dim]")
    if open_report:
        typer.launch(str(html))


def _budget_preflight(settings: Settings, series: Series, llm: object) -> int:
    """Say up front how many requests this build needs (exact: cached ones are counted) against
    today's remaining budget. Returns the number of requests that would actually be sent."""
    from canonkeeper.extract.extractor import Extractor

    total, cached = Extractor(llm, settings).plan(series)  # type: ignore[arg-type]
    needed = total - cached
    passes = 2 if settings.extract_sweep else 1
    cap = settings.daily_budget.get(settings.extract_model)
    used = _today_usage(settings).get(settings.extract_model, 0)
    line = (
        f"[dim]extraction: {total // passes} chunks × {passes} pass(es) = {total} requests, "
        f"{cached} already cached → {needed} to send"
    )
    if cap:
        left = max(0, cap - settings.budget_margin - used)
        line += f" · {settings.extract_model} today {used}/{cap}, {left} left"
        if needed > left:
            line += (
                f" → [yellow]will stop after ~{left} and resume on the next run "
                f"(finished chunks are cached)[/yellow]"
            )
    eta = _eta_minutes(settings, settings.extract_model, needed)
    if eta is not None and needed:
        line += f" · about {eta} min at today's pace"
    console.print(line + "[/dim]")
    return needed


def _eta_minutes(settings: Settings, model: str, requests: int) -> int | None:
    """How long `requests` will take: the median latency of this model's recent successful
    requests (from the request log), times the requests kept in flight, capped by the
    configured requests per minute. None when there is no history to go on."""
    import statistics

    log = settings.request_log
    if not log.exists():
        return None
    lat: list[float] = []
    for line in log.read_text(encoding="utf-8").splitlines()[-400:]:
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("model") == model and d.get("ok") and d.get("latency_s"):
            lat.append(float(d["latency_s"]))
    if len(lat) < 5:
        return None
    per_min = max(1, settings.extract_concurrency) * 60.0 / max(0.5, statistics.median(lat))
    if settings.requests_per_minute > 0:
        per_min = min(per_min, float(settings.requests_per_minute))
    return max(1, round(requests / per_min))


def _select_books(series: Series, books: str | None) -> Series:
    """`--books 1,2` or `--books 01-a-study-in-scarlet` narrows a run to some volumes."""
    if not books:
        return series
    wanted = [b.strip() for b in books.split(",") if b.strip()]
    chosen = []
    for b in series.books:
        if b.key in wanted or str(b.index + 1) in wanted:
            chosen.append(b)
    if not chosen:
        raise typer.BadParameter(f"no book matches {books!r}")
    return Series(series.name, chosen)


@app.command()
def build(
    manifest: ManifestArg,
    provider: str | None = None,
    workdir: Path | None = None,
    books: str | None = typer.Option(
        None, help="Only these books: comma-separated keys or 1-based numbers"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Only report how many requests the build would send, then exit"
    ),
) -> None:
    """Extract claims from every chapter into the series bible (incremental: re-runs hit the cache)."""
    from canonkeeper.pipeline import build_bible

    settings = _settings(provider, workdir)
    m, series = _load(manifest, settings)
    series = _select_books(series, books)
    bible_path, _ = _paths(settings, m)
    llm = _llm(settings, sends_text=not dry_run)
    needed = _budget_preflight(settings, series, llm)
    if dry_run:
        console.print(f"dry run: {needed} request(s) would be sent")
        return
    store = _run_or_explain(build_bible, series, llm, settings, bible_path)
    console.print(
        f"[green]bible:[/green] {bible_path}  ({len(store)} claims, {len(store.entities())} entities)"
    )
    st = store.last_extract_stats
    if st is not None:
        console.print(
            f"[dim]extraction: {st.chunks} chunks · {st.claims_raw} claims returned · "
            f"{st.claims_kept} grounded · {st.dropped_ungrounded} dropped (quote not in text) · "
            f"{st.dropped_invalid} invalid · {store.dropped_generic} unnamed subjects dropped · "
            f"{st.json_repaired} JSON repaired · {st.json_failed} chunks unreadable[/dim]"
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
    llm = _llm(settings)
    report = _run_or_explain(run_audit, series, store, llm, settings)
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
    from canonkeeper.pipeline import check_manuscript
    from canonkeeper.report import write_report

    settings = _settings(provider, workdir)
    m, series = _load(manifest, settings)
    bible_path, report_dir = _paths(settings, m)
    if not bible_path.exists():
        console.print("[red]no bible yet — run `canonkeeper build` first[/red]")
        raise typer.Exit(2)
    store = CanonStore.load(bible_path)
    llm = _llm(settings)
    report = _run_or_explain(check_manuscript, series, store, manuscript, llm, settings)
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
    llm = _llm(settings)

    console.rule("baseline (clean series)")
    baseline = _run_or_explain(run_audit, series, clean_store, llm, settings)
    baseline_ids = {finding_key(f) for f in baseline.errors}
    console.print(f"pre-existing contradictions in source: {len(baseline_ids)}")
    eval_dir = report_dir / f"eval-seed{seed}"
    eval_dir.mkdir(parents=True, exist_ok=True)
    (eval_dir / "baseline.json").write_text(baseline.model_dump_json(indent=1), encoding="utf-8")

    console.rule(f"injecting {n} defects (seed={seed})")
    iset = inject(series, clean_store, n, seed=seed)
    for inj in iset.injections:
        console.print(
            f"  • {inj.subject}.{inj.predicate}: {inj.original!r} → {inj.injected!r} in {inj.book}"
        )

    mutated_store = _run_or_explain(
        build_bible, iset.series, llm, settings, eval_dir / "bible.jsonl", progress=True
    )
    report = _run_or_explain(run_audit, iset.series, mutated_store, llm, settings)
    s = score(iset.injections, report.findings, baseline_ids=baseline_ids)

    from canonkeeper.eval.score import diagnose, misses_markdown

    misses = diagnose(s.missed, mutated_store.claims, report.findings, baseline_ids=baseline_ids)
    console.rule("result")
    console.print(s.markdown())
    for miss in misses:
        inj = miss.injection
        console.print(
            f"[yellow]missed at {miss.stage}[/yellow] {inj.subject}.{inj.predicate} → "
            f"{inj.injected!r} ({inj.book}): {miss.detail}"
        )
    for f in s.extra:
        console.print(f"[red]false positive[/red] {f.candidate.reason}")
    judged = report.models.get("adjudicated_by", "-")
    console.print(f"[dim]judged by: {judged}[/dim]")
    (eval_dir / "result.md").write_text(
        s.markdown() + f"\n\nJudged by: {judged}\n" + misses_markdown(misses) + "\n",
        encoding="utf-8",
    )
    (eval_dir / "injections.json").write_text(
        json.dumps([inj.__dict__ for inj in iset.injections], indent=1), encoding="utf-8"
    )
    (eval_dir / "report.json").write_text(report.model_dump_json(indent=1), encoding="utf-8")
    console.print(f"[dim]cost ${llm.usage.cost_usd:.3f}[/dim]")


@app.command()
def rescore(
    eval_dir: Annotated[Path, typer.Argument(help="An eval-seedN folder written by `eval`")],
    baseline: Annotated[
        Path | None,
        typer.Option(help="Clean-run report.json (default: baseline.json in the folder)"),
    ] = None,
) -> None:
    """Re-score a finished benchmark from its saved files, with no model calls."""
    from canonkeeper.canon import CanonStore
    from canonkeeper.eval.inject import Injection
    from canonkeeper.eval.score import diagnose, finding_key, misses_markdown, score
    from canonkeeper.models import AuditReport

    injections = [
        Injection(**d)
        for d in json.loads((eval_dir / "injections.json").read_text(encoding="utf-8"))
    ]
    report = AuditReport.model_validate_json((eval_dir / "report.json").read_text("utf-8"))
    base_path = baseline or eval_dir / "baseline.json"
    base = AuditReport.model_validate_json(base_path.read_text("utf-8"))
    baseline_ids = {finding_key(f) for f in base.errors}
    s = score(injections, report.findings, baseline_ids=baseline_ids)
    bible = eval_dir / "bible.jsonl"
    misses = (
        diagnose(
            s.missed, CanonStore.load(bible).claims, report.findings, baseline_ids=baseline_ids
        )
        if bible.exists()
        else []  # the stage breakdown needs the mutated bible; the score does not
    )
    judged = report.models.get("adjudicated_by")  # absent in runs that predate the field
    console.print(s.markdown())
    (eval_dir / "result.md").write_text(
        s.markdown()
        + (f"\n\nJudged by: {judged}\n" if judged else "\n")
        + misses_markdown(misses)
        + "\n",
        encoding="utf-8",
    )
    console.print(f"[dim]wrote {eval_dir / 'result.md'}[/dim]")


@app.command()
def compare(
    before: Annotated[Path, typer.Argument(help="report.json of the earlier run")],
    after: Annotated[Path, typer.Argument(help="report.json of the later run")],
) -> None:
    """Show which verdicts changed between two audits of the same series (no model calls)."""
    from canonkeeper.eval.compare import compare as diff
    from canonkeeper.models import AuditReport

    a = AuditReport.model_validate_json(before.read_text("utf-8"))
    b = AuditReport.model_validate_json(after.read_text("utf-8"))
    ca, cb, changes = diff(a, b)
    order = ("contradiction", "deliberate", "ambiguous", "not_conflict")
    console.print("before: " + ", ".join(f"{v} {ca[v]}" for v in order))
    console.print("after:  " + ", ".join(f"{v} {cb[v]}" for v in order))
    console.print(f"judged by (after): {b.models.get('adjudicated_by', 'not recorded')}")
    if not changes:
        console.print("[green]no verdict changed[/green]")
    for ch in changes:
        console.print(f"\n[bold]{ch.label}[/bold]")
        for name, f in (("before", ch.before), ("after", ch.after)):
            if f is None:
                console.print(f"  {name}: (not a candidate)")
            else:
                console.print(f"  {name}: {f.verdict} — {f.explanation[:160]}")


@app.command()
def serve(
    host: str = "127.0.0.1",
    port: int = 8787,
    provider: str | None = None,
    workdir: Path | None = None,
    public_url: str | None = typer.Option(
        None, help="Public base URL PayPal should send buyers back to"
    ),
) -> None:
    """Run the Preflight service (upload → quote → PayPal checkout → audit → report)."""
    import uvicorn

    from canonkeeper.preflight.app import create_app, make_paypal

    settings = _settings(provider, workdir)
    pp = make_paypal(state_dir=settings.workdir / "preflight")
    console.print(f"provider {settings.provider} · paypal {pp.mode} · http://{host}:{port}")
    if settings.trains_on_prompts:
        console.print(
            "[yellow]note:[/yellow] Gemini free tier may train on prompts — public-domain and "
            "demo text only through this service."
        )
    web = create_app(
        settings=settings, paypal=pp, public_base_url=public_url or f"http://{host}:{port}"
    )
    uvicorn.run(web, host=host, port=port, log_level="info")


@app.command()
def mcp(provider: str | None = None, workdir: Path | None = None) -> None:
    """Run Preflight as an MCP server over stdio (quote → order → confirm → status → findings)."""
    from canonkeeper.preflight.mcp_server import build_server, make_service

    settings = _settings(provider, workdir)
    service = make_service(settings)
    build_server(service).run("stdio")


def _print_summary(report: AuditReport) -> None:
    from canonkeeper.report.display import headline

    t = Table(show_header=True)
    t.add_column("verdict")
    t.add_column("count", justify="right")
    for v in ("contradiction", "deliberate", "ambiguous", "not_conflict"):
        t.add_row(v, str(sum(1 for f in report.findings if f.verdict == v)))
    console.print(t)
    for f in report.errors[:10]:
        console.print(f"[bold red]{f.severity.upper()}[/bold red] {headline(f.candidate)}")
        console.print(f"   {f.explanation}")


if __name__ == "__main__":
    app()
