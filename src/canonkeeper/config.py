"""Runtime settings. Everything is overridable via environment variables or a .env file.

Providers are all OpenAI-compatible chat endpoints; only the base URL, key, default models and
rate limits differ. Model IDs verified 2026-10-26 against:
  Gemini — https://ai.google.dev/gemini-api/docs/models  (OpenAI-compat: /v1beta/openai/)
  Meta   — https://dev.meta.ai/docs/overview
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Provider = Literal["gemini", "meta", "openweights", "mock"]


class ProviderDefaults:
    """Per-provider defaults, applied when the corresponding setting is left empty."""

    base_url: dict[str, str] = {
        "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "meta": "https://api.meta.ai/v1",
        # Ollama's OpenAI-compatible endpoint; LM Studio is http://localhost:1234/v1.
        "openweights": "http://localhost:11434/v1",
    }
    extract_model: dict[str, str] = {
        "gemini": "gemini-3.5-flash-lite",  # fast and actually serving on the free tier
        "meta": "muse-spark-1.3",
        "openweights": "qwen3:4b",  # 2.5 GB, runs on a laptop CPU; `ollama pull qwen3:4b`
        "mock": "mock-extract",
    }
    adjudicate_model: dict[str, str] = {
        "gemini": "gemini-3.6-flash",  # 3.8 Flash when it is not overloaded; Argon is paid-only
        "meta": "muse-wonder-1.0",
        "openweights": "qwen3:4b",
        "mock": "mock-adjudicate",
    }
    # If a model keeps answering 503 (overloaded) the client moves down this ladder for the rest
    # of the run. Free-tier capacity varies by the hour; scripts/probe_models.py shows what is up.
    fallbacks: dict[str, dict[str, list[str]]] = {
        "gemini": {
            "gemini-4-argon": ["gemini-3.8-flash", "gemini-3.6-flash"],
            "gemini-3.8-flash": ["gemini-3.6-flash", "gemini-3.5-flash-lite"],
            "gemini-3.6-flash": ["gemini-3.8-flash", "gemini-3.5-flash-lite"],
            "gemini-3.5-flash-lite": ["gemini-3.6-flash", "gemini-3.1-flash-lite"],
            "gemini-3.1-flash-lite": ["gemini-3.5-flash-lite", "gemini-3.6-flash"],
        }
    }
    # Free-tier requests-per-day caps observed on 2026-10-26 (per project, per model). The
    # client stops a run cleanly when today's count reaches the cap instead of discovering it
    # mid-run with a 429. Override with CANONKEEPER_DAILY_BUDGET='{"model": n}'.
    daily_budget: dict[str, dict[str, int]] = {
        "gemini": {
            "gemini-3.5-flash-lite": 500,
            "gemini-3.1-flash-lite": 500,
            "gemini-3.6-flash": 20,
            "gemini-3.8-flash": 20,
        }
    }
    # Hour offset from UTC at which the provider's daily counters reset (Gemini: midnight
    # Pacific; -8 is the conservative choice across DST).
    day_reset_utc_offset: dict[str, int] = {"gemini": -8}
    # Client-side request pacing. Gemini's free tier is roughly 10 RPM on the Flash models
    # (not published per model; see AI Studio → rate limits), so we stay just under it.
    requests_per_minute: dict[str, int] = {"gemini": 9, "meta": 0, "openweights": 0, "mock": 0}
    # Free-tier Flash models also meter input tokens per minute (not published per model;
    # 250k is the historical figure). Stay under it so a long adjudication call is not
    # followed by a 429 for the next one.
    tokens_per_minute: dict[str, int] = {"gemini": 150_000, "meta": 0, "openweights": 0, "mock": 0}
    # Settings whose general default is tuned for a cloud API. A 4B model on a laptop CPU
    # answers a short JSON request in ~3 s with thinking off and was still thinking after
    # 55 s at "low" (qwen3:4b, Ollama 0.21, 2026-10-26); its context window is set by the
    # server (OLLAMA_CONTEXT_LENGTH=16384 recommended), so the adjudicator's series excerpt
    # must stay well inside it.
    overrides: dict[str, dict[str, object]] = {
        "openweights": {
            "extract_reasoning_effort": "none",
            "extract_sweep_reasoning_effort": "none",
            "adjudicate_reasoning_effort": "none",
            "adjudicate_context_tokens": 8_000,
            "adjudicate_batch_size": 4,
            "request_timeout_s": 900,
        }
    }
    # Free-tier Gemini prompts are "used to improve our products" (pricing page). Public-domain
    # corpora: fine. Private manuscripts: not fine. The CLI warns when this is true.
    trains_on_prompts: dict[str, bool] = {
        "gemini": True,
        "meta": False,
        "openweights": False,
        "mock": False,
    }


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CANONKEEPER_", env_file=".env", extra="ignore")

    # --- provider -------------------------------------------------------------------------
    provider: Provider = "gemini"
    """`gemini`: Gemini API (free tier needs no card). `meta`: Meta Model API. `openweights`:
    any OpenAI-compatible host (Ollama, LM Studio, vLLM, Baseten, Modal, Fireworks). `mock`:
    deterministic offline stand-in for tests and demos."""

    gemini_api_key: str | None = Field(default=None, alias="GEMINI_API_KEY")
    meta_api_key: str | None = Field(default=None, alias="MODEL_API_KEY")
    openweights_api_key: str | None = None
    openweights_base_url: str | None = None

    base_url: str = ""
    """Resolved from the provider when empty."""

    # --- models ----------------------------------------------------------------------------
    extract_model: str = ""
    """Cheap, long-context model used for per-chapter structured claim extraction."""
    adjudicate_model: str = ""
    """Stronger model that judges whether a candidate conflict is a real contradiction,
    a deliberate reveal, or noise. Gets as much of the series as the budget allows."""

    extract_mode: str = "two-pass"
    """`two-pass`: general prompt then a narrow sweep (2 calls per chunk). `merged`: one call
    with the sweep folded into the general prompt. `general`: one call, general prompt only.
    Compare them with scripts/probe_extraction.py --mode before spending a day's budget."""

    @property
    def extract_sweep(self) -> bool:
        return self.extract_mode == "two-pass"

    extract_hints: bool = False
    """Append salience hints to each extraction request: the chunk's sentences that look like
    physical or biographical facts (extract/salience.py). Measured 2026-10-27 and left OFF: on
    20 planted facts it raised extraction recall from 10/20 to 16/20, but on 15 random real
    Holmes chunks it cut facts from 305 to 274 and descriptive/biographical ones from 175 to
    160 (scripts/probe_hints_real.py). It helps text that looks like the benchmark, not books."""

    extract_hint_passes: str = ""
    """Which extraction passes get the salience hints: "none", "sweep" or "all" (empty: "all"
    if `extract_hints` else "none"). Measured 2026-10-27 by pass (scripts/probe_hints_by_pass.py,
    no new requests): the real-text loss above was all in the general pass (229 -> 200 facts);
    the sweep pass held (69 -> 67 facts, descriptive 58 -> 63). Hints on the sweep only: planted
    recall 14/20 (from 10/20), real text 274 facts / 153 descriptive (from 268 / 145), with
    gained and lost facts of similar precision on reading. Opt-in until a full `eval` confirms
    it: CANONKEEPER_EXTRACT_HINT_PASSES=sweep."""

    def hints_for(self, sweep_pass: bool) -> bool:
        mode = self.extract_hint_passes or ("all" if self.extract_hints else "none")
        return mode == "all" or (mode == "sweep" and sweep_pass)

    extract_reasoning_effort: str = "medium"
    """`reasoning_effort` for the general extraction pass. On the Holmes benchmark, medium
    doubled recall of planted single-sentence facts over low (14/20 vs 7/20) at ~10 s a call
    on Flash-Lite; the model's default budget is far slower still."""
    extract_sweep_reasoning_effort: str = "low"
    """Effort for the narrow sweep pass, which is a scan rather than a judgement."""
    adjudicate_reasoning_effort: str = "medium"
    """Adjudication is where thinking pays. Raise to "high" for the final run."""

    # --- budgets ---------------------------------------------------------------------------
    extract_chunk_tokens: int = 3500
    """Chapters longer than this are split before extraction. Smaller chunks get denser
    extraction from Flash-class models; with Flash-Lite at ~2 s a call the extra calls are cheap."""
    adjudicate_context_tokens: int = 60_000
    """Ceiling on series text passed to the adjudicator per call. 1M-context models can take a
    whole series, but free tiers also cap tokens per minute, so the default stays well under
    that; the chapters each batch's quotes came from always go in first. Raise it (up to
    ~900000) for a final run on a paid tier."""
    adjudicate_batch_size: int = 8
    """Candidates judged per adjudicator call. The series text is sent once per batch, so
    this divides the dominant cost; 8 keeps each verdict list short enough to stay accurate."""
    max_candidates_per_run: int = 200
    temperature: float = 0.0
    request_timeout_s: float = 300.0
    """Long-context adjudication calls on a thinking model can take a few minutes."""
    requests_per_minute: int = -1
    """Client-side pacing; -1 means use the provider default, 0 means unlimited."""
    daily_budget: dict[str, int] = Field(default_factory=dict)
    """Requests per model per day the client may spend; empty = provider default, {} after
    resolution means no guard. The count comes from the request log, so it only sees this
    workdir's runs."""
    budget_margin: int = 0
    """Requests to keep in reserve under each daily cap (e.g. for a `check` later today)."""
    tokens_per_minute: int = -1
    """Client-side token pacing (input tokens); -1 provider default, 0 unlimited."""
    max_retries: int = 6
    """Retries on 429 / 5xx with exponential backoff (Retry-After honoured when sent)."""

    # --- paths -----------------------------------------------------------------------------
    workdir: Path = Path(".canonkeeper")
    cache_dir: Path = Path(".canonkeeper/cache")

    @model_validator(mode="after")
    def _apply_provider_defaults(self) -> Settings:
        p = self.provider
        if not self.base_url:
            local = self.openweights_base_url if p == "openweights" else None
            self.base_url = local or ProviderDefaults.base_url.get(p, "")
        if not self.extract_model:
            self.extract_model = ProviderDefaults.extract_model[p]
        if not self.adjudicate_model:
            self.adjudicate_model = ProviderDefaults.adjudicate_model[p]
        if self.requests_per_minute < 0:
            self.requests_per_minute = ProviderDefaults.requests_per_minute[p]
        if self.tokens_per_minute < 0:
            self.tokens_per_minute = ProviderDefaults.tokens_per_minute[p]
        if not self.daily_budget:
            self.daily_budget = dict(ProviderDefaults.daily_budget.get(p, {}))
        # Provider-specific values for settings whose general default suits a cloud API. Only
        # applied when the user did not set the field themselves.
        for field, value in ProviderDefaults.overrides.get(p, {}).items():
            if field not in self.model_fields_set:
                setattr(self, field, value)
        return self

    @property
    def day_reset_utc_offset(self) -> int:
        return ProviderDefaults.day_reset_utc_offset.get(self.provider, 0)

    @property
    def model_fallbacks(self) -> dict[str, list[str]]:
        return ProviderDefaults.fallbacks.get(self.provider, {})

    @property
    def api_key(self) -> str | None:
        return {
            "gemini": self.gemini_api_key,
            "meta": self.meta_api_key,
            "openweights": self.openweights_api_key or "not-needed",
            "mock": "mock",
        }[self.provider]

    @property
    def can_run_models(self) -> bool:
        """A key is configured for the provider (or none is needed). Preflight refuses to
        quote or take payment without one: every paid run would fail."""
        return bool(self.api_key)

    @property
    def trains_on_prompts(self) -> bool:
        return ProviderDefaults.trains_on_prompts.get(self.provider, False)


def load_settings(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[arg-type]
