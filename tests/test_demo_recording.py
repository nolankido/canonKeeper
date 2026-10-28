"""The demo series' recorded model answers (T4): record through Preflight's own runner, replay
offline with no key, seed a hosted instance's cache, and never reach the network on a miss."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from canonkeeper.config import Settings
from canonkeeper.llm.client import MockLLM, NotRecorded, OpenAICompatibleLLM
from canonkeeper.preflight import demo_recording
from canonkeeper.preflight.app import create_app
from canonkeeper.preflight.paypal import FakePayPal


def _gemini_like(tmp_path) -> Settings:
    return Settings(
        provider="gemini",
        gemini_api_key="k",
        workdir=tmp_path / "rec",
        requests_per_minute=0,
        tokens_per_minute=0,
        _env_file=None,
    )


def _answer_like_gemini(calls: list[str]):
    """A stand-in for Gemini's OpenAI-compatible SDK client that answers as the rule-based mock
    would, so the whole pipeline runs and every answer passes through the real client class."""
    from types import SimpleNamespace

    mock = MockLLM()

    def create(*, model, messages, response_format=None, **_ignored):
        calls.append(model)
        json_mode = bool(response_format) and response_format.get("type") == "json_object"
        text = mock.complete(model, messages, json_mode=json_mode)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )

    def factory(*_args, **_kwargs):
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    return factory


def _pay(web, app, job_id):
    """Order, approve on the simulated PayPal page, return, and wait for the run."""
    r = web.post(f"/jobs/{job_id}/order", follow_redirects=False)
    assert r.status_code == 303, r.text[:300]
    order_id = app.state.store.get(job_id).order_id
    r = web.post("/fake-approve", data={"token": order_id}, follow_redirects=False)
    web.get(r.headers["location"], follow_redirects=False)
    app.state.runner.wait(job_id, timeout=120)


@pytest.fixture
def recording(tmp_path, monkeypatch):
    import openai

    calls: list[str] = []
    with monkeypatch.context() as m:
        m.setattr(openai, "OpenAI", _answer_like_gemini(calls))
        meta = demo_recording.record(
            _gemini_like(tmp_path), tmp_path / "recording", workdir=tmp_path / "rec"
        )
    monkeypatch.setattr(demo_recording, "RECORDING", tmp_path / "recording")
    return meta, calls


def test_recording_runs_the_real_runner_and_pins_what_shapes_the_prompts(recording, tmp_path):
    meta, calls = recording
    assert (
        calls
        and meta["entries"] == len(set(p.name for p in (tmp_path / "recording").glob("*.json"))) - 1
    )
    assert meta["provider"] == "gemini" and set(meta["settings"]) == set(demo_recording.SHAPING)
    assert meta["results"]["audit"]["contradictions"] >= 1 and "check" in meta["results"]


def test_keyless_demo_replays_the_recording_offline_and_says_so(recording, tmp_path):
    meta, _ = recording
    keyless = Settings(provider="mock", workdir=tmp_path / "k", _env_file=None)
    app = create_app(settings=keyless, paypal=FakePayPal(), workdir=tmp_path / "kpf")
    web = TestClient(app)
    job_id = (
        web.post("/quote-demo", follow_redirects=True)
        .text.split('action="/jobs/')[1]
        .split("/order")[0]
    )
    with respx.mock(assert_all_called=False) as router:  # any network call would show here
        net = router.route().mock(return_value=httpx.Response(500))
        _pay(web, app, job_id)
        assert not net.called
    job = app.state.store.get(job_id)
    assert job.status == "done" and job.contradictions == meta["results"]["audit"]["contradictions"]
    report = json.loads((app.state.store.dir(job_id) / "reports" / "report.json").read_text())
    assert "recorded answers from" in report["models"]["replayed"]
    assert report["models"]["provider"] == "gemini"
    html = (app.state.store.dir(job_id) / "reports" / "report.html").read_text(encoding="utf-8")
    assert 'data-testid="replayed"' in html  # the reader is told, on the report itself
    assert "real Gemini answers recorded earlier" in web.get("/").text


def test_a_keyless_non_demo_upload_still_uses_the_stand_in(recording, tmp_path):
    keyless = Settings(provider="mock", workdir=tmp_path / "k2", _env_file=None)
    app = create_app(settings=keyless, paypal=FakePayPal(), workdir=tmp_path / "k2pf")
    web = TestClient(app)
    files = [("files", ("01-a.txt", b"CHAPTER I.\n\nAnna had green eyes.\n", "text/plain"))]
    page = web.post("/quote", data={"series_name": "Not the demo"}, files=files).text
    job_id = page.split('action="/jobs/')[1].split("/order")[0]
    _pay(web, app, job_id)
    report = json.loads((app.state.store.dir(job_id) / "reports" / "report.json").read_text())
    assert "replayed" not in report["models"] and report["models"]["provider"] == "mock"


def test_a_miss_is_never_sent_anywhere(tmp_path):
    llm = OpenAICompatibleLLM("x", "http://127.0.0.1:9/never", cache_dir=tmp_path, offline=True)
    with pytest.raises(NotRecorded, match="was not sent anywhere"):
        llm.complete("gemini-3.6-flash", [{"role": "user", "content": "anything"}])


def test_a_hosted_instance_seeds_its_demo_cache_and_needs_no_quota_for_the_demo(
    recording, tmp_path
):
    """A free host loses its disk on restart; the packaged answers come back with the code, so
    the demo runs on a cold instance even when the day's quota is spent."""
    from datetime import UTC, datetime

    hosted = _gemini_like(tmp_path).model_copy(update={"workdir": tmp_path / "h"})
    app = create_app(settings=hosted, paypal=FakePayPal(), workdir=tmp_path / "hpf")
    assert len(list(app.state.runner.demo_cache_dir.glob("*.json"))) == recording[0]["entries"]
    spent = {"ts": datetime.now(UTC).isoformat(), "ok": True, "latency_s": 1.0}
    hosted.request_log.parent.mkdir(parents=True, exist_ok=True)
    hosted.request_log.write_text(
        "\n".join(
            json.dumps({**spent, "model": m})
            for m in (
                "gemini-3.5-flash-lite",
                "gemini-3.1-flash-lite",
                "gemini-3.6-flash",
                "gemini-3.8-flash",
            )
            for _ in range(500)
        ),
        encoding="utf-8",
    )
    web = TestClient(app)
    job_id = (
        web.post("/quote-demo", follow_redirects=True)
        .text.split('action="/jobs/')[1]
        .split("/order")[0]
    )
    r = web.post(f"/jobs/{job_id}/order", follow_redirects=False)
    assert r.status_code == 303  # ordered: every request the demo needs is already answered


def test_the_packaged_recording_matches_todays_prompts():
    """When a prompt changes, the packaged recording no longer answers the demo: re-record it
    (`python scripts/record_demo.py`) instead of letting it silently half-apply."""
    if demo_recording.manifest() is None:
        pytest.skip("no demo recording packaged yet")
    import tempfile
    from pathlib import Path

    from canonkeeper.corpus import Book, Chapter, Series  # noqa: F401
    from canonkeeper.demo import BOOK1, BOOK2, BOOK3, SERIES_NAME
    from canonkeeper.extract.extractor import Extractor
    from canonkeeper.preflight.jobs import JobStore

    with tempfile.TemporaryDirectory() as tmp:
        store = JobStore(Path(tmp) / "jobs")
        job, _ = store.create_from_upload(
            SERIES_NAME,
            [("01-the-harbour.txt", BOOK1.encode()), ("02-the-wreck.txt", BOOK2.encode()),
             ("03-the-return.txt", BOOK3.encode())],
        )  # fmt: skip
        settings, llm = demo_recording.replay(Settings(provider="mock", _env_file=None))
        total, cached = Extractor(llm, settings).plan(store.load_series(job))
    assert cached == total, f"the recording answers {cached} of {total} extraction requests"
