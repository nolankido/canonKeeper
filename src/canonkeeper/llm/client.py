"""Thin provider-agnostic chat client.

Meta Model API is OpenAI-SDK compatible (base URL https://api.meta.ai/v1, bearer key), and so are
the open-weights hosts (Baseten / Modal / Fireworks / vLLM). One client class covers all of them;
the `MockLLM` lets the whole pipeline run offline in tests and demos.

Every call is cached on disk by (model, messages) so re-running a series costs nothing, and
usage is tallied so the report can print a real dollar figure.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from canonkeeper.config import Settings

# USD per 1M tokens, Standard mode, as published on dev.meta.ai/docs/pricing-rate-limits
# (2026-10). Unknown models cost 0 so the tally never crashes.
PRICING: dict[str, tuple[float, float]] = {
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

    def complete(self, model: str, messages: list[Message], *, json_mode: bool = False) -> str: ...


def _cache_key(model: str, messages: list[Message], json_mode: bool) -> str:
    raw = json.dumps({"m": model, "msgs": messages, "j": json_mode}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


class OpenAICompatibleLLM:
    """Works against Meta Model API or any OpenAI-compatible endpoint."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        *,
        cache_dir: Path | None = None,
        temperature: float = 0.0,
        timeout_s: float = 120.0,
    ) -> None:
        from openai import OpenAI  # local import keeps the mock path dependency-free

        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout_s)
        self._cache_dir = cache_dir
        self._temperature = temperature
        self.usage = Usage()
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)

    def complete(self, model: str, messages: list[Message], *, json_mode: bool = False) -> str:
        key = _cache_key(model, messages, json_mode)
        if self._cache_dir:
            hit = self._cache_dir / f"{key}.json"
            if hit.exists():
                data = json.loads(hit.read_text())
                self.usage.add(model, 0, 0, cached=True)
                return str(data["content"])

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": self._temperature,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = self._client.chat.completions.create(**kwargs)
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
                json.dumps({"model": model, "content": content}, ensure_ascii=False)
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

    def complete(self, model: str, messages: list[Message], *, json_mode: bool = False) -> str:
        self.calls.append((model, messages))
        out = self._handler(model, messages)
        self.usage.add(model, sum(len(m["content"]) // 4 for m in messages), len(out) // 4, False)
        return out


def make_llm(settings: Settings, *, mock_handler: Callable[..., str] | None = None) -> LLM:
    if settings.provider == "mock":
        return MockLLM(mock_handler)
    if settings.provider == "openweights":
        if not settings.openweights_base_url or not settings.openweights_api_key:
            raise RuntimeError(
                "provider=openweights needs CANONKEEPER_OPENWEIGHTS_BASE_URL and "
                "CANONKEEPER_OPENWEIGHTS_API_KEY"
            )
        return OpenAICompatibleLLM(
            settings.openweights_api_key,
            settings.openweights_base_url,
            cache_dir=settings.cache_dir,
            temperature=settings.temperature,
            timeout_s=settings.request_timeout_s,
        )
    if not settings.meta_api_key:
        raise RuntimeError(
            "MODEL_API_KEY is not set. Create a key at https://dev.meta.ai and put it in .env, "
            "or run with CANONKEEPER_PROVIDER=mock."
        )
    return OpenAICompatibleLLM(
        settings.meta_api_key,
        settings.meta_base_url,
        cache_dir=settings.cache_dir,
        temperature=settings.temperature,
        timeout_s=settings.request_timeout_s,
    )


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_json(text: str) -> Any:
    """Tolerant JSON parse: strips code fences and leading prose, finds the outermost object."""
    cleaned = _FENCE.sub("", text.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1:
            raise
        return json.loads(cleaned[start : end + 1])
