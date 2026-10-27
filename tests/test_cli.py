import json
from pathlib import Path

from typer.testing import CliRunner

from canonkeeper.cli import app
from tests.conftest import BOOK1, BOOK2, BOOK3

runner = CliRunner()


def _write_series(tmp_path: Path) -> Path:
    (tmp_path / "books").mkdir()
    for name, text in [("b1.txt", BOOK1), ("b2.txt", BOOK2), ("b3.txt", BOOK3)]:
        (tmp_path / "books" / name).write_text(text)
    manifest = {
        "series": "Test Series",
        "books": [
            {"key": "01-first", "title": "First", "path": "books/b1.txt"},
            {"key": "02-second", "title": "Second", "path": "books/b2.txt"},
            {"key": "03-third", "title": "Third", "path": "books/b3.txt"},
        ],
    }
    p = tmp_path / "series.json"
    p.write_text(json.dumps(manifest))
    return p


def test_fetch_and_estimate(tmp_path):
    m = _write_series(tmp_path)
    r = runner.invoke(app, ["fetch", str(m), "--workdir", str(tmp_path / "wd")])
    assert r.exit_code == 0, r.output
    assert "Test Series" in r.output
    r = runner.invoke(app, ["estimate", str(m), "--workdir", str(tmp_path / "wd")])
    assert r.exit_code == 0, r.output
    assert "series tokens" in r.output


def test_audit_requires_bible(tmp_path):
    m = _write_series(tmp_path)
    r = runner.invoke(
        app, ["audit", str(m), "--provider", "mock", "--workdir", str(tmp_path / "wd")]
    )
    assert r.exit_code == 2


def test_build_then_audit_with_mock_provider(tmp_path, monkeypatch):
    # Route the mock provider through the fixture handler so the CLI path produces findings.
    from canonkeeper import cli
    from canonkeeper.llm import MockLLM
    from tests.conftest import mock_handler

    monkeypatch.setattr(cli, "make_llm", lambda settings: MockLLM(mock_handler))
    m = _write_series(tmp_path)
    wd = str(tmp_path / "wd")
    r = runner.invoke(app, ["build", str(m), "--provider", "mock", "--workdir", wd])
    assert r.exit_code == 0, r.output
    assert "claims" in r.output
    r = runner.invoke(app, ["audit", str(m), "--provider", "mock", "--workdir", wd])
    assert r.exit_code == 0, r.output
    assert "contradiction" in r.output
    assert list((tmp_path / "wd").rglob("report.html"))


def test_demo_runs_offline_and_finds_every_planted_error(tmp_path):
    from canonkeeper.demo import PLANTED

    r = runner.invoke(app, ["demo", "--dest", str(tmp_path / "demo")])
    assert r.exit_code == 0, r.output
    assert "no API calls were made" in r.output
    report = json.loads(next((tmp_path / "demo").rglob("report.json")).read_text())
    errors = [f for f in report["findings"] if f["verdict"] == "contradiction"]
    assert len(errors) == len(PLANTED)
    for who, what, _ in PLANTED:
        assert any(
            any(
                c["subject"] == who and (c["predicate"] == what or what == "dead")
                for c in f["candidate"]["claims"]
            )
            for f in errors
        ), (who, what)
    bible = next((tmp_path / "demo").rglob("bible.html")).read_text()
    assert "Ivy Marlowe" in bible
    assert 'class="slot disputed"' in bible


def test_bible_command_requires_bible(tmp_path):
    m = _write_series(tmp_path)
    r = runner.invoke(app, ["bible", str(m), "--workdir", str(tmp_path / "wd")])
    assert r.exit_code == 2


def test_demo_check_separates_slips_from_deliberate_reveal(tmp_path):
    r = runner.invoke(app, ["demo", "--dest", str(tmp_path / "demo")])
    assert r.exit_code == 0, r.output
    check = json.loads(next((tmp_path / "demo").rglob("check/report.json")).read_text())
    by_verdict = {}
    for f in check["findings"]:
        subj = f["candidate"]["claims"][0]["subject"]
        pred = f["candidate"]["claims"][0]["predicate"]
        by_verdict.setdefault(f["verdict"], set()).add((subj, pred))
    assert ("Hester Vale", "eye_color") in by_verdict["deliberate"]
    assert ("Ivy Marlowe", "eye_color") in by_verdict["contradiction"]
    assert ("Tobias Crane", "birthplace") in by_verdict["contradiction"]
    assert ("Hester Vale", "eye_color") not in by_verdict["contradiction"]
    # Every finding in a check touches the draft; published-vs-published conflicts are excluded.
    assert all(
        any(c["provenance"]["book"].startswith("04-") for c in f["candidate"]["claims"])
        for f in check["findings"]
    )


def test_provider_outage_exits_cleanly_with_resume_hint(tmp_path, monkeypatch):
    """A 503 that survives every retry must not end in a traceback: the run is cached and
    resumable, and the message should say so."""
    import httpx
    import openai

    from canonkeeper import cli

    class Dying:
        usage = type("U", (), {"cost_usd": 0.0, "retries": 0})()

        def complete(self, *a, **k):
            raise openai.InternalServerError(
                "503", response=httpx.Response(503, request=httpx.Request("POST", "x")), body=None
            )

    monkeypatch.setattr(cli, "make_llm", lambda settings: Dying())
    m = _write_series(tmp_path)
    r = runner.invoke(
        app, ["build", str(m), "--provider", "mock", "--workdir", str(tmp_path / "wd")]
    )
    assert r.exit_code == 2, r.output
    flat = " ".join(r.output.split())
    assert "HTTP 503" in flat and "rerun the same command" in flat
    assert "Traceback" not in flat


