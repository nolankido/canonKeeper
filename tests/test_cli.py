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
