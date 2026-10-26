"""Thin provider-agnostic chat client.

Gemini (via its OpenAI-compatible endpoint), Meta Model API, and local or hosted open-weights
servers (Ollama, LM Studio, vLLM, Baseten, Modal, Fireworks) all speak the OpenAI chat API.
One client class covers all of them; `MockLLM` lets the whole pipeline run offline.

Built for free tiers: client-side request pacing, retries with exponential backoff on 429/5xx
(honouring Retry-After), a JSON-mode fallback for servers that reject `response_format`, and a
disk cache keyed on (model, messages) so a re-run costs nothing. Usage is tallied at list price
so the report can print what the run *would* cost on a paid tier.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from canonkeeper.config import Settings

# USD per 1M tokens (input, output) at list price, 2026-10. Gemini free tier bills $0; the
# figure shown is what the same run costs on the paid tier. Unknown models cost 0.
PRICING: dict[str, tuple[float, float]] = {
    # Gemini — https://ai.google.dev/gemini-api/docs/pricing
    "gemini-4-argon": (2.00, 10.00),
    "gemini-3.8-flash": (0.75, 3.75),
    "gemini-3.6-flash": (0.75, 3.75),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.1-flash-lite": (0.25, 1.50),
    # Meta — https://dev.meta.ai/docs/pricing-rate-limits
    "muse-spark-1.3": (1.25, 4.25),
    "muse-spark-1.2": (1.25, 4.25),
    "muse-spark-1.1": (1.25, 4.25),
    "muse-wonder-1.0": (4.00, 20.00),
}

Message = dict[str, str]


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0
    cached_calls: int = 0
    retries: int = 0
    cost_usd: float = 0.0
    by_model: dict[str, dict[str, float]] = field(default_factory=dict)

    def add(self, model: str, prompt: int, completion: int, cached: bool) -> None:
        self.calls += 1
        if cached:
            self.cached_calls += 1
            return
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        pin, pout = PRICING.get(model, (0.0, 0.0))
        cost = prompt / 1e6 * pin + completion / 1e6 * pout
        self.cost_usd += cost
        m = self.by_model.setdefault(model, {"prompt": 0, "completion": 0, "cost_usd": 0.0})
        m["prompt"] += prompt
        m["completion"] += completion
        m["cost_usd"] += cost


class LLM(Protocol):
    usage: Usage

    def complete(
        self,
        model: str,
        messages: list[Message],
        *,
        json_mode: bool = False,
        reasoning_effort: str | None = None,
    ) -> str: ...


def _cache_key(
    model: str, messages: list[Message], json_mode: bool, effort: str | None = None
) -> str:
    """Cache key over everything that changes the answer. The default effort ("low" or unset)
    is left out of the key so entries written before effort was configurable stay valid."""
    payload: dict[str, Any] = {"m": model, "msgs": messages, "j": json_mode}
    if effort and effort != "low":
        payload["e"] = effort
    raw = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


class RateLimiter:
    """Sliding-window limiter on requests per minute and tokens per minute. Thread-safe.

    Free tiers meter both. A 150k-token adjudication call is one request but most of a
    minute's token budget, so a second one right behind it gets a 429 even at 2 RPM."""

    def __init__(
        self,
        rpm: int,
        tpm: int = 0,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.rpm = rpm
        self.tpm = tpm
        self._clock = clock
        self._sleep = sleep
        self._events: deque[tuple[float, int]] = deque()  # (time, tokens)
        self._lock = threading.Lock()

    def wait(self, tokens: int = 0) -> float:
        """Block until a call of `tokens` input tokens is allowed; return seconds slept."""
        if self.rpm <= 0 and self.tpm <= 0:
            return 0.0
        slept = 0.0
        with self._lock:
            while True:
                now = self._clock()
                while self._events and now - self._events[0][0] >= 60.0:
                    self._events.popleft()
                n = len(self._events)
                used = sum(t for _, t in self._events)
                rpm_ok = self.rpm <= 0 or n < self.rpm
                tpm_ok = self.tpm <= 0 or used + tokens <= self.tpm or (n == 0)
                if rpm_ok and tpm_ok:
                    self._events.append((now, tokens))
                    return slept
                delay = 60.0 - (now - self._events[0][0]) + 0.05
                self._sleep(delay)
                slept += delay


def _approx_tokens(messages: list[Message]) -> int:
    from canonkeeper.tokens import count_tokens

    return sum(count_tokens(m.get("content", "")) for m in messages) + 16 * len(messages)


def _retry_after_seconds(exc: Exception) -> float | None:
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None)
    if not headers:
        return None
    val = headers.get("retry-after") or headers.get("Retry-After")
    if not val:
        return None
    try:
        return float(val)
    except ValueError:
        return None