def test_eval_then_rescore_from_saved_files(tmp_path, monkeypatch):
    """`eval` keeps what it needs; `rescore` reproduces the result with no model calls."""
    from canonkeeper import cli
    from canonkeeper.llm import MockLLM
    from tests.conftest import mock_handler

    monkeypatch.setattr(cli, "make_llm", lambda settings: MockLLM(mock_handler))
    m = _write_series(tmp_path)
    wd = str(tmp_path / "wd")
    assert (
        runner.invoke(app, ["build", str(m), "--provider", "mock", "--workdir", wd]).exit_code == 0
    )
    r = runner.invoke(
        app, ["eval", str(m), "--n", "3", "--seed", "1", "--provider", "mock", "--workdir", wd]
    )
    assert r.exit_code == 0, r.output
    eval_dir = next((tmp_path / "wd").rglob("eval-seed1"))
    for name in ("baseline.json", "report.json", "injections.json", "bible.jsonl", "result.md"):
        assert (eval_dir / name).exists(), name
    first = (eval_dir / "result.md").read_text().split("\n")[2]

    def no_calls(settings):  # rescoring must not need a model
        raise AssertionError("rescore called a model")

    monkeypatch.setattr(cli, "make_llm", no_calls)
    r = runner.invoke(app, ["rescore", str(eval_dir)])
    assert r.exit_code == 0, r.output
    assert (eval_dir / "result.md").read_text().split("\n")[2] == first


def test_doctor_round_trip_runs_the_money_path_in_fake_mode(monkeypatch):
    """The same create → approve → capture → verify → refund code that a person runs against
    the sandbox with `doctor --round-trip`; offline, the approval is simulated."""
    monkeypatch.setattr("canonkeeper.preflight.app.paypal_env", lambda: {"PAYPAL_MODE": "fake"})
    r = runner.invoke(app, ["doctor", "--round-trip"])
    assert r.exit_code == 0, r.output
    assert "captured 1.00 USD" in r.output and "refunded" in r.output
    assert "round trip: create → approve → capture → verify → refund OK" in r.output

    monkeypatch.setattr(
        "canonkeeper.preflight.app.paypal_env",
        lambda: {"PAYPAL_MODE": "live", "PAYPAL_CLIENT_ID": "x", "PAYPAL_CLIENT_SECRET": "y"},
    )
    r = runner.invoke(app, ["doctor", "--round-trip"])
    assert r.exit_code == 1 and "only runs on the sandbox" in r.output


def test_compare_shows_only_the_verdicts_that_moved(tmp_path):
    src = Path("evals/results/holmes-seed0-2026-10-27/baseline.json")
    report = json.loads(src.read_text(encoding="utf-8"))
    before = tmp_path / "before.json"
    before.write_text(json.dumps(report), encoding="utf-8")
    same = runner.invoke(app, ["compare", str(before), str(before)])
    assert same.exit_code == 0 and "no verdict changed" in same.output
    flipped = report["findings"][0]
    old = flipped["verdict"]
    flipped["verdict"] = "not_conflict" if old != "not_conflict" else "contradiction"
    after = tmp_path / "after.json"
    after.write_text(json.dumps(report), encoding="utf-8")
    r = runner.invoke(app, ["compare", str(before), str(after)])
    assert r.exit_code == 0, r.output
    assert f"before: {old}" in r.output and f"after: {flipped['verdict']}" in r.output
    assert r.output.count("before:") == 2  # the counts line and the one changed finding


def test_build_estimates_time_from_todays_latency(tmp_path):
    from canonkeeper.cli import _eta_minutes
    from canonkeeper.config import Settings

    s = Settings(provider="gemini", workdir=tmp_path, _env_file=None)
    m = s.extract_model
    assert _eta_minutes(s, m, 90) is None  # no history: no guess
    lines = [json.dumps({"model": m, "ok": True, "latency_s": 12.0}) for _ in range(10)]
    (tmp_path / "requests.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    one = s.model_copy(update={"extract_concurrency": 1, "requests_per_minute": 9})
    three = s.model_copy(update={"extract_concurrency": 3, "requests_per_minute": 9})
    assert _eta_minutes(one, m, 90) == 18  # 5/min: latency-bound
    assert _eta_minutes(three, m, 90) == 10  # 9/min: the rate limit binds


def test_quota_reports_usage_against_caps(tmp_path, monkeypatch):
    monkeypatch.setenv("CANONKEEPER_PROVIDER", "gemini")
    r = runner.invoke(app, ["quota", "--workdir", str(tmp_path)])
    assert r.exit_code == 0, r.output
    assert "gemini-3.6-flash" in r.output and "resets at" in r.output


def test_archive_eval_copies_the_run_and_prints_its_row(tmp_path):
    import shutil

    src = tmp_path / "eval-seed0"
    shutil.copytree("evals/results/holmes-seed0-2026-10-27", src)
    dest = tmp_path / "evals" / "results" / "x"
    r = runner.invoke(app, ["archive-eval", str(src), str(dest), "--label", "3 · test"])
    assert r.exit_code == 0, r.output
    assert {p.name for p in dest.iterdir()} >= {"report.json", "result.md", "README.md"}
    assert "| 3 · test | 20 | 11 | 8 | 2 | 12 | 0.80 | 0.40 | 0.53 |" in r.output.replace("\n", "")
    readme = (dest / "README.md").read_text(encoding="utf-8")
    assert "precision 0.80, recall 0.40" in readme and "Misses by stage" in readme
