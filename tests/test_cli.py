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
