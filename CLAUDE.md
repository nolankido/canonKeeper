# canonkeeper — agent working notes

Hackathon project: **PayPal AI Hackathon 2026** (Devpost), entered as *Preflight* — pay-per-audit
continuity checking. Submissions due **Thu Nov 12 2026, 12:00 PM PT** (10:00 AM Hawaii).
Record the demo video by Nov 9. Existing projects are allowed; the repo must stay open source
with a visible LICENSE; the demo video must be < 3 min on YouTube.

## What this is

Series-continuity engine. Pipeline: corpus → chapterize → extract claims (cheap long-context
model) → JSONL bible → local candidate heuristics → adjudicate (stronger model, up to ~900k
tokens of context) → HTML report. Default provider is the Gemini free tier (no card). `README.md` has the overview; `docs/ARCHITECTURE.md` has the reasoning.

## Non-negotiables

- **Provenance or nothing.** A claim without a verbatim, grounded quote is dropped, never kept.
  Do not weaken `_ground()` in `extract/extractor.py` to raise recall.
- **Fix later books, never earlier ones.** Earlier books are published. Suggested fixes always
  target the later passage.
- **Public-domain corpora only** in this repo and in any demo material (`evals/corpora/`).
  Never add real author manuscripts, private drafts, or anything from the maintainer's own
  catalog. `drafts/` is git-ignored for local use only.
- **Free tiers may train on prompts.** Gemini's free tier does. Public-domain corpora only go
  through it; the CLI warns. Private manuscripts go through `--provider openweights` (local).
- **No credit card, ever.** Every default must work on a free tier or a local model.
- **Keep the mock provider working.** CI and tests run offline via `MockLLM`. Any change to the
  extraction or adjudication contract must keep `tests/` green with no API key.
- **Measure, don't eyeball.** Prompt changes are evaluated with `canonkeeper eval`, and the
  README benchmark table is updated from `result.md`, not typed by hand.

## Commands

```bash
pip install -e ".[dev]"
pytest                      # offline, mock provider
ruff check src tests && ruff format src tests && mypy
canonkeeper doctor          # verify provider/key/pacing with one tiny call
canonkeeper demo            # offline, end to end
canonkeeper fetch|estimate|build|bible|audit|check|eval evals/corpora/sherlock-holmes/series.json
```

## Layout

- `src/canonkeeper/config.py` — all settings; per-provider defaults in `ProviderDefaults`.
- `src/canonkeeper/llm/client.py` — OpenAI-compatible client: sliding-window pacing, 429/5xx
  backoff with Retry-After, JSON-mode fallback, disk cache, list-price tally (`PRICING`).
- `src/canonkeeper/extract/` — prompts + extractor. Predicate vocabulary is in `prompts.py`;
  keep it in sync with `audit/candidates.py::SINGLE_VALUED`.
- `src/canonkeeper/audit/candidates.py` — cheap heuristics; over-generate, let Wonder decide.
- `src/canonkeeper/audit/adjudicate.py` — batched adjudication; series text is user message #1
  (stable, cacheable), candidates are message #2. Keep that order.
- `src/canonkeeper/canon/normalize.py` — predicate/value synonym tables applied at ingest.
- `src/canonkeeper/llm/rules.py` — the rule-based mock. When adding an injection template to
  `eval/inject.py::TEMPLATES`, add a matching pattern here so the eval test keeps recall 1.0.
- `src/canonkeeper/demo.py` — the invented Saltmarsh series; `canonkeeper demo` must keep
  finding all five planted errors (tested).
- `src/canonkeeper/eval/` — defect injection (`TEMPLATES`) and scoring.
- `tests/conftest.py` — fixture series with planted defects; imports the mock from `llm/rules.py`.

## Conventions

- Python 3.11+, ruff (line length 100), mypy clean, pydantic v2.
- Small focused commits with conventional prefixes (`feat:`, `fix:`, `eval:`, `docs:`).
- Model IDs (2026-10-26): Gemini `gemini-3.8-flash` (free), `gemini-4-argon` (paid), per
  https://ai.google.dev/gemini-api/docs/models; Meta `muse-spark-1.3`, `muse-wonder-1.0`.
  Verify before changing.
