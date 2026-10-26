"""Runtime settings. Everything is overridable via environment variables or a .env file.

Model IDs are those published on Meta Model API (https://dev.meta.ai/docs/overview) as of
2026-10-26. Base URL and auth follow the OpenAI-compatible scheme Meta documents.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Provider = Literal["meta", "openweights", "mock"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CANONKEEPER_", env_file=".env", extra="ignore")

    # --- provider -------------------------------------------------------------------------
    provider: Provider = "meta"
    """`meta`: Meta Model API. `openweights`: any OpenAI-compatible host serving Muse Spark Open /
    Muse Glimmer (Baseten, Modal, Fireworks, vLLM). `mock`: deterministic offline responses
    for tests and demos without a key."""

    meta_api_key: str | None = Field(default=None, alias="MODEL_API_KEY")
    meta_base_url: str = "https://api.meta.ai/v1"

    openweights_api_key: str | None = None
    openweights_base_url: str | None = None
    openweights_model: str = "muse-spark-open"

    # --- models ----------------------------------------------------------------------------
    extract_model: str = "muse-spark-1.3"
    """Cheap, 1M-context model used for per-chapter structured claim extraction."""
    adjudicate_model: str = "muse-wonder-1.0"
    """Frontier model used to judge whether a candidate conflict is a real contradiction,
    a deliberate reveal, or noise. Gets the full series when it fits."""
    image_model: str = "muse-image-1.0"
    """Optional: renders character / setting reference cards from the extracted canon."""

    # --- budgets ---------------------------------------------------------------------------
    extract_chunk_tokens: int = 6000
    """Chapters longer than this are split before extraction."""
    adjudicate_context_tokens: int = 900_000
    """Hard ceiling on series text passed to the adjudicator (1,048,576 is the model max).
    Lower it (e.g. 150000) while iterating; raise it for the final run."""
    adjudicate_batch_size: int = 8
    """Candidates judged per adjudicator call. The series text is sent once per batch, so
    this divides the dominant cost; 8 keeps each verdict list short enough to stay accurate."""
    max_candidates_per_run: int = 200
    temperature: float = 0.0
    request_timeout_s: float = 120.0

    # --- paths -----------------------------------------------------------------------------
    workdir: Path = Path(".canonkeeper")
    cache_dir: Path = Path(".canonkeeper/cache")


def load_settings(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[arg-type]