def _is_retryable(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if status in (408, 409, 425, 429, 500, 502, 503, 504):
        return True
    name = type(exc).__name__
    return name in {
        "APIConnectionError",
        "APITimeoutError",
        "RateLimitError",
        "InternalServerError",
    }


def _rejects_response_format(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    return status == 400 and "response_format" in str(exc).lower()


class _Overloaded(Exception):
    """Raised inside the retry loop when a model has hit the 503 threshold and a fallback exists."""


class DailyQuotaExhausted(RuntimeError):
    """The free tier's requests-per-day cap is spent for every model on the ladder.
    `resets_in_s` is the provider's own estimate; the cache means a later run resumes."""

    def __init__(self, model: str, resets_in_s: float | None) -> None:
        self.model = model
        self.resets_in_s = resets_in_s
        when = f"in {_fmt_duration(resets_in_s)}" if resets_in_s else "later today"
        super().__init__(
            f"daily request quota exhausted for {model} (and every fallback). It resets {when}; "
            "run the same command again then — finished chunks are cached and are not re-sent."
        )


_RETRY_IN = re.compile(r"retry in (?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?", re.I)


def _fmt_duration(sec: float | None) -> str:
    if not sec:
        return "?"
    h, rem = divmod(int(sec), 3600)
    m = rem // 60
    return f"{h}h{m:02d}m" if h else f"{m}m"


def _daily_quota_hit(exc: Exception) -> float | None:
    """If this 429 is the per-day cap, return the provider's retry-in seconds (or 0)."""
    text = str(getattr(exc, "body", None) or exc)
    if "PerDay" not in text and "per day" not in text.lower():
        return None
    m = _RETRY_IN.search(text)
    if not m:
        return 0.0
    h, mi, sec = (float(x) if x else 0.0 for x in m.groups())
    return h * 3600 + mi * 60 + sec


class OpenAICompatibleLLM:
    """Works against Gemini, Meta Model API, or any OpenAI-compatible endpoint."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        *,
        cache_dir: Path | None = None,
        temperature: float = 0.0,
        timeout_s: float = 180.0,
        requests_per_minute: int = 0,
        tokens_per_minute: int = 0,
        max_retries: int = 6,
        reasoning_effort: dict[str, str] | None = None,
        request_log: Path | None = None,
        model_fallbacks: dict[str, list[str]] | None = None,
        overload_threshold: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        from openai import OpenAI  # local import keeps the mock path dependency-free

        # Our own retry loop handles backoff; the SDK's built-in retries would double up.
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout_s, max_retries=0)
        self._cache_dir = cache_dir
        self._temperature = temperature
        self._limiter = RateLimiter(requests_per_minute, tokens_per_minute, sleep=sleep)
        self._max_retries = max_retries
        self._sleep = sleep
        self._json_mode_supported: bool | None = None
        self._reasoning_effort = reasoning_effort or {}
        """model id -> reasoning_effort value ("minimal", "low", "medium", "high", "none")."""
        self._reasoning_supported: bool | None = None
        self._request_log = request_log
        self._fallbacks = model_fallbacks or {}
        self._overload_threshold = overload_threshold
        self._overloaded: set[str] = set()
        """Models that answered 503 `overload_threshold` times in a row this run."""
        self._consecutive_503: dict[str, int] = {}
        self.usage = Usage()
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)

    def _log(self, **fields: Any) -> None:
        """One JSON line per attempt: what was sent, what came back, how long it took."""
        if not self._request_log:
            return
        try:
            self._request_log.parent.mkdir(parents=True, exist_ok=True)
            with self._request_log.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **fields}) + "\n")
        except OSError:
            pass

    # --- one request with retries ----------------------------------------------------------
    def _next_model(self, model: str) -> str | None:
        for m in self._fallbacks.get(model, []):
            if m not in self._overloaded:
                return m
        return None

    def resolve_model(self, model: str) -> str:
        """The model that will actually be called: the requested one unless it is overloaded."""
        seen: set[str] = set()
        while model in self._overloaded and model not in seen:
            seen.add(model)
            nxt = self._next_model(model)
            if not nxt:
                break
            model = nxt
        return model

    def _create(self, kwargs: dict[str, Any]) -> Any:
        attempt = 0
        model = kwargs.get("model", "?")
        approx = _approx_tokens(kwargs.get("messages", []))
        while True:
            waited = self._limiter.wait(approx)
            t0 = time.monotonic()
            try:
                resp = self._client.chat.completions.create(**kwargs)
                self._consecutive_503[model] = 0
                u = getattr(resp, "usage", None)
                self._log(
                    model=model,
                    attempt=attempt,
                    ok=True,
                    waited_s=round(waited, 1),
                    latency_s=round(time.monotonic() - t0, 1),
                    prompt_tokens=getattr(u, "prompt_tokens", None),
                    completion_tokens=getattr(u, "completion_tokens", None),
                )
                return resp
            except Exception as exc:
                status = getattr(exc, "status_code", None)
                body = getattr(exc, "body", None)
                detail = json.dumps(body) if isinstance(body, dict) else str(body or exc)
                self._log(
                    model=model,
                    attempt=attempt,
                    ok=False,
                    status=status,
                    error=f"{type(exc).__name__}: {detail[:1200]}",
                    latency_s=round(time.monotonic() - t0, 1),
                )
                daily = _daily_quota_hit(exc) if status == 429 else None
                if daily is not None:
                    self._overloaded.add(model)
                    self._log(
                        model=model,
                        ok=False,
                        status=429,
                        error=f"daily quota exhausted; resets in {_fmt_duration(daily)}",
                    )
                    if self._next_model(model):
                        raise _Overloaded(model) from exc
                    raise DailyQuotaExhausted(model, daily) from exc
                if status in (429, 503):
                    # Both mean "not now" on a free tier; after a few in a row, move down
                    # the ladder rather than burning the whole retry budget on one model.
                    n = self._consecutive_503.get(model, 0) + 1
                    self._consecutive_503[model] = n
                    if n >= self._overload_threshold and self._next_model(model):
                        self._overloaded.add(model)
                        self._log(
                            model=model,
                            ok=False,
                            status=status,
                            error="unavailable; switching to fallback model",
                        )
                        raise _Overloaded(model) from exc
                else:
                    self._consecutive_503[model] = 0
                if "response_format" in kwargs and _rejects_response_format(exc):
                    self._json_mode_supported = False
                    kwargs = {k: v for k, v in kwargs.items() if k != "response_format"}
                    continue
                if (
                    "reasoning_effort" in kwargs
                    and status == 400
                    and "reasoning" in str(exc).lower()
                ):
                    self._reasoning_supported = False
                    kwargs = {k: v for k, v in kwargs.items() if k != "reasoning_effort"}
                    continue
                if attempt >= self._max_retries or not _is_retryable(exc):
                    raise
                attempt += 1
                self.usage.retries += 1
                delay = _retry_after_seconds(exc)
                if delay is None:
                    delay = min(60.0, 2.0**attempt) + random.uniform(0, 1)
                self._sleep(delay)

    def complete(
        self,
        model: str,
        messages: list[Message],
        *,
        json_mode: bool = False,
        reasoning_effort: str | None = None,
    ) -> str:
        # Cache: the requested model first, then anything on its ladder we may have used before.
        effort_req = reasoning_effort or self._reasoning_effort.get(model)
        if self._cache_dir:
            for m in [model, *self._fallbacks.get(model, [])]:
                e = reasoning_effort or self._reasoning_effort.get(m) or effort_req
                hit = self._cache_dir / f"{_cache_key(m, messages, json_mode, e)}.json"
                if hit.exists():
                    data = json.loads(hit.read_text(encoding="utf-8"))
                    self.usage.add(m, 0, 0, cached=True)
                    return str(data["content"])

        requested = model
        model = self.resolve_model(requested)
        while True:
            kwargs: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "temperature": self._temperature,
            }
            if json_mode and self._json_mode_supported is not False:
                kwargs["response_format"] = {"type": "json_object"}
            effort = (
                reasoning_effort
                or self._reasoning_effort.get(model)
                or self._reasoning_effort.get(requested)
            )
            if effort and self._reasoning_supported is not False:
                kwargs["reasoning_effort"] = effort
            try:
                resp = self._create(kwargs)
                break
            except _Overloaded:
                nxt = self._next_model(model)
                if not nxt:
                    raise
                model = nxt
        key = _cache_key(model, messages, json_mode, effort)
        content = resp.choices[0].message.content or ""
        usage = resp.usage
        self.usage.add(
            model,
            getattr(usage, "prompt_tokens", 0) or 0,
            getattr(usage, "completion_tokens", 0) or 0,
            cached=False,
        )
        if self._cache_dir:
            (self._cache_dir / f"{key}.json").write_text(
                json.dumps({"model": model, "content": content}, ensure_ascii=False),
                encoding="utf-8",
            )
        return content


class MockLLM:
    """Deterministic stand-in. Pass a `handler(model, messages) -> str`, or rely on the default
    rule-based handler in `canonkeeper.llm.rules`, which recognises a small set of sentence
    shapes and judges candidates with a fixed policy — enough to run every command offline."""

    def __init__(self, handler: Callable[[str, list[Message]], str] | None = None) -> None:
        from canonkeeper.llm import rules

        self._handler = handler or rules.handler
        self.usage = Usage()
        self.calls: list[tuple[str, list[Message]]] = []

    def complete(
        self,
        model: str,
        messages: list[Message],
        *,
        json_mode: bool = False,
        reasoning_effort: str | None = None,
    ) -> str:
        self.calls.append((model, messages))
        out = self._handler(model, messages)
        self.usage.add(model, sum(len(m["content"]) // 4 for m in messages), len(out) // 4, False)
        return out


_KEY_HELP = {
    "gemini": "GEMINI_API_KEY is not set. Create a free key at https://aistudio.google.com/apikey "
    "(no card needed) and put it in .env, or run with --provider mock.",
    "meta": "MODEL_API_KEY is not set. Create a key at https://dev.meta.ai and put it in .env.",
    "openweights": "provider=openweights needs CANONKEEPER_OPENWEIGHTS_BASE_URL "
    "(e.g. http://localhost:11434/v1 for Ollama, http://localhost:1234/v1 for LM Studio).",
}


def make_llm(settings: Settings, *, mock_handler: Callable[..., str] | None = None) -> LLM:
    if settings.provider == "mock":
        return MockLLM(mock_handler)
    if settings.provider == "openweights" and not settings.base_url:
        raise RuntimeError(_KEY_HELP["openweights"])
    key = settings.api_key
    if not key:
        raise RuntimeError(_KEY_HELP[settings.provider])
    return OpenAICompatibleLLM(
        key,
        settings.base_url,
        cache_dir=settings.cache_dir,
        temperature=settings.temperature,
        timeout_s=settings.request_timeout_s,
        requests_per_minute=settings.requests_per_minute,
        tokens_per_minute=settings.tokens_per_minute,
        max_retries=settings.max_retries,
        reasoning_effort={
            settings.extract_model: settings.extract_reasoning_effort,
            settings.adjudicate_model: settings.adjudicate_reasoning_effort,
        },
        request_log=settings.workdir / "requests.jsonl",
        model_fallbacks=settings.model_fallbacks,
    )


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def parse_json(text: str) -> Any:
    """Tolerant JSON parse: strips code fences and surrounding prose, finds the outermost
    object, removes trailing commas and stray control characters. Raises ValueError when the
    text cannot be read as JSON at all."""
    cleaned = _FENCE.sub("", text.strip())
    candidates = [cleaned]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        candidates.append(cleaned[start : end + 1])
    last: Exception | None = None
    for cand in candidates:
        for attempt in (cand, _TRAILING_COMMA.sub(r"\1", _CONTROL.sub("", cand))):
            try:
                return json.loads(attempt, strict=False)
            except json.JSONDecodeError as e:
                last = e
    raise ValueError(f"not JSON: {last}")


REPAIR_PROMPT = (
    "The following was supposed to be a single valid JSON object but is malformed. Return the "
    "same content as valid JSON and nothing else — no commentary, no code fences. Fix only "
    "the syntax (quotes, commas, brackets, escaping); do not change or drop any values.\n\n"
)


def repair_json(llm: LLM, model: str, broken: str) -> Any | None:
    """One cheap call asking the model to fix its own JSON. None if that fails too."""
    try:
        fixed = llm.complete(
            model, [{"role": "user", "content": REPAIR_PROMPT + broken[:60_000]}], json_mode=True
        )
        return parse_json(fixed)
    except Exception:  # noqa: BLE001 - the caller counts and skips
        return None
