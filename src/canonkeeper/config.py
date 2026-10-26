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
    }
    extract_model: dict[str, str] = {
        "gemini": "gemini-3.5-flash-lite",  # fast and actually serving on the free tier
        "meta": "muse-spark-1.3",
        "openweights": "muse-spark-open",
        "mock": "mock-extract",
    }
    adjudicate_model: dict[str, str] = {
        "gemini": "gemini-3.6-flash",  # 3.8 Flash when it is not overloaded; Argon is paid-only
        "meta": "muse-wonder-1.0",
        "openweights": "muse-spark-open",
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
    # Client-side request pacing. Gemini's free tier is roughly 10 RPM on the Flash models
    # (not published per model; see AI Studio → rate limits), so we stay just under it.
    requests_per_minute: dict[str, int] = {"gemini": 9, "meta": 0, "openweights": 0, "mock": 0}
    # Free-tier Flash models also meter input tokens per minute (not published per model;
    # 250k is the historical figure). Stay under it so a long adjudication call is not
    # followed by a 429 for the next one.
    tokens_per_minute: dict[str, int] = {"gemini": 150_000, "meta": 0, "openweights": 0, "mock": 0}
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

    extract_reasoning_effort: str = "low"
    """`reasoning_effort` sent with extraction calls. Extraction is transcription, not
    reasoning; a thinking model left on its default budget spends minutes per chunk."""
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
            self.base_url = self.openweights_base_url or ProviderDefaults.base_url.get(p, "")
        if not self.extract_model:
            self.extract_model = ProviderDefaults.extract_model[p]
        if not self.adjudicate_model:
            self.adjudicate_model = ProviderDefaults.adjudicate_model[p]
        if self.requests_per_minute < 0:
            self.requests_per_minute = ProviderDefaults.requests_per_minute[p]
        if self.tokens_per_minute < 0:
            self.tokens_per_minute = ProviderDefaults.tokens_per_minute[p]
        return self

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
    def trains_on_prompts(self) -> bool:
        return ProviderDefaults.trains_on_prompts.get(self.provider, False)


def load_settings(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[arg-type]
