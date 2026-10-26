"""Client behaviour under free-tier conditions: pacing, 429 backoff, JSON-mode fallback, cache."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from openai import BadRequestError, InternalServerError, RateLimitError

from canonkeeper.config import Settings
from canonkeeper.llm.client import OpenAICompatibleLLM, RateLimiter, make_llm


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0
        self.slept: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.t += s


def test_rate_limiter_sliding_window():
    clk = FakeClock()
    rl = RateLimiter(3, clock=clk.now, sleep=clk.sleep)
    for _ in range(3):
        assert rl.wait() == 0.0
    slept = rl.wait()  # 4th call inside the same minute must wait for the first to age out
    assert 59.9 < slept < 60.2
    # The wait aged out all three earlier stamps, so two more calls are free, then we wait again.
    assert rl.wait() == 0.0
    assert rl.wait() == 0.0
    assert rl.wait() > 0
    rl0 = RateLimiter(0, clock=clk.now, sleep=clk.sleep)
    assert rl0.wait() == 0.0  # 0 = unlimited


def _resp(status: int, headers: dict[str, str] | None = None) -> httpx.Response:
    req = httpx.Request("POST", "https://example.test/v1/chat/completions")
    return httpx.Response(
        status, headers=headers or {}, request=req, json={"error": {"message": "x"}}
    )


def _completion(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
    )


def _llm(tmp_path, create, *, rpm=0, retries=6):
    clk = FakeClock()
    llm = OpenAICompatibleLLM(
        "k",
        "https://example.test/v1",
        cache_dir=tmp_path / "c",
        requests_per_minute=rpm,
        max_retries=retries,
        sleep=clk.sleep,
    )
    llm._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return llm, clk


def test_retries_on_429_honouring_retry_after_then_succeeds(tmp_path):
    calls = {"n": 0}

    def create(**kwargs):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RateLimitError("slow down", response=_resp(429, {"retry-after": "7"}), body=None)
        return _completion('{"ok": true}')

    llm, clk = _llm(tmp_path, create)
    out = llm.complete("gemini-3.8-flash", [{"role": "user", "content": "hi"}], json_mode=True)
    assert out == '{"ok": true}'
    assert calls["n"] == 3
    assert clk.slept == [7.0, 7.0]
    assert llm.usage.retries == 2
    assert llm.usage.cost_usd > 0  # priced at list for gemini-3.8-flash


def test_gives_up_after_max_retries(tmp_path):
    def create(**kwargs):
        raise RateLimitError("nope", response=_resp(429), body=None)

    llm, clk = _llm(tmp_path, create, retries=2)
    with pytest.raises(RateLimitError):
        llm.complete("m", [{"role": "user", "content": "hi"}])
    assert len(clk.slept) == 2
    assert all(1.0 <= s <= 61.0 for s in clk.slept)  # exponential backoff with jitter


def test_json_mode_fallback_when_server_rejects_response_format(tmp_path):
    seen: list[dict] = []

    def create(**kwargs):
        seen.append(kwargs)
        if "response_format" in kwargs:
            raise BadRequestError(
                "Invalid parameter: response_format", response=_resp(400), body=None
            )
        return _completion('```json\n{"ok": true}\n```')

    llm, _ = _llm(tmp_path, create)
    out = llm.complete("m", [{"role": "user", "content": "a"}], json_mode=True)
    assert "ok" in out
    assert "response_format" in seen[0] and "response_format" not in seen[1]
    # The second call skips response_format entirely: the limitation is remembered.
    llm.complete("m", [{"role": "user", "content": "b"}], json_mode=True)
    assert "response_format" not in seen[2]
    assert llm.usage.retries == 0  # a capability fallback is not a retry


def test_disk_cache_short_circuits_second_call(tmp_path):
    calls = {"n": 0}

    def create(**kwargs):
        calls["n"] += 1
        return _completion("x")

    llm, _ = _llm(tmp_path, create)
    msgs = [{"role": "user", "content": "same"}]
    assert llm.complete("m", msgs) == "x"
    assert llm.complete("m", msgs) == "x"
    assert calls["n"] == 1
    assert llm.usage.cached_calls == 1


def test_provider_defaults_and_key_help(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    s = Settings(provider="gemini", _env_file=None)
    assert s.base_url.startswith("https://generativelanguage.googleapis.com/v1beta/openai")
    assert s.extract_model == "gemini-3.5-flash-lite"
    assert s.requests_per_minute == 9
    assert s.trains_on_prompts is True
    with pytest.raises(RuntimeError, match="aistudio.google.com"):
        make_llm(s)

    s2 = Settings(
        provider="openweights", openweights_base_url="http://localhost:11434/v1", _env_file=None
    )
    assert s2.base_url == "http://localhost:11434/v1"
    assert s2.requests_per_minute == 0
    assert s2.trains_on_prompts is False
    assert make_llm(s2) is not None  # no key needed for a local server

    s3 = Settings(
        provider="gemini",
        extract_model="gemini-3.5-flash-lite",
        requests_per_minute=20,
        _env_file=None,
    )
    assert s3.extract_model == "gemini-3.5-flash-lite"
    assert s3.adjudicate_model == "gemini-3.6-flash"
    assert s3.requests_per_minute == 20


def test_reasoning_effort_sent_per_model_and_dropped_when_rejected(tmp_path):
    seen: list[dict] = []

    def create(**kwargs):
        seen.append(kwargs)
        if "reasoning_effort" in kwargs and kwargs["model"] == "old-model":
            raise BadRequestError(
                "Unknown parameter: reasoning_effort", response=_resp(400), body=None
            )
        return _completion("{}")

    clk = FakeClock()
    llm = OpenAICompatibleLLM(
        "k",
        "https://example.test/v1",
        cache_dir=tmp_path / "c",
        sleep=clk.sleep,
        reasoning_effort={"gemini-3.8-flash": "low", "old-model": "low"},
        request_log=tmp_path / "requests.jsonl",
    )
    llm._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    llm.complete("gemini-3.8-flash", [{"role": "user", "content": "a"}])
    assert seen[0]["reasoning_effort"] == "low"
    llm.complete("old-model", [{"role": "user", "content": "b"}])
    assert "reasoning_effort" in seen[1] and "reasoning_effort" not in seen[2]
    llm.complete("gemini-3.8-flash", [{"role": "user", "content": "c"}])
    assert "reasoning_effort" not in seen[3]  # remembered: the server does not support it
    log = [json.loads(line) for line in (tmp_path / "requests.jsonl").read_text().splitlines()]
    assert len(log) == 4
    assert log[1]["ok"] is False and log[1]["status"] == 400
    assert log[0]["ok"] is True and log[0]["prompt_tokens"] == 10


def test_cache_is_utf8_regardless_of_platform_default(tmp_path, monkeypatch):
    def create(**kwargs):
        return _completion("“curly quotes” and an em—dash and 日本語")

    llm, _ = _llm(tmp_path, create)
    msgs = [{"role": "user", "content": "x"}]
    out = llm.complete("m", msgs)
    raw = next((tmp_path / "c").glob("*.json")).read_bytes()
    assert "日本語".encode() in raw  # utf-8 bytes on disk
    assert llm.complete("m", msgs) == out  # and reads back identically


def test_overloaded_model_falls_down_the_ladder(tmp_path):
    seen: list[str] = []

    def create(**kwargs):
        seen.append(kwargs["model"])
        if kwargs["model"] == "busy":
            raise InternalServerError("overloaded", response=_resp(503), body=None)
        return _completion('{"from": "' + kwargs["model"] + '"}')

    clk = FakeClock()
    llm = OpenAICompatibleLLM(
        "k",
        "https://example.test/v1",
        cache_dir=tmp_path / "c",
        sleep=clk.sleep,
        model_fallbacks={"busy": ["calm", "calmer"]},
        overload_threshold=3,
        request_log=tmp_path / "r.jsonl",
    )
    llm._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    out = llm.complete("busy", [{"role": "user", "content": "a"}])
    assert out == '{"from": "calm"}'
    assert seen == ["busy", "busy", "busy", "calm"]  # three 503s, then the fallback
    # The rest of the run goes straight to the fallback; no more 503 dances.
    llm.complete("busy", [{"role": "user", "content": "b"}])
    assert seen[-1] == "calm" and seen.count("busy") == 3
    assert llm.resolve_model("busy") == "calm"
    # Cache lookups find the fallback's answer under the requested model.
    assert llm.complete("busy", [{"role": "user", "content": "a"}]) == '{"from": "calm"}'
    assert llm.usage.cached_calls == 1
    assert "switching to fallback" in (tmp_path / "r.jsonl").read_text()


def test_no_fallback_means_503s_surface_after_retries(tmp_path):
    def create(**kwargs):
        raise InternalServerError("overloaded", response=_resp(503), body=None)

    llm, clk = _llm(tmp_path, create, retries=2)
    with pytest.raises(InternalServerError):
        llm.complete("m", [{"role": "user", "content": "a"}])
    assert len(clk.slept) == 2


def test_rate_limiter_meters_tokens_too():
    clk = FakeClock()
    rl = RateLimiter(10, 100_000, clock=clk.now, sleep=clk.sleep)
    assert rl.wait(80_000) == 0.0
    assert rl.wait(30_000) > 59  # would exceed 100k in the window → waits for the first to age out
    assert rl.wait(150_000) > 59  # waits for the window to empty, then an oversized call may go
    assert rl.wait(5_000) > 59  # ...and the next one waits for it to age out again
